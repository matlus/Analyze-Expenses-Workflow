from decimal import Decimal

from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction, TransactionTreatment
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class ExpenseReconciliationProcessor:
    async def reconcile(
        self,
        parsed_lines: tuple[ParsedExpenseLine, ...],
        categorizations: tuple[ExpenseCategorization | None, ...],
        confirmed_duplicate_line_numbers: tuple[int, ...] = (),
    ) -> tuple[ExpenseTransaction, ...]:
        self._validate_alignment(parsed_lines, categorizations)
        confirmations: frozenset[int] = frozenset(confirmed_duplicate_line_numbers)
        if len(confirmations) != len(confirmed_duplicate_line_numbers):
            raise ValueError("Confirmed duplicate line numbers must be unique")
        return self._build_transactions(parsed_lines, categorizations, confirmations)

    @staticmethod
    def _validate_alignment(
        parsed_lines: tuple[ParsedExpenseLine, ...],
        categorizations: tuple[ExpenseCategorization | None, ...],
    ) -> None:
        if len(parsed_lines) != len(categorizations):
            raise ValueError("Each parsed expense line must have a corresponding categorization slot")

        for parsed_line, categorization in zip(parsed_lines, categorizations, strict=True):
            if categorization is not None and (
                categorization.source_line_number != parsed_line.line_number
                or categorization.source_text != parsed_line.source_text
            ):
                raise ValueError(f"Categorization does not match expense source line {parsed_line.line_number}")

    def _build_transactions(
        self,
        parsed_lines: tuple[ParsedExpenseLine, ...],
        categorizations: tuple[ExpenseCategorization | None, ...],
        confirmed_duplicate_line_numbers: frozenset[int],
    ) -> tuple[ExpenseTransaction, ...]:
        transactions: list[ExpenseTransaction] = []

        for parsed_line, categorization in zip(parsed_lines, categorizations, strict=True):
            previous_transaction: ExpenseTransaction | None = transactions[-1] if transactions else None
            transaction: ExpenseTransaction = self._reconcile_line(
                parsed_line, categorization, previous_transaction, confirmed_duplicate_line_numbers
            )
            transactions.append(transaction)

        if confirmed_duplicate_line_numbers - {item.parsed_line.line_number for item in transactions if item.duplicate_of_line_number is not None}:
            raise ValueError("A confirmed duplicate must match an earlier adjacent source line")

        return tuple(transactions)

    def _reconcile_line(
        self,
        parsed_line: ParsedExpenseLine,
        categorization: ExpenseCategorization | None,
        previous_transaction: ExpenseTransaction | None,
        confirmed_duplicate_line_numbers: frozenset[int],
    ) -> ExpenseTransaction:
        if parsed_line.occurred_on is None or parsed_line.description is None or parsed_line.amount is None:
            return ExpenseTransaction(parsed_line, categorization, TransactionTreatment.UNPARSED, None)

        if categorization is None:
            raise ValueError(f"Complete expense source line {parsed_line.line_number} has no categorization")

        duplicate_of_line_number: int | None = (
            self._duplicate_origin(parsed_line, previous_transaction)
            if parsed_line.line_number in confirmed_duplicate_line_numbers
            else None
        )
        if duplicate_of_line_number is not None:
            return ExpenseTransaction(parsed_line, categorization, TransactionTreatment.EXCLUDED_DUPLICATE, duplicate_of_line_number)

        treatment: TransactionTreatment = self._treatment_for(parsed_line.amount, categorization)
        return ExpenseTransaction(parsed_line, categorization, treatment, None)

    @staticmethod
    def _duplicate_origin(parsed_line: ParsedExpenseLine, previous_transaction: ExpenseTransaction | None) -> int | None:
        if previous_transaction is None or previous_transaction.treatment == TransactionTreatment.UNPARSED:
            return None
        previous_line: ParsedExpenseLine = previous_transaction.parsed_line
        if parsed_line.line_number != previous_line.line_number + 1 or parsed_line.source_text != previous_line.source_text:
            return None
        if (
            parsed_line.occurred_on != previous_line.occurred_on
            or parsed_line.description != previous_line.description
            or parsed_line.amount != previous_line.amount
        ):
            return None
        return previous_transaction.duplicate_of_line_number or previous_line.line_number

    @staticmethod
    def _treatment_for(amount: Decimal, categorization: ExpenseCategorization) -> TransactionTreatment:
        if categorization.category != ExpenseCategory.UNCATEGORIZED:
            return TransactionTreatment.INCLUDED_SPENDING
        return TransactionTreatment.UNCATEGORIZED_CREDIT if amount < 0 else TransactionTreatment.UNCATEGORIZED_OUTFLOW
