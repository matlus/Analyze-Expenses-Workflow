from collections import Counter
from collections.abc import Sequence
from typing import ClassVar

from analyze_expenses_workflow.managers.exceptions.expense_input_exception import ExpenseInputException


class ValidatorExpenseLines:
    # A confirmed duplicate repeats the line before it, so the first line can never be one.
    _FIRST_POSSIBLE_DUPLICATE_LINE_NUMBER: ClassVar[int] = 2

    @staticmethod
    def validate(expense_lines: Sequence[str], confirmed_duplicate_line_numbers: Sequence[int] = ()) -> None:
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
        ValidatorExpenseLines._validate_confirmed_duplicates(confirmed_duplicate_line_numbers, len(expense_lines))

    @staticmethod
    def _validate_confirmed_duplicates(confirmed_duplicate_line_numbers: Sequence[int], expense_line_count: int) -> None:
        confirmed_line_number: int
        for confirmed_line_number, use_count in Counter(confirmed_duplicate_line_numbers).items():
            if use_count > 1:
                raise ExpenseInputException(
                    f"Confirmed duplicate line {confirmed_line_number} is listed {use_count} times; list each line once",
                    line_number=confirmed_line_number,
                )
        for confirmed_line_number in confirmed_duplicate_line_numbers:
            if not ValidatorExpenseLines._FIRST_POSSIBLE_DUPLICATE_LINE_NUMBER <= confirmed_line_number <= expense_line_count:
                raise ExpenseInputException(
                    f"Confirmed duplicate line {confirmed_line_number} must repeat an earlier line, so it must be from 2 to {expense_line_count}",
                    line_number=confirmed_line_number,
                )
