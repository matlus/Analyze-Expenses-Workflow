import secrets
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_llm_gateway_failure
from copilot import CopilotClient
from copilot.session_events import AssistantMessageData, SessionEvent
from openai_codex import AsyncCodex

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmRequestFailedException,
    LlmUnsupportedReasoningEffortException,
)
from analyze_expenses_workflow.managers.gateways.llm_gateway_codex_subscription import LlmGatewayCodexSubscription
from analyze_expenses_workflow.managers.gateways.llm_gateway_copilot_subscription import LlmGatewayCopilotSubscription


@pytest.mark.asyncio
async def test_complete_WhenCodexTurnCompletes_ThenReturnsResponseAndForwardsSettings() -> None:
    expected_response: str = secrets.token_hex(8)
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    codex_client_mock: MagicMock = MagicMock(spec=AsyncCodex)
    codex_thread_mock: MagicMock = MagicMock()
    codex_thread_mock.run = AsyncMock(return_value=MagicMock(status=MagicMock(value="completed"), final_response=expected_response))
    codex_client_mock.thread_start = AsyncMock(return_value=codex_thread_mock)
    codex_client_mock.close = AsyncMock()
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(cast("AsyncCodex", codex_client_mock))

    actual_response: str = await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)
    await llm_gateway_codex_subscription.close()

    assert (
        actual_response,
        codex_client_mock.thread_start.await_args.kwargs["model"],
        codex_thread_mock.run.await_args.kwargs["effort"].value,
    ) == (expected_response, expected_model, expected_reasoning_effort)
    codex_client_mock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_WhenCodexEffortIsInvalid_ThenRejectsBeforeRequest() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    rejected_reasoning_effort: str = f"invalid-{secrets.token_hex(8)}"
    expected_allowed_reasoning_efforts: str = "high, low, max, medium, minimal, none, ultra, xhigh"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmUnsupportedReasoningEffortException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED,
        reason="Coding assistant reasoning effort is unsupported",
        message_phrases=("Codex", "does not recognize reasoning effort"),
        contextual_fields=(
            ("Operation", "reasoning_effort_validation"),
            ("RejectedReasoningEffort", rejected_reasoning_effort),
            ("AllowedReasoningEfforts", expected_allowed_reasoning_efforts),
        ),
        forbidden_message_phrases=(expected_prompt,),
    )
    codex_client_mock: MagicMock = MagicMock(spec=AsyncCodex)
    codex_client_mock.thread_start = AsyncMock()
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(cast("AsyncCodex", codex_client_mock))

    with pytest.raises(LlmUnsupportedReasoningEffortException) as raised_exception:
        await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, rejected_reasoning_effort)

    actual_llm_gateway_exception: LlmUnsupportedReasoningEffortException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
    codex_client_mock.thread_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_WhenCodexProviderFails_ThenTranslatesFailure() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED,
        reason="Coding assistant request failed",
        message_phrases=("Codex", "request failed"),
        contextual_fields=(("Operation", "turn_request"), ("Model", expected_model), ("ReasoningEffort", "default")),
        cause_type="OSError",
        forbidden_message_phrases=(expected_prompt,),
    )
    codex_client_mock: MagicMock = MagicMock(spec=AsyncCodex)
    codex_client_mock.thread_start = AsyncMock(side_effect=OSError(secrets.token_hex(8)))
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(cast("AsyncCodex", codex_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_gateway_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenCopilotSessionCompletes_ThenReturnsMessageAndForwardsSettings() -> None:
    expected_response: str = secrets.token_hex(8)
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    copilot_session_event: SessionEvent = SessionEvent(
        data=AssistantMessageData(content=expected_response, message_id=secrets.token_hex(8)),
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type="assistant.message",
    )
    copilot_session_mock: MagicMock = MagicMock()
    copilot_session_mock.send_and_wait = AsyncMock(return_value=copilot_session_event)
    copilot_session_mock.__aenter__ = AsyncMock(return_value=copilot_session_mock)
    copilot_session_mock.__aexit__ = AsyncMock(return_value=None)
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.create_session = AsyncMock(return_value=copilot_session_mock)
    copilot_client_mock.stop = AsyncMock()
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    actual_response: str = await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)
    await llm_gateway_copilot_subscription.close()

    assert (
        actual_response,
        copilot_client_mock.create_session.await_args.kwargs["model"],
        copilot_client_mock.create_session.await_args.kwargs["reasoning_effort"],
    ) == (expected_response, expected_model, expected_reasoning_effort)
    copilot_session_mock.send_and_wait.assert_awaited_once_with(expected_prompt)
    copilot_client_mock.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_WhenCopilotReceivesCodexOnlyEffort_ThenRejectsBeforeRequest() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    rejected_reasoning_effort: str = "ultra"
    expected_allowed_reasoning_efforts: str = "high, low, max, medium, xhigh"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmUnsupportedReasoningEffortException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED,
        reason="Coding assistant reasoning effort is unsupported",
        message_phrases=("Copilot", "does not recognize reasoning effort"),
        contextual_fields=(
            ("Operation", "reasoning_effort_validation"),
            ("RejectedReasoningEffort", rejected_reasoning_effort),
            ("AllowedReasoningEfforts", expected_allowed_reasoning_efforts),
        ),
        forbidden_message_phrases=(expected_prompt,),
    )
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.create_session = AsyncMock()
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmUnsupportedReasoningEffortException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, rejected_reasoning_effort)

    actual_llm_gateway_exception: LlmUnsupportedReasoningEffortException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
    copilot_client_mock.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_WhenCopilotProviderFails_ThenTranslatesFailure() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED,
        reason="Coding assistant request failed",
        message_phrases=("Copilot", "request failed"),
        contextual_fields=(("Operation", "session_request"), ("Model", expected_model), ("ReasoningEffort", "default")),
        cause_type="OSError",
        forbidden_message_phrases=(expected_prompt,),
    )
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.create_session = AsyncMock(side_effect=OSError(secrets.token_hex(8)))
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_gateway_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
