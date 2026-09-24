import json
from datetime import date
from decimal import Decimal

import pytest

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.exceptions.expense_findings_exception import ExpenseFindingsException
from analyze_expenses_workflow.managers.llm_processors.bases.llm_processor_base import ProcessingEventKind
from analyze_expenses_workflow.managers.llm_processors.expense_findings_llm_processor import ExpenseFindingsLlmProcessor
from analyze_expenses_workflow.models.expense_calculation_result import (
    BudgetComparison,
    CalculationReconciliation,
    ExpenseCalculationResult,
    MonthCategoryDelta,
)
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_finding import ExpenseFinding


class FakeLlmGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses: list[str] = responses
        self.prompts: list[str] = []

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self.prompts.append(prompt)
        assert model == "test-model"
        assert reasoning_effort is None
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


def _calculations() -> ExpenseCalculationResult:
    june: date = date(2026, 6, 1)
    july: date = date(2026, 7, 1)
    august: date = date(2026, 8, 1)
    return ExpenseCalculationResult(
        monthly_category_totals=(),
        monthly_totals=(),
        budget_comparisons=(
            BudgetComparison(june, ExpenseCategory.DINING_COFFEE, Decimal(260), Decimal("212.74"), Decimal("-47.26"), (1,), None),
            BudgetComparison(august, ExpenseCategory.DINING_COFFEE, Decimal(260), Decimal("398.69"), Decimal("138.69"), (8, 9), None),
            BudgetComparison(
                august,
                ExpenseCategory.SHOPPING,
                Decimal(1200),
                Decimal("712.98"),
                Decimal("-487.02"),
                (10,),
                "Shopping coverage is incomplete because mixed-retailer charges are in Other (source lines 11, 12).",
                (11, 12),
            ),
        ),
        month_category_deltas=(
            MonthCategoryDelta(july, august, ExpenseCategory.TRAVEL, Decimal(0), Decimal("1655.92"), Decimal("1655.92"), (4, 5, 6)),
            MonthCategoryDelta(july, august, ExpenseCategory.SHOPPING, Decimal("526.62"), Decimal("712.98"), Decimal("186.36"), (10,)),
        ),
        reconciliation=CalculationReconciliation(
            source_line_count=12,
            included_line_numbers=(1, 4, 5, 6, 8, 9, 10, 11, 12),
            duplicate_line_numbers=(2,),
            unresolved_line_numbers=(3,),
            unparsed_line_numbers=(7,),
            classified_spending=Decimal(10000),
            unresolved_outflow=Decimal(100),
            observed_outflow=Decimal(10100),
            is_balanced=True,
        ),
    )


def _response(*evidence_ids: str) -> str:
    return json.dumps({"findings": [{"evidence_id": evidence_id} for evidence_id in evidence_ids]})


def _processor(gateway: FakeLlmGateway) -> ExpenseFindingsLlmProcessor:
    return ExpenseFindingsLlmProcessor(gateway, LlmOperationSettings(model="test-model", reasoning_effort=None))


@pytest.mark.parametrize("fenced_json", [False, True])
async def test_three_findings_use_exact_calculated_numbers_and_required_coverage_context(fenced_json: bool) -> None:
    response: str = _response(
        "budget-2026-08-01-dining_coffee",
        "change-2026-07-01-2026-08-01-travel",
        "shopping-coverage",
    )
    gateway: FakeLlmGateway = FakeLlmGateway([f"```json\n{response}\n```" if fenced_json else response])

    result: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await _processor(gateway).findings(_calculations())

    assert len(result) == 3
    assert "$398.69" in result[0].text
    assert "$138.69 over target" in result[0].text
    assert "$1,655.92" in result[1].text
    assert "July 2026 to August 2026" in result[1].text
    assert "merchant-only mixed-retailer charges assigned to Other" in result[2].text
    assert result[2].evidence_id == "shopping-coverage"
    assert result[2].source_line_numbers == (10, 11, 12)
    assert "budget-2026-08-01-dining_coffee" in gateway.prompts[0]
    assert "change-2026-07-01-2026-08-01-travel" in gateway.prompts[0]
    assert "shopping-coverage" in gateway.prompts[0]


async def test_unsupported_model_prose_is_rejected_then_semantically_retried() -> None:
    valid_ids: tuple[str, ...] = (
        "budget-2026-08-01-dining_coffee",
        "change-2026-07-01-2026-08-01-travel",
        "shopping-coverage",
    )
    unsupported_response: str = json.dumps(
        {
            "findings": [
                {"evidence_id": valid_ids[0], "headline": "Unsupported claim about future spending"},
                {"evidence_id": valid_ids[1]},
                {"evidence_id": valid_ids[2]},
            ]
        }
    )
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            unsupported_response,
            _response(*valid_ids),
        ]
    )
    events: list[tuple[ProcessingEventKind, str]] = []

    async def record_event(kind: ProcessingEventKind, message: str) -> None:
        events.append((kind, message))

    processor: ExpenseFindingsLlmProcessor = ExpenseFindingsLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None), record_event
    )

    result: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await processor.findings(_calculations())

    assert len(gateway.prompts) == 2
    assert "Unsupported claim" not in " ".join(finding.text for finding in result)
    assert [event[0] for event in events] == ["progress", "complete"]


async def test_missing_priority_finding_after_retries_raises_domain_exception() -> None:
    invalid_response: str = _response(
        "budget-2026-08-01-dining_coffee",
        "change-2026-07-01-2026-08-01-travel",
        "reconciliation-spending",
    )
    gateway: FakeLlmGateway = FakeLlmGateway([invalid_response, invalid_response])

    with pytest.raises(ExpenseFindingsException, match="three grounded expense findings"):
        await _processor(gateway).findings(_calculations())

    assert len(gateway.prompts) == 2


async def test_sparse_calculations_still_offer_three_evidenced_findings() -> None:
    calculations: ExpenseCalculationResult = _calculations()
    sparse: ExpenseCalculationResult = ExpenseCalculationResult((), (), (), (), calculations.reconciliation)
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            _response(
                "reconciliation-spending",
                "reconciliation-unresolved",
                "reconciliation-coverage",
            )
        ]
    )

    result: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await _processor(gateway).findings(sparse)

    assert "$10,000.00" in result[0].text
    assert "$100.00" in result[1].text
    assert "12 source lines" in result[2].text
