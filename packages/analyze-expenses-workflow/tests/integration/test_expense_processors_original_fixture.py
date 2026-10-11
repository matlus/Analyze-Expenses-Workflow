import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from acceptance_support.asserter_expense_processing_outcome import (
    CalculationStageOutcome,
    CategorizationStageOutcome,
    ExpenseCategorizationDecision,
    ExpenseProcessingOutcome,
    ExpenseProcessingOutcomeAsserter,
    FindingsStageOutcome,
    ParsingStageOutcome,
    ReconciliationStageOutcome,
)

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.llm_processors.expense_findings_llm_processor import ExpenseFindingsLlmProcessor
from analyze_expenses_workflow.managers.processors.expense_calculation_processor import ExpenseCalculationProcessor
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.managers.processors.expense_reconciliation_processor import ExpenseReconciliationProcessor
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_calculation_result import BudgetTarget, ExpenseCalculationResult
from analyze_expenses_workflow.models.expense_category_catalog import STANDARD_EXPENSE_CATEGORY_CATALOG, ExpenseCategory
from analyze_expenses_workflow.models.expense_finding import ExpenseFinding
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine

ORIGINAL_FIXTURE_SOURCE_LINE_COUNT: int = 198
DUPLICATED_SOURCE_LINE_NUMBER: int = 109


class _FixtureJevGateway:
    def __init__(self, category_by_source: Mapping[str, ExpenseCategory]) -> None:
        self._category_by_source: Mapping[str, ExpenseCategory] = category_by_source

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        category: ExpenseCategory = self._category_by_source[source_text]
        probabilities: dict[str, float] = {name: (1.0 if name == category.value else 0.0) for name in choice_question.criterion_by_choice_key}
        return ChoiceDecision(choice=category.value, confidence=1.0, probabilities=probabilities)

    async def close(self) -> None:
        return None


class _FixtureFindingsGateway:
    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        assert model == "fixture-model"
        assert reasoning_effort is None
        required_ids: list[str] = json.loads(prompt.split("required_evidence_ids: ", maxsplit=1)[1].split("\n", maxsplit=1)[0])
        assert len(required_ids) == 3
        return json.dumps({"findings": [{"evidence_id": evidence_id} for evidence_id in required_ids]})

    async def close(self) -> None:
        return None


@pytest.fixture
def source_fixture() -> tuple[list[str], dict[str, ExpenseCategory]]:
    fixture_directory: Path = Path(__file__).parents[1] / "fixtures"
    expense_lines: list[str] = (fixture_directory / "personal_expenses.txt").read_text(encoding="utf-8").splitlines()[3:]
    expected_category_rows: list[str] = (fixture_directory / "personal_expenses_expected_categories.tsv").read_text(encoding="utf-8").splitlines()[1:]
    category_by_source: dict[str, ExpenseCategory] = {}
    expected_category_row: str
    expense_line: str
    for expected_category_row, expense_line in zip(expected_category_rows, expense_lines, strict=True):
        expected_category_row_fields: list[str] = expected_category_row.split("\t", maxsplit=2)
        category: str = expected_category_row_fields[1]
        expected_source: str = expected_category_row_fields[2]
        assert expense_line == expected_source
        category_by_source[expense_line] = ExpenseCategory(category)
    assert len(expense_lines) == ORIGINAL_FIXTURE_SOURCE_LINE_COUNT
    return expense_lines, category_by_source


async def test_ExpenseProcessors_WhenTheCompleteOriginalFixtureIsProcessed_ThenReconcilesEveryLineWithGroundedFindings(
    source_fixture: tuple[list[str], dict[str, ExpenseCategory]],
) -> None:
    expense_lines: list[str]
    category_by_source: dict[str, ExpenseCategory]
    expense_lines, category_by_source = source_fixture
    candidate_categories: tuple[ExpenseCategory, ...] = tuple(definition.category for definition in STANDARD_EXPENSE_CATEGORY_CATALOG.definitions)
    expected_expense_processing_outcome: ExpenseProcessingOutcome = ExpenseProcessingOutcome(
        parsing_stage_outcome=ParsingStageOutcome(parsed_line_count=ORIGINAL_FIXTURE_SOURCE_LINE_COUNT, line_numbers_with_issues=()),
        categorization_stage_outcome=CategorizationStageOutcome(
            categorization_count=ORIGINAL_FIXTURE_SOURCE_LINE_COUNT,
            expense_categorization_decision_by_source_text={
                expense_line: ExpenseCategorizationDecision.create_certain(category_by_source[expense_line], candidate_categories)
                for expense_line in expense_lines
            },
            has_policy_noted_other_categorization=True,
        ),
        reconciliation_stage_outcome=ReconciliationStageOutcome(
            transaction_count=ORIGINAL_FIXTURE_SOURCE_LINE_COUNT,
            is_balanced=True,
            source_line_count=ORIGINAL_FIXTURE_SOURCE_LINE_COUNT,
            duplicate_line_count=1,
            unresolved_line_count=8,
            unparsed_line_numbers=(),
            classified_spending=Decimal("11111.95"),
            unresolved_outflow=Decimal("646.73"),
            observed_outflow=Decimal("11758.68"),
        ),
        calculation_stage_outcome=CalculationStageOutcome(
            monthly_total_count=3,
            budget_comparison_count=6,
            observed_outflow_by_month={
                date(2026, 6, 1): Decimal("3276.71"),
                date(2026, 7, 1): Decimal("2835.78"),
                date(2026, 8, 1): Decimal("5646.19"),
            },
            shopping_amount_by_month={
                date(2026, 6, 1): Decimal("572.48"),
                date(2026, 7, 1): Decimal("526.62"),
                date(2026, 8, 1): Decimal("712.98"),
            },
            shopping_budget_months_without_coverage_note=(),
        ),
        findings_stage_outcome=FindingsStageOutcome(
            evidence_ids=frozenset({"budget-2026-08-01-dining_coffee", "change-2026-07-01-2026-08-01-travel", "shopping-coverage"}),
            evidence_ids_without_source_lines=(),
        ),
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        _FixtureJevGateway(category_by_source)
    )
    budget_targets: tuple[BudgetTarget, ...] = (
        BudgetTarget(ExpenseCategory.DINING_COFFEE, Decimal("260.00")),
        BudgetTarget(ExpenseCategory.SHOPPING, Decimal("1200.00")),
    )
    expense_findings_llm_processor: ExpenseFindingsLlmProcessor = ExpenseFindingsLlmProcessor(
        _FixtureFindingsGateway(), LlmOperationSettings(model="fixture-model", reasoning_effort=None)
    )

    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)
    categorized_lines: list[ExpenseCategorization] = [
        await transaction_categorization_processor.categorize(line.line_number, line.source_text, line.description) for line in parsed_lines
    ]
    categorizations: tuple[ExpenseCategorization, ...] = tuple(categorized_lines)
    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(
        parsed_lines, categorizations, (DUPLICATED_SOURCE_LINE_NUMBER,)
    )
    expense_calculation_result: ExpenseCalculationResult = await ExpenseCalculationProcessor().calculate(transactions, budget_targets)
    findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await expense_findings_llm_processor.findings(expense_calculation_result)

    actual_expense_processing_outcome: ExpenseProcessingOutcome = ExpenseProcessingOutcome(
        parsing_stage_outcome=ParsingStageOutcome.from_parsed_expense_lines(parsed_lines),
        categorization_stage_outcome=CategorizationStageOutcome.from_expense_categorizations(categorizations),
        reconciliation_stage_outcome=ReconciliationStageOutcome.from_reconciled_expenses(transactions, expense_calculation_result.reconciliation),
        calculation_stage_outcome=CalculationStageOutcome.from_expense_calculation_result(expense_calculation_result),
        findings_stage_outcome=FindingsStageOutcome.from_expense_findings(findings),
    )
    ExpenseProcessingOutcomeAsserter.assert_expected_and_actual_are_equal(
        expected_expense_processing_outcome,
        actual_expense_processing_outcome,
        "Every line of the original fixture is parsed, categorized from the model's decision, reconciled to the observed outflow, "
        "totalled by month against the budgets, and summarized in findings grounded in source lines",
    )
