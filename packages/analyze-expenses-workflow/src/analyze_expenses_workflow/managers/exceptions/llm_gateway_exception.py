from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)


class LlmGatewayExceptionBase(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_GATEWAY, contextual_data_by_name)


@final
class LlmRequestFailedException(LlmGatewayExceptionBase):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant request failed"


@final
class LlmGatewayClosedException(LlmGatewayExceptionBase):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant gateway was used after closing"


@final
class LlmResponseInvalidException(LlmGatewayExceptionBase):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant response violates its contract"


@final
class LlmClientCleanupFailedException(LlmGatewayExceptionBase):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant client cleanup failed"


@final
class LlmRequestRejectedException(LlmGatewayExceptionBase):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant provider rejected the request"


@final
class LlmCliUnavailableException(LlmGatewayExceptionBase):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.INFRA_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant CLI is not installed or could not be started"
