import asyncio
import secrets
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from pydantic import HttpUrl, SecretStr
from typesafe_sdk import AsyncTypeSafeClient

from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import JevResourceCleanupFailedException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev import SystemOneGatewayOpenRouterJev
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion


def _settings() -> JevSettings:
    return JevSettings(
        open_router_key=SecretStr(secrets.token_hex(16)),
        open_router_base_url=HttpUrl("https://openrouter.ai/api/v1"),
        jev_model="typesafe/jev",
    )


@pytest.mark.asyncio
async def test_choose_WhenJevRequestsAreConcurrent_ThenSharesOneClient() -> None:
    expected_choice: str = secrets.token_hex(8)
    expected_request_count: int = 8
    expected_client_creation_count: int = 1
    expected_client_close_count: int = 1
    expense_description: str = f"{secrets.token_hex(8)} $10"
    choice_answer_mock: MagicMock = MagicMock()
    choice_answer_mock.model_dump.return_value = {
        "choice": expected_choice,
        "confidence": 0.9,
        "probabilities": {expected_choice: 0.9},
    }
    system_one_response_mock: MagicMock = MagicMock(choices={"category": choice_answer_mock})
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.system_one = AsyncMock(return_value=system_one_response_mock)
    async_type_safe_client_mock.aclose = AsyncMock()
    choice_question: ChoiceQuestion = ChoiceQuestion(
        instructions=f"Classify expense {secrets.token_hex(8)}",
        criteria={expected_choice: secrets.token_hex(8)},
    )

    with patch(
        "analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev.AsyncTypeSafeClient",
        return_value=async_type_safe_client_mock,
    ) as async_type_safe_client_factory_mock:
        system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(_settings())
        choice_decisions: list[ChoiceDecision] = list(
            await asyncio.gather(
                *(system_one_gateway_open_router_jev.choose(expense_description, choice_question) for _ in range(expected_request_count))
            )
        )
        await system_one_gateway_open_router_jev.close()

    assert (
        len(choice_decisions),
        [choice_decision.choice for choice_decision in choice_decisions],
        async_type_safe_client_factory_mock.call_count,
        async_type_safe_client_mock.system_one.await_count,
        async_type_safe_client_mock.aclose.await_count,
    ) == (
        expected_request_count,
        [expected_choice] * expected_request_count,
        expected_client_creation_count,
        expected_request_count,
        expected_client_close_count,
    )


@pytest.mark.asyncio
async def test_close_WhenJevGatewayIsUnused_ThenClosesItsClient() -> None:
    with patch(
        "analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev.AsyncTypeSafeClient"
    ) as async_type_safe_client_factory_mock:
        async_type_safe_client_mock: MagicMock = async_type_safe_client_factory_mock.return_value
        async_type_safe_client_mock.aclose = AsyncMock()
        system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(_settings())
        await system_one_gateway_open_router_jev.close()

    async_type_safe_client_factory_mock.assert_called_once()
    assert async_type_safe_client_factory_mock.call_args.kwargs["transport"] is None
    async_type_safe_client_mock.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_WhenJevClientIsInjected_ThenClosesThatClient() -> None:
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.aclose = AsyncMock()
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _settings(), cast("AsyncTypeSafeClient", async_type_safe_client_mock)
    )

    await system_one_gateway_open_router_jev.close()

    async_type_safe_client_mock.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_WhenPendingTransportFailsAfterClientCloses_ThenRetriesOnlyTransport() -> None:
    expected_client_close_count: int = 2
    expected_transport_close_count: int = 2
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.aclose = AsyncMock(side_effect=[OSError(secrets.token_hex(8)), None])
    async_base_transport_mock: MagicMock = MagicMock(spec=httpx2.AsyncBaseTransport)
    async_base_transport_mock.aclose = AsyncMock(side_effect=[OSError(secrets.token_hex(8)), None])

    with patch(
        "analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev.AsyncTypeSafeClient",
        return_value=async_type_safe_client_mock,
    ):
        system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
            _settings(), async_base_transport=cast("httpx2.AsyncBaseTransport", async_base_transport_mock)
        )
        with pytest.raises(JevResourceCleanupFailedException):
            await system_one_gateway_open_router_jev.close()
        with pytest.raises(JevResourceCleanupFailedException):
            await system_one_gateway_open_router_jev.close()
        await system_one_gateway_open_router_jev.close()
        await system_one_gateway_open_router_jev.close()

    assert (async_type_safe_client_mock.aclose.await_count, async_base_transport_mock.aclose.await_count) == (
        expected_client_close_count,
        expected_transport_close_count,
    )
