from types import TracebackType
from typing import Final, Self, final

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, CodexError, Sandbox, TurnResult
from openai_codex.generated.v2_all import ReasoningEffort

from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmGatewayClosedException,
    LlmRequestFailedException,
    LlmResponseInvalidException,
    LlmUnsupportedReasoningEffortException,
)


@final
class LlmGatewayCodexSubscription:
    _SUPPORTED_EFFORTS: frozenset[str] = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"

    def __init__(self, async_codex: AsyncCodex) -> None:
        self._async_codex: AsyncCodex = async_codex
        self._closed: bool = False

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self._ensure_open()
        resolved_reasoning_effort: ReasoningEffort | None = self._resolve_effort(reasoning_effort)

        try:
            turn_result: TurnResult = await self._request(prompt, model, resolved_reasoning_effort)
        except (CodexError, RuntimeError, OSError, TimeoutError) as exc:
            raise self._request_failure(model, resolved_reasoning_effort) from exc

        return self._response_text(turn_result)

    @classmethod
    def _request_failure(cls, model: str, reasoning_effort: ReasoningEffort | None) -> LlmRequestFailedException:
        return LlmRequestFailedException(
            "Codex subscription request failed",
            {
                cls._OPERATION_CONTEXT_FIELD_NAME: "turn_request",
                "Model": model,
                "ReasoningEffort": reasoning_effort.value if reasoning_effort is not None else "default",
            },
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise LlmGatewayClosedException(
                "Codex gateway is closed", {self._OPERATION_CONTEXT_FIELD_NAME: "gateway_open_state_check", "GatewayState": "closed"}
            )

    @classmethod
    def _resolve_effort(cls, reasoning_effort: str | None) -> ReasoningEffort | None:
        if reasoning_effort is None:
            return None
        if reasoning_effort not in cls._SUPPORTED_EFFORTS:
            supported_efforts: str = ", ".join(sorted(cls._SUPPORTED_EFFORTS))
            raise LlmUnsupportedReasoningEffortException(
                f"Codex does not recognize reasoning effort {reasoning_effort!r}; supported values: {supported_efforts}",
                {
                    cls._OPERATION_CONTEXT_FIELD_NAME: "reasoning_effort_validation",
                    "RejectedReasoningEffort": reasoning_effort,
                    "AllowedReasoningEfforts": supported_efforts,
                },
            )
        return ReasoningEffort(reasoning_effort)

    def _client_for_request(self) -> AsyncCodex:
        self._ensure_open()
        return self._async_codex

    async def _request(self, prompt: str, model: str, resolved_reasoning_effort: ReasoningEffort | None) -> TurnResult:
        async_codex: AsyncCodex = self._client_for_request()
        async_thread: AsyncThread = await self._start_thread(async_codex, model)
        return await self._run_turn(async_thread, prompt, resolved_reasoning_effort)

    @staticmethod
    async def _start_thread(async_codex: AsyncCodex, model: str) -> AsyncThread:
        return await async_codex.thread_start(
            model=model,
            sandbox=Sandbox.read_only,
            approval_mode=ApprovalMode.deny_all,
            ephemeral=True,
        )

    @staticmethod
    async def _run_turn(async_thread: AsyncThread, prompt: str, resolved_reasoning_effort: ReasoningEffort | None) -> TurnResult:
        return await async_thread.run(prompt, effort=resolved_reasoning_effort)

    @staticmethod
    def _response_text(turn_result: TurnResult) -> str:
        if turn_result.status.value != "completed" or not turn_result.final_response:
            raise LlmResponseInvalidException(
                "Codex subscription request returned no completed response",
                {LlmGatewayCodexSubscription._OPERATION_CONTEXT_FIELD_NAME: "response_validation", "TurnStatus": turn_result.status.value},
            )
        return turn_result.final_response

    async def close(self) -> None:
        if not self._closed:
            try:
                await self._async_codex.close()
            except (CodexError, RuntimeError, OSError, TimeoutError) as close_error:
                raise LlmClientCleanupFailedException(
                    "Codex subscription cleanup failed", {self._OPERATION_CONTEXT_FIELD_NAME: "client_close"}
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
