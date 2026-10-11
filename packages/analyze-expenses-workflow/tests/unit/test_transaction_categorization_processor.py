import secrets

import pytest
from acceptance_support.asserter_jev import ExpectedJevGatewayFailure, assert_jev_gateway_failure

from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import JevChoiceCategoryUnrecognizedException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory

KNOWN_EXPENSE_CATEGORIES_TEXT: str = ", ".join(expense_category.value for expense_category in ExpenseCategory)


class StubSystemOneGateway:
    def __init__(self, choice_decision: ChoiceDecision) -> None:
        self._choice_decision: ChoiceDecision = choice_decision

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        return self._choice_decision

    async def close(self) -> None:
        return None


def _expected_jev_choice_category_unrecognized(selected_category: str, unrecognized_category: str) -> ExpectedJevGatewayFailure:
    return ExpectedJevGatewayFailure(
        exception_type=JevChoiceCategoryUnrecognizedException,
        action="DeveloperActionRequired",
        reason="Jev selected a category that is not a known expense category",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev", unrecognized_category, "not known expense categories", KNOWN_EXPENSE_CATEGORIES_TEXT),
        contextual_fields=(
            ("Operation", "choice_category_translation"),
            ("SelectedCategory", selected_category),
            ("UnrecognizedCategories", unrecognized_category),
            ("AllowedCategories", KNOWN_EXPENSE_CATEGORIES_TEXT),
        ),
        cause_exception_type=ValueError,
    )


async def test_categorize_WhenJevSelectsAnUnknownCategory_ThenReportsTheUnrecognizedCategoryAndTheKnownCategories() -> None:
    unknown_category: str = f"unknown-{secrets.token_hex(4)}"
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _expected_jev_choice_category_unrecognized(unknown_category, unknown_category)
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ChoiceDecision(choice=unknown_category, confidence=1.0, probabilities={unknown_category: 1.0}))
    )

    with pytest.raises(JevChoiceCategoryUnrecognizedException) as raised_exception:
        await transaction_categorization_processor.categorize(1, f"June 1 {secrets.token_hex(8)} $25.00")

    actual_jev_choice_category_unrecognized_exception: JevChoiceCategoryUnrecognizedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_category_unrecognized_exception, expected_jev_gateway_failure)


async def test_categorize_WhenJevReturnsAProbabilityForAnUnknownCategory_ThenReportsTheUnknownProbabilityCategoryAndTheKnownCategories() -> None:
    selected_category: str = ExpenseCategory.GROCERIES.value
    unknown_probability_category: str = f"unknown-{secrets.token_hex(4)}"
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _expected_jev_choice_category_unrecognized(
        selected_category, unknown_probability_category
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(
            ChoiceDecision(
                choice=selected_category,
                confidence=0.9,
                probabilities={selected_category: 0.9, unknown_probability_category: 0.1},
            )
        )
    )

    with pytest.raises(JevChoiceCategoryUnrecognizedException) as raised_exception:
        await transaction_categorization_processor.categorize(1, f"June 1 {secrets.token_hex(8)} $25.00")

    actual_jev_choice_category_unrecognized_exception: JevChoiceCategoryUnrecognizedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_category_unrecognized_exception, expected_jev_gateway_failure)
