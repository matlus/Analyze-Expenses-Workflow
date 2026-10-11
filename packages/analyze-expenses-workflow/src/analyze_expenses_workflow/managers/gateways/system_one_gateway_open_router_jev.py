import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from types import TracebackType
from typing import Final, NoReturn, Self, final

import httpx2
from pydantic import ValidationError
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    RetryPolicy,
    SystemOneResponse,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeError,
)

from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionContext
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import (
    JevChoiceCategoriesMismatchException,
    JevChoiceResponseMalformedException,
    JevClientCleanupFailedException,
    JevGatewayClosedException,
    JevRateLimitRetriesExhaustedException,
    JevRequestFailedException,
    JevRequestRejectedException,
    JevServerRetryRequestedRetriesExhaustedException,
    JevTransportCleanupFailedException,
)
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion


@final
class SystemOneGatewayOpenRouterJev:
    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"
    _MODEL_CONTEXT_FIELD_NAME: Final[str] = "Model"
    _REQUEST_ATTEMPT_COUNT_CONTEXT_FIELD_NAME: Final[str] = "RequestAttemptCount"
    _PROVIDER_HTTP_STATUS_CODE_CONTEXT_FIELD_NAME: Final[str] = "ProviderHttpStatusCode"
    _PROVIDER_MESSAGE_CONTEXT_FIELD_NAME: Final[str] = "ProviderMessage"
    _LAST_RETRY_AFTER_SECONDS_CONTEXT_FIELD_NAME: Final[str] = "LastRetryAfterSeconds"
    _CATEGORIZATION_REQUEST_OPERATION: Final[str] = "categorization_request"
    _CATEGORY_QUESTION_NAME: Final[str] = "category"

    # This gateway owns the retry policy, so the SDK's own retries are disabled. Only a 429, or a 500 whose
    # provider message asks for a retry, is retried; any other 4xx is rejected unchanged and any other 5xx fails.
    _MAX_REQUEST_ATTEMPTS: Final[int] = 5
    _SDK_RETRY_POLICY: Final[RetryPolicy] = RetryPolicy(max_retries=0)
    _HTTP_STATUS_CLIENT_ERROR_FLOOR: Final[int] = 400
    _HTTP_STATUS_TOO_MANY_REQUESTS: Final[int] = 429
    _HTTP_STATUS_INTERNAL_SERVER_ERROR: Final[int] = 500
    _SERVER_RETRY_REQUEST_MARKERS: Final[tuple[str, ...]] = ("retry",)
    _RETRY_AFTER_HEADER_NAME: Final[str] = "Retry-After"
    _MAX_RETRY_AFTER_SECONDS: Final[int] = 60
    _RETRY_AFTER_JITTER_CEILING_SECONDS: Final[float] = 0.5
    _INITIAL_RETRY_DELAY_SECONDS: Final[float] = 1.0
    _RETRY_DELAY_MULTIPLIER: Final[float] = 2.0
    _MAX_RETRY_DELAY_SECONDS: Final[float] = 4.0
    _PROVIDER_MESSAGE_MAX_LENGTH: Final[int] = 300

    def __init__(
        self,
        jev_settings: JevSettings,
        async_type_safe_client: AsyncTypeSafeClient | None = None,
        *,
        async_base_transport: httpx2.AsyncBaseTransport | None = None,
        wait_for_retry: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_jitter_fraction: Callable[[], float] = random.random,
    ) -> None:
        if async_type_safe_client is not None and async_base_transport is not None:
            raise ValueError("Provide either a Jev client or an HTTP transport")
        self._jev_model: str = jev_settings.jev_model
        self._async_type_safe_client: AsyncTypeSafeClient = (
            async_type_safe_client
            if async_type_safe_client is not None
            else AsyncTypeSafeClient(
                api_key=jev_settings.open_router_key.get_secret_value(),
                base_url=str(jev_settings.open_router_base_url),
                retry=self._SDK_RETRY_POLICY,
                transport=async_base_transport,
            )
        )
        self._async_base_transport: httpx2.AsyncBaseTransport | None = async_base_transport
        self._wait_for_retry: Callable[[float], Awaitable[None]] = wait_for_retry
        self._retry_jitter_fraction: Callable[[], float] = retry_jitter_fraction
        # The supplied HTTP transport, once a failed client close may have left it open; the next close() closes it.
        self._pending_async_base_transport: httpx2.AsyncBaseTransport | None = None
        self._client_closed: bool = False
        self._closed: bool = False

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        system_one_response: SystemOneResponse = await self._request_with_retries(source_text, choice_question)
        choice_decision: ChoiceDecision = self._map_choice_decision(system_one_response)
        self._validate_expected_categories(choice_decision, choice_question)
        return choice_decision

    def _raise_choice_response_malformed(self, response_shape_problem: str, response_validation_error: Exception | None) -> NoReturn:
        raise JevChoiceResponseMalformedException(
            f"Jev via OpenRouter returned a choice response that does not have the expected shape: {response_shape_problem}",
            {self._OPERATION_CONTEXT_FIELD_NAME: "choice_response_validation", self._MODEL_CONTEXT_FIELD_NAME: self._jev_model},
        ) from response_validation_error

    def _ensure_open(self) -> None:
        if self._closed:
            raise JevGatewayClosedException(
                "Jev via OpenRouter gateway is closed", {self._OPERATION_CONTEXT_FIELD_NAME: "gateway_open_state_check", "GatewayState": "closed"}
            )

    async def _request_with_retries(self, source_text: str, choice_question: ChoiceQuestion) -> SystemOneResponse:
        choice_by_question_name: Mapping[str, Choice] = self._questions_from(choice_question)
        request_attempt_number: int = 1
        while True:
            # The gateway can be closed while a retry waits, so each attempt checks the open state.
            self._ensure_open()
            try:
                return await self._request(source_text, choice_by_question_name)
            except TypeSafeAPIResponseValidationError as type_safe_api_response_validation_error:
                self._raise_choice_response_malformed("the SDK could not read the response body", type_safe_api_response_validation_error)
            except TypeSafeError as type_safe_error:
                retryable_type_safe_api_error: TypeSafeAPIError = self._retryable_api_error_or_raise(type_safe_error)
            retry_after_seconds: int | None = self._parse_retry_after_seconds(retryable_type_safe_api_error)
            if request_attempt_number == self._MAX_REQUEST_ATTEMPTS:
                self._raise_retries_exhausted(retryable_type_safe_api_error, request_attempt_number, retry_after_seconds)
            await self._wait_for_retry(
                self._calculate_retry_delay_seconds(request_attempt_number, retry_after_seconds, self._retry_jitter_fraction())
            )
            request_attempt_number += 1

    async def _request(self, source_text: str, choice_by_question_name: Mapping[str, Choice]) -> SystemOneResponse:
        return await self._async_type_safe_client.system_one(
            state=source_text,
            questions=choice_by_question_name,
            model=self._jev_model,
            retry=self._SDK_RETRY_POLICY,
        )

    def _retryable_api_error_or_raise(self, type_safe_error: TypeSafeError) -> TypeSafeAPIError:
        if isinstance(type_safe_error, TypeSafeAPIError):
            if self._is_retryable(type_safe_error):
                return type_safe_error
            if self._HTTP_STATUS_CLIENT_ERROR_FLOOR <= type_safe_error.status < self._HTTP_STATUS_INTERNAL_SERVER_ERROR:
                raise JevRequestRejectedException(
                    f"Jev via OpenRouter rejected the categorization request with HTTP {type_safe_error.status}; "
                    "it was not retried because the same request would be rejected again",
                    self._provider_failure_context(type_safe_error),
                ) from type_safe_error
        request_failure_context: ExceptionContext = self._request_failure_context(type_safe_error)
        raise JevRequestFailedException("Jev via OpenRouter categorization request failed", request_failure_context) from type_safe_error

    @classmethod
    def _is_retryable(cls, type_safe_api_error: TypeSafeAPIError) -> bool:
        if type_safe_api_error.status == cls._HTTP_STATUS_TOO_MANY_REQUESTS:
            return True
        return type_safe_api_error.status == cls._HTTP_STATUS_INTERNAL_SERVER_ERROR and cls._asks_for_retry(
            cls._provider_message(type_safe_api_error)
        )

    @classmethod
    def _asks_for_retry(cls, provider_message: str) -> bool:
        casefolded_provider_message: str = provider_message.casefold()
        return any(server_retry_request_marker in casefolded_provider_message for server_retry_request_marker in cls._SERVER_RETRY_REQUEST_MARKERS)

    @classmethod
    def _provider_message(cls, type_safe_api_error: TypeSafeAPIError) -> str:
        # The SDK renders the provider's error message with the endpoint and request id, never the credential.
        return str(type_safe_api_error)[: cls._PROVIDER_MESSAGE_MAX_LENGTH]

    @classmethod
    def _parse_retry_after_seconds(cls, type_safe_api_error: TypeSafeAPIError) -> int | None:
        # Only the delay-seconds form is honoured; a hostile value is bounded so one response cannot stall the run.
        retry_after_header_value: str | None = type_safe_api_error.headers.get(cls._RETRY_AFTER_HEADER_NAME)
        if retry_after_header_value is None:
            return None
        delay_seconds_text: str = retry_after_header_value.strip()
        if not delay_seconds_text.isdecimal():
            return None
        return min(int(delay_seconds_text), cls._MAX_RETRY_AFTER_SECONDS)

    @classmethod
    def _calculate_retry_delay_seconds(cls, request_attempt_number: int, retry_after_seconds: int | None, jitter_fraction: float) -> float:
        if retry_after_seconds is not None:
            # Jitter is added to the instructed wait, so a retry never comes earlier than the provider asked.
            return retry_after_seconds + jitter_fraction * cls._RETRY_AFTER_JITTER_CEILING_SECONDS
        exponential_delay_seconds: float = min(
            cls._INITIAL_RETRY_DELAY_SECONDS * (cls._RETRY_DELAY_MULTIPLIER ** (request_attempt_number - 1)),
            cls._MAX_RETRY_DELAY_SECONDS,
        )
        return jitter_fraction * exponential_delay_seconds

    def _raise_retries_exhausted(
        self, type_safe_api_error: TypeSafeAPIError, request_attempt_count: int, retry_after_seconds: int | None
    ) -> NoReturn:
        contextual_data_by_name: ExceptionContext = {
            **self._provider_failure_context(type_safe_api_error),
            self._REQUEST_ATTEMPT_COUNT_CONTEXT_FIELD_NAME: request_attempt_count,
        }
        if retry_after_seconds is not None:
            contextual_data_by_name[self._LAST_RETRY_AFTER_SECONDS_CONTEXT_FIELD_NAME] = retry_after_seconds
        if type_safe_api_error.status == self._HTTP_STATUS_TOO_MANY_REQUESTS:
            last_retry_after_text: str = f"{retry_after_seconds} seconds" if retry_after_seconds is not None else "not provided"
            raise JevRateLimitRetriesExhaustedException(
                "Jev via OpenRouter kept answering HTTP 429 Too Many Requests; stopped retrying the rate limiting after "
                f"{request_attempt_count} attempts; last Retry-After: {last_retry_after_text}",
                contextual_data_by_name,
            ) from type_safe_api_error
        raise JevServerRetryRequestedRetriesExhaustedException(
            f"Jev via OpenRouter kept answering HTTP {type_safe_api_error.status} responses that asked to be retried; stopped retrying after "
            f"{request_attempt_count} attempts; last provider message: {self._provider_message(type_safe_api_error)!r}",
            contextual_data_by_name,
        ) from type_safe_api_error

    def _request_failure_context(self, type_safe_error: TypeSafeError) -> ExceptionContext:
        if isinstance(type_safe_error, TypeSafeAPIError):
            return self._provider_failure_context(type_safe_error)
        return {self._OPERATION_CONTEXT_FIELD_NAME: self._CATEGORIZATION_REQUEST_OPERATION, self._MODEL_CONTEXT_FIELD_NAME: self._jev_model}

    def _provider_failure_context(self, type_safe_api_error: TypeSafeAPIError) -> ExceptionContext:
        return {
            self._OPERATION_CONTEXT_FIELD_NAME: self._CATEGORIZATION_REQUEST_OPERATION,
            self._MODEL_CONTEXT_FIELD_NAME: self._jev_model,
            self._PROVIDER_HTTP_STATUS_CODE_CONTEXT_FIELD_NAME: type_safe_api_error.status,
            self._PROVIDER_MESSAGE_CONTEXT_FIELD_NAME: self._provider_message(type_safe_api_error),
        }

    @classmethod
    def _questions_from(cls, choice_question: ChoiceQuestion) -> Mapping[str, Choice]:
        return {cls._CATEGORY_QUESTION_NAME: Choice(instructions=choice_question.instructions, criteria=choice_question.criterion_by_choice_key)}

    def _map_choice_decision(self, system_one_response: SystemOneResponse) -> ChoiceDecision:
        choice_answer: ChoiceAnswer | None = system_one_response.choices.get(self._CATEGORY_QUESTION_NAME)
        if choice_answer is None:
            self._raise_choice_response_malformed(f"it has no {self._CATEGORY_QUESTION_NAME!r} choice answer", None)
        try:
            return ChoiceDecision.model_validate(choice_answer.model_dump())
        except ValidationError as validation_error:
            self._raise_choice_response_malformed("its choice answer is not a valid choice decision", validation_error)

    @staticmethod
    def _validate_expected_categories(choice_decision: ChoiceDecision, choice_question: ChoiceQuestion) -> None:
        if set(choice_decision.probabilities) != set(choice_question.criterion_by_choice_key):
            raise JevChoiceCategoriesMismatchException(
                "Jev via OpenRouter returned probabilities for categories other than those asked about",
                {
                    SystemOneGatewayOpenRouterJev._OPERATION_CONTEXT_FIELD_NAME: "choice_category_validation",
                    "ExpectedCategories": ",".join(sorted(choice_question.criterion_by_choice_key)),
                    "ActualCategories": ",".join(sorted(choice_decision.probabilities)),
                },
            )

    async def close(self) -> None:
        if not self._closed:
            await self._close_resources()
            self._closed = True

    async def _close_resources(self) -> None:
        if not self._client_closed:
            try:
                await self._async_type_safe_client.aclose()
            except BaseException as client_close_error:
                self._pending_async_base_transport = self._async_base_transport
                if isinstance(client_close_error, Exception):
                    raise JevClientCleanupFailedException(
                        "Jev via OpenRouter client cleanup failed",
                        {self._OPERATION_CONTEXT_FIELD_NAME: "client_close", self._MODEL_CONTEXT_FIELD_NAME: self._jev_model},
                    ) from client_close_error
                raise
            self._client_closed = True
        if self._pending_async_base_transport is not None:
            try:
                await self._pending_async_base_transport.aclose()
            except Exception as transport_close_error:
                raise JevTransportCleanupFailedException(
                    "Jev via OpenRouter HTTP transport cleanup failed",
                    {self._OPERATION_CONTEXT_FIELD_NAME: "transport_close", self._MODEL_CONTEXT_FIELD_NAME: self._jev_model},
                ) from transport_close_error
            self._pending_async_base_transport = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
