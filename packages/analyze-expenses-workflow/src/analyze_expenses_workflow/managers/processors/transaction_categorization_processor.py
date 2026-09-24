import re
from typing import ClassVar

from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import SystemOneGatewayException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion, SystemOneGatewayProtocol
from analyze_expenses_workflow.models.expense_analysis_result import CategoryProbability, ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import STANDARD_EXPENSE_CATEGORY_CATALOG, ExpenseCategory, ExpenseCategoryCatalog


class TransactionCategorizationProcessor:
    _STATED_PURPOSE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:rent|mortgage|grocer(?:y|ies)|dinner|lunch|breakfast|meal|utility|utilities|electric|"
        r"internet|phone|gas|fuel|parking|flight|hotel|lodging|medical|pharmacy|tuition|childcare|gift|donation)\b"
    )
    _CASH_MOVEMENT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:zelle\b|venmo\b|"
        r"atm (?:cash )?withdrawal|cash withdrawal|paypal (?:transfer|payment)|bank transfer|ach transfer|wire transfer)"
    )

    def __init__(self, gateway: SystemOneGatewayProtocol, catalog: ExpenseCategoryCatalog = STANDARD_EXPENSE_CATEGORY_CATALOG) -> None:
        self._gateway: SystemOneGatewayProtocol = gateway
        self._catalog: ExpenseCategoryCatalog = catalog

    async def categorize(self, source_line_number: int, source_text: str, description: str | None = None) -> ExpenseCategorization:
        decision: ChoiceDecision = await self._gateway.choose(source_text, self._choice_question())
        model_category, probabilities = self._translate_decision(decision)
        category, policy_note = self._apply_category_policy(description, model_category)
        return ExpenseCategorization(
            source_line_number=source_line_number,
            source_text=source_text,
            category=category,
            model_category=model_category,
            policy_note=policy_note,
            confidence=decision.confidence,
            probabilities=probabilities,
        )

    def _choice_question(self) -> ChoiceQuestion:
        return ChoiceQuestion(
            instructions=(
                "Select the expense category using only evidence in this transaction. "
                "Choose Other for a merchant-only mixed retailer charge when the purchased item is not stated. "
                "Choose Uncategorized for a transfer or cash withdrawal with no stated spending purpose."
            ),
            criteria={definition.category.value: definition.criterion for definition in self._catalog.definitions},
        )

    @staticmethod
    def _translate_decision(decision: ChoiceDecision) -> tuple[ExpenseCategory, tuple[CategoryProbability, ...]]:
        try:
            model_category: ExpenseCategory = ExpenseCategory(decision.choice)
            probabilities: tuple[CategoryProbability, ...] = tuple(
                CategoryProbability(category=ExpenseCategory(name), probability=probability) for name, probability in decision.probabilities.items()
            )
        except ValueError as exc:
            raise SystemOneGatewayException("Jev selected an unrecognized expense category") from exc
        return model_category, probabilities

    def _apply_category_policy(self, description: str | None, model_category: ExpenseCategory) -> tuple[ExpenseCategory, str | None]:
        category: ExpenseCategory = model_category
        policy_note: str | None = None
        normalized_description: str | None = description.casefold().removeprefix("return processed - ") if description is not None else None
        if normalized_description is not None and normalized_description in (
            merchant.casefold() for merchant in self._catalog.mixed_retailer_merchants
        ):
            category = ExpenseCategory.OTHER
            policy_note = "Merchant-only mixed retailer charge; item type is not stated"
        if normalized_description is not None:
            for merchant in self._catalog.known_merchants:
                if normalized_description == merchant.name.casefold():
                    category = merchant.expense_category
                    policy_note = f"Known expense merchant: {merchant.name}"
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
