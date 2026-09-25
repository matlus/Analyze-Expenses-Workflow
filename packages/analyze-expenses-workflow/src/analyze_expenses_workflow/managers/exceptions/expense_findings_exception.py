from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)


@final
class ExpenseFindingsException(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.FINDINGS_SELECTION, contextual_data_by_name)

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Expense findings could not be grounded"
