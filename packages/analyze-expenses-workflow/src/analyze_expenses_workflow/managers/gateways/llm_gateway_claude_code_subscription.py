from types import TracebackType
from typing import Final, Self, final

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionContext
from analyze_expenses_workflow.managers.exceptions.claude_code_cli_exception import (
    ClaudeCodeCliException,
    ClaudeCodeCliResultInvalidException,
    ClaudeCodeCliStartFailedException,
)
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmCliUnavailableException,
    LlmGatewayExceptionBase,
    LlmRequestFailedException,
    LlmResponseInvalidException,
)
from analyze_expenses_workflow.managers.gateways.clients.claude_code_cli_client import ClaudeCodeCliClient


@final
class LlmGatewayClaudeCodeSubscription:
    """Each request runs in its own short-lived Claude Code process, so the gateway holds no open resource; the
    configuration provider owns which reasoning efforts this subscription accepts."""

    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"
    _MODEL_CONTEXT_FIELD_NAME: Final[str] = "Model"
    _REASONING_EFFORT_CONTEXT_FIELD_NAME: Final[str] = "ReasoningEffort"

    def __init__(self, claude_code_cli_client: ClaudeCodeCliClient) -> None:
        self._claude_code_cli_client: ClaudeCodeCliClient = claude_code_cli_client

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        try:
            result_text: str | None = await self._claude_code_cli_client.run_prompt(prompt, model, reasoning_effort)
        except ClaudeCodeCliException as claude_code_cli_exception:
            raise self._request_failure(claude_code_cli_exception, model, reasoning_effort) from claude_code_cli_exception
        return self._response_text(result_text, model, reasoning_effort)

    @classmethod
    def _request_failure(cls, claude_code_cli_exception: ClaudeCodeCliException, model: str, reasoning_effort: str | None) -> LlmGatewayExceptionBase:
        """Keeps the action the CLI failure calls for: a missing or unstartable CLI needs installing, unreadable output
        needs a developer, and every other failure of one request may succeed when retried."""
        contextual_data_by_name: ExceptionContext = cls._request_context("cli_request", model, reasoning_effort)
        failure_description: str = claude_code_cli_exception.message
        if isinstance(claude_code_cli_exception, ClaudeCodeCliStartFailedException):
            return LlmCliUnavailableException(f"Claude Code is unavailable: {failure_description}", contextual_data_by_name)
        if isinstance(claude_code_cli_exception, ClaudeCodeCliResultInvalidException):
            return LlmResponseInvalidException(f"Claude Code returned an unreadable result: {failure_description}", contextual_data_by_name)
        return LlmRequestFailedException(f"Claude Code subscription request failed: {failure_description}", contextual_data_by_name)

    @classmethod
    def _response_text(cls, result_text: str | None, model: str, reasoning_effort: str | None) -> str:
        if result_text is None or not result_text.strip():
            raise LlmResponseInvalidException(
                "Claude Code subscription request returned no response text",
                cls._request_context("response_validation", model, reasoning_effort),
            )
        return result_text

    @classmethod
    def _request_context(cls, operation: str, model: str, reasoning_effort: str | None) -> ExceptionContext:
        return {
            cls._OPERATION_CONTEXT_FIELD_NAME: operation,
            cls._MODEL_CONTEXT_FIELD_NAME: model,
            cls._REASONING_EFFORT_CONTEXT_FIELD_NAME: reasoning_effort if reasoning_effort is not None else "default",
        }

    async def close(self) -> None:
        # Nothing to release: every Claude Code process ends with the request that started it.
        return

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
