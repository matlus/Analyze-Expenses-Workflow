from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalBaseException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)


class LlmGatewayBaseException(AnalyzeExpensesTechnicalBaseException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_GATEWAY, contextual_data_by_name)


@final
class LlmRequestFailedException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant request failed"


@final
class LlmGatewayClosedException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant gateway was used after closing"


@final
class LlmResponseEmptyException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant returned no response text"


@final
class LlmResponseUnreadableException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant response could not be read"


@final
class LlmClientCleanupFailedException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant client cleanup failed"


@final
class LlmSessionDeleteFailedException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant session could not be deleted from the user's history"


@final
class LlmRequestRejectedException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant provider rejected the request"


@final
class LlmCliNotFoundException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.INFRA_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant CLI is not installed"


@final
class LlmCliStartFailedException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.INFRA_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant CLI is installed but could not be started"


@final
class LlmRequestTimedOutException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant request did not finish within its time limit"


@final
class LlmCliExitedException(LlmGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant CLI exited with an error and no result"
