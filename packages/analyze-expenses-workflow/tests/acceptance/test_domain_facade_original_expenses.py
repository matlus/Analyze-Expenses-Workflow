import os
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from analyze_expenses_workflow.domain_facades import (
    CalculationReconciliation,
    DomainFacade,
    ExpenseAnalysisResult,
    ExpenseCategorization,
    ExpenseCategory,
    ParsedExpenseLine,
)


@pytest.fixture
def original_expense_lines() -> list[str]:
    source_path: Path = Path(__file__).parents[1] / "fixtures" / "personal_expenses.txt"
    source_lines: list[str] = source_path.read_text(encoding="utf-8").splitlines()
    assert source_lines[2] == ""
    expense_lines: list[str] = source_lines[3:]
    assert len(expense_lines) == 198
    return expense_lines


@pytest.fixture
def expected_categories(original_expense_lines: Sequence[str]) -> list[ExpenseCategory]:
    expected_path: Path = Path(__file__).parents[1] / "fixtures" / "personal_expenses_expected_categories.tsv"
    rows: list[str] = expected_path.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "line_number\texpected_category\tsource_text"
    assert len(rows) == len(original_expense_lines) + 1

    categories: list[ExpenseCategory] = []
    line_number: int
    row: str
    expense_line: str
    for line_number, (row, expense_line) in enumerate(zip(rows[1:], original_expense_lines, strict=True), start=1):
        number_text: str
        category_text: str
        expected_source_text: str
        number_text, category_text, expected_source_text = row.split("\t", maxsplit=2)
        assert int(number_text) == line_number
        assert expected_source_text == expense_line
        categories.append(ExpenseCategory(category_text))
    return categories


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_TESTS") != "1", reason="Real Jev calls are opt-in")
async def test_analyze_expenses_categorizes_the_original_expense_log(
    original_expense_lines: list[str],
    expected_categories: Sequence[ExpenseCategory],
) -> None:
    async with DomainFacade() as facade:
        actual_expense_analysis_result: ExpenseAnalysisResult = await facade.analyze_expenses(
            original_expense_lines, confirmed_duplicate_line_numbers=(109,)
        )

    assert_original_expenses_result(actual_expense_analysis_result, original_expense_lines, expected_categories)


def assert_original_expenses_result(
    actual_expense_analysis_result: ExpenseAnalysisResult,
    expected_expense_lines: Sequence[str],
    expected_expense_categories: Sequence[ExpenseCategory],
) -> None:
    mismatches: list[str] = []
    expected_line_count: int = len(expected_expense_lines)
    if len(actual_expense_analysis_result.categorizations) != expected_line_count:
        mismatches.append(f"Expected {expected_line_count} categorizations, got {len(actual_expense_analysis_result.categorizations)}")
    if len(actual_expense_analysis_result.parsed_lines) != expected_line_count:
        mismatches.append(f"Expected {expected_line_count} parsed lines, got {len(actual_expense_analysis_result.parsed_lines)}")
    if len(actual_expense_analysis_result.transactions) != expected_line_count:
        mismatches.append(f"Expected {expected_line_count} transactions, got {len(actual_expense_analysis_result.transactions)}")
    if len(expected_expense_categories) != expected_line_count:
        mismatches.append(f"Expected {expected_line_count} category expectations, got {len(expected_expense_categories)}")
    unresolved_parse_lines: tuple[int, ...] = tuple(
        parsed_expense_line.line_number for parsed_expense_line in actual_expense_analysis_result.parsed_lines if parsed_expense_line.issues
    )
    if unresolved_parse_lines:
        mismatches.append(f"Expected no parsing issues, got issues on lines {unresolved_parse_lines}")

    actual_calculation_reconciliation: CalculationReconciliation = actual_expense_analysis_result.calculations.reconciliation
    if not actual_calculation_reconciliation.is_balanced:
        mismatches.append("Expected balanced reconciliation")
    if actual_calculation_reconciliation.source_line_count != 198:
        mismatches.append(f"Expected 198 source lines, got {actual_calculation_reconciliation.source_line_count}")
    if len(actual_calculation_reconciliation.duplicate_line_numbers) != 1:
        mismatches.append(f"Expected one duplicate line, got {actual_calculation_reconciliation.duplicate_line_numbers}")
    if len(actual_calculation_reconciliation.unresolved_line_numbers) != 8:
        mismatches.append(f"Expected eight unresolved lines, got {actual_calculation_reconciliation.unresolved_line_numbers}")
    if actual_calculation_reconciliation.unparsed_line_numbers:
        mismatches.append(f"Expected no unparsed lines, got {actual_calculation_reconciliation.unparsed_line_numbers}")
    if len(actual_expense_analysis_result.calculations.monthly_totals) != 3:
        mismatches.append(f"Expected three monthly totals, got {len(actual_expense_analysis_result.calculations.monthly_totals)}")
    if len(actual_expense_analysis_result.calculations.budget_comparisons) != 6:
        mismatches.append(f"Expected six budget comparisons, got {len(actual_expense_analysis_result.calculations.budget_comparisons)}")
    if len(actual_expense_analysis_result.findings) != 3:
        mismatches.append(f"Expected three findings, got {len(actual_expense_analysis_result.findings)}")
    if any(not expense_finding.source_line_numbers for expense_finding in actual_expense_analysis_result.findings):
        mismatches.append("Expected every finding to cite source lines")
    if not actual_expense_analysis_result.method:
        mismatches.append("Expected a calculation method description")
    if not actual_expense_analysis_result.calculation_code:
        mismatches.append("Expected calculation code")
    if (
        actual_calculation_reconciliation.classified_spending + actual_calculation_reconciliation.unresolved_outflow
        != actual_calculation_reconciliation.observed_outflow
    ):
        mismatches.append("Classified spending and unresolved outflow do not sum to observed outflow")
    actual_monthly_observed: dict[date, Decimal] = {
        monthly_total.month: monthly_total.observed_outflow for monthly_total in actual_expense_analysis_result.calculations.monthly_totals
    }
    expected_monthly_observed: dict[date, Decimal] = {
        date(2026, 6, 1): Decimal("3276.71"),
        date(2026, 7, 1): Decimal("2835.78"),
        date(2026, 8, 1): Decimal("5646.19"),
    }
    if actual_monthly_observed != expected_monthly_observed:
        mismatches.append(f"Expected monthly outflow {expected_monthly_observed}, got {actual_monthly_observed}")

    line_number: int
    actual_expense_categorization: ExpenseCategorization
    actual_parsed_expense_line: ParsedExpenseLine
    expected_source_text: str
    expected_expense_category: ExpenseCategory
    for line_number, (actual_expense_categorization, actual_parsed_expense_line, expected_source_text, expected_expense_category) in enumerate(
        zip(
            actual_expense_analysis_result.categorizations,
            actual_expense_analysis_result.parsed_lines,
            expected_expense_lines,
            expected_expense_categories,
            strict=False,
        ),
        start=1,
    ):
        if actual_expense_categorization.source_line_number != line_number:
            mismatches.append(f"Line {line_number}: categorization source line is {actual_expense_categorization.source_line_number}")
        if actual_expense_categorization.source_text != expected_source_text:
            mismatches.append(f"Line {line_number}: expected source text {expected_source_text!r}, got {actual_expense_categorization.source_text!r}")
        if actual_parsed_expense_line.description in {"Target", "Amazon", "Costco"}:
            if actual_expense_categorization.category is not ExpenseCategory.OTHER:
                mismatches.append(f"Line {line_number}: merchant-only retailer should be Other, got {actual_expense_categorization.category}")
            if actual_expense_categorization.policy_note is None:
                mismatches.append(f"Line {line_number}: merchant-only retailer has no policy note")
            continue
        if expected_source_text == "Uber, August 16 -- $24.10" and actual_expense_categorization.category is ExpenseCategory.TRANSPORTATION:
            continue
        if actual_expense_categorization.category is not expected_expense_category:
            mismatches.append(
                f"{line_number}: expected {expected_expense_category.value}, got {actual_expense_categorization.category.value} "
                f"(confidence {actual_expense_categorization.confidence:.2f}): {expected_source_text}"
            )

    assert not mismatches, f"Original expense analysis has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)
