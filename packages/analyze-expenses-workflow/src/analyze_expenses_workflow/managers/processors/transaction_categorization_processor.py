import asyncio
import re
from typing import ClassVar

from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import JevChoiceCategoryUnrecognizedException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion, SystemOneGatewayProtocol
from analyze_expenses_workflow.models.expense_analysis_result import CategoryProbability, ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import (
    STANDARD_EXPENSE_CATEGORY_CATALOG,
    ExpenseCategory,
    ExpenseCategoryCatalog,
    KnownExpenseMerchant,
)
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class TransactionCategorizationProcessor:
    _STATED_PURPOSE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:rent|mortgage|grocer(?:y|ies)|dinner|lunch|breakfast|meal|utility|utilities|electric|"
        r"internet|phone|gas|fuel|parking|flight|hotel|lodging|medical|pharmacy|tuition|childcare|gift|donation)\b"
    )
    _MAXIMUM_CONCURRENT_CATEGORIZATIONS: ClassVar[int] = 8
    _CASH_MOVEMENT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:zelle\b|venmo\b|"
        r"atm (?:cash )?withdrawal|cash withdrawal|paypal (?:transfer|payment)|bank transfer|ach transfer|wire transfer)"
    )

    def __init__(
        self,
        system_one_gateway_protocol: SystemOneGatewayProtocol,
        expense_category_catalog: ExpenseCategoryCatalog = STANDARD_EXPENSE_CATEGORY_CATALOG,
    ) -> None:
        self._system_one_gateway_protocol: SystemOneGatewayProtocol = system_one_gateway_protocol
        self._expense_category_catalog: ExpenseCategoryCatalog = expense_category_catalog

    async def categorize_lines(self, parsed_expense_lines: tuple[ParsedExpenseLine, ...]) -> tuple[ExpenseCategorization | None, ...]:
        """One slot per parsed line, aligned by position; a line without a date, amount and description has no category."""
        semaphore: asyncio.Semaphore = asyncio.Semaphore(self._MAXIMUM_CONCURRENT_CATEGORIZATIONS)
        async with asyncio.TaskGroup() as task_group:
            tasks: tuple[asyncio.Task[ExpenseCategorization | None], ...] = tuple(
                task_group.create_task(self._categorize_complete_line(parsed_expense_line, semaphore)) for parsed_expense_line in parsed_expense_lines
            )
        return tuple(task.result() for task in tasks)

    async def _categorize_complete_line(self, parsed_expense_line: ParsedExpenseLine, semaphore: asyncio.Semaphore) -> ExpenseCategorization | None:
        if parsed_expense_line.occurred_on is None or parsed_expense_line.amount is None or parsed_expense_line.description is None:
            return None
        async with semaphore:
            return await self.categorize(parsed_expense_line.line_number, parsed_expense_line.source_text, parsed_expense_line.description)

    async def categorize(self, source_line_number: int, source_text: str, description: str | None = None) -> ExpenseCategorization:
        choice_decision: ChoiceDecision = await self._system_one_gateway_protocol.choose(source_text, self._choice_question())
        model_category: ExpenseCategory
        probabilities: tuple[CategoryProbability, ...]
        model_category, probabilities = self._translate_decision(choice_decision)
        category: ExpenseCategory
        policy_note: str | None
        category, policy_note = self._apply_category_policy(description, model_category)
        return ExpenseCategorization(
            source_line_number=source_line_number,
            source_text=source_text,
            category=category,
            model_category=model_category,
            policy_note=policy_note,
            confidence=choice_decision.confidence,
            probabilities=probabilities,
        )

    def _choice_question(self) -> ChoiceQuestion:
        return ChoiceQuestion(
            instructions=(
                "Select the expense category using only evidence in this transaction. "
                "Choose Other for a merchant-only mixed retailer charge when the purchased item is not stated. "
                "Choose Uncategorized for a transfer or cash withdrawal with no stated spending purpose."
            ),
            criterion_by_choice_key={definition.category.value: definition.criterion for definition in self._expense_category_catalog.definitions},
        )

    @classmethod
    def _translate_decision(cls, choice_decision: ChoiceDecision) -> tuple[ExpenseCategory, tuple[CategoryProbability, ...]]:
        try:
            model_category: ExpenseCategory = ExpenseCategory(choice_decision.choice)
            probabilities: tuple[CategoryProbability, ...] = tuple(
                CategoryProbability(category=ExpenseCategory(name), probability=probability)
                for name, probability in choice_decision.probabilities.items()
            )
        except ValueError as value_error:
            raise cls._choice_category_unrecognized_exception(choice_decision) from value_error
        return model_category, probabilities

    @staticmethod
    def _choice_category_unrecognized_exception(choice_decision: ChoiceDecision) -> JevChoiceCategoryUnrecognizedException:
        allowed_category_names: frozenset[str] = frozenset(expense_category.value for expense_category in ExpenseCategory)
        unrecognized_category_names: tuple[str, ...] = tuple(
            dict.fromkeys(
                category_name
                for category_name in (choice_decision.choice, *choice_decision.probabilities)
                if category_name not in allowed_category_names
            )
        )
        unrecognized_categories_text: str = ", ".join(unrecognized_category_names)
        allowed_categories_text: str = ", ".join(expense_category.value for expense_category in ExpenseCategory)
        return JevChoiceCategoryUnrecognizedException(
            f"Jev returned category names that are not known expense categories: {unrecognized_categories_text}. "
            f"Known expense categories are: {allowed_categories_text}",
            {
                "Operation": "choice_category_translation",
                "SelectedCategory": choice_decision.choice,
                "UnrecognizedCategories": unrecognized_categories_text,
                "AllowedCategories": allowed_categories_text,
            },
        )

    def _apply_category_policy(self, description: str | None, model_category: ExpenseCategory) -> tuple[ExpenseCategory, str | None]:
        category: ExpenseCategory = model_category
        policy_note: str | None = None
        normalized_description: str | None = description.casefold().removeprefix("return processed - ") if description is not None else None
        if normalized_description is not None and normalized_description in (
            mixed_retailer_merchant.casefold() for mixed_retailer_merchant in self._expense_category_catalog.mixed_retailer_merchants
        ):
            category = ExpenseCategory.OTHER
            policy_note = "Merchant-only mixed retailer charge; item type is not stated"
        if normalized_description is not None:
            known_expense_merchant: KnownExpenseMerchant
            for known_expense_merchant in self._expense_category_catalog.known_merchants:
                if normalized_description == known_expense_merchant.name.casefold():
                    category = known_expense_merchant.expense_category
                    policy_note = f"Known expense merchant: {known_expense_merchant.name}"
                    break
        if description is not None and self._is_ambiguous_cash_movement(description):
            category = ExpenseCategory.UNCATEGORIZED
            policy_note = "Cash movement without an identified spending purpose"
        return category, policy_note

    @staticmethod
    def _is_ambiguous_cash_movement(description: str) -> bool:
        normalized: str = " ".join(description.casefold().split())
        if TransactionCategorizationProcessor._STATED_PURPOSE_PATTERN.search(normalized):
            return False
        return TransactionCategorizationProcessor._CASH_MOVEMENT_PATTERN.match(normalized) is not None
