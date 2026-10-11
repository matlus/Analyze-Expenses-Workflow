import secrets
from typing import cast

import pytest
from acceptance_support.asserter_expense_input import ExpectedExpenseInputInvalid, assert_expense_input_invalid
from acceptance_support.mediator_jev import TestMediatorJev
from acceptance_support.service_locator_testing import create_domain_facade

from analyze_expenses_workflow.domain_facades import ExpenseInputInvalidException

EXPENSE_LINE_COUNT: int = 3


def create_expense_lines() -> list[str]:
    return [f"6/{day} Merchant {secrets.token_hex(4)} $1{day}.00" for day in range(1, EXPENSE_LINE_COUNT + 1)]


async def test_analyze_expenses_WhenExpenseLinesAreOneString_ThenRejectsInputBeforeJevRequest() -> None:
    invalid_expense_lines: str = secrets.token_hex(8)
    expected_expense_input_invalid: ExpectedExpenseInputInvalid = ExpectedExpenseInputInvalid.create(
        message_phrases=("received str",), problem_count=1
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev()

    async with create_domain_facade(test_mediator_jev) as facade:
        with pytest.raises(ExpenseInputInvalidException) as raised_expense_input_invalid_exception:
            await facade.analyze_expenses(cast("list[str]", invalid_expense_lines))

    actual_expense_input_invalid_exception: ExpenseInputInvalidException = raised_expense_input_invalid_exception.value
    assert_expense_input_invalid(actual_expense_input_invalid_exception, test_mediator_jev.captured_jev_requests, expected_expense_input_invalid)


async def test_analyze_expenses_WhenAConfirmedDuplicateLineIsListedTwice_ThenRejectsInputBeforeJevRequest() -> None:
    expense_lines: list[str] = create_expense_lines()
    repeated_confirmed_line_number: int = 2 + secrets.randbelow(EXPENSE_LINE_COUNT - 1)
    confirmed_duplicate_line_numbers: tuple[int, ...] = (repeated_confirmed_line_number, repeated_confirmed_line_number)
    expected_expense_input_invalid: ExpectedExpenseInputInvalid = ExpectedExpenseInputInvalid.create(
        message_phrases=(f"Confirmed duplicate line {repeated_confirmed_line_number}", "is listed 2 times", "list each line once"),
        problem_count=1,
        line_numbers=(repeated_confirmed_line_number,),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev()

    async with create_domain_facade(test_mediator_jev) as facade:
        with pytest.raises(ExpenseInputInvalidException) as raised_expense_input_invalid_exception:
            await facade.analyze_expenses(expense_lines, confirmed_duplicate_line_numbers=confirmed_duplicate_line_numbers)

    actual_expense_input_invalid_exception: ExpenseInputInvalidException = raised_expense_input_invalid_exception.value
    assert_expense_input_invalid(actual_expense_input_invalid_exception, test_mediator_jev.captured_jev_requests, expected_expense_input_invalid)


@pytest.mark.parametrize("out_of_range_line_number", [1, EXPENSE_LINE_COUNT + 1])
async def test_analyze_expenses_WhenAConfirmedDuplicateLineDoesNotRepeatAnEarlierLine_ThenRejectsInputBeforeJevRequest(
    out_of_range_line_number: int,
) -> None:
    expense_lines: list[str] = create_expense_lines()
    expected_expense_input_invalid: ExpectedExpenseInputInvalid = ExpectedExpenseInputInvalid.create(
        message_phrases=(
            f"Confirmed duplicate line {out_of_range_line_number}",
            f"must repeat an earlier line, so it must be from 2 to {EXPENSE_LINE_COUNT}",
        ),
        problem_count=1,
        line_numbers=(out_of_range_line_number,),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev()

    async with create_domain_facade(test_mediator_jev) as facade:
        with pytest.raises(ExpenseInputInvalidException) as raised_expense_input_invalid_exception:
            await facade.analyze_expenses(expense_lines, confirmed_duplicate_line_numbers=(out_of_range_line_number,))

    actual_expense_input_invalid_exception: ExpenseInputInvalidException = raised_expense_input_invalid_exception.value
    assert_expense_input_invalid(actual_expense_input_invalid_exception, test_mediator_jev.captured_jev_requests, expected_expense_input_invalid)


async def test_analyze_expenses_WhenInputHasSeveralProblems_ThenReportsEveryProblemInOneExceptionBeforeJevRequest() -> None:
    non_text_line_value: int = secrets.randbelow(10_000)
    expense_lines: list[str] = [
        f"6/1 Merchant {secrets.token_hex(4)} $11.00",
        cast("str", non_text_line_value),
        f"6/3 Merchant {secrets.token_hex(4)} $13.00",
    ]
    out_of_range_line_number: int = 4 + secrets.randbelow(10)
    confirmed_duplicate_line_numbers: tuple[int, ...] = (3, 3, out_of_range_line_number)
    expected_problems: tuple[str, ...] = (
        "Expense line 2 must contain text; received int",
        "Confirmed duplicate line 3 is listed 2 times; list each line once",
        f"Confirmed duplicate line {out_of_range_line_number} must repeat an earlier line, so it must be from 2 to 3",
    )
    expected_expense_input_invalid: ExpectedExpenseInputInvalid = ExpectedExpenseInputInvalid.create(
        message_phrases=expected_problems,
        problem_count=len(expected_problems),
        line_numbers=(2, 3, out_of_range_line_number),
        problems=expected_problems,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev()

    async with create_domain_facade(test_mediator_jev) as facade:
        with pytest.raises(ExpenseInputInvalidException) as raised_expense_input_invalid_exception:
            await facade.analyze_expenses(expense_lines, confirmed_duplicate_line_numbers=confirmed_duplicate_line_numbers)

    actual_expense_input_invalid_exception: ExpenseInputInvalidException = raised_expense_input_invalid_exception.value
    assert_expense_input_invalid(actual_expense_input_invalid_exception, test_mediator_jev.captured_jev_requests, expected_expense_input_invalid)
