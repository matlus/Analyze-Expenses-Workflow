from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory

if TYPE_CHECKING:
    from analyze_expenses_workflow.models.expense_calculation_result import ExpenseCalculationResult
    from analyze_expenses_workflow.models.expense_finding import ExpenseFinding
    from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction
    from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


@dataclass(frozen=True, slots=True)
class CategoryProbability:
    category: ExpenseCategory
    probability: float


@dataclass(frozen=True, slots=True)
class ExpenseCategorization:
    source_line_number: int
    source_text: str
    category: ExpenseCategory
    model_category: ExpenseCategory
    policy_note: str | None
    confidence: float
    probabilities: tuple[CategoryProbability, ...]


@dataclass(frozen=True, slots=True)
class ExpenseAnalysisResult:
    categorizations: tuple[ExpenseCategorization, ...]
    parsed_lines: tuple[ParsedExpenseLine, ...]
    transactions: tuple[ExpenseTransaction, ...]
    calculations: ExpenseCalculationResult
    findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]
    method: str
    calculation_code: str = (
        "classified_spending = sum((total.amount for total in month_categories), Decimal(0))\n"
        "unresolved_outflow = sum((transaction.parsed_line.amount for transaction in unresolved_transactions), Decimal(0))\n"
        "observed_outflow = classified_spending + unresolved_outflow\n"
        "variance = actual - monthly_target\n"
        "change = current_amount - previous_amount"
    )
