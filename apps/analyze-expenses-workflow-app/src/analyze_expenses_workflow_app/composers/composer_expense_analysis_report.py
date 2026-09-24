from datetime import date
from decimal import Decimal
from typing import final

from analyze_expenses_workflow import ExpenseAnalysisResult, ExpenseCategory


@final
class ComposerExpenseAnalysisReport:
    @classmethod
    def compose(cls, analysis: ExpenseAnalysisResult) -> str:
        sections: list[str] = [
            "# Expense analysis",
            cls._ledger(analysis),
            cls._monthly_categories(analysis),
            cls._monthly_totals(analysis),
            cls._budgets(analysis),
            cls._deltas(analysis),
            cls._quality(analysis),
            cls._method(analysis),
            cls._findings(analysis),
        ]
        return "\n\n".join(sections) + "\n"

    @classmethod
    def _ledger(cls, analysis: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = []
        for transaction in analysis.transactions:
            parsed = transaction.parsed_line
            decision = transaction.categorization
            rows.append(
                (
                    str(parsed.line_number),
                    parsed.source_text,
                    parsed.occurred_on.isoformat() if parsed.occurred_on is not None else "—",
                    parsed.description or "—",
                    cls._money(parsed.amount) if parsed.amount is not None else "—",
                    cls._category(decision.category) if decision is not None else "—",
                    transaction.treatment.value.replace("_", " "),
                    decision.policy_note if decision is not None and decision.policy_note is not None else ", ".join(parsed.issues),
                )
            )
        return "## Transaction ledger\n\n" + cls._table(
            ("Line", "Source text", "Date", "Description", "Amount", "Expense category", "Treatment", "Note"), rows
        )

    @classmethod
    def _monthly_categories(cls, analysis: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (cls._month(total.month), cls._category(total.category), cls._money(total.amount), cls._line_numbers(total.source_line_numbers))
            for total in analysis.calculations.monthly_category_totals
        ]
        return "## Monthly spending by expense category\n\n" + cls._table(("Month", "Expense category", "Amount", "Source lines"), rows)

    @classmethod
    def _monthly_totals(cls, analysis: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (
                cls._month(total.month),
                cls._money(total.classified_spending),
                cls._money(total.unresolved_outflow),
                cls._money(total.observed_outflow),
                cls._line_numbers(total.source_line_numbers),
            )
            for total in analysis.calculations.monthly_totals
        ]
        return "## Monthly totals\n\n" + cls._table(
            ("Month", "Classified spending", "Unresolved outflow", "Observed outflow", "Source lines"), rows
        )

    @classmethod
    def _budgets(cls, analysis: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (
                cls._month(comparison.month),
                cls._category(comparison.category),
                cls._money(comparison.target),
                cls._money(comparison.actual),
                cls._money(comparison.variance),
                cls._line_numbers(comparison.source_line_numbers),
                comparison.coverage_note or "—",
                cls._line_numbers(comparison.coverage_source_line_numbers),
            )
            for comparison in analysis.calculations.budget_comparisons
        ]
        return "## Budget comparisons\n\n" + cls._table(
            ("Month", "Expense category", "Target", "Actual", "Actual minus target", "Source lines", "Coverage", "Coverage source lines"), rows
        )

    @classmethod
    def _deltas(cls, analysis: ExpenseAnalysisResult) -> str:
        rows: list[tuple[str, ...]] = [
            (
                f"{cls._month(delta.from_month)} → {cls._month(delta.to_month)}",
                cls._category(delta.category),
                cls._money(delta.previous_amount),
                cls._money(delta.current_amount),
                cls._money(delta.change),
                cls._line_numbers(delta.source_line_numbers),
            )
            for delta in analysis.calculations.month_category_deltas
        ]
        return "## Month-to-month changes\n\n" + cls._table(
            ("Months", "Expense category", "Previous", "Current", "Change", "Source lines"), rows
        )

    @classmethod
    def _quality(cls, analysis: ExpenseAnalysisResult) -> str:
        checks = analysis.calculations.reconciliation
        summary_rows: list[tuple[str, ...]] = [
            ("Source lines", str(checks.source_line_count)),
            ("Included spending lines", cls._line_numbers(checks.included_line_numbers)),
            ("Confirmed duplicate lines", cls._line_numbers(checks.duplicate_line_numbers)),
            ("Unresolved outflow lines", cls._line_numbers(checks.unresolved_line_numbers)),
            ("Unresolved credit lines", cls._line_numbers(checks.unresolved_credit_line_numbers)),
            ("Refund or negative adjustment lines", cls._line_numbers(checks.refund_line_numbers)),
            ("Unparsed lines", cls._line_numbers(checks.unparsed_line_numbers)),
            ("Classified spending", cls._money(checks.classified_spending)),
            ("Unresolved outflow", cls._money(checks.unresolved_outflow)),
            ("Observed outflow", cls._money(checks.observed_outflow)),
            ("Reconciled", "Yes" if checks.is_balanced else "No"),
        ]
        issue_rows: list[tuple[str, ...]] = [
            (str(line.line_number), ", ".join(line.issues)) for line in analysis.parsed_lines if line.issues
        ]
        unresolved_note: str = (
            f"Unable to parse source lines {cls._line_numbers(checks.unparsed_line_numbers)}. "
            "Their source text remains in the ledger and is excluded from spending totals.\n\n"
            if checks.unparsed_line_numbers
            else ""
        )
        return (
            "## Quality and reconciliation\n\n"
            + unresolved_note
            + cls._table(("Check", "Result"), summary_rows)
            + "\n\n### Parsing issues\n\n"
            + cls._table(("Line", "Issue"), issue_rows)
        )

    @staticmethod
    def _method(analysis: ExpenseAnalysisResult) -> str:
        return (
            "## Method and calculation formulas\n\n"
            + analysis.method
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
            + analysis.calculation_code
            + "\n"
            + "```"
        )

    @staticmethod
    def _findings(analysis: ExpenseAnalysisResult) -> str:
        numbered_findings: list[str] = [
            f"{index}. {finding.text} (Source lines: {ComposerExpenseAnalysisReport._line_numbers(finding.source_line_numbers)}.)"
            for index, finding in enumerate(analysis.findings, start=1)
        ]
        return "## Three evidence-based findings\n\n" + "\n".join(numbered_findings)

    @staticmethod
    def _table(columns: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
        header: str = "| " + " | ".join(columns) + " |"
        separator: str = "| " + " | ".join("---" for _ in columns) + " |"
        body: list[str] = ["| " + " | ".join(ComposerExpenseAnalysisReport._cell(cell) for cell in row) + " |" for row in rows]
        if not body:
            body = ["| No entries" + " |" * (len(columns) - 1) + " |"]
        return "\n".join((header, separator, *body))

    @staticmethod
    def _cell(value: str) -> str:
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")

    @staticmethod
    def _money(amount: Decimal) -> str:
        return f"${amount:,.2f}" if amount >= 0 else f"-${abs(amount):,.2f}"

    @staticmethod
    def _month(value: date) -> str:
        return value.strftime("%B %Y")

    @staticmethod
    def _category(value: ExpenseCategory) -> str:
        if value == ExpenseCategory.DINING_COFFEE:
            return "Dining & Coffee"
        return str(value).replace("_", " ").title()

    @staticmethod
    def _line_numbers(values: tuple[int, ...]) -> str:
        return ", ".join(str(value) for value in values) if values else "—"
