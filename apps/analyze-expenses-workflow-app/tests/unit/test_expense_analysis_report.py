import asyncio
import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

import analyze_expenses_workflow_app.main as app_main
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseAnalysisResult, ExpenseCategorization
from analyze_expenses_workflow.models.expense_calculation_result import (
    BudgetComparison,
    CalculationReconciliation,
    ExpenseCalculationResult,
    MonthlyCategoryTotal,
    MonthlyTotal,
)
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_finding import ExpenseFinding
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction, TransactionTreatment
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine
from analyze_expenses_workflow_app.composers.composer_expense_analysis_report import ComposerExpenseAnalysisReport


def _analysis_result() -> ExpenseAnalysisResult:
    parsed_line: ParsedExpenseLine = ParsedExpenseLine(
        line_number=1,
        source_text="6/1/26 | Trader Joe's | $82.18",
        occurred_on=date(2026, 6, 1),
        description="Trader Joe's",
        amount=Decimal("82.18"),
        issues=(),
    )
    categorization: ExpenseCategorization = ExpenseCategorization(
        source_line_number=1,
        source_text=parsed_line.source_text,
        category=ExpenseCategory.GROCERIES,
        model_category=ExpenseCategory.GROCERIES,
        policy_note=None,
        confidence=0.91,
        probabilities=(),
    )
    transaction: ExpenseTransaction = ExpenseTransaction(
        parsed_line=parsed_line,
        categorization=categorization,
        treatment=TransactionTreatment.INCLUDED_SPENDING,
        duplicate_of_line_number=None,
    )
    calculations: ExpenseCalculationResult = ExpenseCalculationResult(
        monthly_category_totals=(MonthlyCategoryTotal(date(2026, 6, 1), ExpenseCategory.GROCERIES, Decimal("82.18"), (1,)),),
        monthly_totals=(MonthlyTotal(date(2026, 6, 1), Decimal("82.18"), Decimal(0), Decimal("82.18"), (1,)),),
        budget_comparisons=(
            BudgetComparison(date(2026, 6, 1), ExpenseCategory.GROCERIES, Decimal(500), Decimal("82.18"), Decimal("-417.82"), (1,), None),
        ),
        month_category_deltas=(),
        reconciliation=CalculationReconciliation(1, (1,), (), (), (), Decimal("82.18"), Decimal(0), Decimal("82.18"), True),
    )
    return ExpenseAnalysisResult(
        categorizations=(categorization,),
        parsed_lines=(parsed_line,),
        transactions=(transaction,),
        calculations=calculations,
        findings=(
            ExpenseFinding("first", "First finding cites line 1.", (1,)),
            ExpenseFinding("second", "Second finding cites June.", (1,)),
            ExpenseFinding("third", "Third finding cites the budget.", (1,)),
        ),
        method="Each source line was parsed and reviewed before totals were calculated.",
    )


def test_original_expense_file_supplies_all_198_transaction_lines() -> None:
    fixture_path: Path = (
        Path(__file__).resolve().parents[4] / "packages" / "analyze-expenses-workflow" / "tests" / "fixtures" / "personal_expenses.txt"
    )
    expense_lines: list[str] = app_main._expense_lines_from_text(fixture_path.read_text(encoding="utf-8"))
    assert len(expense_lines) == 198
    assert expense_lines[0].startswith("- June 1")
    assert expense_lines[-1].startswith("8/31 | Riverside")


@pytest.mark.asyncio
async def test_console_app_passes_lines_to_facade_and_renders_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path: Path = tmp_path / "expenses.txt"
    output_path: Path = tmp_path / "report.md"
    await asyncio.to_thread(input_path.write_text, "6/1/26 | Trader Joe's | $82.18\n", encoding="utf-8")
    observed_lines: list[str] = []

    class FakeDomainFacade:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
        ) -> None:
            return None

        async def analyze_expenses(
            self, expense_lines: list[str], *, confirmed_duplicate_line_numbers: tuple[int, ...] = ()
        ) -> ExpenseAnalysisResult:
            observed_lines.extend(expense_lines)
            assert confirmed_duplicate_line_numbers == ()
            return _analysis_result()

    monkeypatch.setattr(app_main, "DomainFacade", FakeDomainFacade)
    report: str = await app_main.run(input_path, output_path)

    assert observed_lines == ["6/1/26 | Trader Joe's | $82.18"]
    assert await asyncio.to_thread(output_path.read_text, encoding="utf-8") == report
    headings: tuple[str, ...] = (
        "## Transaction ledger",
        "## Monthly spending by expense category",
        "## Monthly totals",
        "## Budget comparisons",
        "## Month-to-month changes",
        "## Quality and reconciliation",
        "## Method and calculation formulas",
        "## Three evidence-based findings",
    )
    assert [report.index(heading) for heading in headings] == sorted(report.index(heading) for heading in headings)
    assert "| June 2026 | $82.18 | $0.00 | $82.18 | 1 |" in report
    assert "| June 2026 | Groceries | $500.00 | $82.18 | -$417.82 | 1 | — | — |" in report
    assert "Observed outflow = classified spending + unresolved outflow" in report
    assert report.count("First finding") == report.count("Second finding") == report.count("Third finding") == 1
    assert "First finding cites line 1. (Source lines: 1.)" in report


@pytest.mark.asyncio
async def test_console_app_rejects_output_overwriting_its_input(tmp_path: Path) -> None:
    input_path: Path = tmp_path / "expenses.txt"
    source_text: str = "6/1/26 | Trader Joe's | $82.18\n"
    await asyncio.to_thread(input_path.write_text, source_text, encoding="utf-8")

    with pytest.raises(ValueError, match="Output path must differ"):
        await app_main.run(input_path, input_path)

    assert await asyncio.to_thread(input_path.read_text, encoding="utf-8") == source_text


@pytest.mark.asyncio
async def test_console_app_rejects_hard_link_output_alias(tmp_path: Path) -> None:
    input_path: Path = tmp_path / "expenses.txt"
    output_path: Path = tmp_path / "linked-report.md"
    source_text: str = "6/1/26 | Trader Joe's | $82.18\n"
    await asyncio.to_thread(input_path.write_text, source_text, encoding="utf-8")
    await asyncio.to_thread(os.link, input_path, output_path)

    with pytest.raises(ValueError, match="Output path must differ"):
        await app_main.run(input_path, output_path)

    assert await asyncio.to_thread(input_path.read_text, encoding="utf-8") == source_text


def test_markdown_table_escapes_backslash_before_pipe() -> None:
    assert ComposerExpenseAnalysisReport._cell(r"merchant\|memo") == r"merchant\\\|memo"


def test_report_shows_refund_audit_and_shopping_finding_sources() -> None:
    analysis: ExpenseAnalysisResult = _analysis_result()
    comparison: BudgetComparison = replace(
        analysis.calculations.budget_comparisons[0],
        category=ExpenseCategory.SHOPPING,
        coverage_note="Mixed-retailer charges are recorded in Other.",
        coverage_source_line_numbers=(2, 3),
    )
    reconciliation: CalculationReconciliation = replace(
        analysis.calculations.reconciliation,
        refund_line_numbers=(4,),
        unresolved_credit_line_numbers=(5,),
    )
    calculations: ExpenseCalculationResult = replace(
        analysis.calculations,
        budget_comparisons=(comparison,),
        reconciliation=reconciliation,
    )
    findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = (
        analysis.findings[0],
        analysis.findings[1],
        ExpenseFinding("shopping-coverage", "Shopping coverage is limited.", (1, 2, 3)),
    )

    report: str = ComposerExpenseAnalysisReport.compose(replace(analysis, calculations=calculations, findings=findings))

    assert "| Refund or negative adjustment lines | 4 |" in report
    assert "| Unresolved credit lines | 5 |" in report
    assert "| June 2026 | Shopping | $500.00 | $82.18 | -$417.82 | 1 | Mixed-retailer charges are recorded in Other. | 2, 3 |" in report
    assert "Shopping coverage is limited. (Source lines: 1, 2, 3.)" in report


def test_report_calls_out_unparsed_source_line_without_stopping_analysis() -> None:
    analysis: ExpenseAnalysisResult = _analysis_result()
    unparsed_line: ParsedExpenseLine = ParsedExpenseLine(2, "unreadable entry", None, None, None, ("missing_date", "missing_amount"))
    transaction: ExpenseTransaction = ExpenseTransaction(unparsed_line, None, TransactionTreatment.UNPARSED, None)
    reconciliation: CalculationReconciliation = replace(
        analysis.calculations.reconciliation, source_line_count=2, unparsed_line_numbers=(2,)
    )
    calculations: ExpenseCalculationResult = replace(analysis.calculations, reconciliation=reconciliation)
    report: str = ComposerExpenseAnalysisReport.compose(
        replace(
            analysis,
            parsed_lines=(*analysis.parsed_lines, unparsed_line),
            transactions=(*analysis.transactions, transaction),
            calculations=calculations,
        )
    )

    assert "Unable to parse source lines 2." in report
    assert "| 2 | unreadable entry | — | — | — | — | unparsed | missing_date, missing_amount |" in report
    assert "| 2 | missing_date, missing_amount |" in report
    assert "| June 2026 | $82.18 | $0.00 | $82.18 | 1 |" in report


def test_unknown_preamble_is_preserved_for_validation() -> None:
    source_text: str = "Expense list\n\n6/1/26 | Trader Joe's | $82.18\n"

    assert app_main._expense_lines_from_text(source_text) == ["Expense list", "6/1/26 | Trader Joe's | $82.18"]
