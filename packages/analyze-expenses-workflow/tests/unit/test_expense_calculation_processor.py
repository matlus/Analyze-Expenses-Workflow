from datetime import date
from decimal import Decimal

from analyze_expenses_workflow.managers.processors.expense_calculation_processor import ExpenseCalculationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_calculation_result import BudgetTarget, ExpenseCalculationResult
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction, TransactionTreatment
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


def _transaction(
    line_number: int,
    occurred_on: date | None,
    amount: str | None,
    treatment: TransactionTreatment,
    category: ExpenseCategory | None,
    description: str,
    *,
    duplicate_of_line_number: int | None = None,
) -> ExpenseTransaction:
    source_text: str = f"{description} {amount}"
    parsed_line: ParsedExpenseLine = ParsedExpenseLine(
        line_number=line_number,
        source_text=source_text,
        occurred_on=occurred_on,
        description=description,
        amount=None if amount is None else Decimal(amount),
        issues=() if occurred_on is not None and amount is not None else ("incomplete",),
    )
    categorization: ExpenseCategorization | None = (
        None
        if category is None
        else ExpenseCategorization(
            source_line_number=line_number,
            source_text=source_text,
            category=category,
            model_category=category,
            policy_note="Merchant-only mixed retailer charge; item type is not stated" if description == "Target" else None,
            confidence=1.0,
            probabilities=(),
        )
    )
    return ExpenseTransaction(
        parsed_line=parsed_line,
        categorization=categorization,
        treatment=treatment,
        duplicate_of_line_number=duplicate_of_line_number,
    )


async def test_calculation_preserves_refunds_excludes_duplicates_and_reconciles_unresolved_outflow() -> None:
    transactions: tuple[ExpenseTransaction, ...] = (
        _transaction(1, date(2026, 6, 1), "100.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.GROCERIES, "Grocer"),
        _transaction(2, date(2026, 6, 2), "-20.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.GROCERIES, "Grocer refund"),
        _transaction(3, date(2026, 6, 3), "50.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.SHOPPING, "Clothing"),
        _transaction(4, date(2026, 6, 4), "30.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.OTHER, "Target"),
        _transaction(5, date(2026, 6, 5), "200.00", TransactionTreatment.UNCATEGORIZED_OUTFLOW, None, "Bank transfer"),
        _transaction(
            6, date(2026, 6, 1), "100.00", TransactionTreatment.EXCLUDED_DUPLICATE, ExpenseCategory.GROCERIES, "Grocer", duplicate_of_line_number=1
        ),
        _transaction(7, None, None, TransactionTreatment.UNPARSED, None, "unreadable line"),
        _transaction(8, date(2026, 7, 1), "70.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.GROCERIES, "Grocer"),
        _transaction(9, date(2026, 7, 2), "100.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.TRAVEL, "Hotel"),
    )
    targets: tuple[BudgetTarget, ...] = (
        BudgetTarget(ExpenseCategory.SHOPPING, Decimal("1200.00")),
        BudgetTarget(ExpenseCategory.DINING_COFFEE, Decimal("260.00")),
    )

    result: ExpenseCalculationResult = await ExpenseCalculationProcessor().calculate(transactions, targets)

    june_categories: dict[ExpenseCategory, Decimal] = {
        total.category: total.amount for total in result.monthly_category_totals if total.month == date(2026, 6, 1)
    }
    assert june_categories == {
        ExpenseCategory.GROCERIES: Decimal("80.00"),
        ExpenseCategory.SHOPPING: Decimal("50.00"),
        ExpenseCategory.OTHER: Decimal("30.00"),
    }
    assert result.monthly_totals[0].classified_spending == Decimal("160.00")
    assert result.monthly_totals[0].unresolved_outflow == Decimal("200.00")
    assert result.monthly_totals[0].observed_outflow == Decimal("360.00")
    assert result.monthly_totals[1].observed_outflow == Decimal("170.00")
    assert result.monthly_totals[0].source_line_numbers == (1, 2, 3, 4, 5)

    june_shopping = next(
        comparison
        for comparison in result.budget_comparisons
        if comparison.month == date(2026, 6, 1) and comparison.category == ExpenseCategory.SHOPPING
    )
    assert june_shopping.actual == Decimal("50.00")
    assert june_shopping.variance == Decimal("-1150.00")
    assert june_shopping.source_line_numbers == (3,)
    assert june_shopping.coverage_note is not None
    assert "source lines 4" in june_shopping.coverage_note
    assert june_shopping.coverage_source_line_numbers == (4,)
    july_shopping = next(
        comparison
        for comparison in result.budget_comparisons
        if comparison.month == date(2026, 7, 1) and comparison.category == ExpenseCategory.SHOPPING
    )
    assert july_shopping.actual == 0
    assert july_shopping.coverage_note is None
    assert july_shopping.coverage_source_line_numbers == ()

    assert [(delta.category, delta.change) for delta in result.month_category_deltas] == [
        (ExpenseCategory.TRAVEL, Decimal("100.00")),
        (ExpenseCategory.SHOPPING, Decimal("-50.00")),
        (ExpenseCategory.OTHER, Decimal("-30.00")),
        (ExpenseCategory.GROCERIES, Decimal("-10.00")),
    ]
    assert result.reconciliation.included_line_numbers == (1, 2, 3, 4, 8, 9)
    assert result.reconciliation.duplicate_line_numbers == (6,)
    assert result.reconciliation.unresolved_line_numbers == (5,)
    assert result.reconciliation.unparsed_line_numbers == (7,)
    assert result.reconciliation.refund_line_numbers == (2,)
    assert result.reconciliation.source_line_count == 9
    assert result.reconciliation.classified_spending == Decimal("330.00")
    assert result.reconciliation.observed_outflow == Decimal("530.00")
    assert result.reconciliation.is_balanced


async def test_unresolved_credit_is_audited_without_reducing_observed_outflow() -> None:
    transactions: tuple[ExpenseTransaction, ...] = (
        _transaction(1, date(2026, 6, 1), "100.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.GROCERIES, "Grocer"),
        _transaction(2, date(2026, 6, 2), "-20.00", TransactionTreatment.UNCATEGORIZED_CREDIT, ExpenseCategory.UNCATEGORIZED, "Unknown credit"),
    )

    result: ExpenseCalculationResult = await ExpenseCalculationProcessor().calculate(transactions, ())

    assert result.monthly_totals[0].observed_outflow == Decimal("100.00")
    assert result.reconciliation.unresolved_credit_line_numbers == (2,)
    assert result.reconciliation.unresolved_outflow == 0
    assert result.reconciliation.is_balanced


async def test_zero_net_category_is_omitted_from_month_comparison() -> None:
    transactions: tuple[ExpenseTransaction, ...] = (
        _transaction(1, date(2026, 6, 1), "40.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.SHOPPING, "Shop"),
        _transaction(2, date(2026, 6, 2), "-40.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.SHOPPING, "Shop refund"),
        _transaction(3, date(2026, 7, 1), "10.00", TransactionTreatment.INCLUDED_SPENDING, ExpenseCategory.GROCERIES, "Grocer"),
    )

    result: ExpenseCalculationResult = await ExpenseCalculationProcessor().calculate(transactions, ())

    assert len(result.month_category_deltas) == 1
    assert result.month_category_deltas[0].category == ExpenseCategory.GROCERIES
    assert result.month_category_deltas[0].change == Decimal("10.00")
