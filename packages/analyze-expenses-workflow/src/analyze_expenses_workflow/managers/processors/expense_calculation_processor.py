from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise

from analyze_expenses_workflow.models.expense_calculation_result import (
    BudgetComparison,
    BudgetTarget,
    CalculationReconciliation,
    ExpenseCalculationResult,
    MonthCategoryDelta,
    MonthlyCategoryTotal,
    MonthlyTotal,
)
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction, TransactionTreatment


class ExpenseCalculationProcessor:
    async def calculate(self, transactions: tuple[ExpenseTransaction, ...], budget_targets: tuple[BudgetTarget, ...]) -> ExpenseCalculationResult:
        included_transactions: tuple[ExpenseTransaction, ...] = self._transactions_with_treatment(
            transactions, TransactionTreatment.INCLUDED_SPENDING
        )
        unresolved_transactions: tuple[ExpenseTransaction, ...] = self._transactions_with_treatment(
            transactions, TransactionTreatment.UNCATEGORIZED_OUTFLOW
        )
        monthly_category_totals: tuple[MonthlyCategoryTotal, ...] = self._monthly_category_totals(included_transactions)
        monthly_totals: tuple[MonthlyTotal, ...] = self._monthly_totals(monthly_category_totals, unresolved_transactions)
        return ExpenseCalculationResult(
            monthly_category_totals=monthly_category_totals,
            monthly_totals=monthly_totals,
            budget_comparisons=self._budget_comparisons(monthly_category_totals, monthly_totals, budget_targets, included_transactions),
            month_category_deltas=self._month_category_deltas(monthly_category_totals, monthly_totals),
            reconciliation=self._reconciliation(transactions, monthly_category_totals, monthly_totals),
        )

    @staticmethod
    def _transactions_with_treatment(transactions: tuple[ExpenseTransaction, ...], treatment: TransactionTreatment) -> tuple[ExpenseTransaction, ...]:
        return tuple(transaction for transaction in transactions if transaction.treatment == treatment)

    @classmethod
    def _monthly_category_totals(cls, transactions: tuple[ExpenseTransaction, ...]) -> tuple[MonthlyCategoryTotal, ...]:
        grouped_transactions: Mapping[tuple[date, ExpenseCategory], Sequence[ExpenseTransaction]] = cls._group_by_month_and_category(transactions)
        return cls._build_monthly_category_totals(grouped_transactions)

    @classmethod
    def _group_by_month_and_category(
        cls, transactions: tuple[ExpenseTransaction, ...]
    ) -> Mapping[tuple[date, ExpenseCategory], Sequence[ExpenseTransaction]]:
        grouped_transactions: defaultdict[tuple[date, ExpenseCategory], list[ExpenseTransaction]] = defaultdict(list)
        for transaction in transactions:
            occurred_on: date = cls._dated_amount(transaction)[0]
            category: ExpenseCategory = cls._spending_category(transaction)
            grouped_transactions[(cls._month(occurred_on), category)].append(transaction)
        return grouped_transactions

    @classmethod
    def _build_monthly_category_totals(
        cls, grouped_transactions: Mapping[tuple[date, ExpenseCategory], Sequence[ExpenseTransaction]]
    ) -> tuple[MonthlyCategoryTotal, ...]:
        monthly_category_totals: list[MonthlyCategoryTotal] = []
        month_and_category: tuple[date, ExpenseCategory]
        category_transactions: Sequence[ExpenseTransaction]
        for month_and_category, category_transactions in sorted(
            grouped_transactions.items(),
            key=lambda grouped_transaction_entry: (grouped_transaction_entry[0][0], grouped_transaction_entry[0][1].value),
        ):
            monthly_category_totals.append(
                MonthlyCategoryTotal(
                    month=month_and_category[0],
                    category=month_and_category[1],
                    amount=sum((cls._dated_amount(transaction)[1] for transaction in category_transactions), Decimal(0)),
                    source_line_numbers=tuple(sorted(transaction.parsed_line.line_number for transaction in category_transactions)),
                )
            )
        return tuple(monthly_category_totals)

    @classmethod
    def _monthly_totals(
        cls, monthly_category_totals: tuple[MonthlyCategoryTotal, ...], unresolved_transactions: tuple[ExpenseTransaction, ...]
    ) -> tuple[MonthlyTotal, ...]:
        unresolved_by_month: Mapping[date, Sequence[ExpenseTransaction]] = cls._group_unresolved_by_month(unresolved_transactions)
        category_months: tuple[date, ...] = tuple(monthly_category_total.month for monthly_category_total in monthly_category_totals)
        months: tuple[date, ...] = cls._months_to_total(category_months, unresolved_by_month)
        return tuple(cls._monthly_total(month, monthly_category_totals, unresolved_by_month.get(month, [])) for month in months)

    @classmethod
    def _group_unresolved_by_month(cls, transactions: tuple[ExpenseTransaction, ...]) -> Mapping[date, Sequence[ExpenseTransaction]]:
        unresolved_by_month: defaultdict[date, list[ExpenseTransaction]] = defaultdict(list)
        for transaction in transactions:
            occurred_on: date = cls._dated_amount(transaction)[0]
            unresolved_by_month[cls._month(occurred_on)].append(transaction)
        return unresolved_by_month

    @staticmethod
    def _months_to_total(category_months: Sequence[date], unresolved_by_month: Mapping[date, Sequence[ExpenseTransaction]]) -> tuple[date, ...]:
        months: set[date] = set(category_months) | set(unresolved_by_month)
        return tuple(sorted(months))

    @classmethod
    def _monthly_total(
        cls, month: date, category_totals: tuple[MonthlyCategoryTotal, ...], unresolved_transactions: Sequence[ExpenseTransaction]
    ) -> MonthlyTotal:
        month_categories: tuple[MonthlyCategoryTotal, ...] = tuple(total for total in category_totals if total.month == month)
        classified_spending: Decimal = sum((total.amount for total in month_categories), Decimal(0))
        unresolved_outflow: Decimal = sum((cls._dated_amount(transaction)[1] for transaction in unresolved_transactions), Decimal(0))
        classified_line_numbers: tuple[int, ...] = tuple(line_number for total in month_categories for line_number in total.source_line_numbers)
        unresolved_line_numbers: tuple[int, ...] = tuple(transaction.parsed_line.line_number for transaction in unresolved_transactions)
        source_line_numbers: tuple[int, ...] = tuple(sorted((*classified_line_numbers, *unresolved_line_numbers)))
        return MonthlyTotal(
            month=month,
            classified_spending=classified_spending,
            unresolved_outflow=unresolved_outflow,
            observed_outflow=classified_spending + unresolved_outflow,
            source_line_numbers=source_line_numbers,
        )

    @classmethod
    def _budget_comparisons(
        cls,
        category_totals: tuple[MonthlyCategoryTotal, ...],
        monthly_totals: tuple[MonthlyTotal, ...],
        budget_targets: tuple[BudgetTarget, ...],
        included_transactions: tuple[ExpenseTransaction, ...],
    ) -> tuple[BudgetComparison, ...]:
        monthly_category_total_by_month_and_category: Mapping[tuple[date, ExpenseCategory], MonthlyCategoryTotal] = (
            cls._category_totals_by_month_and_category(category_totals)
        )
        return tuple(
            cls._budget_comparison(monthly_total.month, budget_target, monthly_category_total_by_month_and_category, included_transactions)
            for monthly_total in monthly_totals
            for budget_target in budget_targets
        )

    @classmethod
    def _budget_comparison(
        cls,
        month: date,
        budget_target: BudgetTarget,
        monthly_category_total_by_month_and_category: Mapping[tuple[date, ExpenseCategory], MonthlyCategoryTotal],
        included_transactions: tuple[ExpenseTransaction, ...],
    ) -> BudgetComparison:
        monthly_category_total: MonthlyCategoryTotal | None = monthly_category_total_by_month_and_category.get((month, budget_target.category))
        actual_spending: Decimal = monthly_category_total.amount if monthly_category_total is not None else Decimal(0)
        source_line_numbers: tuple[int, ...] = monthly_category_total.source_line_numbers if monthly_category_total is not None else ()
        return BudgetComparison(
            month=month,
            category=budget_target.category,
            target=budget_target.monthly_amount,
            actual=actual_spending,
            variance=actual_spending - budget_target.monthly_amount,
            source_line_numbers=source_line_numbers,
            coverage_note=cls._shopping_coverage_note(month, budget_target.category, included_transactions),
            coverage_source_line_numbers=cls._shopping_coverage_lines(month, budget_target.category, included_transactions),
        )

    @classmethod
    def _shopping_coverage_note(cls, month: date, category: ExpenseCategory, transactions: tuple[ExpenseTransaction, ...]) -> str | None:
        mixed_retailer_lines: tuple[int, ...] = cls._shopping_coverage_lines(month, category, transactions)
        if not mixed_retailer_lines:
            return None
        line_numbers: str = ", ".join(str(line_number) for line_number in mixed_retailer_lines)
        return f"Shopping coverage is incomplete because mixed-retailer charges are in Other (source lines {line_numbers})."

    @classmethod
    def _shopping_coverage_lines(cls, month: date, category: ExpenseCategory, transactions: tuple[ExpenseTransaction, ...]) -> tuple[int, ...]:
        if category != ExpenseCategory.SHOPPING:
            return ()
        return tuple(
            sorted(
                transaction.parsed_line.line_number
                for transaction in transactions
                if cls._is_mixed_retailer_other_charge_for_month(transaction, month)
            )
        )

    @classmethod
    def _is_mixed_retailer_other_charge_for_month(cls, transaction: ExpenseTransaction, month: date) -> bool:
        return (
            cls._month(cls._dated_amount(transaction)[0]) == month
            and cls._spending_category(transaction) == ExpenseCategory.OTHER
            and transaction.categorization is not None
            and transaction.categorization.policy_note == "Merchant-only mixed retailer charge; item type is not stated"
        )

    @classmethod
    def _month_category_deltas(
        cls, category_totals: tuple[MonthlyCategoryTotal, ...], monthly_totals: tuple[MonthlyTotal, ...]
    ) -> tuple[MonthCategoryDelta, ...]:
        monthly_category_total_by_month_and_category: Mapping[tuple[date, ExpenseCategory], MonthlyCategoryTotal] = (
            cls._category_totals_by_month_and_category(category_totals)
        )
        months: tuple[date, ...] = tuple(monthly_total.month for monthly_total in monthly_totals)
        month_pairs: tuple[tuple[date, date], ...] = cls._adjacent_month_pairs(months)
        deltas: list[MonthCategoryDelta] = []
        month_pair: tuple[date, date]
        for month_pair in month_pairs:
            deltas.extend(cls._deltas_for_pair(month_pair[0], month_pair[1], monthly_category_total_by_month_and_category))
        return tuple(deltas)

    @staticmethod
    def _category_totals_by_month_and_category(
        category_totals: tuple[MonthlyCategoryTotal, ...],
    ) -> Mapping[tuple[date, ExpenseCategory], MonthlyCategoryTotal]:
        return {(total.month, total.category): total for total in category_totals}

    @staticmethod
    def _adjacent_month_pairs(months: Sequence[date]) -> tuple[tuple[date, date], ...]:
        return tuple(pairwise(months))

    @staticmethod
    def _deltas_for_pair(
        from_month: date,
        to_month: date,
        monthly_category_total_by_month_and_category: Mapping[tuple[date, ExpenseCategory], MonthlyCategoryTotal],
    ) -> tuple[MonthCategoryDelta, ...]:
        categories: set[ExpenseCategory] = {
            month_and_category[1]
            for month_and_category in monthly_category_total_by_month_and_category
            if month_and_category[0] in (from_month, to_month)
        }
        deltas: list[MonthCategoryDelta] = []
        for category in categories:
            from_month_monthly_category_total: MonthlyCategoryTotal | None = monthly_category_total_by_month_and_category.get((from_month, category))
            to_month_monthly_category_total: MonthlyCategoryTotal | None = monthly_category_total_by_month_and_category.get((to_month, category))
            previous_amount: Decimal = from_month_monthly_category_total.amount if from_month_monthly_category_total is not None else Decimal(0)
            current_amount: Decimal = to_month_monthly_category_total.amount if to_month_monthly_category_total is not None else Decimal(0)
            if previous_amount == 0 and current_amount == 0:
                continue
            source_line_numbers: tuple[int, ...] = tuple(
                sorted(
                    (
                        *(() if from_month_monthly_category_total is None else from_month_monthly_category_total.source_line_numbers),
                        *(() if to_month_monthly_category_total is None else to_month_monthly_category_total.source_line_numbers),
                    )
                )
            )
            deltas.append(
                MonthCategoryDelta(
                    from_month=from_month,
                    to_month=to_month,
                    category=category,
                    previous_amount=previous_amount,
                    current_amount=current_amount,
                    change=current_amount - previous_amount,
                    source_line_numbers=source_line_numbers,
                )
            )
        return tuple(sorted(deltas, key=lambda delta: (-abs(delta.change), delta.category.value)))

    @staticmethod
    def _reconciliation(
        transactions: tuple[ExpenseTransaction, ...], category_totals: tuple[MonthlyCategoryTotal, ...], monthly_totals: tuple[MonthlyTotal, ...]
    ) -> CalculationReconciliation:
        line_numbers_by_treatment: dict[TransactionTreatment, tuple[int, ...]] = {
            treatment: tuple(sorted(transaction.parsed_line.line_number for transaction in transactions if transaction.treatment == treatment))
            for treatment in TransactionTreatment
        }
        classified_spending: Decimal = sum((total.amount for total in category_totals), Decimal(0))
        monthly_classified_spending: Decimal = sum((total.classified_spending for total in monthly_totals), Decimal(0))
        unresolved_outflow: Decimal = sum((total.unresolved_outflow for total in monthly_totals), Decimal(0))
        observed_outflow: Decimal = sum((total.observed_outflow for total in monthly_totals), Decimal(0))
        source_line_numbers: tuple[int, ...] = tuple(transaction.parsed_line.line_number for transaction in transactions)
        refunds: tuple[int, ...] = tuple(
            sorted(
                transaction.parsed_line.line_number
                for transaction in transactions
                if transaction.treatment == TransactionTreatment.INCLUDED_SPENDING
                and transaction.parsed_line.amount is not None
                and transaction.parsed_line.amount < 0
            )
        )
        calculation_reconciliation: CalculationReconciliation = CalculationReconciliation(
            source_line_count=len(transactions),
            included_line_numbers=line_numbers_by_treatment[TransactionTreatment.INCLUDED_SPENDING],
            duplicate_line_numbers=line_numbers_by_treatment[TransactionTreatment.EXCLUDED_DUPLICATE],
            unresolved_line_numbers=line_numbers_by_treatment[TransactionTreatment.UNCATEGORIZED_OUTFLOW],
            unparsed_line_numbers=line_numbers_by_treatment[TransactionTreatment.UNPARSED],
            classified_spending=classified_spending,
            unresolved_outflow=unresolved_outflow,
            observed_outflow=observed_outflow,
            is_balanced=(
                len(source_line_numbers) == len(set(source_line_numbers))
                and sum(len(lines) for lines in line_numbers_by_treatment.values()) == len(source_line_numbers)
                and classified_spending == monthly_classified_spending
                and classified_spending + unresolved_outflow == observed_outflow
            ),
            unresolved_credit_line_numbers=line_numbers_by_treatment[TransactionTreatment.UNCATEGORIZED_CREDIT],
            refund_line_numbers=refunds,
        )
        if not calculation_reconciliation.is_balanced:
            raise ValueError("Expense totals did not reconcile")
        return calculation_reconciliation

    @staticmethod
    def _dated_amount(transaction: ExpenseTransaction) -> tuple[date, Decimal]:
        occurred_on: date | None = transaction.parsed_line.occurred_on
        amount: Decimal | None = transaction.parsed_line.amount
        if occurred_on is None or amount is None:
            raise ValueError(f"Source line {transaction.parsed_line.line_number} must be dated and priced before calculation")
        return occurred_on, amount

    @staticmethod
    def _spending_category(transaction: ExpenseTransaction) -> ExpenseCategory:
        if transaction.categorization is None or transaction.categorization.category == ExpenseCategory.UNCATEGORIZED:
            raise ValueError(f"Source line {transaction.parsed_line.line_number} must have a spending category before calculation")
        return transaction.categorization.category

    @staticmethod
    def _month(occurred_on: date) -> date:
        return date(occurred_on.year, occurred_on.month, 1)
