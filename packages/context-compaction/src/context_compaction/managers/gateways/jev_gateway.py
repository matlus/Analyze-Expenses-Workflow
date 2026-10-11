"""Translate Jev SDK answers into validated context-retention probabilities."""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Set as AbstractSet
from dataclasses import asdict, dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Final, Self

import httpx2
from pydantic import SecretStr, ValidationError
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Noul,
    RetryPolicy,
    SystemOneResponse,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
)

from context_compaction.managers.exceptions.jev_gateway_exception import (
    JevAnswerSetMismatchException,
    JevClientCleanupException,
    JevGatewayClosedException,
    JevProbabilityOutOfRangeException,
    JevRateLimitRetriesExhaustedException,
    JevRequestFailedException,
    JevRequestRejectedException,
    JevResponseSchemaInvalidException,
    JevServerRetryRequestedRetriesExhaustedException,
    JevTransportCleanupException,
)
from context_compaction.managers.models.context_models import JevDecisionBatch, JevSettings, RetentionQuestion
from context_compaction.managers.models.records import JevState


# One provider answer that the retry policy admits for another attempt.
@dataclass(frozen=True)
class JevRetryableFailure:
    http_status: int
    retry_after_seconds: int | None
    provider_message: str
    provider_error: TypeSafeAPIError


class JevGateway:
    # The gateway owns the only retry policy: SDK retries are disabled, HTTP 429 and HTTP 500 responses that ask
    # to be retried are retried, every other 4xx is rejected at once, and other 5xx or transport failures are not retried.
    TARGET_NAME: Final[str] = "Jev via OpenRouter"
    REQUEST_FAILED_MESSAGE: Final[str] = f"{TARGET_NAME} context-retention request failed."
    MAXIMUM_REQUEST_ATTEMPTS: Final[int] = 5
    INITIAL_RETRY_DELAY_SECONDS: Final[float] = 1.0
    RETRY_DELAY_MULTIPLIER: Final[float] = 2.0
    MAXIMUM_RETRY_DELAY_SECONDS: Final[float] = 5.0
    MAX_RETRY_AFTER_SECONDS: Final[int] = 60
    RETRY_AFTER_JITTER_CEILING_SECONDS: Final[float] = 1.0
    RETRY_AFTER_HEADER_NAME: Final[str] = "Retry-After"
    RETRY_AFTER_MILLISECONDS_HEADER_NAME: Final[str] = "Retry-After-Ms"
    MILLISECONDS_PER_SECOND: Final[int] = 1000
    HTTP_STATUS_TOO_MANY_REQUESTS: Final[int] = 429
    HTTP_STATUS_INTERNAL_SERVER_ERROR: Final[int] = 500
    HTTP_STATUS_CLIENT_ERROR_FLOOR: Final[int] = 400
    SERVER_RETRY_REQUEST_MARKERS: Final[tuple[str, ...]] = ("retry",)
    PROVIDER_MESSAGE_MAXIMUM_LENGTH: Final[int] = 300
    PROVIDER_TEXT_REDACTION_MARKER: Final[str] = "[REDACTED]"
    _SYSTEM_ONE_OPERATION: Final[str] = "system_one"
    _VALIDATE_RESPONSE_OPERATION: Final[str] = "validate_response"
    _ACLOSE_OPERATION: Final[str] = "aclose"

    def __init__(
        self,
        jev_settings: JevSettings,
        async_type_safe_client: AsyncTypeSafeClient | None = None,
        *,
        async_base_transport: httpx2.AsyncBaseTransport | None = None,
        wait_for_retry: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_jitter_fraction: Callable[[], float] = random.random,
        current_epoch_seconds: Callable[[], float] = time.time,
    ) -> None:
        if async_type_safe_client is not None and async_base_transport is not None:
            raise ValueError("Provide either a Jev client or an HTTP transport.")
        self._model: str = jev_settings.model
        self._api_key: SecretStr = jev_settings.api_key
        self._wait_for_retry: Callable[[float], Awaitable[None]] = wait_for_retry
        self._retry_jitter_fraction: Callable[[], float] = retry_jitter_fraction
        self._current_epoch_seconds: Callable[[], float] = current_epoch_seconds
        self._async_type_safe_client: AsyncTypeSafeClient = (
            async_type_safe_client
            if async_type_safe_client is not None
            else AsyncTypeSafeClient(
                api_key=jev_settings.api_key.get_secret_value(),
                base_url=jev_settings.base_url,
                transport=async_base_transport,
                retry=RetryPolicy(max_retries=0),
            )
        )

        self._async_base_transport: httpx2.AsyncBaseTransport | None = async_base_transport
        self._transport_close_pending: bool = False
        self._client_closed: bool = False
        self._closed: bool = False

    async def decide(self, jev_state: JevState, retention_questions_by_tool_id: Mapping[str, RetentionQuestion]) -> JevDecisionBatch:
        if self._closed:
            raise JevGatewayClosedException("Jev gateway is closed.", operation=JevGateway._SYSTEM_ONE_OPERATION, model=self._model)
        sdk_questions_by_tool_id: Mapping[str, Noul] = self._sdk_questions(retention_questions_by_tool_id)
        system_one_response: SystemOneResponse = await self._request_system_one(jev_state, sdk_questions_by_tool_id)
        return self._interpret_retention_probabilities(system_one_response, set(retention_questions_by_tool_id))

    def _sdk_questions(self, retention_questions_by_tool_id: Mapping[str, RetentionQuestion]) -> Mapping[str, Noul]:
        return {tool_id: Noul(instructions=retention_questions_by_tool_id[tool_id].instructions) for tool_id in retention_questions_by_tool_id}

    async def _request_system_one(self, jev_state: JevState, sdk_questions_by_tool_id: Mapping[str, Noul]) -> SystemOneResponse:
        request_attempt_number: int
        for request_attempt_number in range(1, JevGateway.MAXIMUM_REQUEST_ATTEMPTS):
            attempt_outcome: SystemOneResponse | JevRetryableFailure = await self._attempt_system_one(jev_state, sdk_questions_by_tool_id)
            if isinstance(attempt_outcome, SystemOneResponse):
                return attempt_outcome
            await self._wait_for_retry(
                JevGateway._calculate_retry_delay_seconds(request_attempt_number, attempt_outcome.retry_after_seconds, self._retry_jitter_fraction())
            )
        final_attempt_outcome: SystemOneResponse | JevRetryableFailure = await self._attempt_system_one(jev_state, sdk_questions_by_tool_id)
        if isinstance(final_attempt_outcome, SystemOneResponse):
            return final_attempt_outcome
        raise self._retries_exhausted_exception(final_attempt_outcome) from final_attempt_outcome.provider_error

    async def _attempt_system_one(self, jev_state: JevState, sdk_questions_by_tool_id: Mapping[str, Noul]) -> SystemOneResponse | JevRetryableFailure:
        try:
            return await self._async_type_safe_client.system_one(state=asdict(jev_state), questions=sdk_questions_by_tool_id, model=self._model)
        except (ValidationError, TypeSafeAPIResponseValidationError) as error:
            raise JevResponseSchemaInvalidException(
                "Jev returned an invalid structured response.", operation=JevGateway._VALIDATE_RESPONSE_OPERATION, model=self._model
            ) from error
        except TypeSafeAPIError as error:
            return self._classify_status_failure(error)
        except TypeSafeAPIConnectionError as error:
            raise JevRequestFailedException(
                JevGateway.REQUEST_FAILED_MESSAGE, operation=JevGateway._SYSTEM_ONE_OPERATION, model=self._model
            ) from error

    def _classify_status_failure(self, provider_error: TypeSafeAPIError) -> JevRetryableFailure:
        http_status: int = provider_error.status
        provider_message: str = self._sanitize_provider_message(str(provider_error))
        if JevGateway._is_retryable(http_status, provider_message):
            return JevRetryableFailure(http_status, self._parse_retry_after_seconds(provider_error.headers), provider_message, provider_error)
        if JevGateway.HTTP_STATUS_CLIENT_ERROR_FLOOR <= http_status < JevGateway.HTTP_STATUS_INTERNAL_SERVER_ERROR:
            raise JevRequestRejectedException(
                JevGateway.TARGET_NAME, http_status, provider_message, operation=JevGateway._SYSTEM_ONE_OPERATION, model=self._model
            ) from provider_error
        raise JevRequestFailedException(
            JevGateway.REQUEST_FAILED_MESSAGE,
            operation=JevGateway._SYSTEM_ONE_OPERATION,
            model=self._model,
            detail_value_by_name={"http_status": str(http_status), "provider_message": provider_message},
        ) from provider_error

    @staticmethod
    def _is_retryable(http_status: int, provider_message: str) -> bool:
        if http_status == JevGateway.HTTP_STATUS_TOO_MANY_REQUESTS:
            return True
        normalized_provider_message: str = provider_message.casefold()
        return http_status == JevGateway.HTTP_STATUS_INTERNAL_SERVER_ERROR and any(
            retry_request_marker in normalized_provider_message for retry_request_marker in JevGateway.SERVER_RETRY_REQUEST_MARKERS
        )

    def _sanitize_provider_message(self, raw_provider_message: str) -> str:
        # Provider text lands in exception messages; never let it echo the API key or flood the diagnostic.
        api_key_value: str = self._api_key.get_secret_value()
        redacted_provider_message: str = (
            raw_provider_message.replace(api_key_value, JevGateway.PROVIDER_TEXT_REDACTION_MARKER) if api_key_value else raw_provider_message
        )
        single_line_provider_message: str = " ".join(redacted_provider_message.split())
        return single_line_provider_message[: JevGateway.PROVIDER_MESSAGE_MAXIMUM_LENGTH]

    def _parse_retry_after_seconds(self, response_headers: httpx2.Headers) -> int | None:
        # Every provider hint form is honoured in the SDK's precedence: Retry-After-Ms, then Retry-After as delay
        # seconds or as an HTTP date. Fractions round up so no retry happens earlier than instructed; an
        # unparsable value is ignored and the exponential backoff applies.
        retry_after_milliseconds_text: str | None = response_headers.get(JevGateway.RETRY_AFTER_MILLISECONDS_HEADER_NAME)
        if retry_after_milliseconds_text is not None and retry_after_milliseconds_text.strip().isdecimal():
            return math.ceil(int(retry_after_milliseconds_text.strip()) / JevGateway.MILLISECONDS_PER_SECOND)
        retry_after_header_value: str | None = response_headers.get(JevGateway.RETRY_AFTER_HEADER_NAME)
        if retry_after_header_value is None:
            return None
        retry_after_text: str = retry_after_header_value.strip()
        if retry_after_text.isdecimal():
            return int(retry_after_text)
        try:
            retry_after_moment: datetime = parsedate_to_datetime(retry_after_text)
        except TypeError, ValueError:
            return None
        return max(0, math.ceil(retry_after_moment.timestamp() - self._current_epoch_seconds()))

    @staticmethod
    def _calculate_retry_delay_seconds(request_attempt_number: int, retry_after_seconds: int | None, jitter_fraction: float) -> float:
        if retry_after_seconds is not None:
            # Jitter is added, never subtracted, so no retry happens earlier than the provider instructed;
            # the instruction itself is bounded so a hostile value cannot stall the trial for hours.
            bounded_retry_after_seconds: int = min(retry_after_seconds, JevGateway.MAX_RETRY_AFTER_SECONDS)
            return bounded_retry_after_seconds + jitter_fraction * JevGateway.RETRY_AFTER_JITTER_CEILING_SECONDS
        exponential_delay_seconds: float = min(
            JevGateway.INITIAL_RETRY_DELAY_SECONDS * (JevGateway.RETRY_DELAY_MULTIPLIER ** (request_attempt_number - 1)),
            JevGateway.MAXIMUM_RETRY_DELAY_SECONDS,
        )
        return jitter_fraction * exponential_delay_seconds

    def _retries_exhausted_exception(
        self, jev_retryable_failure: JevRetryableFailure
    ) -> JevRateLimitRetriesExhaustedException | JevServerRetryRequestedRetriesExhaustedException:
        if jev_retryable_failure.http_status == JevGateway.HTTP_STATUS_TOO_MANY_REQUESTS:
            return JevRateLimitRetriesExhaustedException(
                JevGateway.TARGET_NAME,
                JevGateway.MAXIMUM_REQUEST_ATTEMPTS,
                jev_retryable_failure.retry_after_seconds,
                operation=JevGateway._SYSTEM_ONE_OPERATION,
                model=self._model,
            )
        return JevServerRetryRequestedRetriesExhaustedException(
            JevGateway.TARGET_NAME,
            JevGateway.MAXIMUM_REQUEST_ATTEMPTS,
            jev_retryable_failure.provider_message,
            operation=JevGateway._SYSTEM_ONE_OPERATION,
            model=self._model,
        )

    def _interpret_retention_probabilities(self, system_one_response: SystemOneResponse, expected_question_ids: AbstractSet[str]) -> JevDecisionBatch:
        answer_ids: set[str] = set(system_one_response.answers)
        noul_ids: set[str] = set(system_one_response.nouls)
        if answer_ids != expected_question_ids or noul_ids != expected_question_ids:
            raise JevAnswerSetMismatchException(
                "Jev returned a missing or unexpected retention answer.",
                operation=JevGateway._VALIDATE_RESPONSE_OPERATION,
                model=self._model,
                detail_value_by_name={
                    "expected_question_ids": repr(sorted(expected_question_ids)),
                    "answer_ids": repr(sorted(answer_ids)),
                    "noul_ids": repr(sorted(noul_ids)),
                },
            )
        probability_by_question_id: dict[str, float] = {
            question_id: system_one_response.nouls[question_id].noul for question_id in system_one_response.nouls
        }
        rejected_probability_by_question_id: dict[str, float] = {
            question_id: probability_by_question_id[question_id]
            for question_id in probability_by_question_id
            if not 0 <= probability_by_question_id[question_id] <= 1
        }
        if rejected_probability_by_question_id:
            raise JevProbabilityOutOfRangeException(
                "Jev returned a probability outside zero to one.",
                operation=JevGateway._VALIDATE_RESPONSE_OPERATION,
                model=self._model,
                detail_value_by_name={"rejected_probabilities": repr(rejected_probability_by_question_id)},
            )
        usage: dict[str, int | None] = {
            "input_tokens": system_one_response.usage.input_tokens,
            "output_tokens": system_one_response.usage.output_tokens,
        }
        return JevDecisionBatch(system_one_response.model, usage, probability_by_question_id)

    async def close(self) -> None:
        if not self._closed:
            await self._close_resources()
            self._closed = True

    async def _close_resources(self) -> None:
        if not self._client_closed:
            try:
                await self._async_type_safe_client.aclose()
            except BaseException as error:
                if self._async_base_transport is not None:
                    self._transport_close_pending = True
                if isinstance(error, Exception):
                    raise JevClientCleanupException(
                        "Jev client cleanup failed.", operation=JevGateway._ACLOSE_OPERATION, model=self._model
                    ) from error
                raise
            self._client_closed = True
        if self._transport_close_pending and self._async_base_transport is not None:
            try:
                await self._async_base_transport.aclose()
            except Exception as error:
                raise JevTransportCleanupException(
                    "Jev HTTP transport cleanup failed.", operation=JevGateway._ACLOSE_OPERATION, model=self._model
                ) from error
            self._transport_close_pending = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
