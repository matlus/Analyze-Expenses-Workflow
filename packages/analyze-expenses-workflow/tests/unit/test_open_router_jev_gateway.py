import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl, SecretStr
from typesafe_sdk import AsyncTypeSafeClient

from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.gateways.open_router_jev_gateway import OpenRouterJevGateway
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion


def _settings() -> JevSettings:
    return JevSettings(
        open_router_key=SecretStr("test-only-key"),
        open_router_base_url=HttpUrl("https://openrouter.ai/api/v1"),
        jev_model="typesafe/jev",
    )


@pytest.mark.asyncio
async def test_concurrent_jev_requests_share_one_lazily_created_client() -> None:
    answer: MagicMock = MagicMock()
    answer.model_dump.return_value = {"choice": "groceries", "confidence": 0.9, "probabilities": {"groceries": 0.9}}
    response: MagicMock = MagicMock(choices={"category": answer})
    client: MagicMock = MagicMock()
    client.system_one = AsyncMock(return_value=response)
    client.aclose = AsyncMock()
    question: ChoiceQuestion = ChoiceQuestion(instructions="Classify expense", criteria={"groceries": "food"})

    with patch(
        "analyze_expenses_workflow.managers.gateways.open_router_jev_gateway.AsyncTypeSafeClient",
        return_value=client,
    ) as client_factory:
        gateway: OpenRouterJevGateway = OpenRouterJevGateway(_settings())
        decisions: list[ChoiceDecision] = list(await asyncio.gather(*(gateway.choose("Kroger $10", question) for _ in range(8))))
        await gateway.close()

    assert len(decisions) == 8
    assert all(decision.choice == "groceries" for decision in decisions)
    client_factory.assert_called_once()
    assert client.system_one.await_count == 8
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_unused_jev_gateway_does_not_create_client() -> None:
    with patch("analyze_expenses_workflow.managers.gateways.open_router_jev_gateway.AsyncTypeSafeClient") as client_factory:
        gateway: OpenRouterJevGateway = OpenRouterJevGateway(_settings())
        await gateway.close()

    client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_jev_gateway_accepts_injected_client() -> None:
    client: MagicMock = MagicMock()
    client.aclose = AsyncMock()
    gateway: OpenRouterJevGateway = OpenRouterJevGateway(_settings(), cast("AsyncTypeSafeClient", client))

    await gateway.close()

    client.aclose.assert_awaited_once()
