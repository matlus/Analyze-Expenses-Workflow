from analyze_expenses_workflow.managers.exceptions.expense_input_exception import ExpenseInputException


class ValidatorExpenseLines:
    @staticmethod
    def validate(expense_lines: list[str]) -> None:
        if not expense_lines:
            raise ExpenseInputException("At least one expense line is required")
        for line_number, line in enumerate(expense_lines, start=1):
            if not isinstance(line, str):
                raise ExpenseInputException(f"Expense line {line_number} must contain text", line_number=line_number)
