from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)


class ClaudeCodeCliException(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_GATEWAY, contextual_data_by_name)


@final
class ClaudeCodeCliStartFailedException(ClaudeCodeCliException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.INFRA_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Claude Code CLI could not be started"


@final
class ClaudeCodeCliTimeoutException(ClaudeCodeCliException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Claude Code CLI did not finish within its time limit"


@final
class ClaudeCodeCliExitedException(ClaudeCodeCliException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Claude Code CLI exited with an error and no result document"


@final
class ClaudeCodeCliResultInvalidException(ClaudeCodeCliException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Claude Code CLI wrote output that is not a result document"


@final
class ClaudeCodeCliRequestFailedException(ClaudeCodeCliException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Claude Code reported a failed request"
