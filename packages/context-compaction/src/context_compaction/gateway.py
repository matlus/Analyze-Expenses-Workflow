"""Translate Jev SDK answers into validated context-retention probabilities."""

from __future__ import annotations

from collections.abc import Mapping, Set as AbstractSet
from dataclasses import asdict, dataclass
from types import TracebackType
from typing import Self, final

from pydantic import SecretStr
from typesafe_sdk import AsyncTypeSafeClient, Noul, SystemOneResponse

from context_compaction.errors import ContextCompactionTechnicalError
from context_compaction.questions import RetentionQuestion
from context_compaction.records import JevState


class JevGatewayError(ContextCompactionTechnicalError):
    """Base class for distinct Jev gateway failures."""

    def __init__(self, message: str, *, operation: str, model: str, details: Mapping[str, str] | None = None) -> None:
        self.operation: str = operation
        self.model: str = model
        self.details: dict[str, str] = dict(details or {})
        context: str = ", ".join(f"{name}={self.details[name]}" for name in self.details)
        super().__init__(f"{message} operation={operation}, model={model}" + (f", {context}" if context else ""))


@final
class JevGatewayClosedError(JevGatewayError):
    """The caller used a gateway after its client was released."""


@final
class JevRequestError(JevGatewayError):
    """The provider request failed."""


@final
class JevResponseError(JevGatewayError):
    """The provider returned an unusable answer."""


@final
class JevCleanupError(JevGatewayError):
    """The provider client could not be closed."""


@dataclass(frozen=True)
class JevSettings:
    api_key: SecretStr
    base_url: str
    model: str


@dataclass(frozen=True)
class JevDecisionBatch:
    model: str
    usage: dict[str, int | None]
    probabilities: dict[str, float]


class JevGateway:
    def __init__(self, jev_settings: JevSettings, async_type_safe_client: AsyncTypeSafeClient | None = None) -> None:
        self._model: str = jev_settings.model
        self._async_type_safe_client: AsyncTypeSafeClient | None = async_type_safe_client or AsyncTypeSafeClient(
            api_key=jev_settings.api_key.get_secret_value(), base_url=jev_settings.base_url
        )

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
        async_type_safe_client: AsyncTypeSafeClient | None = self._async_type_safe_client
        if async_type_safe_client is None:
            raise JevGatewayClosedError("Jev gateway is closed.", operation="system_one", model=self._model)
        sdk_questions: Mapping[str, Noul] = self._sdk_questions(questions)
        try:
            system_one_response: SystemOneResponse = await async_type_safe_client.system_one(
                state=asdict(jev_state),
                questions=sdk_questions,
                model=self._model,
            )
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
                details={
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
                details={"rejected_probabilities": repr(rejected_probabilities)},
            )
        usage: dict[str, int | None] = {
            "input_tokens": system_one_response.usage.input_tokens,
            "output_tokens": system_one_response.usage.output_tokens,
        }
        return JevDecisionBatch(system_one_response.model, usage, probabilities)

    async def close(self) -> None:
        async_type_safe_client: AsyncTypeSafeClient | None = self._async_type_safe_client
        if async_type_safe_client is None:
            return
        try:
            await async_type_safe_client.aclose()
        except Exception as error:
            raise self._provider_failure("aclose") from error
        self._async_type_safe_client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
