from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)


@final
class LlmGatewayException(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_GATEWAY, contextual_data_by_name)

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant request failed"
