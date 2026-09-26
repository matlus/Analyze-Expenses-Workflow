from types import TracebackType
from typing import Final, Self, cast, final

from copilot import CopilotClient
from copilot._jsonrpc import JsonRpcError, ProcessExitedError
from copilot.client import StopError
from copilot.session import ReasoningEffort
from copilot.session_events import AssistantMessageData, SessionEvent

from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmGatewayClosedException,
    LlmRequestFailedException,
    LlmResponseInvalidException,
    LlmUnsupportedReasoningEffortException,
)


@final
class LlmGatewayCopilotSubscription:
    _SUPPORTED_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})
    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"
    _MODEL_CONTEXT_FIELD_NAME: Final[str] = "Model"

    def __init__(self, copilot_client: CopilotClient) -> None:
        self._copilot_client: CopilotClient = copilot_client
        self._closed: bool = False

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self._ensure_open()
        resolved_reasoning_effort: ReasoningEffort | None = self._resolve_effort(reasoning_effort)

        try:
            session_event: SessionEvent | None = await self._request(prompt, model, resolved_reasoning_effort)
        except (JsonRpcError, ProcessExitedError, StopError, RuntimeError, OSError, TimeoutError) as exc:
            raise self._request_failure(model, resolved_reasoning_effort) from exc
        except Exception as exc:
            if type(exc) is Exception and str(exc).startswith("Session error: "):
                raise self._request_failure(model, resolved_reasoning_effort) from exc
            raise

        return self._response_text(session_event)

    @classmethod
    def _request_failure(cls, model: str, reasoning_effort: ReasoningEffort | None) -> LlmRequestFailedException:
        return LlmRequestFailedException(
            "Copilot subscription request failed",
            {
                cls._OPERATION_CONTEXT_FIELD_NAME: "session_request",
                cls._MODEL_CONTEXT_FIELD_NAME: model,
                "ReasoningEffort": reasoning_effort if reasoning_effort is not None else "default",
            },
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise LlmGatewayClosedException(
                "Copilot gateway is closed", {self._OPERATION_CONTEXT_FIELD_NAME: "gateway_open_state_check", "GatewayState": "closed"}
            )

    @classmethod
    def _resolve_effort(cls, reasoning_effort: str | None) -> ReasoningEffort | None:
        if reasoning_effort is not None and reasoning_effort not in cls._SUPPORTED_EFFORTS:
            supported_efforts: str = ", ".join(sorted(cls._SUPPORTED_EFFORTS))
            raise LlmUnsupportedReasoningEffortException(
                f"Copilot does not recognize reasoning effort {reasoning_effort!r}; supported values: {supported_efforts}",
                {
                    cls._OPERATION_CONTEXT_FIELD_NAME: "reasoning_effort_validation",
                    "RejectedReasoningEffort": reasoning_effort,
                    "AllowedReasoningEfforts": supported_efforts,
                },
            )
        return cast("ReasoningEffort | None", reasoning_effort)

    def _client_for_request(self) -> CopilotClient:
        self._ensure_open()
        return self._copilot_client

    async def _request(self, prompt: str, model: str, resolved_reasoning_effort: ReasoningEffort | None) -> SessionEvent | None:
        copilot_client: CopilotClient = self._client_for_request()
        return await self._send_message(copilot_client, prompt, model, resolved_reasoning_effort)

    @staticmethod
    async def _send_message(
        copilot_client: CopilotClient, prompt: str, model: str, resolved_reasoning_effort: ReasoningEffort | None
    ) -> SessionEvent | None:
        async with await copilot_client.create_session(
            model=model,
            reasoning_effort=resolved_reasoning_effort,
            available_tools=[],
        ) as copilot_session:
            return await copilot_session.send_and_wait(prompt)

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
