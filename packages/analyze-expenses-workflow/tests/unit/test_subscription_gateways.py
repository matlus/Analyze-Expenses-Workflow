import asyncio
import json
import secrets
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_exception_notes, assert_llm_gateway_failure
from copilot import CopilotClient
from copilot.session_events import AssistantMessageData, SessionEvent
from openai_codex import AsyncCodex

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmRequestFailedException,
    LlmRequestTimedOutException,
    LlmResponseUnreadableException,
    LlmSessionDeleteFailedException,
)
from analyze_expenses_workflow.managers.gateways.llm_gateway_codex_subscription import LlmGatewayCodexSubscription
from analyze_expenses_workflow.managers.gateways.llm_gateway_copilot_subscription import LlmGatewayCopilotSubscription


@pytest.mark.asyncio
async def test_complete_WhenCodexTurnCompletes_ThenReturnsResponseAndForwardsSettings() -> None:
    expected_response: str = secrets.token_hex(8)
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_codex_turn_outcome: tuple[str, str, str] = (expected_response, expected_model, expected_reasoning_effort)
    codex_turn_handle_mock: MagicMock = create_codex_turn_handle_mock(
        AsyncMock(return_value=MagicMock(status=MagicMock(value="completed"), final_response=expected_response))
    )
    codex_client_mock: MagicMock = create_codex_client_mock(codex_turn_handle_mock)
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(cast("AsyncCodex", codex_client_mock))

    actual_response: str = await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)
    await llm_gateway_codex_subscription.close()

    actual_codex_turn_outcome: tuple[str, str, str] = (
        actual_response,
        codex_client_mock.thread_start.await_args.kwargs["model"],
        codex_client_mock.thread_start.return_value.turn.await_args.kwargs["effort"].value,
    )
    assert actual_codex_turn_outcome == expected_codex_turn_outcome, (
        f"A completed Codex turn must return its final response and forward model {expected_model!r} and effort "
        f"{expected_reasoning_effort!r}; expected (response, forwarded model, forwarded effort) {expected_codex_turn_outcome}; "
        f"actual {actual_codex_turn_outcome}"
    )
    codex_client_mock.close.assert_awaited_once()


def create_codex_turn_handle_mock(run_mock: AsyncMock, interrupt_failure: Exception | None = None) -> MagicMock:
    codex_turn_handle_mock: MagicMock = MagicMock()
    codex_turn_handle_mock.id = secrets.token_hex(8)
    codex_turn_handle_mock.run = run_mock
    codex_turn_handle_mock.interrupt = AsyncMock(side_effect=interrupt_failure)
    return codex_turn_handle_mock


def create_codex_client_mock(codex_turn_handle_mock: MagicMock) -> MagicMock:
    codex_thread_mock: MagicMock = MagicMock()
    codex_thread_mock.turn = AsyncMock(return_value=codex_turn_handle_mock)
    codex_client_mock: MagicMock = MagicMock(spec=AsyncCodex)
    codex_client_mock.thread_start = AsyncMock(return_value=codex_thread_mock)
    codex_client_mock.close = AsyncMock()
    return codex_client_mock


async def _wait_without_end() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_complete_WhenTheCodexTurnDoesNotFinishInTime_ThenInterruptsTheTurnAndReportsATimedOutRequest() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    turn_timeout_seconds: float = 0.05
    expected_turn_deadline_message: str = f"the Codex turn did not finish within {turn_timeout_seconds} seconds"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestTimedOutException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request did not finish within its time limit",
        message_phrases=("Codex CLI", "timed out", expected_turn_deadline_message),
        contextual_fields=(
            ("Operation", "turn_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderStatusCode", "unknown"),
            ("ProviderError", expected_turn_deadline_message),
        ),
        cause_type="TimeoutError",
        forbidden_message_phrases=(expected_prompt,),
        cause_message=expected_turn_deadline_message,
    )
    expected_interrupt_count: int = 1
    codex_turn_handle_mock: MagicMock = create_codex_turn_handle_mock(AsyncMock(side_effect=_wait_without_end))
    codex_client_mock: MagicMock = create_codex_client_mock(codex_turn_handle_mock)
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(
        cast("AsyncCodex", codex_client_mock), turn_timeout_seconds=turn_timeout_seconds
    )

    with pytest.raises(LlmRequestTimedOutException) as raised_exception:
        async with asyncio.timeout(10):
            await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_timed_out_exception: LlmRequestTimedOutException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_timed_out_exception, expected_llm_gateway_failure)
    assert codex_turn_handle_mock.interrupt.await_count == expected_interrupt_count, (
        f"A Codex turn for model {expected_model!r} that misses its {turn_timeout_seconds}-second deadline must be interrupted once; "
        f"expected {expected_interrupt_count} interrupt; actual {codex_turn_handle_mock.interrupt.await_count}"
    )


@pytest.mark.asyncio
async def test_complete_WhenATimedOutCodexTurnCannotBeInterrupted_ThenReportsTheTimeoutWithAnInterruptNote() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    turn_timeout_seconds: float = 0.05
    expected_interrupt_failure: str = f"turn/interrupt failed {secrets.token_hex(4)}"
    expected_turn_deadline_message: str = f"the Codex turn did not finish within {turn_timeout_seconds} seconds"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestTimedOutException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request did not finish within its time limit",
        message_phrases=("Codex CLI", "timed out", expected_turn_deadline_message),
        contextual_fields=(
            ("Operation", "turn_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderStatusCode", "unknown"),
            ("ProviderError", expected_turn_deadline_message),
        ),
        cause_type="TimeoutError",
        forbidden_message_phrases=(expected_prompt,),
        cause_message=expected_turn_deadline_message,
        cause_note_phrases=("could not be interrupted", expected_interrupt_failure),
        forbidden_cause_note_phrases=(expected_prompt,),
    )
    codex_turn_handle_mock: MagicMock = create_codex_turn_handle_mock(
        AsyncMock(side_effect=_wait_without_end), interrupt_failure=RuntimeError(expected_interrupt_failure)
    )
    codex_client_mock: MagicMock = create_codex_client_mock(codex_turn_handle_mock)
    llm_gateway_codex_subscription: LlmGatewayCodexSubscription = LlmGatewayCodexSubscription(
        cast("AsyncCodex", codex_client_mock), turn_timeout_seconds=turn_timeout_seconds
    )

    with pytest.raises(LlmRequestTimedOutException) as raised_exception:
        async with asyncio.timeout(10):
            await llm_gateway_codex_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_timed_out_exception: LlmRequestTimedOutException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_timed_out_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenCodexProviderFails_ThenTranslatesFailure() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_provider_failure: str = secrets.token_hex(8)
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Codex CLI", "request failed", expected_provider_failure),
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
        message_phrases=("Codex CLI", "request failed", expected_provider_reason),
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
    expected_copilot_session_outcome: tuple[str, str, str] = (expected_response, expected_model, expected_reasoning_effort)
    copilot_session_event: SessionEvent = SessionEvent(
        data=AssistantMessageData(content=expected_response, message_id=secrets.token_hex(8)),
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type="assistant.message",
    )
    copilot_session_mock: MagicMock = MagicMock()
    copilot_session_mock.session_id = expected_session_id
    copilot_session_mock.send_and_wait = AsyncMock(return_value=copilot_session_event)
    copilot_session_mock.disconnect = AsyncMock()
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.create_session = AsyncMock(return_value=copilot_session_mock)
    copilot_client_mock.delete_session = AsyncMock()
    copilot_client_mock.stop = AsyncMock()
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    actual_response: str = await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)
    await llm_gateway_copilot_subscription.close()

    actual_copilot_session_outcome: tuple[str, str, str] = (
        actual_response,
        copilot_client_mock.create_session.await_args.kwargs["model"],
        copilot_client_mock.create_session.await_args.kwargs["reasoning_effort"],
    )
    assert actual_copilot_session_outcome == expected_copilot_session_outcome, (
        f"A completed Copilot session must return its assistant message and forward model {expected_model!r} and effort "
        f"{expected_reasoning_effort!r}; expected (response, forwarded model, forwarded effort) {expected_copilot_session_outcome}; "
        f"actual {actual_copilot_session_outcome}"
    )
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
        message_phrases=("Copilot CLI", "request failed", expected_provider_failure),
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


def create_copilot_session_mock(session_id: str, send_result: SessionEvent | Exception, disconnect_failure: Exception | None = None) -> MagicMock:
    copilot_session_mock: MagicMock = MagicMock()
    copilot_session_mock.session_id = session_id
    if isinstance(send_result, Exception):
        copilot_session_mock.send_and_wait = AsyncMock(side_effect=send_result)
    else:
        copilot_session_mock.send_and_wait = AsyncMock(return_value=send_result)
    copilot_session_mock.disconnect = AsyncMock(side_effect=disconnect_failure)
    return copilot_session_mock


@pytest.mark.asyncio
async def test_complete_WhenCopilotSdkReturnsAnUnreadableResponse_ThenReportsAnUnreadableResponseAndDeletesTheSession() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_provider_failure: str = f"Invalid JSON-RPC response {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseUnreadableException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED.value,
        reason="Coding assistant response could not be read",
        message_phrases=("Copilot CLI", "unreadable response", expected_provider_failure),
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

    with pytest.raises(LlmResponseUnreadableException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_response_unreadable_exception: LlmResponseUnreadableException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_unreadable_exception, expected_llm_gateway_failure)
    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)


@pytest.mark.asyncio
async def test_complete_WhenTheSessionCannotBeDeletedAfterAFailedRequest_ThenReportsTheRequestFailureWithADeletionNote() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_request_failure: str = f"Session error: {secrets.token_hex(4)}"
    expected_delete_failure: str = f"delete failed {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Copilot CLI", "request failed", expected_request_failure),
        contextual_fields=(
            ("Operation", "session_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderError", expected_request_failure),
        ),
        cause_type="Exception",
        forbidden_message_phrases=(expected_prompt,),
        cause_message=expected_request_failure,
        cause_note_phrases=(expected_delete_failure,),
        forbidden_cause_note_phrases=(expected_prompt,),
    )
    copilot_session_mock: MagicMock = create_copilot_session_mock(expected_session_id, Exception(expected_request_failure))
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock, delete_failure=RuntimeError(expected_delete_failure))
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenTheCopilotRequestIsCancelled_ThenDeletesTheSessionAndPropagatesTheCancellation() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    request_started_event: asyncio.Event = asyncio.Event()

    async def wait_until_cancelled(prompt: str, *, timeout: float) -> SessionEvent | None:  # noqa: ASYNC109 - mirrors the Copilot SDK signature
        request_started_event.set()
        await asyncio.Event().wait()
        return None

    copilot_session_mock: MagicMock = create_copilot_session_mock(expected_session_id, Exception("not reached"))
    copilot_session_mock.send_and_wait = AsyncMock(side_effect=wait_until_cancelled)
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock)
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    complete_task: asyncio.Task[str] = asyncio.create_task(llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None))
    await request_started_event.wait()
    complete_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await complete_task

    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)


@pytest.mark.asyncio
async def test_complete_WhenACancelledCopilotRequestCannotDisconnect_ThenPropagatesTheCancellationWithADisconnectNote() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_disconnect_failure: str = f"session.detach failed {secrets.token_hex(4)}"
    expected_note_phrases: tuple[str, ...] = (expected_disconnect_failure,)
    forbidden_note_phrases: tuple[str, ...] = (expected_prompt,)
    request_started_event: asyncio.Event = asyncio.Event()

    async def wait_until_cancelled(prompt: str, *, timeout: float) -> SessionEvent | None:  # noqa: ASYNC109 - mirrors the Copilot SDK signature
        request_started_event.set()
        await asyncio.Event().wait()
        return None

    copilot_session_mock: MagicMock = create_copilot_session_mock(
        expected_session_id, Exception("not reached"), disconnect_failure=RuntimeError(expected_disconnect_failure)
    )
    copilot_session_mock.send_and_wait = AsyncMock(side_effect=wait_until_cancelled)
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock)
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    complete_task: asyncio.Task[str] = asyncio.create_task(llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None))
    await request_started_event.wait()
    complete_task.cancel()
    with pytest.raises(asyncio.CancelledError) as raised_cancellation:
        await complete_task

    assert_exception_notes(
        raised_cancellation.value,
        expected_note_phrases,
        forbidden_note_phrases,
        f"Cancelled Copilot request for model {expected_model!r} whose session {expected_session_id} could not be disconnected",
    )
    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)


@pytest.mark.asyncio
async def test_complete_WhenATimedOutCopilotRequestCannotDisconnect_ThenReportsTheTimeoutWithADisconnectNote() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_request_failure: str = f"Timeout after 900.0s waiting for session.idle {secrets.token_hex(4)}"
    expected_disconnect_failure: str = f"session.detach failed {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestTimedOutException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request did not finish within its time limit",
        message_phrases=("Copilot CLI", "timed out", expected_request_failure),
        contextual_fields=(
            ("Operation", "session_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderError", expected_request_failure),
        ),
        cause_type="TimeoutError",
        forbidden_message_phrases=(expected_prompt,),
        cause_note_phrases=(expected_disconnect_failure,),
        forbidden_cause_note_phrases=(expected_prompt,),
    )
    copilot_session_mock: MagicMock = create_copilot_session_mock(
        expected_session_id, TimeoutError(expected_request_failure), disconnect_failure=RuntimeError(expected_disconnect_failure)
    )
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock)
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmRequestTimedOutException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_timed_out_exception: LlmRequestTimedOutException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_timed_out_exception, expected_llm_gateway_failure)
    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)


@pytest.mark.asyncio
async def test_complete_WhenAnAnsweredCopilotSessionCannotDisconnect_ThenReportsARequestFailureAndDeletesTheSession() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_disconnect_failure: str = f"session.detach failed {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Copilot CLI", "request failed", expected_disconnect_failure),
        contextual_fields=(
            ("Operation", "session_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "default"),
            ("ProviderError", expected_disconnect_failure),
        ),
        cause_type="RuntimeError",
        forbidden_message_phrases=(expected_prompt,),
    )
    copilot_session_event: SessionEvent = SessionEvent(
        data=AssistantMessageData(content=secrets.token_hex(8), message_id=secrets.token_hex(8)),
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type="assistant.message",
    )
    copilot_session_mock: MagicMock = create_copilot_session_mock(
        expected_session_id, copilot_session_event, disconnect_failure=RuntimeError(expected_disconnect_failure)
    )
    copilot_client_mock: MagicMock = create_copilot_client_mock(copilot_session_mock)
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)
    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_failure_type", [ValueError, Exception])
async def test_close_WhenCopilotStopRaisesABareSdkFailure_ThenReportsTheClientCleanupFailure(stop_failure_type: type[Exception]) -> None:
    expected_stop_failure: str = f"connection lost {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmClientCleanupFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant client cleanup failed",
        message_phrases=("Copilot CLI", "client cleanup failed"),
        contextual_fields=(("Operation", "client_stop"),),
        cause_type=stop_failure_type.__name__,
    )
    copilot_client_mock: MagicMock = MagicMock(spec=CopilotClient)
    copilot_client_mock.stop = AsyncMock(side_effect=stop_failure_type(expected_stop_failure))
    llm_gateway_copilot_subscription: LlmGatewayCopilotSubscription = LlmGatewayCopilotSubscription(cast("CopilotClient", copilot_client_mock))

    with pytest.raises(LlmClientCleanupFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.close()

    actual_llm_client_cleanup_failed_exception: LlmClientCleanupFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_client_cleanup_failed_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenTheSessionCannotBeDeletedAfterASuccessfulRequest_ThenReportsTheSessionDeletionFailure() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_session_id: str = secrets.token_hex(8)
    expected_delete_failure: str = f"delete failed {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmSessionDeleteFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant session could not be deleted from the user's history",
        message_phrases=("Copilot CLI", expected_session_id, "could not be deleted", expected_delete_failure),
        contextual_fields=(
            ("Operation", "session_delete"),
            ("Model", expected_model),
            ("SessionId", expected_session_id),
            ("ProviderError", expected_delete_failure),
        ),
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

    with pytest.raises(LlmSessionDeleteFailedException) as raised_exception:
        await llm_gateway_copilot_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_session_delete_failed_exception: LlmSessionDeleteFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_session_delete_failed_exception, expected_llm_gateway_failure)
    copilot_client_mock.delete_session.assert_awaited_once_with(expected_session_id)
