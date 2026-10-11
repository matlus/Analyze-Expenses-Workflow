from secrets import randbelow

import pytest
from acceptance_support.asserter_expense_categorization import ExpenseCategorizationAsserter

from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import CategoryProbability, ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import STANDARD_EXPENSE_CATEGORY_CATALOG, ExpenseCategory

STUB_MODEL_CONFIDENCE: float = 1.0
MAXIMUM_SOURCE_LINE_NUMBER: int = 500
MIXED_RETAILER_POLICY_NOTE: str = "Merchant-only mixed retailer charge; item type is not stated"
CASH_MOVEMENT_POLICY_NOTE: str = "Cash movement without an identified spending purpose"


class StubSystemOneGateway:
    def __init__(self, model_choice: ExpenseCategory) -> None:
        self._model_choice: ExpenseCategory = model_choice

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        assert source_text
        assert set(choice_question.criterion_by_choice_key) == {category.value for category in ExpenseCategory}
        return ChoiceDecision(
            choice=self._model_choice.value,
            confidence=STUB_MODEL_CONFIDENCE,
            probabilities={category.value: float(category is self._model_choice) for category in ExpenseCategory},
        )

    async def close(self) -> None:
        return None


def create_source_line_number() -> int:
    return randbelow(MAXIMUM_SOURCE_LINE_NUMBER) + 1


def create_expected_expense_categorization(
    source_line_number: int,
    source_text: str,
    category: ExpenseCategory,
    model_category: ExpenseCategory,
    policy_note: str | None,
) -> ExpenseCategorization:
    """The stub model is certain of its choice, so the decision drivers follow from the model category alone."""
    return ExpenseCategorization(
        source_line_number=source_line_number,
        source_text=source_text,
        category=category,
        model_category=model_category,
        policy_note=policy_note,
        confidence=STUB_MODEL_CONFIDENCE,
        probabilities=tuple(
            CategoryProbability(category=expense_category, probability=float(expense_category is model_category))
            for expense_category in ExpenseCategory
        ),
    )


@pytest.mark.parametrize("merchant", ["Target", "Amazon", "Costco"])
async def test_Categorize_WhenAMerchantOnlyMixedRetailerIsDescribed_ThenReturnsOtherWithAPolicyNote(merchant: str) -> None:
    source_line_number: int = create_source_line_number()
    source_text: str = f"June 1 {merchant} $25.00"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, ExpenseCategory.OTHER, ExpenseCategory.SHOPPING, MIXED_RETAILER_POLICY_NOTE
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.SHOPPING)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, merchant
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A merchant-only mixed retailer charge is Other even when the model guesses Shopping, because the item type is not stated",
    )


async def test_Categorize_WhenACashWithdrawalStatesNoPurpose_ThenReturnsUncategorizedAndKeepsTheModelCategory() -> None:
    source_line_number: int = create_source_line_number()
    description: str = "ATM Cash Withdrawal"
    source_text: str = f"{description} $80.00 (Aug. 1)"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, ExpenseCategory.UNCATEGORIZED, ExpenseCategory.TRANSPORTATION, CASH_MOVEMENT_POLICY_NOTE
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.TRANSPORTATION)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A cash withdrawal with no stated spending purpose is Uncategorized, while the model's guess is kept as the model category",
    )


@pytest.mark.parametrize(
    "description",
    ["Venmo - Sam", "Zelle Payment", "PayPal Transfer", "ATM Cash Withdrawal"],
)
async def test_Categorize_WhenACashMovementStatesNoPurpose_ThenReturnsUncategorized(description: str) -> None:
    source_line_number: int = create_source_line_number()
    source_text: str = f"June 1 {description} $25.00"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, ExpenseCategory.UNCATEGORIZED, ExpenseCategory.DINING_COFFEE, CASH_MOVEMENT_POLICY_NOTE
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.DINING_COFFEE)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A cash movement that states no spending purpose is Uncategorized",
    )


@pytest.mark.parametrize(
    "description",
    ["Venmo - Sam for dinner", "Venmo - Sam - dinner", "Zelle Payment for dinner", "PayPal Transfer for dinner", "ATM Cash Withdrawal for dinner"],
)
async def test_Categorize_WhenACashMovementStatesAPurpose_ThenKeepsTheModelCategoryWithoutAPolicyNote(description: str) -> None:
    source_line_number: int = create_source_line_number()
    source_text: str = f"June 1 {description} $25.00"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, ExpenseCategory.DINING_COFFEE, ExpenseCategory.DINING_COFFEE, None
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.DINING_COFFEE)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A cash movement that states its spending purpose keeps the model category and carries no policy note",
    )


@pytest.mark.parametrize(
    ("description", "expense_category"),
    [("Zelle Payment - rent", ExpenseCategory.HOUSING), ("PayPal Transfer - groceries", ExpenseCategory.GROCERIES)],
)
async def test_Categorize_WhenATransferNamesAPurchasePurpose_ThenKeepsTheModelCategoryWithoutAPolicyNote(
    description: str, expense_category: ExpenseCategory
) -> None:
    source_line_number: int = create_source_line_number()
    source_text: str = f"June 1 {description} $25.00"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, expense_category, expense_category, None
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(expense_category)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A transfer that names what it paid for keeps the model category and carries no policy note",
    )


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
async def test_Categorize_WhenACashMovementNamesNoPurchasePurpose_ThenReturnsUncategorized(description: str) -> None:
    source_line_number: int = create_source_line_number()
    source_text: str = f"June 1 {description} $25.00"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, ExpenseCategory.UNCATEGORIZED, ExpenseCategory.DINING_COFFEE, CASH_MOVEMENT_POLICY_NOTE
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.DINING_COFFEE)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A cash movement whose wording names no purchase purpose stays Uncategorized",
    )


async def test_Categorize_WhenADescriptionNamesASpecificSubscription_ThenKeepsTheSubscriptionsCategoryWithoutAPolicyNote() -> None:
    source_line_number: int = create_source_line_number()
    description: str = "Amazon Prime"
    source_text: str = f"June 1 {description} $14.99"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, ExpenseCategory.SUBSCRIPTIONS, ExpenseCategory.SUBSCRIPTIONS, None
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.SUBSCRIPTIONS)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A description naming a specific subscription is not a merchant-only mixed retailer charge, so it keeps its category",
    )


async def test_Categorize_WhenAReturnComesFromAMerchantOnlyMixedRetailer_ThenReturnsOtherWithAPolicyNote() -> None:
    source_line_number: int = create_source_line_number()
    description: str = "Return processed - Amazon"
    source_text: str = f"June 1 {description} -$25.00"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, ExpenseCategory.OTHER, ExpenseCategory.SHOPPING, MIXED_RETAILER_POLICY_NOTE
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.SHOPPING)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A return from a merchant-only mixed retailer is Other, like the charge it reverses",
    )


@pytest.mark.parametrize(
    ("description", "expected_expense_category", "expected_policy_note"),
    [
        ("Shell", ExpenseCategory.TRANSPORTATION, "Known expense merchant: Shell"),
        ("Best Buy", ExpenseCategory.SHOPPING, "Known expense merchant: Best Buy"),
        ("Return processed - REI", ExpenseCategory.SHOPPING, "Known expense merchant: REI"),
    ],
)
async def test_Categorize_WhenAKnownExpenseMerchantIsDescribed_ThenReturnsTheCatalogCategoryWithAPolicyNote(
    description: str, expected_expense_category: ExpenseCategory, expected_policy_note: str
) -> None:
    source_line_number: int = create_source_line_number()
    source_text: str = f"June 1 {description} $25.00"
    expected_expense_categorization: ExpenseCategorization = create_expected_expense_categorization(
        source_line_number, source_text, expected_expense_category, ExpenseCategory.OTHER, expected_policy_note
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ExpenseCategory.OTHER)
    )

    actual_expense_categorization: ExpenseCategorization = await transaction_categorization_processor.categorize(
        source_line_number, source_text, description
    )

    ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
        expected_expense_categorization,
        actual_expense_categorization,
        "A known expense merchant takes its catalog category over the model's guess and names the merchant in the policy note",
    )


def test_ExpenseCategoryCatalog_WhenTheStandardCatalogIsInspected_ThenDefinesEveryCategoryExactlyOnce() -> None:
    expected_expense_categories: tuple[ExpenseCategory, ...] = tuple(ExpenseCategory)

    actual_expense_categories: tuple[ExpenseCategory, ...] = tuple(
        definition.category for definition in STANDARD_EXPENSE_CATEGORY_CATALOG.definitions
    )

    assert actual_expense_categories == expected_expense_categories, (
        f"The standard catalog should define every expense category once, in declaration order; "
        f"expected {expected_expense_categories!r}, got {actual_expense_categories!r}"
    )
