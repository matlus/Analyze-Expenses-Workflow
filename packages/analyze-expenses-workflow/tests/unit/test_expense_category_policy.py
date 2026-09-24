import pytest

from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import STANDARD_EXPENSE_CATEGORY_CATALOG, ExpenseCategory


class StubSystemOneGateway:
    def __init__(self, model_choice: ExpenseCategory) -> None:
        self._model_choice: ExpenseCategory = model_choice

    async def choose(self, state: str, question: ChoiceQuestion) -> ChoiceDecision:
        assert state
        assert set(question.criteria) == {category.value for category in ExpenseCategory}
        return ChoiceDecision(
            choice=self._model_choice.value,
            confidence=1.0,
            probabilities={category.value: float(category is self._model_choice) for category in ExpenseCategory},
        )

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("merchant", ["Target", "Amazon", "Costco"])
async def test_merchant_only_mixed_retailer_is_other_even_when_model_guesses_shopping(merchant: str) -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(StubSystemOneGateway(ExpenseCategory.SHOPPING))

    result: ExpenseCategorization = await processor.categorize(1, f"June 1 {merchant} $25.00", merchant)

    assert result.category is ExpenseCategory.OTHER
    assert result.model_category is ExpenseCategory.SHOPPING
    assert result.policy_note is not None


async def test_unknown_cash_movement_is_uncategorized_even_when_model_guesses_spending() -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(StubSystemOneGateway(ExpenseCategory.TRANSPORTATION))

    result: ExpenseCategorization = await processor.categorize(1, "ATM Cash Withdrawal $80.00 (Aug. 1)", "ATM Cash Withdrawal")

    assert result.category is ExpenseCategory.UNCATEGORIZED
    assert result.model_category is ExpenseCategory.TRANSPORTATION


@pytest.mark.parametrize(
    "description",
    ["Venmo - Sam", "Zelle Payment", "PayPal Transfer", "ATM Cash Withdrawal"],
)
async def test_cash_movement_without_a_stated_purpose_is_uncategorized(description: str) -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(StubSystemOneGateway(ExpenseCategory.DINING_COFFEE))

    result: ExpenseCategorization = await processor.categorize(1, f"June 1 {description} $25.00", description)

    assert result.category is ExpenseCategory.UNCATEGORIZED


@pytest.mark.parametrize(
    "description",
    ["Venmo - Sam for dinner", "Venmo - Sam - dinner", "Zelle Payment for dinner", "PayPal Transfer for dinner", "ATM Cash Withdrawal for dinner"],
)
async def test_cash_movement_with_a_stated_purpose_keeps_the_model_category(description: str) -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(StubSystemOneGateway(ExpenseCategory.DINING_COFFEE))

    result: ExpenseCategorization = await processor.categorize(1, f"June 1 {description} $25.00", description)

    assert result.category is ExpenseCategory.DINING_COFFEE
    assert result.policy_note is None


@pytest.mark.parametrize(
    ("description", "category"),
    [("Zelle Payment - rent", ExpenseCategory.HOUSING), ("PayPal Transfer - groceries", ExpenseCategory.GROCERIES)],
)
async def test_transfer_with_named_purchase_purpose_keeps_the_model_category(
    description: str, category: ExpenseCategory
) -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(StubSystemOneGateway(category))

    result: ExpenseCategorization = await processor.categorize(1, f"June 1 {description} $25.00", description)

    assert result.category is category
    assert result.policy_note is None


@pytest.mark.parametrize(
    "description",
    [
        "Venmo - Sam for transfer",
        "Venmo - Sam for Sam",
        "Venmo to Sam",
        "Zelle - Sam",
        "Zelle to Sam",
        "ATM withdrawal",
        "Bank transfer",
        "Venmo payment",
    ],
)
async def test_cash_movement_with_no_purchase_purpose_remains_uncategorized(description: str) -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.DINING_COFFEE)
    )

    result: ExpenseCategorization = await processor.categorize(1, f"June 1 {description} $25.00", description)

    assert result.category is ExpenseCategory.UNCATEGORIZED


async def test_specific_subscription_description_remains_specific() -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(StubSystemOneGateway(ExpenseCategory.SUBSCRIPTIONS))

    result: ExpenseCategorization = await processor.categorize(1, "June 1 Amazon Prime $14.99", "Amazon Prime")

    assert result.category is ExpenseCategory.SUBSCRIPTIONS
    assert result.policy_note is None


async def test_return_from_a_merchant_only_mixed_retailer_remains_other() -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.SHOPPING)
    )

    result: ExpenseCategorization = await processor.categorize(
        1, "June 1 Return processed - Amazon -$25.00", "Return processed - Amazon"
    )

    assert result.category is ExpenseCategory.OTHER
    assert result.policy_note is not None


@pytest.mark.parametrize(
    ("description", "expected"),
    [("Shell", ExpenseCategory.TRANSPORTATION), ("Best Buy", ExpenseCategory.SHOPPING), ("Return processed - REI", ExpenseCategory.SHOPPING)],
)
async def test_known_expense_merchants_keep_their_explicit_catalog_category(description: str, expected: ExpenseCategory) -> None:
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(StubSystemOneGateway(ExpenseCategory.OTHER))

    result: ExpenseCategorization = await processor.categorize(1, f"June 1 {description} $25.00", description)

    assert result.category is expected
    assert result.model_category is ExpenseCategory.OTHER
    assert result.policy_note is not None


def test_standard_expense_category_catalog_defines_every_category_once() -> None:
    assert tuple(definition.category for definition in STANDARD_EXPENSE_CATEGORY_CATALOG.definitions) == tuple(ExpenseCategory)
