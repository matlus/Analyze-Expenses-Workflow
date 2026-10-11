import json
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
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmRequestFailedException, LlmResponseInvalidException
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
async def test_complete_WhenCodexProviderFails_ThenTranslatesFailure() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_provider_failure: str = secrets.token_hex(8)
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Codex", "request failed", expected_provider_failure),
        contextual_fields=(
            ("Operation", "turn_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderStatusCode", "unknown"),
            ("ProviderError", expected_provider_failure),
        ),
        cause_type="OSError",
        forbidden_message_phrases=(expected_prompt,),
    )
    codex_client_mock: MagicMock = MagicMock(spec=AsyncCodex)
    codex_client_mock.thread_start = AsyncMock(side_effect=OSError(expected_provider_failure))
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(cast("AsyncCodex", codex_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenCodexIsRateLimited_ThenReportsARetryableFailureWithTheProviderReason() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_provider_reason: str = f"Rate limit reached {secrets.token_hex(4)}"
    provider_error_document: str = json.dumps(
        {"type": "error", "status": 429, "error": {"type": "rate_limit_error", "message": expected_provider_reason}}
    )
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Codex", "request failed", expected_provider_reason),
        contextual_fields=(
            ("Operation", "turn_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderStatusCode", 429),
            ("ProviderError", expected_provider_reason),
        ),
        cause_type="RuntimeError",
        forbidden_message_phrases=(expected_prompt,),
    )
    codex_client_mock: MagicMock = MagicMock(spec=AsyncCodex)
    codex_client_mock.thread_start = AsyncMock(side_effect=RuntimeError(provider_error_document))
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(cast("AsyncCodex", codex_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, None)

    assert_llm_gateway_failure(raised_exception.value, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenCopilotSessionCompletes_ThenReturnsMessageForwardsSettingsAndDeletesTheSession() -> None:
    expected_response: str = secrets.token_hex(8)
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_session_id: str = secrets.token_hex(8)
    expected_session_timeout_seconds: float = 900.0
    copilot_session_event: SessionEvent = SessionEvent(
        data=AssistantMessageData(content=expected_response, message_id=secrets.token_hex(8)),
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type="assistant.message",
    )
    copilot_session_mock: MagicMock = MagicMock()
    copilot_session_mock.session_id = expected_session_id
    copilot_session_mock.send_and_wait = AsyncMock(return_value=copilot_session_event)
    copilot_session_mock.__aenter__ = AsyncMock(return_value=copilot_session_mock)
    copilot_session_mock.__aexit__ = AsyncMock(return_value=None)
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.create_session = AsyncMock(return_value=copilot_session_mock)
    copilot_client_mock.delete_session = AsyncMock()
    copilot_client_mock.stop = AsyncMock()
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    actual_response: str = await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)
    await llm_gateway_copilot_subscription.close()

    assert (
        actual_response,
        copilot_client_mock.create_session.await_args.kwargs["model"],
        copilot_client_mock.create_session.await_args.kwargs["reasoning_effort"],
    ) == (expected_response, expected_model, expected_reasoning_effort)
    copilot_session_mock.send_and_wait.assert_awaited_once_with(expected_prompt, timeout=expected_session_timeout_seconds)
    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)
    copilot_client_mock.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_WhenCopilotProviderFails_ThenTranslatesFailure() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_provider_failure: str = secrets.token_hex(8)
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Copilot", "request failed", expected_provider_failure),
        contextual_fields=(
            ("Operation", "session_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderError", expected_provider_failure),
        ),
        cause_type="OSError",
        forbidden_message_phrases=(expected_prompt,),
    )
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.create_session = AsyncMock(side_effect=OSError(expected_provider_failure))
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)


def create_copilot_client_mock(copilot_session_mock: MagicMock, delete_failure: Exception | None = None) -> MagicMock:
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.create_session = AsyncMock(return_value=copilot_session_mock)
    copilot_client_mock.delete_session = AsyncMock(side_effect=delete_failure)
    return copilot_client_mock


def create_copilot_session_mock(session_id: str, send_result: SessionEvent | Exception) -> MagicMock:
    copilot_session_mock: MagicMock = MagicMock()
    copilot_session_mock.session_id = session_id
    if isinstance(send_result, Exception):
        copilot_session_mock.send_and_wait = AsyncMock(side_effect=send_result)
    else:
        copilot_session_mock.send_and_wait = AsyncMock(return_value=send_result)
    copilot_session_mock.__aenter__ = AsyncMock(return_value=copilot_session_mock)
    copilot_session_mock.__aexit__ = AsyncMock(return_value=None)
    return copilot_session_mock


@pytest.mark.asyncio
async def test_complete_WhenCopilotSdkReturnsAnUnreadableResponse_ThenReportsAnInvalidResponseAndDeletesTheSession() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_provider_failure: str = f"Invalid JSON-RPC response {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseInvalidException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED.value,
        reason="Coding assistant response violates its contract",
        message_phrases=("Copilot", "unreadable response", expected_provider_failure),
        contextual_fields=(
            ("Operation", "session_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderError", expected_provider_failure),
        ),
        cause_type="ValueError",
        forbidden_message_phrases=(expected_prompt,),
    )
    copilot_session_mock: MagicMock = create_copilot_session_mock(expected_session_id, ValueError(expected_provider_failure))
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock)
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmResponseInvalidException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_response_invalid_exception: LlmResponseInvalidException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_invalid_exception, expected_llm_gateway_failure)
    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)


@pytest.mark.asyncio
async def test_complete_WhenTheSessionCannotBeDeletedAfterAFailedRequest_ThenReportsTheRequestFailureWithADeletionNote() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_request_failure: str = f"Session error: {secrets.token_hex(4)}"
    expected_delete_failure: str = f"delete failed {secrets.token_hex(4)}"
    expected_outcome: tuple[type[BaseException], str, bool] = (Exception, expected_request_failure, True)
    copilot_session_mock: MagicMock = create_copilot_session_mock(expected_session_id, Exception(expected_request_failure))
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock, delete_failure=RuntimeError(expected_delete_failure))
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_request_error: BaseException | None = raised_exception.value.__cause__
    actual_outcome: tuple[type[BaseException], str, bool] = (
        type(actual_request_error),
        str(actual_request_error),
        any(expected_delete_failure in note for note in getattr(actual_request_error, "__notes__", [])),
    )
    assert actual_outcome == expected_outcome, (
        f"expected (cause type, cause message, deletion note present) {expected_outcome}; actual {actual_outcome}"
    )


@pytest.mark.asyncio
async def test_complete_WhenTheSessionCannotBeDeletedAfterASuccessfulRequest_ThenReportsTheDeletionFailure() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_delete_failure: str = f"delete failed {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Copilot", "request failed", expected_delete_failure),
        contextual_fields=(("Operation", "session_request"), ("Model", expected_model), ("ProviderError", expected_delete_failure)),
        cause_type="RuntimeError",
        forbidden_message_phrases=(expected_prompt,),
    )
    copilot_session_event: SessionEvent = SessionEvent(
        data=AssistantMessageData(content=secrets.token_hex(8), message_id=secrets.token_hex(8)),
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type="assistant.message",
    )
    copilot_session_mock: MagicMock = create_copilot_session_mock(expected_session_id, copilot_session_event)
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock, delete_failure=RuntimeError(expected_delete_failure))
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)
