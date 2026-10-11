from types import TracebackType
from typing import Final, Self, cast, final

from copilot import CopilotClient
from copilot._jsonrpc import JsonRpcError, ProcessExitedError
from copilot.client import StopError
from copilot.session import CopilotSession, ReasoningEffort
from copilot.session_events import AssistantMessageData, SessionEvent

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionContext
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmGatewayBaseException,
    LlmGatewayClosedException,
    LlmRequestFailedException,
    LlmResponseInvalidException,
)


@final
class LlmGatewayCopilotSubscription:
    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"
    _MODEL_CONTEXT_FIELD_NAME: Final[str] = "Model"
    _PROVIDER_ERROR_MESSAGE_LIMIT: Final[int] = 500
    # The SDK waits only 60 seconds by default, and a high-effort turn can take longer; Claude Code allows the same 900.
    _SESSION_TIMEOUT_SECONDS: Final[float] = 900.0
    # The SDK reports a session error or a lost connection as a bare Exception and an invalid JSON-RPC response as a
    # bare ValueError, alongside its own exception types; none of them may leave the gateway untranslated.
    _SDK_FAILURE_TYPES: Final[tuple[type[Exception], ...]] = (
        JsonRpcError,
        ProcessExitedError,
        StopError,
        RuntimeError,
        OSError,
        TimeoutError,
        ValueError,
    )

    def __init__(self, copilot_client: CopilotClient) -> None:
        self._copilot_client: CopilotClient = copilot_client
        self._closed: bool = False

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self._ensure_open()
        resolved_reasoning_effort: ReasoningEffort | None = self._resolve_effort(reasoning_effort)

        try:
            session_event: SessionEvent | None = await self._send_message(self._copilot_client, prompt, model, resolved_reasoning_effort)
        except Exception as exc:
            if not self._is_sdk_failure(exc):
                raise
            raise self._request_failure(exc, model, resolved_reasoning_effort) from exc

        return self._response_text(session_event)

    @classmethod
    def _is_sdk_failure(cls, request_error: Exception) -> bool:
        return isinstance(request_error, cls._SDK_FAILURE_TYPES) or type(request_error) is Exception

    @classmethod
    def _request_failure(cls, request_error: Exception, model: str, reasoning_effort: ReasoningEffort | None) -> LlmGatewayBaseException:
        """An unreadable JSON-RPC response (the SDK's bare ValueError) fails the same way when resent; every other SDK
        failure, such as a lost connection or a session error, may succeed on another attempt."""
        copilot_failure_message: str = (str(request_error) or type(request_error).__name__)[: cls._PROVIDER_ERROR_MESSAGE_LIMIT]
        contextual_data_by_name: ExceptionContext = {
            cls._OPERATION_CONTEXT_FIELD_NAME: "session_request",
            cls._MODEL_CONTEXT_FIELD_NAME: model,
            "ReasoningEffort": reasoning_effort if reasoning_effort is not None else "default",
            "ProviderError": copilot_failure_message,
        }
        if type(request_error) is ValueError:
            return LlmResponseInvalidException(f"Copilot returned an unreadable response: {copilot_failure_message}", contextual_data_by_name)
        return LlmRequestFailedException(f"Copilot subscription request failed: {copilot_failure_message}", contextual_data_by_name)

    def _ensure_open(self) -> None:
        if self._closed:
            raise LlmGatewayClosedException(
                "Copilot gateway is closed", {self._OPERATION_CONTEXT_FIELD_NAME: "gateway_open_state_check", "GatewayState": "closed"}
            )

    @staticmethod
    def _resolve_effort(reasoning_effort: str | None) -> ReasoningEffort | None:
        # The configuration provider has already accepted only the efforts this subscription supports.
        return cast("ReasoningEffort | None", reasoning_effort)

    @classmethod
    async def _send_message(
        cls, copilot_client: CopilotClient, prompt: str, model: str, resolved_reasoning_effort: ReasoningEffort | None
    ) -> SessionEvent | None:
        copilot_session: CopilotSession = await copilot_client.create_session(
            model=model,
            reasoning_effort=resolved_reasoning_effort,
            available_tools=[],
        )
        try:
            async with copilot_session:
                session_event: SessionEvent | None = await copilot_session.send_and_wait(prompt, timeout=cls._SESSION_TIMEOUT_SECONDS)
        except Exception as request_error:
            await cls._delete_session_keeping_request_error(copilot_client, copilot_session.session_id, request_error)
            raise
        # Disconnecting keeps the session, and so the user's expense lines, in the user's Copilot history; a session that
        # cannot be deleted fails the request so that the leftover history does not go unnoticed.
        await copilot_client.delete_session(copilot_session.session_id)
        return session_event

    @classmethod
    async def _delete_session_keeping_request_error(cls, copilot_client: CopilotClient, session_id: str, request_error: Exception) -> None:
        """The request's own failure is the one to report; a deletion failure after it is recorded on it as a note."""
        try:
            await copilot_client.delete_session(session_id)
        except Exception as delete_error:
            if not cls._is_sdk_failure(delete_error):
                raise
            request_error.add_note(f"The Copilot session {session_id} could not be deleted from the user's history: {delete_error}")

    @staticmethod
    def _response_text(session_event: SessionEvent | None) -> str:
        if session_event is None or not isinstance(session_event.data, AssistantMessageData) or not session_event.data.content:
            event_data_type: str = type(session_event.data).__name__ if session_event is not None else "None"
            raise LlmResponseInvalidException(
                "Copilot subscription request returned no message",
                {LlmGatewayCopilotSubscription._OPERATION_CONTEXT_FIELD_NAME: "response_validation", "EventDataType": event_data_type},
            )
        return session_event.data.content

    async def close(self) -> None:
        if not self._closed:
            try:
                await self._copilot_client.stop()
            except (JsonRpcError, ProcessExitedError, StopError, RuntimeError, OSError, TimeoutError, ExceptionGroup) as close_error:
                raise LlmClientCleanupFailedException(
                    "Copilot subscription cleanup failed", {self._OPERATION_CONTEXT_FIELD_NAME: "client_stop"}
                ) from close_error
            self._closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
