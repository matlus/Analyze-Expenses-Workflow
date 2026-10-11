from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesBusinessBaseException,
    ExceptionAction,
    ExceptionContext,
    ExpenseLogEvent,
)


@final
class ExpenseInputInvalidException(AnalyzeExpensesBusinessBaseException):
    def __init__(self, problems: tuple[str, ...], line_numbers: tuple[int, ...]) -> None:
        message: str = f"Expense input has {len(problems)} problem(s): " + "; ".join(problems)
        contextual_data_by_name: ExceptionContext = {"ProblemCount": len(problems)}
        if line_numbers:
            contextual_data_by_name["LineNumbers"] = ", ".join(str(line_number) for line_number in line_numbers)
        super().__init__(message, ExpenseLogEvent.INPUT_VALIDATION, contextual_data_by_name)
        self.problems: tuple[str, ...] = problems
        self.line_numbers: tuple[int, ...] = line_numbers

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.USER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Expense input failed validation"
