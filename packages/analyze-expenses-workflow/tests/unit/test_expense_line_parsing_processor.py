from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from acceptance_support.asserter_parsed_expense_line import ParsedExpenseLineAsserter

from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine

YEAR_SETTING_LINE: str = "2026-06-01 | Example | $1.00"
YEAR_SETTING_PARSED_LINE: ParsedExpenseLine = ParsedExpenseLine(2, YEAR_SETTING_LINE, date(2026, 6, 1), "Example", Decimal("1.00"), ())


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
        ("Panera Bread - $27.69 (August 28)", date(2026, 8, 28), "Panera Bread", Decimal("27.69")),
    ],
)
async def test_ExpenseLineParsing_WhenALineUsesASupportedFormat_ThenReadsItsDateMerchantAndAmount(
    source_text: str, expected_date: date, expected_description: str, expected_amount: Decimal
) -> None:
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, expected_date, expected_description, expected_amount, ()),
        YEAR_SETTING_PARSED_LINE,
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text, YEAR_SETTING_LINE])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "Each supported layout yields the line's date, merchant and amount"
    )


@pytest.mark.parametrize(
    "source_text",
    [
        "Return processed - REI (7/18/26) - -$68.00",
        "July 18 | REI refund | $-68.00",
        "July 18 | REI refund | ($68.00)",
        "July 18 | REI refund | (68.00)",
        "July 18 | REI refund | ($ 68.00)",
        "July 18 | REI refund | $68.00",
    ],
)
async def test_ExpenseLineParsing_WhenALineIsARefund_ThenTheAmountIsNegativeAndTheMerchantOmitsTheRefundWording(source_text: str) -> None:
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, date(2026, 7, 18), "REI", Decimal("-68.00"), ()),
        YEAR_SETTING_PARSED_LINE,
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text, YEAR_SETTING_LINE])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "A refund is a credit, and its merchant is named without the refund wording"
    )


async def test_ExpenseLineParsing_WhenRefundWordingFollowsTheAmount_ThenTheChargeAndItsWordingAreBothKept() -> None:
    source_text: str = "8/2/26 REI $68.00 refund"
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, date(2026, 8, 2), "REI refund", Decimal("68.00"), ()),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines,
        actual_parsed_expense_lines,
        "Refund wording leaves the merchant's name only when the amount's sign records the refund",
    )


async def test_ExpenseLineParsing_WhenTheYearIsNotUnique_ThenAnUndatedYearIsNotGuessed() -> None:
    expense_lines: list[str] = ["6/1 Market $10.00", "2025-06-01 Store $2.00", "2026-06-01 Cafe $3.00"]
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, expense_lines[0], None, "Market", Decimal("10.00"), ("year_not_inferable",)),
        ParsedExpenseLine(2, expense_lines[1], date(2025, 6, 1), "Store", Decimal("2.00"), ()),
        ParsedExpenseLine(3, expense_lines[2], date(2026, 6, 1), "Cafe", Decimal("3.00"), ()),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "A yearless date is not given a year when the log states two years"
    )


async def test_ExpenseLineParsing_WhenAmountGroupingIsMalformed_ThenTheAmountIsNotPartiallyParsed() -> None:
    source_text: str = "2026-06-01 | Market | $1,23.45"
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, date(2026, 6, 1), "Market | $1,23.45", None, ("missing_amount",)),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "Malformed thousands grouping is not read as an amount"
    )


async def test_ExpenseLineParsing_WhenTheOnlyMoneyIsAnAvailableBalance_ThenItIsNotATransactionAmount() -> None:
    source_text: str = "2026-08-16 | available balance $102.00"
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, date(2026, 8, 16), "available balance", None, ("nontransaction_amount",)),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "A balance is not a transaction amount"
    )


async def test_ExpenseLineParsing_WhenARefundIsNegated_ThenTheChargeKeepsItsPositiveSign() -> None:
    source_text: str = "2026-08-16 | No refund; charge $10.00"
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, date(2026, 8, 16), "No refund; charge", Decimal("10.00"), ()),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "Negated refund wording leaves a charge positive"
    )


async def test_ExpenseLineParsing_WhenRefundWordingIsTheOnlyDescription_ThenItIsKeptAsTheDescription() -> None:
    source_text: str = "2026-08-16 | Refund | $10.00"
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, date(2026, 8, 16), "Refund", Decimal("-10.00"), ()),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "Refund wording that names no merchant stays the description"
    )


async def test_ExpenseLineParsing_WhenTheExplicitYearIsZero_ThenTheDateIsInvalidEvenWithAnotherInferredYear() -> None:
    expense_lines: list[str] = ["0000-06-01 | Market | $10.00", "2026-06-02 | Cafe | $5.00"]
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, expense_lines[0], None, "Market", Decimal("10.00"), ("invalid_date",)),
        ParsedExpenseLine(2, expense_lines[1], date(2026, 6, 2), "Cafe", Decimal("5.00"), ()),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "Year zero is invalid and is not replaced by the inferred year"
    )


@pytest.mark.parametrize("malformed_date", ["6/1/202", "6/1/20266", "6/1.2026"])
async def test_ExpenseLineParsing_WhenANumericDateHasAnInvalidYear_ThenTheDateIsNotPartiallyParsed(malformed_date: str) -> None:
    expense_lines: list[str] = [f"{malformed_date} Market $10.00", "2026-06-02 Cafe $5.00"]
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, expense_lines[0], None, f"{malformed_date} Market", Decimal("10.00"), ("missing_date",)),
        ParsedExpenseLine(2, expense_lines[1], date(2026, 6, 2), "Cafe", Decimal("5.00"), ()),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "A date token with a malformed year is not read as a date"
    )


@pytest.mark.parametrize("malformed_amount", ["123.45,67", "$1,234.56,78"])
async def test_ExpenseLineParsing_WhenAnAmountHasANumericCommaSuffix_ThenTheAmountIsNotPartiallyParsed(malformed_amount: str) -> None:
    source_text: str = f"2026-06-01 Market {malformed_amount}"
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, source_text, date(2026, 6, 1), f"Market {malformed_amount}", None, ("missing_amount",)),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse([source_text])

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "An amount followed by more digits is not read as an amount"
    )


async def test_ExpenseLineParsing_WhenDatesCrossDecemberToJanuary_ThenTheSoleExplicitYearIsNotAppliedToJanuary() -> None:
    expense_lines: list[str] = ["2025-12-31 | Grocery | $10.00", "Jan 1 | Cafe | $5.00"]
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, expense_lines[0], date(2025, 12, 31), "Grocery", Decimal("10.00"), ()),
        ParsedExpenseLine(2, expense_lines[1], None, "Cafe", Decimal("5.00"), ("year_not_inferable",)),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "A year boundary stops the explicit year from being inferred"
    )


async def test_ExpenseLineParsing_WhenTheInputHasNontransactionLines_ThenPositionsArePreservedAndFailuresFlagged() -> None:
    expense_lines: list[str] = ["expense log", "", "2026-08-31 Best Buy 183.41"]
    expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = (
        ParsedExpenseLine(1, expense_lines[0], None, "expense log", None, ("missing_date", "missing_amount")),
        ParsedExpenseLine(2, expense_lines[1], None, None, None, ("missing_date", "missing_amount", "missing_description")),
        ParsedExpenseLine(3, expense_lines[2], date(2026, 8, 31), "Best Buy", Decimal("183.41"), ()),
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    ParsedExpenseLineAsserter.assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines, actual_parsed_expense_lines, "Every input line keeps its position, and lines without a transaction say why"
    )


async def test_ExpenseLineParsing_WhenTheOriginalFixtureIsParsed_ThenOnlyTheIntroLinesAreFlaggedAndTheRefundIsACredit(
    original_expense_lines: list[str],
) -> None:
    expected_line_count: int = 201
    expected_flagged_line_numbers: tuple[int, ...] = (1, 2, 3)
    expected_refund_parsed_line: ParsedExpenseLine = ParsedExpenseLine(
        108, "Return processed - REI (7/18/26) - -$68.00", date(2026, 7, 18), "REI", Decimal("-68.00"), ()
    )

    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(original_expense_lines)

    actual_flagged_line_numbers: tuple[int, ...] = tuple(
        parsed_expense_line.line_number for parsed_expense_line in actual_parsed_expense_lines if parsed_expense_line.issues
    )
    assert (len(actual_parsed_expense_lines), actual_flagged_line_numbers, actual_parsed_expense_lines[107]) == (
        expected_line_count,
        expected_flagged_line_numbers,
        expected_refund_parsed_line,
    ), "The fixture's 198 expense lines parse without issues, only its three intro lines are flagged, and the REI return is a credit"
