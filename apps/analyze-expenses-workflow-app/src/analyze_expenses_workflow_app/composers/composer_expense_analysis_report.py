from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Final, final

from analyze_expenses_workflow.domain_facades import (
    ExpenseAnalysisResult,
    ExpenseCategorization,
    ExpenseCategory,
    ExpenseFinding,
    ExpenseTransaction,
    ParsedExpenseLine,
)


@final
class ComposerExpenseAnalysisReport:
    _EXPENSE_CATEGORY_COLUMN: Final[str] = "Expense category"
    _MONTH_COLUMN: Final[str] = "Month"
    _SOURCE_LINES_COLUMN: Final[str] = "Source lines"
    _CLASSIFIED_SPENDING_LABEL: Final[str] = "Classified spending"
    _UNRESOLVED_OUTFLOW_LABEL: Final[str] = "Unresolved outflow"
    _OBSERVED_OUTFLOW_LABEL: Final[str] = "Observed outflow"

    @classmethod
    def compose(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        sections: list[str] = [
            "# Expense analysis",
            cls._ledger(expense_analysis_result),
            cls._monthly_categories(expense_analysis_result),
            cls._monthly_totals(expense_analysis_result),
            cls._budgets(expense_analysis_result),
            cls._deltas(expense_analysis_result),
            cls._quality(expense_analysis_result),
            cls._method(expense_analysis_result),
            cls._findings(expense_analysis_result),
        ]
        return "\n\n".join(sections) + "\n"

    @classmethod
    def _ledger(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = cls._ledger_rows(expense_analysis_result)
        return "## Transaction ledger\n\n" + cls._table(
            ("Line", "Source text", "Date", "Description", "Amount", cls._EXPENSE_CATEGORY_COLUMN, "Treatment", "Note"), rows
        )

    @classmethod
    def _ledger_rows(cls, expense_analysis_result: ExpenseAnalysisResult) -> list[tuple[str, ...]]:
        return [cls._ledger_row(expense_transaction) for expense_transaction in expense_analysis_result.transactions]

    @classmethod
    def _ledger_row(cls, expense_transaction: ExpenseTransaction) -> tuple[str, ...]:
        parsed_expense_line: ParsedExpenseLine = expense_transaction.parsed_line
        expense_categorization: ExpenseCategorization | None = expense_transaction.categorization
        return (
            str(parsed_expense_line.line_number),
            parsed_expense_line.source_text,
            parsed_expense_line.occurred_on.isoformat() if parsed_expense_line.occurred_on is not None else "—",
            parsed_expense_line.description or "—",
            cls._money(parsed_expense_line.amount) if parsed_expense_line.amount is not None else "—",
            cls._category(expense_categorization.category) if expense_categorization is not None else "—",
            expense_transaction.treatment.value.replace("_", " "),
            expense_categorization.policy_note
            if expense_categorization is not None and expense_categorization.policy_note is not None
            else ", ".join(parsed_expense_line.issues),
        )

    @classmethod
    def _monthly_categories(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (
                cls._month(monthly_category_total.month),
                cls._category(monthly_category_total.category),
                cls._money(monthly_category_total.amount),
                cls._line_numbers(monthly_category_total.source_line_numbers),
            )
            for monthly_category_total in expense_analysis_result.calculations.monthly_category_totals
        ]
        return "## Monthly spending by expense category\n\n" + cls._table(
            (cls._MONTH_COLUMN, cls._EXPENSE_CATEGORY_COLUMN, "Amount", cls._SOURCE_LINES_COLUMN), rows
        )

    @classmethod
    def _monthly_totals(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (
                cls._month(monthly_total.month),
                cls._money(monthly_total.classified_spending),
                cls._money(monthly_total.unresolved_outflow),
                cls._money(monthly_total.observed_outflow),
                cls._line_numbers(monthly_total.source_line_numbers),
            )
            for monthly_total in expense_analysis_result.calculations.monthly_totals
        ]
        return "## Monthly totals\n\n" + cls._table(
            (cls._MONTH_COLUMN, cls._CLASSIFIED_SPENDING_LABEL, cls._UNRESOLVED_OUTFLOW_LABEL, cls._OBSERVED_OUTFLOW_LABEL, cls._SOURCE_LINES_COLUMN),
            rows,
        )

    @classmethod
    def _budgets(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (
                cls._month(budget_comparison.month),
                cls._category(budget_comparison.category),
                cls._money(budget_comparison.target),
                cls._money(budget_comparison.actual),
                cls._money(budget_comparison.variance),
                cls._line_numbers(budget_comparison.source_line_numbers),
                budget_comparison.coverage_note or "—",
                cls._line_numbers(budget_comparison.coverage_source_line_numbers),
            )
            for budget_comparison in expense_analysis_result.calculations.budget_comparisons
        ]
        return "## Budget comparisons\n\n" + cls._table(
            (
                cls._MONTH_COLUMN,
                cls._EXPENSE_CATEGORY_COLUMN,
                "Target",
                "Actual",
                "Actual minus target",
                cls._SOURCE_LINES_COLUMN,
                "Coverage",
                "Coverage source lines",
            ),
            rows,
        )

    @classmethod
    def _deltas(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (
                f"{cls._month(month_category_delta.from_month)} → {cls._month(month_category_delta.to_month)}",
                cls._category(month_category_delta.category),
                cls._money(month_category_delta.previous_amount),
                cls._money(month_category_delta.current_amount),
                cls._money(month_category_delta.change),
                cls._line_numbers(month_category_delta.source_line_numbers),
            )
            for month_category_delta in expense_analysis_result.calculations.month_category_deltas
        ]
        return "## Month-to-month changes\n\n" + cls._table(
            ("Months", cls._EXPENSE_CATEGORY_COLUMN, "Previous", "Current", "Change", cls._SOURCE_LINES_COLUMN), rows
        )

    @classmethod
    def _quality(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        summary_rows: list[tuple[str, ...]] = cls._quality_summary_rows(expense_analysis_result)
        issue_rows: list[tuple[str, ...]] = cls._quality_issue_rows(expense_analysis_result)
        unresolved_note: str = cls._unparsed_note(expense_analysis_result)
        return (
            "## Quality and reconciliation\n\n"
            + unresolved_note
            + cls._table(("Check", "Result"), summary_rows)
            + "\n\n### Parsing issues\n\n"
            + cls._table(("Line", "Issue"), issue_rows)
        )

    @classmethod
    def _quality_summary_rows(cls, expense_analysis_result: ExpenseAnalysisResult) -> list[tuple[str, ...]]:
        calculation_reconciliation = expense_analysis_result.calculations.reconciliation
        return [
            (cls._SOURCE_LINES_COLUMN, str(calculation_reconciliation.source_line_count)),
            ("Included spending lines", cls._line_numbers(calculation_reconciliation.included_line_numbers)),
            ("Confirmed duplicate lines", cls._line_numbers(calculation_reconciliation.duplicate_line_numbers)),
            ("Unresolved outflow lines", cls._line_numbers(calculation_reconciliation.unresolved_line_numbers)),
            ("Unresolved credit lines", cls._line_numbers(calculation_reconciliation.unresolved_credit_line_numbers)),
            ("Refund or negative adjustment lines", cls._line_numbers(calculation_reconciliation.refund_line_numbers)),
            ("Unparsed lines", cls._line_numbers(calculation_reconciliation.unparsed_line_numbers)),
            (cls._CLASSIFIED_SPENDING_LABEL, cls._money(calculation_reconciliation.classified_spending)),
            (cls._UNRESOLVED_OUTFLOW_LABEL, cls._money(calculation_reconciliation.unresolved_outflow)),
            (cls._OBSERVED_OUTFLOW_LABEL, cls._money(calculation_reconciliation.observed_outflow)),
            ("Reconciled", "Yes" if calculation_reconciliation.is_balanced else "No"),
        ]

    @staticmethod
    def _quality_issue_rows(expense_analysis_result: ExpenseAnalysisResult) -> list[tuple[str, ...]]:
        return [
            (str(parsed_expense_line.line_number), ", ".join(parsed_expense_line.issues))
            for parsed_expense_line in expense_analysis_result.parsed_lines
            if parsed_expense_line.issues
        ]

    @classmethod
    def _unparsed_note(cls, expense_analysis_result: ExpenseAnalysisResult) -> str:
        calculation_reconciliation = expense_analysis_result.calculations.reconciliation
        return (
            f"Unable to parse source lines {cls._line_numbers(calculation_reconciliation.unparsed_line_numbers)}. "
            "Their source text remains in the ledger and is excluded from spending totals.\n\n"
            if calculation_reconciliation.unparsed_line_numbers
            else ""
        )

    @staticmethod
    def _method(expense_analysis_result: ExpenseAnalysisResult) -> str:
        return (
            "## Method and calculation formulas\n\n"
            + expense_analysis_result.method
            + "\n\n"
            + "- Classified spending = sum of signed amounts for included spending lines, grouped by month and expense category.\n"
            + "- Unresolved outflow = sum of signed amounts for unresolved outflow lines; these are not verified spending.\n"
            + "- Observed outflow = classified spending + unresolved outflow.\n"
            + "- Budget variance = actual category spending - monthly target.\n"
            + "- Month-to-month change = current category total - previous category total.\n"
            + "- Only caller-confirmed line numbers matching the preceding source line are excluded as duplicates. Refunds retain negative amounts.\n"
            + "- Uncategorized credits remain in the ledger and are excluded from outflow.\n\n"
            + "Category-policy difference from the worked example: merchant-only Target, Amazon, and Costco entries are Other. "
            + "The coded Shopping totals and target conclusions can therefore differ from merchant-based assignments in that example. "
            + "Trip-related Uber may be classified as Transportation rather than Travel.\n\n"
            + "The core calculation equations, after validation and duplicate/category decisions, are:\n\n"
            + "```python\n"
            + expense_analysis_result.calculation_code
            + "\n"
            + "```"
        )

    @staticmethod
    def _findings(expense_analysis_result: ExpenseAnalysisResult) -> str:
        numbered_findings: list[str] = []
        finding_number: int
        expense_finding: ExpenseFinding
        for finding_number, expense_finding in enumerate(expense_analysis_result.findings, start=1):
            numbered_findings.append(
                f"{finding_number}. {expense_finding.text} "
                f"(Source lines: {ComposerExpenseAnalysisReport._line_numbers(expense_finding.source_line_numbers)}.)"
            )
        return "## Three evidence-based findings\n\n" + "\n".join(numbered_findings)

    @staticmethod
    def _table(columns: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> str:
        header: str = "| " + " | ".join(columns) + " |"
        separator: str = "| " + " | ".join("---" for _ in columns) + " |"
        body: list[str] = ["| " + " | ".join(ComposerExpenseAnalysisReport._cell(cell) for cell in row) + " |" for row in rows]
        if not body:
            body = ["| No entries" + " |" * (len(columns) - 1) + " |"]
        return "\n".join((header, separator, *body))

    @staticmethod
    def _cell(cell_text: str) -> str:
        return cell_text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")

    @staticmethod
    def _money(amount: Decimal) -> str:
        return f"${amount:,.2f}" if amount >= 0 else f"-${abs(amount):,.2f}"

    @staticmethod
    def _month(expense_month: date) -> str:
        return expense_month.strftime("%B %Y")

    @staticmethod
    def _category(expense_category: ExpenseCategory) -> str:
        if expense_category == ExpenseCategory.DINING_COFFEE:
            return "Dining & Coffee"
        return str(expense_category).replace("_", " ").title()

    @staticmethod
    def _line_numbers(source_line_numbers: tuple[int, ...]) -> str:
        return ", ".join(str(source_line_number) for source_line_number in source_line_numbers) if source_line_numbers else "—"
