from datetime import date
from decimal import Decimal

import pytest

from analyze_expenses_workflow.managers.processors.expense_reconciliation_processor import ExpenseReconciliationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction, TransactionTreatment
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


def _parsed_line(
    line_number: int,
    description: str | None,
    amount: str | None,
    occurred_on: date | None = date(2026, 6, 1),
    source_text: str | None = None,
) -> ParsedExpenseLine:
    return ParsedExpenseLine(
        line_number=line_number,
        source_text=source_text or f"source line {line_number}",
        occurred_on=occurred_on,
        description=description,
        amount=Decimal(amount) if amount is not None else None,
        issues=(),
    )


def _categorization(parsed_line: ParsedExpenseLine, category: ExpenseCategory) -> ExpenseCategorization:
    return ExpenseCategorization(
        source_line_number=parsed_line.line_number,
        source_text=parsed_line.source_text,
        category=category,
        model_category=category,
        policy_note=None,
        confidence=1.0,
        probabilities=(),
    )


async def test_only_confirmed_adjacent_verbatim_repeated_source_lines_are_excluded() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = (
        _parsed_line(1, "Trader Joe's", "35.72", source_text="Jun 1 - Trader Joe's - $35.72"),
        _parsed_line(2, "Trader Joe's", "35.72", source_text="Jun 1 - Trader Joe's - $35.72"),
        _parsed_line(3, "Trader Joe's", "35.72", source_text="Jun 1 | Trader Joe's | $35.72"),
        _parsed_line(4, "Trader Joe's", "35.72", source_text="Jun 1 - Trader Joe's - $35.72"),
        _parsed_line(5, "Trader Joe's", "-35.72", source_text="Jun 1 - Trader Joe's - -$35.72"),
        _parsed_line(6, "Trader Joe's", "35.72", date(2026, 6, 2), source_text="Jun 2 - Trader Joe's - $35.72"),
    )
    categorizations: tuple[ExpenseCategorization | None, ...] = tuple(
        _categorization(parsed_line, ExpenseCategory.GROCERIES) for parsed_line in parsed_lines
    )

    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(parsed_lines, categorizations, (2,))

    assert tuple(transaction.treatment for transaction in transactions) == (
        TransactionTreatment.INCLUDED_SPENDING,
        TransactionTreatment.EXCLUDED_DUPLICATE,
        TransactionTreatment.INCLUDED_SPENDING,
        TransactionTreatment.INCLUDED_SPENDING,
        TransactionTreatment.INCLUDED_SPENDING,
        TransactionTreatment.INCLUDED_SPENDING,
    )
    assert tuple(transaction.duplicate_of_line_number for transaction in transactions) == (None, 1, None, None, None, None)
    assert transactions[4].parsed_line.amount == Decimal("-35.72")


async def test_when_expense_is_uncategorized_or_unparsed_then_it_remains_in_ledger_without_verified_spending_treatment() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = (
        _parsed_line(1, "ATM withdrawal", "80.00"),
        _parsed_line(2, None, "20.00"),
        _parsed_line(3, "Unknown", None),
        _parsed_line(4, "Unidentified credit", "-20.00"),
    )
    categorizations: tuple[ExpenseCategorization | None, ...] = (
        _categorization(parsed_lines[0], ExpenseCategory.UNCATEGORIZED),
        None,
        None,
        _categorization(parsed_lines[3], ExpenseCategory.UNCATEGORIZED),
    )

    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(parsed_lines, categorizations)

    assert len(transactions) == len(parsed_lines)
    assert tuple(transaction.treatment for transaction in transactions) == (
        TransactionTreatment.UNCATEGORIZED_OUTFLOW,
        TransactionTreatment.UNPARSED,
        TransactionTreatment.UNPARSED,
        TransactionTreatment.UNCATEGORIZED_CREDIT,
    )


async def test_when_uncategorized_transaction_repeats_then_later_occurrence_is_excluded_as_duplicate() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = (
        _parsed_line(1, "Bank transfer", "200.00", source_text="Jun 1 - Bank transfer - $200.00"),
        _parsed_line(2, "Bank transfer", "200.00", source_text="Jun 1 - Bank transfer - $200.00"),
    )
    categorizations: tuple[ExpenseCategorization | None, ...] = tuple(
        _categorization(parsed_line, ExpenseCategory.UNCATEGORIZED) for parsed_line in parsed_lines
    )

    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(parsed_lines, categorizations, (2,))

    assert transactions[0].treatment == TransactionTreatment.UNCATEGORIZED_OUTFLOW
    assert transactions[1].treatment == TransactionTreatment.EXCLUDED_DUPLICATE
    assert transactions[1].duplicate_of_line_number == 1


async def test_adjacent_identical_charges_remain_included_without_duplicate_confirmation() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = (
        _parsed_line(1, "Market", "20.00", source_text="June 1 Market $20.00"),
        _parsed_line(2, "Market", "20.00", source_text="June 1 Market $20.00"),
    )
    categorizations: tuple[ExpenseCategorization | None, ...] = tuple(
        _categorization(line, ExpenseCategory.GROCERIES) for line in parsed_lines
    )

    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(parsed_lines, categorizations)

    assert tuple(item.treatment for item in transactions) == (
        TransactionTreatment.INCLUDED_SPENDING,
        TransactionTreatment.INCLUDED_SPENDING,
    )


async def test_duplicate_confirmation_must_match_an_earlier_identical_line() -> None:
    parsed_lines: tuple[ParsedExpenseLine, ...] = (
        _parsed_line(1, "Market", "20.00"),
        _parsed_line(2, "Cafe", "20.00"),
    )
    categorizations: tuple[ExpenseCategorization | None, ...] = tuple(
        _categorization(line, ExpenseCategory.GROCERIES) for line in parsed_lines
    )

    with pytest.raises(ValueError, match="must match"):
        await ExpenseReconciliationProcessor().reconcile(parsed_lines, categorizations, (2,))


async def test_when_categorization_slots_do_not_align_with_source_lines_then_reconciliation_rejects_them() -> None:
    parsed_line: ParsedExpenseLine = _parsed_line(1, "Market", "10.00")
    wrong_line: ParsedExpenseLine = _parsed_line(2, "Market", "10.00")

    with pytest.raises(ValueError, match="corresponding categorization slot"):
        await ExpenseReconciliationProcessor().reconcile((parsed_line,), ())

    with pytest.raises(ValueError, match="does not match expense source line 1"):
        await ExpenseReconciliationProcessor().reconcile((parsed_line,), (_categorization(wrong_line, ExpenseCategory.GROCERIES),))

    with pytest.raises(ValueError, match="has no categorization"):
        await ExpenseReconciliationProcessor().reconcile((parsed_line,), (None,))
