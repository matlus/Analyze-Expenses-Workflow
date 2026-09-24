import os

import pytest

from analyze_expenses_workflow import DomainFacade, ExpenseAnalysisResult, ExpenseCategory


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_TESTS") != "1", reason="Real Jev calls are opt-in")
async def test_analyze_expenses_categorizes_a_clear_grocery_purchase() -> None:
    expense_line: str = "2026-08-14 WHOLE FOODS MARKET grocery purchase -$42.18"

    async with DomainFacade() as facade:
        result: ExpenseAnalysisResult = await facade.analyze_expenses([expense_line])

    assert len(result.categorizations) == 1
    categorization = result.categorizations[0]
    assert categorization.source_line_number == 1
    assert categorization.source_text == expense_line
    assert categorization.category == ExpenseCategory.GROCERIES
    assert 0.0 <= categorization.confidence <= 1.0
    assert {item.category for item in categorization.probabilities} == set(ExpenseCategory)
    assert categorization.model_category in {item.category for item in categorization.probabilities}
