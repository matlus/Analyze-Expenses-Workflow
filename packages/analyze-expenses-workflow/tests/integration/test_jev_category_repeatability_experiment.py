import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass

import pytest

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev import SystemOneGatewayOpenRouterJev
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory


@dataclass(frozen=True, slots=True)
class CategoryRepeatabilityObservation:
    source_line_number: int
    expected_category: str
    selected_categories: tuple[str, ...]
    model_categories: tuple[str, ...]
    chosen_probabilities: tuple[float, ...]
    agreement_with_first: float
    matches_expected: int


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_REPEATABILITY_TESTS") != "1", reason="Repeated real Jev calls are opt-in")
async def test_categorize_WhenJevRepeatsExamples_ThenRecordsCategoryAgreement(record_property: Callable[[str, object], None]) -> None:
    expense_line_examples: tuple[tuple[str, str, ExpenseCategory], ...] = (
        ("- June 1  Trader Joe's  $82.18", "Trader Joe's", ExpenseCategory.GROCERIES),
        ("June 1 Amazon Prime $14.99", "Amazon Prime", ExpenseCategory.SUBSCRIPTIONS),
        ("Amazon (June 5) - $182.78", "Amazon", ExpenseCategory.OTHER),
        ("6/20/26 - Zelle Payment - $102.66", "Zelle Payment", ExpenseCategory.UNCATEGORIZED),
        ("Uber, August 16 -- $24.10", "Uber", ExpenseCategory.TRANSPORTATION),
    )
    jev_settings: JevSettings = ConfigurationProvider().get_jev_settings()
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(jev_settings)
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(system_one_gateway_open_router_jev)
    category_repeatability_observations: list[CategoryRepeatabilityObservation] = []
    try:
        source_line_number: int
        source_text: str
        description: str
        expected_category: ExpenseCategory
        for source_line_number, (source_text, description, expected_category) in enumerate(expense_line_examples, start=1):
            expense_categorizations: list[ExpenseCategorization] = [
                await transaction_categorization_processor.categorize(source_line_number, source_text, description) for _ in range(3)
            ]
            selected_categories: tuple[str, ...] = tuple(expense_categorization.category.value for expense_categorization in expense_categorizations)
            category_repeatability_observations.append(
                CategoryRepeatabilityObservation(
                    source_line_number=source_line_number,
                    expected_category=expected_category.value,
                    selected_categories=selected_categories,
                    model_categories=tuple(expense_categorization.model_category.value for expense_categorization in expense_categorizations),
                    chosen_probabilities=tuple(
                        next(
                            category_probability.probability
                            for category_probability in expense_categorization.probabilities
                            if category_probability.category == expense_categorization.model_category
                        )
                        for expense_categorization in expense_categorizations
                    ),
                    agreement_with_first=sum(category == selected_categories[0] for category in selected_categories) / len(expense_categorizations),
                    matches_expected=sum(expense_categorization.category == expected_category for expense_categorization in expense_categorizations),
                )
            )
    finally:
        await system_one_gateway_open_router_jev.close()

    record_property(
        "category_repeatability_observations",
        json.dumps([asdict(category_repeatability_observation) for category_repeatability_observation in category_repeatability_observations]),
    )
    assert len(category_repeatability_observations) == len(expense_line_examples)
