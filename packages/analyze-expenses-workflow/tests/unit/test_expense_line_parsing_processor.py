from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


@pytest.fixture
def original_expense_lines() -> list[str]:
    fixture_path: Path = Path(__file__).resolve().parents[1] / "fixtures" / "personal_expenses.txt"
    return fixture_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("source_text", "expected_date", "expected_description", "expected_amount"),
    [
        ("- June 1  Trader Joe's  $82.18", date(2026, 6, 1), "Trader Joe's", Decimal("82.18")),
        ("06-01: iCloud+ 200GB, 2.99", date(2026, 6, 1), "iCloud+ 200GB", Decimal("2.99")),
        ("6/3/26 - Local Coffee Co - $11.35", date(2026, 6, 3), "Local Coffee Co", Decimal("11.35")),
        ("Trader Joe's (Jun. 4) - $35.72", date(2026, 6, 4), "Trader Joe's", Decimal("35.72")),
        ("2026-06-09 | Home Depot | $103.93", date(2026, 6, 9), "Home Depot", Decimal("103.93")),
        ("Return processed - REI (7/18/26) - -$68.00", date(2026, 7, 18), "Return processed - REI", Decimal("-68.00")),
        ("July 18 | REI refund | $-68.00", date(2026, 7, 18), "REI refund", Decimal("-68.00")),
        ("July 18 | REI refund | ($68.00)", date(2026, 7, 18), "REI refund", Decimal("-68.00")),
        ("July 18 | REI refund | (68.00)", date(2026, 7, 18), "REI refund", Decimal("-68.00")),
        ("July 18 | REI refund | ($ 68.00)", date(2026, 7, 18), "REI refund", Decimal("-68.00")),
        ("July 18 | REI refund | $68.00", date(2026, 7, 18), "REI refund", Decimal("-68.00")),
        ("Panera Bread - $27.69 (August 28)", date(2026, 8, 28), "Panera Bread", Decimal("27.69")),
    ],
)
async def test_when_line_uses_supported_format_then_extracts_date_description_and_amount(
    source_text: str, expected_date: date, expected_description: str, expected_amount: Decimal
) -> None:
    expense_lines: list[str] = [source_text, "2026-06-01 | Example | $1.00"]

    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    assert parsed_lines[0].occurred_on == expected_date
    assert parsed_lines[0].description == expected_description
    assert parsed_lines[0].amount == expected_amount
    assert parsed_lines[0].issues == ()


async def test_when_year_is_not_unique_then_undated_year_is_not_guessed() -> None:
    expense_lines: list[str] = ["6/1 Market $10.00", "2025-06-01 Store $2.00", "2026-06-01 Cafe $3.00"]

    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    assert parsed_lines[0].occurred_on is None
    assert parsed_lines[0].issues == ("year_not_inferable",)
    assert parsed_lines[1].occurred_on == date(2025, 6, 1)
    assert parsed_lines[2].occurred_on == date(2026, 6, 1)


async def test_when_amount_grouping_is_malformed_then_it_is_not_partially_parsed() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(
        ["2026-06-01 | Market | $1,23.45"]
    )

    assert parsed_lines[0].amount is None
    assert "missing_amount" in parsed_lines[0].issues


async def test_when_only_money_is_an_available_balance_then_it_is_not_a_transaction_amount() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(
        ["2026-08-16 | available balance $102.00"]
    )

    assert parsed_lines[0].amount is None
    assert parsed_lines[0].issues == ("nontransaction_amount",)


async def test_when_refund_is_negated_then_charge_keeps_positive_sign() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(
        ["2026-08-16 | No refund; charge $10.00"]
    )

    assert parsed_lines[0].amount == Decimal("10.00")
    assert parsed_lines[0].issues == ()


async def test_when_explicit_year_is_zero_then_it_is_invalid_even_with_a_different_inferred_year() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(
        ["0000-06-01 | Market | $10.00", "2026-06-02 | Cafe | $5.00"]
    )

    assert parsed_lines[0].occurred_on is None
    assert "invalid_date" in parsed_lines[0].issues
    assert parsed_lines[1].occurred_on == date(2026, 6, 2)


@pytest.mark.parametrize("malformed_date", ["6/1/202", "6/1/20266", "6/1.2026"])
async def test_when_numeric_date_token_has_an_invalid_year_then_it_is_not_partially_parsed(malformed_date: str) -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(
        [f"{malformed_date} Market $10.00", "2026-06-02 Cafe $5.00"]
    )

    assert parsed_lines[0].occurred_on is None
    assert "missing_date" in parsed_lines[0].issues


@pytest.mark.parametrize("malformed_amount", ["123.45,67", "$1,234.56,78"])
async def test_when_amount_has_a_numeric_comma_suffix_then_it_is_not_partially_parsed(malformed_amount: str) -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(
        [f"2026-06-01 Market {malformed_amount}"]
    )

    assert parsed_lines[0].amount is None
    assert "missing_amount" in parsed_lines[0].issues


async def test_when_dates_cross_december_to_january_then_the_sole_explicit_year_is_not_applied_to_january() -> None:
    expense_lines: list[str] = ["2025-12-31 | Grocery | $10.00", "Jan 1 | Cafe | $5.00"]

    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    assert parsed_lines[0].occurred_on == date(2025, 12, 31)
    assert parsed_lines[1].occurred_on is None
    assert "year_not_inferable" in parsed_lines[1].issues


async def test_when_input_has_nontransaction_lines_then_preserves_positions_and_flags_failures() -> None:
    expense_lines: list[str] = ["expense log", "", "2026-08-31 Best Buy 183.41"]

    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    assert len(parsed_lines) == 3
    assert tuple(line.line_number for line in parsed_lines) == (1, 2, 3)
    assert parsed_lines[0].issues == ("missing_date", "missing_amount")
    assert parsed_lines[1].issues == ("missing_date", "missing_amount", "missing_description")
    assert parsed_lines[2].issues == ()


async def test_when_original_fixture_is_parsed_then_all_expenses_have_fields_and_intro_is_flagged(original_expense_lines: list[str]) -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(original_expense_lines)

    assert len(parsed_lines) == 201
    assert all(not line.issues for line in parsed_lines[3:])
    assert all(line.occurred_on is not None and line.description is not None and line.amount is not None for line in parsed_lines[3:])
    assert parsed_lines[107].amount == Decimal("-68.00")
