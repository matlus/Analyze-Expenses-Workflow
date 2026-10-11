from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from analyze_expenses_workflow.managers.exceptions.expense_input_exception import ExpenseInputInvalidException


@dataclass(frozen=True, slots=True)
class _ExpenseInputProblem:
    description: str
    line_number: int | None = None


class ValidatorExpenseLines:
    # A confirmed duplicate repeats the line before it, so the first line can never be one.
    _FIRST_POSSIBLE_DUPLICATE_LINE_NUMBER: ClassVar[int] = 2

    @staticmethod
    def validate(expense_lines: Sequence[str], confirmed_duplicate_line_numbers: Sequence[int] = ()) -> None:
        problems: tuple[_ExpenseInputProblem, ...] = (
            *ValidatorExpenseLines._expense_line_problems(expense_lines),
            *ValidatorExpenseLines._confirmed_duplicate_problems(expense_lines, confirmed_duplicate_line_numbers),
        )
        if problems:
            raise ExpenseInputInvalidException(
                tuple(expense_input_problem.description for expense_input_problem in problems),
                tuple(
                    sorted({expense_input_problem.line_number for expense_input_problem in problems if expense_input_problem.line_number is not None})
                ),
            )

    @staticmethod
    def _expense_line_problems(expense_lines: Sequence[str]) -> tuple[_ExpenseInputProblem, ...]:
        if isinstance(expense_lines, str):
            return (_ExpenseInputProblem("Expense lines must be a sequence of text lines; received str"),)
        if not expense_lines:
            return (_ExpenseInputProblem("At least one expense line is required; received 0 lines"),)
        return tuple(
            _ExpenseInputProblem(f"Expense line {line_number} must contain text; received {type(expense_line).__name__}", line_number)
            for line_number, expense_line in enumerate(expense_lines, start=1)
            if not isinstance(expense_line, str)
        )

    @staticmethod
    def _confirmed_duplicate_problems(
        expense_lines: Sequence[str], confirmed_duplicate_line_numbers: Sequence[int]
    ) -> tuple[_ExpenseInputProblem, ...]:
        use_count_by_confirmed_line_number: Counter[int] = Counter(confirmed_duplicate_line_numbers)
        repeated_line_problems: tuple[_ExpenseInputProblem, ...] = tuple(
            _ExpenseInputProblem(
                f"Confirmed duplicate line {confirmed_line_number} is listed {use_count} times; list each line once", confirmed_line_number
            )
            for confirmed_line_number, use_count in use_count_by_confirmed_line_number.items()
            if use_count > 1
        )
        # Without a usable sequence of lines there is no line count to check the confirmed lines against.
        if isinstance(expense_lines, str) or not expense_lines:
            return repeated_line_problems
        expense_line_count: int = len(expense_lines)
        out_of_range_line_problems: tuple[_ExpenseInputProblem, ...] = tuple(
            _ExpenseInputProblem(
                f"Confirmed duplicate line {confirmed_line_number} must repeat an earlier line, so it must be from 2 to {expense_line_count}",
                confirmed_line_number,
            )
            for confirmed_line_number in use_count_by_confirmed_line_number
            if not ValidatorExpenseLines._FIRST_POSSIBLE_DUPLICATE_LINE_NUMBER <= confirmed_line_number <= expense_line_count
        )
        return (*repeated_line_problems, *out_of_range_line_problems)
