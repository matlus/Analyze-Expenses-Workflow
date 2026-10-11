import secrets
from typing import ClassVar, cast

from copilot import CopilotClient
from copilot.client import StopError
from copilot.session import CopilotSession, ReasoningEffort
from copilot.session_events import SessionEvent


class TestMediatorCopilot:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        emit_session_error: bool = False,
        stop_failures_before_success: int = 0,
        return_no_message: bool = False,
        time_out_session: bool = False,
    ) -> None:
        self.captured_models: list[str] = []
        self.captured_reasoning_efforts: list[ReasoningEffort | None] = []
        self.captured_available_tools: list[list[object]] = []
        self.captured_timeouts: list[float] = []
        self.deleted_session_ids: list[str] = []
        self.session_id: str = f"scripted-copilot-session-{secrets.token_hex(4)}"
        self.stop_count: int = 0
        self._emit_session_error: bool = emit_session_error
        self._return_no_message: bool = return_no_message
        self._time_out_session: bool = time_out_session
        self.stop_failures_before_success: int = stop_failures_before_success

    def create_client(self) -> CopilotClient:
        return cast("CopilotClient", self)

    async def create_session(self, *, model: str, reasoning_effort: ReasoningEffort | None, available_tools: list[object]) -> CopilotSession:
        self.captured_models.append(model)
        self.captured_reasoning_efforts.append(reasoning_effort)
        self.captured_available_tools.append(available_tools)
        if self._emit_session_error or self._return_no_message or self._time_out_session:
            return cast("CopilotSession", self)
        raise RuntimeError("scripted Copilot session failure")

    async def disconnect(self) -> None:
        return None

    async def send_and_wait(self, prompt: str, *, timeout: float) -> SessionEvent | None:  # noqa: ASYNC109 - mirrors the Copilot SDK signature
        self.captured_timeouts.append(timeout)
        if self._return_no_message:
            return None
        if self._time_out_session:
            raise TimeoutError(f"Timeout after {timeout}s waiting for session.idle")
        sdk_error: Exception = Exception("Session error: scripted Copilot session failure")
        raise sdk_error

    async def delete_session(self, session_id: str) -> None:
        self.deleted_session_ids.append(session_id)

    async def stop(self) -> None:
        self.stop_count += 1
        if self.stop_failures_before_success > 0:
            self.stop_failures_before_success -= 1
            raise ExceptionGroup("scripted Copilot stop failure", [StopError("scripted stop error")])
