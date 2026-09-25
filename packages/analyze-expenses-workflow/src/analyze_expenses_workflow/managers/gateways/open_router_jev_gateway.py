from types import TracebackType
from typing import Final, Self, final

import httpx2
from pydantic import ValidationError
from typesafe_sdk import AsyncTypeSafeClient, Choice, ChoiceAnswer, SystemOneResponse, TypeSafeError

from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import SystemOneGatewayException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion


@final
class OpenRouterJevGateway:
    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"
    _MODEL_CONTEXT_FIELD_NAME: Final[str] = "Model"
    _CATEGORY_QUESTION_NAME: Final[str] = "category"

    def __init__(
        self,
        jev_settings: JevSettings,
        async_type_safe_client: AsyncTypeSafeClient | None = None,
        *,
        async_base_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if async_type_safe_client is not None and async_base_transport is not None:
            raise ValueError("Provide either a Jev client or an HTTP transport")
        self._jev_settings: JevSettings = jev_settings
        self._jev_model: str = jev_settings.jev_model
        self._async_type_safe_client: AsyncTypeSafeClient | None = async_type_safe_client
        self._async_base_transport: httpx2.AsyncBaseTransport | None = async_base_transport
        self._transport_close_pending: bool = False
        self._closed: bool = False

    async def choose(self, state: str, question: ChoiceQuestion) -> ChoiceDecision:
        self._ensure_open()
        try:
            system_one_response: SystemOneResponse = await self._request(state, question)
            return self._decision_from_response(system_one_response, question)
        except (KeyError, TypeError, ValidationError) as response_validation_error:
            raise SystemOneGatewayException(
                "Jev returned an invalid choice response",
                {self._OPERATION_CONTEXT_FIELD_NAME: "choice_response_validation", self._MODEL_CONTEXT_FIELD_NAME: self._jev_model},
            ) from response_validation_error
        except TypeSafeError as type_safe_error:
            raise SystemOneGatewayException(
                "Jev categorization request failed",
                {self._OPERATION_CONTEXT_FIELD_NAME: "categorization_request", self._MODEL_CONTEXT_FIELD_NAME: self._jev_model},
            ) from type_safe_error

    def _ensure_open(self) -> None:
        if self._closed:
            raise SystemOneGatewayException(
                "Jev gateway is closed", {self._OPERATION_CONTEXT_FIELD_NAME: "gateway_open_state_check", "GatewayState": "closed"}
            )

    def _client_for_request(self) -> AsyncTypeSafeClient:
        self._ensure_open()
        if self._async_type_safe_client is None:
            self._async_type_safe_client = AsyncTypeSafeClient(
                api_key=self._jev_settings.open_router_key.get_secret_value(),
                base_url=str(self._jev_settings.open_router_base_url),
                transport=self._async_base_transport,
            )
        return self._async_type_safe_client

    async def _request(self, state: str, choice_question: ChoiceQuestion) -> SystemOneResponse:
        async_type_safe_client: AsyncTypeSafeClient = self._client_for_request()
        return await async_type_safe_client.system_one(
            state=state,
            questions={self._CATEGORY_QUESTION_NAME: Choice(instructions=choice_question.instructions, criteria=choice_question.criteria)},
            model=self._jev_model,
        )

    @staticmethod
    def _decision_from_response(system_one_response: SystemOneResponse, choice_question: ChoiceQuestion) -> ChoiceDecision:
        choice_decision: ChoiceDecision = OpenRouterJevGateway._map_choice_decision(system_one_response)
        OpenRouterJevGateway._validate_expected_categories(choice_decision, choice_question)
        return choice_decision

    @staticmethod
    def _map_choice_decision(system_one_response: SystemOneResponse) -> ChoiceDecision:
        choice_answer: ChoiceAnswer = system_one_response.choices[OpenRouterJevGateway._CATEGORY_QUESTION_NAME]
        return ChoiceDecision.model_validate(choice_answer.model_dump())

    @staticmethod
    def _validate_expected_categories(choice_decision: ChoiceDecision, choice_question: ChoiceQuestion) -> None:
        if set(choice_decision.probabilities) != set(choice_question.criteria):
            raise SystemOneGatewayException(
                "Jev returned probabilities for unexpected categories",
                {
                    OpenRouterJevGateway._OPERATION_CONTEXT_FIELD_NAME: "choice_category_validation",
                    "ExpectedCategories": ",".join(sorted(choice_question.criteria)),
                    "ActualCategories": ",".join(sorted(choice_decision.probabilities)),
                },
            )

    async def close(self) -> None:
        if not self._closed:
            await self._close_resources()
            self._closed = True

    async def _close_resources(self) -> None:
        if self._async_type_safe_client is not None:
            try:
                await self._async_type_safe_client.aclose()
            except BaseException:
                if self._async_base_transport is not None:
                    self._transport_close_pending = True
                raise
            if self._transport_close_pending and self._async_base_transport is not None:
                await self._async_base_transport.aclose()
        elif self._async_base_transport is not None:
            await self._async_base_transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
