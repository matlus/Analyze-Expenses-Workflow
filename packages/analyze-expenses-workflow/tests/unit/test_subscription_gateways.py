from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from copilot import CopilotClient
from copilot.session_events import AssistantMessageData, SessionEvent
from openai_codex import AsyncCodex

from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException
from analyze_expenses_workflow.managers.gateways.codex_subscription_gateway import CodexSubscriptionGateway
from analyze_expenses_workflow.managers.gateways.copilot_subscription_gateway import CopilotSubscriptionGateway


@pytest.mark.asyncio
async def test_codex_gateway_passes_operation_model_and_effort() -> None:
    client = MagicMock(spec=AsyncCodex)
    thread = MagicMock()
    thread.run = AsyncMock(return_value=MagicMock(status=MagicMock(value="completed"), final_response='{"amount": 10}'))
    client.thread_start = AsyncMock(return_value=thread)
    client.close = AsyncMock()
    gateway = CodexSubscriptionGateway(cast("AsyncCodex", client))

    result = await gateway.complete("Extract this line", "gpt-6-luna", "low")

    assert result == '{"amount": 10}'
    assert client.thread_start.await_args.kwargs["model"] == "gpt-6-luna"
    assert thread.run.await_args.kwargs["effort"].value == "low"
    await gateway.close()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_codex_gateway_rejects_invalid_effort_before_request() -> None:
    client = MagicMock(spec=AsyncCodex)
    client.thread_start = AsyncMock()
    gateway = CodexSubscriptionGateway(cast("AsyncCodex", client))

    with pytest.raises(LlmGatewayException, match="does not recognize reasoning effort"):
        await gateway.complete("Extract this line", "gpt-6-luna", "invalid")

    client.thread_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_codex_gateway_translates_provider_failure() -> None:
    client = MagicMock(spec=AsyncCodex)
    client.thread_start = AsyncMock(side_effect=OSError("runtime unavailable"))
    gateway = CodexSubscriptionGateway(cast("AsyncCodex", client))

    with pytest.raises(LlmGatewayException, match="Codex subscription request failed"):
        await gateway.complete("Extract this line", "gpt-6-luna", None)


@pytest.mark.asyncio
async def test_copilot_gateway_returns_message_and_passes_operation_settings() -> None:
    event = SessionEvent(
        data=AssistantMessageData(content='{"amount": 10}', message_id="message-1"),
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type="assistant.message",
    )
    session = MagicMock()
    session.send_and_wait = AsyncMock(return_value=event)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    client = MagicMock(spec=CopilotClient)
    client.create_session = AsyncMock(return_value=session)
    client.stop = AsyncMock()
    gateway = CopilotSubscriptionGateway(cast("CopilotClient", client))

    result = await gateway.complete("Extract this line", "gpt-6-luna", "low")

    assert result == '{"amount": 10}'
    assert client.create_session.await_args.kwargs["model"] == "gpt-6-luna"
    assert client.create_session.await_args.kwargs["reasoning_effort"] == "low"
    session.send_and_wait.assert_awaited_once_with("Extract this line")
    await gateway.close()
    client.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_copilot_gateway_rejects_invalid_effort_before_request() -> None:
    client = MagicMock(spec=CopilotClient)
    client.create_session = AsyncMock()
    gateway = CopilotSubscriptionGateway(cast("CopilotClient", client))

    with pytest.raises(LlmGatewayException, match="does not recognize reasoning effort"):
        await gateway.complete("Extract this line", "gpt-6-luna", "ultra")

    client.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_copilot_gateway_translates_provider_failure() -> None:
    client = MagicMock(spec=CopilotClient)
    client.create_session = AsyncMock(side_effect=OSError("runtime unavailable"))
    gateway = CopilotSubscriptionGateway(cast("CopilotClient", client))

    with pytest.raises(LlmGatewayException, match="Copilot subscription request failed"):
        await gateway.complete("Extract this line", "gpt-6-luna", None)


@pytest.mark.asyncio
async def test_unused_subscription_gateways_do_not_create_clients() -> None:
    with (
        patch("analyze_expenses_workflow.managers.gateways.codex_subscription_gateway.AsyncCodex") as codex_client_factory,
        patch("analyze_expenses_workflow.managers.gateways.copilot_subscription_gateway.CopilotClient") as copilot_client_factory,
    ):
        codex_gateway = CodexSubscriptionGateway()
        copilot_gateway = CopilotSubscriptionGateway()

        await codex_gateway.close()
        await copilot_gateway.close()

    codex_client_factory.assert_not_called()
    copilot_client_factory.assert_not_called()
