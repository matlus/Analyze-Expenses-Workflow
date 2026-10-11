from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalBaseException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)


class SystemOneGatewayBaseException(AnalyzeExpensesTechnicalBaseException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.JEV_GATEWAY, contextual_data_by_name)


@final
class JevRequestFailedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Jev gateway request failed"


@final
class JevResponseInvalidException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Jev choice response violates its contract"


@final
class JevGatewayClosedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Jev gateway was used after closing"


@final
class JevResourceCleanupFailedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Jev gateway resource cleanup failed"
