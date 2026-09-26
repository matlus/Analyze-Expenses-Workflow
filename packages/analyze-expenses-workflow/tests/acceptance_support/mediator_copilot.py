from types import TracebackType
from typing import ClassVar, cast

from copilot import CopilotClient
from copilot.client import StopError
from copilot.session import CopilotSession, ReasoningEffort
from copilot.session_events import SessionEvent


class TestMediatorCopilot:
    __test__: ClassVar[bool] = False

    def __init__(self, emit_session_error: bool = False, stop_failures_before_success: int = 0, return_no_message: bool = False) -> None:
        self.captured_models: list[str] = []
        self.captured_reasoning_efforts: list[ReasoningEffort | None] = []
        self.captured_available_tools: list[list[object]] = []
        self.stop_count: int = 0
        self._emit_session_error: bool = emit_session_error
        self._return_no_message: bool = return_no_message
        self.stop_failures_before_success: int = stop_failures_before_success

    def create_client(self) -> CopilotClient:
        return cast("CopilotClient", self)

    async def create_session(self, *, model: str, reasoning_effort: ReasoningEffort | None, available_tools: list[object]) -> CopilotSession:
        self.captured_models.append(model)
        self.captured_reasoning_efforts.append(reasoning_effort)
        self.captured_available_tools.append(available_tools)
        if self._emit_session_error or self._return_no_message:
            return cast("CopilotSession", self)
        raise RuntimeError("scripted Copilot session failure")

    async def __aenter__(self) -> CopilotSession:
        return cast("CopilotSession", self)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None

    async def send_and_wait(self, prompt: str) -> SessionEvent | None:
        if self._return_no_message:
            return None
        sdk_error: Exception = Exception("Session error: scripted Copilot session failure")
        raise sdk_error

    async def stop(self) -> None:
        self.stop_count += 1
        if self.stop_failures_before_success > 0:
            self.stop_failures_before_success -= 1
            raise ExceptionGroup("scripted Copilot stop failure", [StopError("scripted stop error")])
