from types import TracebackType
from typing import Self, final

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, CodexError, Sandbox, TurnResult
from openai_codex.generated.v2_all import ReasoningEffort

from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException


@final
class CodexSubscriptionGateway:
    _SUPPORTED_EFFORTS: frozenset[str] = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})

    def __init__(self, async_codex: AsyncCodex | None = None) -> None:
        self._async_codex: AsyncCodex | None = async_codex
        self._closed: bool = False

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self._ensure_open()
        effort: ReasoningEffort | None = self._resolve_effort(reasoning_effort)

        try:
            turn_result: TurnResult = await self._request(prompt, model, effort)
        except (CodexError, OSError, TimeoutError) as exc:
            raise LlmGatewayException("Codex subscription request failed") from exc

        return self._response_text(turn_result)

    def _ensure_open(self) -> None:
        if self._closed:
            raise LlmGatewayException("Codex gateway is closed")

    @classmethod
    def _resolve_effort(cls, reasoning_effort: str | None) -> ReasoningEffort | None:
        if reasoning_effort is None:
            return None
        if reasoning_effort not in cls._SUPPORTED_EFFORTS:
            raise LlmGatewayException(f"Codex does not recognize reasoning effort {reasoning_effort!r}")
        return ReasoningEffort(reasoning_effort)

    def _client_for_request(self) -> AsyncCodex:
        self._ensure_open()
        if self._async_codex is None:
            self._async_codex = AsyncCodex()
        return self._async_codex

    async def _request(self, prompt: str, model: str, effort: ReasoningEffort | None) -> TurnResult:
        async_codex: AsyncCodex = self._client_for_request()
        async_thread: AsyncThread = await async_codex.thread_start(
            model=model,
            sandbox=Sandbox.read_only,
            approval_mode=ApprovalMode.deny_all,
            ephemeral=True,
        )
        return await async_thread.run(prompt, effort=effort)

    @staticmethod
    def _response_text(turn_result: TurnResult) -> str:
        if turn_result.status.value != "completed" or not turn_result.final_response:
            raise LlmGatewayException("Codex subscription request returned no completed response")
        return turn_result.final_response

    async def close(self) -> None:
        if not self._closed:
            if self._async_codex is not None:
                await self._async_codex.close()
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
