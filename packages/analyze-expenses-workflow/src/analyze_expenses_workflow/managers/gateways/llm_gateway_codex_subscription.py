import asyncio
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Self, final

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, AsyncTurnHandle, CodexError, Sandbox, TurnResult
from openai_codex.generated.v2_all import ReasoningEffort
from pydantic import BaseModel, ConfigDict, ValidationError

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionContext
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmGatewayBaseException,
    LlmGatewayClosedException,
    LlmRequestFailedException,
    LlmRequestRejectedException,
    LlmRequestTimedOutException,
    LlmResponseEmptyException,
)


@final
class _CodexErrorBodyResource(BaseModel):
    model_config: ConfigDict = ConfigDict(frozen=True)

    message: str | None = None


@final
class _CodexErrorDocumentResource(BaseModel):
    """The provider's error document that the Codex SDK raises as the message of a failed turn."""

    model_config: ConfigDict = ConfigDict(frozen=True)

    status: int | None = None
    error: _CodexErrorBodyResource | None = None


@dataclass(frozen=True, slots=True)
class _CodexProviderError:
    status_code: int | None
    message: str


@final
class LlmGatewayCodexSubscription:
    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"
    # A rejected request fails the same way when sent again; a timeout or a rate limit may not.
    _CLIENT_ERROR_STATUS_CODES: Final[range] = range(400, 500)
    _RETRYABLE_CLIENT_ERROR_STATUS_CODES: Final[frozenset[int]] = frozenset({408, 429})
    _PROVIDER_ERROR_MESSAGE_LIMIT: Final[int] = 500
    # The SDK waits for a turn without a deadline; a stalled turn is interrupted, and the interrupt request is bounded too.
    _TURN_INTERRUPT_TIMEOUT_SECONDS: Final[float] = 30.0
    # A high-effort turn can take minutes; Claude Code and Copilot allow the same 900 seconds.
    DEFAULT_TURN_TIMEOUT_SECONDS: Final[float] = 900.0

    def __init__(self, async_codex: AsyncCodex, turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS) -> None:
        self._async_codex: AsyncCodex = async_codex
        self._turn_timeout_seconds: float = turn_timeout_seconds
        self._closed: bool = False

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self._ensure_open()
        resolved_reasoning_effort: ReasoningEffort | None = self._resolve_effort(reasoning_effort)

        try:
            turn_result: TurnResult = await self._request(prompt, model, resolved_reasoning_effort)
        except (CodexError, RuntimeError, OSError, TimeoutError) as exc:
            raise self._request_failure(exc, model, resolved_reasoning_effort) from exc

        return self._response_text(turn_result)

    @classmethod
    def _request_failure(cls, request_error: BaseException, model: str, reasoning_effort: ReasoningEffort | None) -> LlmGatewayBaseException:
        codex_provider_error: _CodexProviderError = cls._read_provider_error(request_error)
        contextual_data_by_name: ExceptionContext = {
            cls._OPERATION_CONTEXT_FIELD_NAME: "turn_request",
            "Model": model,
            "ReasoningEffort": reasoning_effort.value if reasoning_effort is not None else "default",
            "ProviderStatusCode": codex_provider_error.status_code if codex_provider_error.status_code is not None else "unknown",
            "ProviderError": codex_provider_error.message,
        }
        if isinstance(request_error, TimeoutError):
            return LlmRequestTimedOutException(f"Codex CLI request timed out: {codex_provider_error.message}", contextual_data_by_name)
        if cls._is_rejected_request(codex_provider_error.status_code):
            return LlmRequestRejectedException(
                f"Codex CLI rejected the request for model {model!r}: {codex_provider_error.message}", contextual_data_by_name
            )
        return LlmRequestFailedException(f"Codex CLI request failed: {codex_provider_error.message}", contextual_data_by_name)

    @classmethod
    def _read_provider_error(cls, request_error: BaseException) -> _CodexProviderError:
        """A failed turn carries the provider's JSON error document as its message; any other failure is described as raised."""
        error_text: str = str(request_error) or type(request_error).__name__
        try:
            codex_error_document_resource: _CodexErrorDocumentResource = _CodexErrorDocumentResource.model_validate_json(error_text)
        except ValidationError:
            return _CodexProviderError(None, error_text[: cls._PROVIDER_ERROR_MESSAGE_LIMIT])
        provider_message: str | None = codex_error_document_resource.error.message if codex_error_document_resource.error is not None else None
        return _CodexProviderError(codex_error_document_resource.status, (provider_message or error_text)[: cls._PROVIDER_ERROR_MESSAGE_LIMIT])

    @classmethod
    def _is_rejected_request(cls, status_code: int | None) -> bool:
        return status_code in cls._CLIENT_ERROR_STATUS_CODES and status_code not in cls._RETRYABLE_CLIENT_ERROR_STATUS_CODES

    def _ensure_open(self) -> None:
        if self._closed:
            raise LlmGatewayClosedException(
                "Codex CLI gateway is closed", {self._OPERATION_CONTEXT_FIELD_NAME: "gateway_open_state_check", "GatewayState": "closed"}
            )

    @staticmethod
    def _resolve_effort(reasoning_effort: str | None) -> ReasoningEffort | None:
        # The configuration provider has already accepted only the efforts this subscription supports.
        return ReasoningEffort(reasoning_effort) if reasoning_effort is not None else None

    async def _request(self, prompt: str, model: str, resolved_reasoning_effort: ReasoningEffort | None) -> TurnResult:
        async_thread: AsyncThread = await self._start_thread(self._async_codex, model)
        return await self._run_turn(async_thread, prompt, resolved_reasoning_effort)

    @staticmethod
    async def _start_thread(async_codex: AsyncCodex, model: str) -> AsyncThread:
        return await async_codex.thread_start(
            model=model,
            sandbox=Sandbox.read_only,
            approval_mode=ApprovalMode.deny_all,
            ephemeral=True,
        )

    async def _run_turn(self, async_thread: AsyncThread, prompt: str, resolved_reasoning_effort: ReasoningEffort | None) -> TurnResult:
        async_turn_handle: AsyncTurnHandle = await async_thread.turn(prompt, effort=resolved_reasoning_effort)
        turn_deadline: asyncio.Timeout = asyncio.timeout(self._turn_timeout_seconds)
        try:
            async with turn_deadline:
                return await async_turn_handle.run()
        except TimeoutError as turn_timeout_error:
            if not turn_deadline.expired():
                raise
            # Only the deadline's own expiry is described here; a timeout the SDK raises keeps its provider message.
            turn_deadline_error: TimeoutError = TimeoutError(f"the Codex turn did not finish within {self._turn_timeout_seconds} seconds")
            await self._interrupt_turn_keeping_deadline_error(async_turn_handle, turn_deadline_error)
            raise turn_deadline_error from turn_timeout_error

    @classmethod
    async def _interrupt_turn_keeping_deadline_error(cls, async_turn_handle: AsyncTurnHandle, turn_deadline_error: TimeoutError) -> None:
        """The missed deadline is the failure to report; an interrupt that fails after it is recorded on it as a note."""
        try:
            async with asyncio.timeout(cls._TURN_INTERRUPT_TIMEOUT_SECONDS):
                await async_turn_handle.interrupt()
        except (CodexError, RuntimeError, OSError, TimeoutError) as interrupt_error:
            interrupt_failure_message: str = (str(interrupt_error) or type(interrupt_error).__name__)[: cls._PROVIDER_ERROR_MESSAGE_LIMIT]
            turn_deadline_error.add_note(f"The Codex turn {async_turn_handle.id} could not be interrupted: {interrupt_failure_message}")

    @staticmethod
    def _response_text(turn_result: TurnResult) -> str:
        if turn_result.status.value != "completed" or not turn_result.final_response:
            raise LlmResponseEmptyException(
                "Codex CLI returned no completed response",
                {LlmGatewayCodexSubscription._OPERATION_CONTEXT_FIELD_NAME: "response_validation", "TurnStatus": turn_result.status.value},
            )
        return turn_result.final_response

    async def close(self) -> None:
        if not self._closed:
            try:
                await self._async_codex.close()
            except (CodexError, RuntimeError, OSError, TimeoutError) as close_error:
                raise LlmClientCleanupFailedException(
                    "Codex CLI client cleanup failed", {self._OPERATION_CONTEXT_FIELD_NAME: "client_close"}
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
