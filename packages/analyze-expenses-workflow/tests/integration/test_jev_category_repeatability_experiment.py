import json
import os
from collections.abc import Callable

import pytest

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.gateways.open_router_jev_gateway import OpenRouterJevGateway
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_REPEATABILITY_TESTS") != "1", reason="Repeated real Jev calls are opt-in")
async def test_jev_category_repeatability_probe(record_property: Callable[[str, object], None]) -> None:
    examples: tuple[tuple[str, str, ExpenseCategory], ...] = (
        ("- June 1  Trader Joe's  $82.18", "Trader Joe's", ExpenseCategory.GROCERIES),
        ("June 1 Amazon Prime $14.99", "Amazon Prime", ExpenseCategory.SUBSCRIPTIONS),
        ("Amazon (June 5) - $182.78", "Amazon", ExpenseCategory.OTHER),
        ("6/20/26 - Zelle Payment - $102.66", "Zelle Payment", ExpenseCategory.UNCATEGORIZED),
        ("Uber, August 16 -- $24.10", "Uber", ExpenseCategory.TRANSPORTATION),
    )
    settings: JevSettings = ConfigurationProvider().get_jev_settings()
    gateway: OpenRouterJevGateway = OpenRouterJevGateway(settings)
    processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(gateway)
    observations: list[dict[str, object]] = []
    try:
        for source_line_number, (source_text, description, expected_category) in enumerate(examples, start=1):
            results: list[ExpenseCategorization] = [await processor.categorize(source_line_number, source_text, description) for _ in range(3)]
            selected_categories: list[str] = [result.category.value for result in results]
            observations.append(
                {
                    "line_number": source_line_number,
                    "expected_category": expected_category.value,
                    "selected_categories": selected_categories,
                    "model_categories": [result.model_category.value for result in results],
                    "chosen_probabilities": [
                        next(item.probability for item in result.probabilities if item.category == result.model_category) for result in results
                    ],
                    "agreement_with_first": sum(category == selected_categories[0] for category in selected_categories) / len(results),
                    "matches_expected": sum(result.category == expected_category for result in results),
                }
            )
    finally:
        await gateway.close()

    record_property("category_repeatability_observations", json.dumps(observations))
    assert len(observations) == len(examples)
