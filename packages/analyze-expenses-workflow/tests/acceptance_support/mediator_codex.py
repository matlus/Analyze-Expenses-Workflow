from typing import ClassVar, cast

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, AsyncTurnHandle, Sandbox, TurnResult
from openai_codex.generated.v2_all import ReasoningEffort
from openai_codex.types import TurnStatus


class TestMediatorCodex:
    __test__: ClassVar[bool] = False

    SCRIPTED_TURN_FAILURE_MESSAGE: ClassVar[str] = "scripted Codex turn failure"

    def __init__(
        self,
        close_failures_before_success: int = 0,
        return_incomplete_turn: bool = False,
        turn_failure_message: str = SCRIPTED_TURN_FAILURE_MESSAGE,
        time_out_turn: bool = False,
    ) -> None:
        self.captured_models: list[str] = []
        self.captured_prompts: list[str] = []
        self.captured_reasoning_efforts: list[ReasoningEffort | None] = []
        self.captured_sandboxes: list[Sandbox] = []
        self.captured_approval_modes: list[ApprovalMode] = []
        self.close_count: int = 0
        self.close_failures_before_success: int = close_failures_before_success
        self._return_incomplete_turn: bool = return_incomplete_turn
        self._turn_failure_message: str = turn_failure_message
        self._time_out_turn: bool = time_out_turn

    def create_client(self) -> AsyncCodex:
        return cast("AsyncCodex", self)

    async def thread_start(self, *, model: str, sandbox: Sandbox, approval_mode: ApprovalMode, ephemeral: bool) -> AsyncThread:
        if not ephemeral:
            raise AssertionError("Codex acceptance requests must use ephemeral threads")
        self.captured_models.append(model)
        self.captured_sandboxes.append(sandbox)
        self.captured_approval_modes.append(approval_mode)
        return cast("AsyncThread", self)

    async def turn(self, prompt: str, *, effort: ReasoningEffort | None) -> AsyncTurnHandle:
        self.captured_prompts.append(prompt)
        self.captured_reasoning_efforts.append(effort)
        return cast("AsyncTurnHandle", self)

    async def run(self) -> TurnResult:
        if self._return_incomplete_turn:
            return TurnResult(
                id="scripted-incomplete-turn",
                status=TurnStatus.completed,
                error=None,
                started_at=None,
                completed_at=None,
                duration_ms=None,
                final_response=None,
                items=[],
                usage=None,
            )
        if self._time_out_turn:
            raise TimeoutError(self._turn_failure_message)
        raise RuntimeError(self._turn_failure_message)

    async def close(self) -> None:
        self.close_count += 1
        if self.close_failures_before_success > 0:
            self.close_failures_before_success -= 1
            raise RuntimeError("scripted Codex close failure")
