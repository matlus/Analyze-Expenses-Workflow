from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesBusinessException,
    ExceptionAction,
    ExceptionContext,
    ExpenseLogEvent,
)


@final
class ExpenseInputException(AnalyzeExpensesBusinessException):
    def __init__(self, message: str, line_number: int | None = None) -> None:
        contextual_data_by_name: ExceptionContext = {"LineNumber": line_number} if line_number is not None else {}
        super().__init__(message, ExpenseLogEvent.INPUT_VALIDATION, contextual_data_by_name)
        self.line_number: int | None = line_number

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.USER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Expense input failed validation"
