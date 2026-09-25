import os
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from analyze_expenses_workflow import DomainFacade, ExpenseAnalysisResult, ExpenseCategory
from analyze_expenses_workflow.models.expense_category_catalog import STANDARD_EXPENSE_CATEGORY_CATALOG


@pytest.fixture
def original_expense_lines() -> list[str]:
    source_path: Path = Path(__file__).parents[1] / "fixtures" / "personal_expenses.txt"
    source_lines: list[str] = source_path.read_text(encoding="utf-8").splitlines()
    assert source_lines[2] == ""
    expense_lines: list[str] = source_lines[3:]
    assert len(expense_lines) == 198
    return expense_lines


@pytest.fixture
def expected_categories(original_expense_lines: list[str]) -> list[ExpenseCategory]:
    expected_path: Path = Path(__file__).parents[1] / "fixtures" / "personal_expenses_expected_categories.tsv"
    rows: list[str] = expected_path.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "line_number\texpected_category\tsource_text"
    assert len(rows) == len(original_expense_lines) + 1

    categories: list[ExpenseCategory] = []
    for line_number, (row, expense_line) in enumerate(zip(rows[1:], original_expense_lines, strict=True), start=1):
        number_text, category_text, expected_source_text = row.split("\t", maxsplit=2)
        assert int(number_text) == line_number
        assert expected_source_text == expense_line
        categories.append(ExpenseCategory(category_text))
    return categories


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_TESTS") != "1", reason="Real Jev calls are opt-in")
async def test_analyze_expenses_categorizes_the_original_expense_log(
    original_expense_lines: list[str],
    expected_categories: list[ExpenseCategory],
    record_property: Callable[[str, object], None],
) -> None:
    async with DomainFacade() as facade:
        result: ExpenseAnalysisResult = await facade.analyze_expenses(original_expense_lines, confirmed_duplicate_line_numbers=(109,))

    assert len(result.categorizations) == len(result.parsed_lines) == len(result.transactions) == len(original_expense_lines)
    assert all(not parsed_line.issues for parsed_line in result.parsed_lines)
    reconciliation = result.calculations.reconciliation
    assert reconciliation.is_balanced
    assert reconciliation.source_line_count == 198
    assert len(reconciliation.duplicate_line_numbers) == 1
    assert len(reconciliation.unresolved_line_numbers) == 8
    assert not reconciliation.unparsed_line_numbers
    assert len(result.calculations.monthly_totals) == 3
    assert len(result.calculations.budget_comparisons) == 6
    assert len(result.findings) == 3
    assert all(finding.source_line_numbers for finding in result.findings)
    assert result.method
    assert result.calculation_code
    assert reconciliation.classified_spending + reconciliation.unresolved_outflow == reconciliation.observed_outflow
    monthly_observed: dict[date, Decimal] = {item.month: item.observed_outflow for item in result.calculations.monthly_totals}
    assert monthly_observed == {
        date(2026, 6, 1): Decimal("3276.71"),
        date(2026, 7, 1): Decimal("2835.78"),
        date(2026, 8, 1): Decimal("5646.19"),
    }
    mismatches: list[str] = []
    for line_number, (categorization, parsed_line, source_text, expected_category) in enumerate(
        zip(result.categorizations, result.parsed_lines, original_expense_lines, expected_categories, strict=True), start=1
    ):
        assert categorization.source_line_number == line_number
        assert categorization.source_text == source_text
        if parsed_line.description in STANDARD_EXPENSE_CATEGORY_CATALOG.mixed_retailer_merchants:
            assert categorization.category is ExpenseCategory.OTHER
            assert categorization.policy_note is not None
            continue
        if source_text == "Uber, August 16 -- $24.10" and categorization.category is ExpenseCategory.TRANSPORTATION:
            continue
        if categorization.category is not expected_category:
            mismatches.append(
                f"{line_number}: expected {expected_category.value}, got {categorization.category.value} "
                f"(confidence {categorization.confidence:.2f}): {source_text}"
            )

    record_property("category_benchmark_mismatch_count", len(mismatches))
    record_property("category_benchmark_mismatches", "\n".join(mismatches))
