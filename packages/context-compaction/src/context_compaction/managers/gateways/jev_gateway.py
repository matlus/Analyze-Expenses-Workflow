"""Translate Jev SDK answers into validated context-retention probabilities."""

from __future__ import annotations

from collections.abc import Mapping, Set as AbstractSet
from dataclasses import asdict
from types import TracebackType
from typing import Self

import httpx2
from pydantic import ValidationError
from typesafe_sdk import AsyncTypeSafeClient, Noul, SystemOneResponse, TypeSafeAPIResponseValidationError

from context_compaction.managers.exceptions.jev_gateway_error import (
    JevCleanupError,
    JevGatewayClosedError,
    JevGatewayError,
    JevRequestError,
    JevResponseError,
)
from context_compaction.managers.models.context_models import JevDecisionBatch, JevSettings, RetentionQuestion
from context_compaction.managers.models.records import JevState


class JevGateway:
    def __init__(
        self,
        jev_settings: JevSettings,
        async_type_safe_client: AsyncTypeSafeClient | None = None,
        *,
        async_base_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if async_type_safe_client is not None and async_base_transport is not None:
            raise ValueError("Provide either a Jev client or an HTTP transport.")
        self._model: str = jev_settings.model
        self._async_type_safe_client: AsyncTypeSafeClient = (
            async_type_safe_client
            if async_type_safe_client is not None
            else AsyncTypeSafeClient(api_key=jev_settings.api_key.get_secret_value(), base_url=jev_settings.base_url, transport=async_base_transport)
        )

        self._async_base_transport: httpx2.AsyncBaseTransport | None = async_base_transport
        self._transport_close_pending: bool = False
        self._client_closed: bool = False
        self._closed: bool = False

    async def decide(self, jev_state: JevState, questions: Mapping[str, RetentionQuestion]) -> JevDecisionBatch:
        system_one_response: SystemOneResponse = await self._request(jev_state, questions)
        return self._interpret(system_one_response, set(questions))

    def _sdk_questions(self, questions: Mapping[str, RetentionQuestion]) -> Mapping[str, Noul]:
        return {question_id: Noul(instructions=questions[question_id].instructions) for question_id in questions}

    def _provider_failure(self, operation: str) -> JevGatewayError:
        if operation == "aclose":
            return JevCleanupError("Jev gateway cleanup failed.", operation=operation, model=self._model)
        return JevRequestError("Jev context-retention request failed.", operation=operation, model=self._model)

    async def _request(self, jev_state: JevState, questions: Mapping[str, RetentionQuestion]) -> SystemOneResponse:
        if self._closed:
            raise JevGatewayClosedError("Jev gateway is closed.", operation="system_one", model=self._model)
        sdk_questions: Mapping[str, Noul] = self._sdk_questions(questions)
        return await self._request_system_one(jev_state, sdk_questions)

    async def _request_system_one(self, jev_state: JevState, sdk_questions: Mapping[str, Noul]) -> SystemOneResponse:
        async_type_safe_client: AsyncTypeSafeClient = self._async_type_safe_client
        try:
            system_one_response: SystemOneResponse = await async_type_safe_client.system_one(
                state=asdict(jev_state),
                questions=sdk_questions,
                model=self._model,
            )
        except (ValidationError, TypeSafeAPIResponseValidationError) as error:
            raise JevResponseError("Jev returned an invalid structured response.", operation="validate_response", model=self._model) from error
        except Exception as error:
            raise self._provider_failure("system_one") from error
        return system_one_response

    def _interpret(self, system_one_response: SystemOneResponse, expected_question_ids: AbstractSet[str]) -> JevDecisionBatch:
        answer_ids: set[str] = set(system_one_response.answers)
        noul_ids: set[str] = set(system_one_response.nouls)
        if answer_ids != expected_question_ids or noul_ids != expected_question_ids:
            raise JevResponseError(
                "Jev returned a missing or unexpected retention answer.",
                operation="validate_response",
                model=self._model,
                detail_value_by_name={
                    "expected_question_ids": repr(sorted(expected_question_ids)),
                    "answer_ids": repr(sorted(answer_ids)),
                    "noul_ids": repr(sorted(noul_ids)),
                },
            )
        probabilities: dict[str, float] = {name: system_one_response.nouls[name].noul for name in system_one_response.nouls}
        rejected_probabilities: dict[str, float] = {name: probabilities[name] for name in probabilities if not 0 <= probabilities[name] <= 1}
        if rejected_probabilities:
            raise JevResponseError(
                "Jev returned a probability outside zero to one.",
                operation="validate_response",
                model=self._model,
                detail_value_by_name={"rejected_probabilities": repr(rejected_probabilities)},
            )
        usage: dict[str, int | None] = {
            "input_tokens": system_one_response.usage.input_tokens,
            "output_tokens": system_one_response.usage.output_tokens,
        }
        return JevDecisionBatch(system_one_response.model, usage, probabilities)

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
                    raise self._provider_failure("aclose") from error
                raise
            self._client_closed = True
        if self._transport_close_pending and self._async_base_transport is not None:
            try:
                await self._async_base_transport.aclose()
            except Exception as error:
                raise self._provider_failure("aclose") from error
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
