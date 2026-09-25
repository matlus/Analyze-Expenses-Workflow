from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory


@dataclass(frozen=True, slots=True)
class BudgetTarget:
    category: ExpenseCategory
    monthly_amount: Decimal


@dataclass(frozen=True, slots=True)
class MonthlyCategoryTotal:
    month: date
    category: ExpenseCategory
    amount: Decimal
    source_line_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class MonthlyTotal:
    month: date
    classified_spending: Decimal
    unresolved_outflow: Decimal
    observed_outflow: Decimal
    source_line_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BudgetComparison:
    month: date
    category: ExpenseCategory
    target: Decimal
    actual: Decimal
    variance: Decimal
    source_line_numbers: tuple[int, ...]
    coverage_note: str | None
    coverage_source_line_numbers: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class MonthCategoryDelta:
    from_month: date
    to_month: date
    category: ExpenseCategory
    previous_amount: Decimal
    current_amount: Decimal
    change: Decimal
    source_line_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CalculationReconciliation:
    source_line_count: int
    included_line_numbers: tuple[int, ...]
    duplicate_line_numbers: tuple[int, ...]
    unresolved_line_numbers: tuple[int, ...]
    unparsed_line_numbers: tuple[int, ...]
    classified_spending: Decimal
    unresolved_outflow: Decimal
    observed_outflow: Decimal
    is_balanced: bool
    unresolved_credit_line_numbers: tuple[int, ...] = ()
    refund_line_numbers: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpenseCalculationResult:
    monthly_category_totals: tuple[MonthlyCategoryTotal, ...]
    monthly_totals: tuple[MonthlyTotal, ...]
    budget_comparisons: tuple[BudgetComparison, ...]
    month_category_deltas: tuple[MonthCategoryDelta, ...]
    reconciliation: CalculationReconciliation
