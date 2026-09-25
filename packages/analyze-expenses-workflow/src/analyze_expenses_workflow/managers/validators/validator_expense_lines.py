from collections.abc import Sequence

from analyze_expenses_workflow.managers.exceptions.expense_input_exception import ExpenseInputException


class ValidatorExpenseLines:
    @staticmethod
    def validate(expense_lines: Sequence[str]) -> None:
        if isinstance(expense_lines, str):
            raise ExpenseInputException("Expense lines must be a sequence of text lines; received str")
        if not expense_lines:
            raise ExpenseInputException("At least one expense line is required; received 0 lines")
        line_number: int
        expense_line: str
        for line_number, expense_line in enumerate(expense_lines, start=1):
            if not isinstance(expense_line, str):
                raise ExpenseInputException(
                    f"Expense line {line_number} must contain text; received {type(expense_line).__name__}",
                    line_number=line_number,
                )
