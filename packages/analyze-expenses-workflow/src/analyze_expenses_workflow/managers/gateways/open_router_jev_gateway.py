from typing import final

from pydantic import ValidationError
from typesafe_sdk import AsyncTypeSafeClient, Choice, ChoiceAnswer, SystemOneResponse, TypeSafeError

from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import SystemOneGatewayException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion


@final
class OpenRouterJevGateway:
    def __init__(self, settings: JevSettings, client: AsyncTypeSafeClient | None = None) -> None:
        self._settings: JevSettings = settings
        self._client: AsyncTypeSafeClient | None = client
        self._closed: bool = False

    async def choose(self, state: str, question: ChoiceQuestion) -> ChoiceDecision:
        self._ensure_open()
        try:
            response: SystemOneResponse = await self._request(state, question)
            return self._decision_from_response(response, question)
        except (KeyError, TypeError, ValidationError) as exc:
            raise SystemOneGatewayException("Jev returned an invalid choice response") from exc
        except TypeSafeError as exc:
            raise SystemOneGatewayException("Jev categorization request failed") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise SystemOneGatewayException("Jev gateway is closed")

    def _client_for_request(self) -> AsyncTypeSafeClient:
        self._ensure_open()
        if self._client is None:
            self._client = AsyncTypeSafeClient(
                api_key=self._settings.open_router_key.get_secret_value(),
                base_url=str(self._settings.open_router_base_url),
            )
        return self._client

    async def _request(self, state: str, question: ChoiceQuestion) -> SystemOneResponse:
        client: AsyncTypeSafeClient = self._client_for_request()
        return await client.system_one(
            state=state,
            questions={"category": Choice(instructions=question.instructions, criteria=question.criteria)},
            model=self._settings.jev_model,
        )

    @staticmethod
    def _decision_from_response(response: SystemOneResponse, question: ChoiceQuestion) -> ChoiceDecision:
        answer: ChoiceAnswer = response.choices["category"]
        decision: ChoiceDecision = ChoiceDecision.model_validate(answer.model_dump())
        if set(decision.probabilities) != set(question.criteria):
            raise SystemOneGatewayException("Jev returned probabilities for unexpected categories")
        return decision

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._client is not None:
                await self._client.aclose()
