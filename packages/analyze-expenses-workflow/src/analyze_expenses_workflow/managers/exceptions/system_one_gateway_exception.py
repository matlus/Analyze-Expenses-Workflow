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
class JevRequestRejectedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Jev rejected the request and will reject it again unless the request changes"


@final
class JevRateLimitRetriesExhaustedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Jev kept rate limiting the request through every retry attempt"


@final
class JevServerRetryRequestedRetriesExhaustedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Jev kept failing with a server error that asked for a retry through every retry attempt"


@final
class JevChoiceResponseMalformedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Jev choice response does not have the expected shape"


@final
class JevChoiceCategoriesMismatchException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Jev returned probabilities for categories other than those asked about"


@final
class JevChoiceCategoryUnrecognizedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Jev selected a category that is not a known expense category"


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
class JevClientCleanupFailedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Jev client cleanup failed"


@final
class JevTransportCleanupFailedException(SystemOneGatewayBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Jev HTTP transport cleanup failed"
