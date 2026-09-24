import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.llm_processors.expense_findings_llm_processor import ExpenseFindingsLlmProcessor
from analyze_expenses_workflow.managers.processors.expense_calculation_processor import ExpenseCalculationProcessor
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.managers.processors.expense_reconciliation_processor import ExpenseReconciliationProcessor
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_calculation_result import BudgetTarget, ExpenseCalculationResult
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_finding import ExpenseFinding
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class _FixtureJevGateway:
    def __init__(self, category_by_source: dict[str, ExpenseCategory]) -> None:
        self._category_by_source: dict[str, ExpenseCategory] = category_by_source

    async def choose(self, state: str, question: ChoiceQuestion) -> ChoiceDecision:
        category: ExpenseCategory = self._category_by_source[state]
        probabilities: dict[str, float] = {name: (1.0 if name == category.value else 0.0) for name in question.criteria}
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
    lines: list[str] = (fixture_directory / "personal_expenses.txt").read_text(encoding="utf-8").splitlines()[3:]
    expected_rows: list[str] = (fixture_directory / "personal_expenses_expected_categories.tsv").read_text(encoding="utf-8").splitlines()[1:]
    category_by_source: dict[str, ExpenseCategory] = {}
    for row, source in zip(expected_rows, lines, strict=True):
        _, category, expected_source = row.split("\t", maxsplit=2)
        assert source == expected_source
        category_by_source[source] = ExpenseCategory(category)
    assert len(lines) == 198
    return lines, category_by_source


async def test_processors_reconcile_the_complete_original_fixture(
    source_fixture: tuple[list[str], dict[str, ExpenseCategory]],
) -> None:
    lines, categories = source_fixture
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(lines)
    categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(_FixtureJevGateway(categories))
    categorized_lines: list[ExpenseCategorization] = [
        await categorization_processor.categorize(line.line_number, line.source_text, line.description) for line in parsed_lines
    ]
    categorizations: tuple[ExpenseCategorization, ...] = tuple(categorized_lines)
    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(parsed_lines, categorizations, (109,))
    budget_targets: tuple[BudgetTarget, ...] = (
        BudgetTarget(ExpenseCategory.DINING_COFFEE, Decimal("260.00")),
        BudgetTarget(ExpenseCategory.SHOPPING, Decimal("1200.00")),
    )
    calculations: ExpenseCalculationResult = await ExpenseCalculationProcessor().calculate(transactions, budget_targets)
    findings_processor: ExpenseFindingsLlmProcessor = ExpenseFindingsLlmProcessor(
        _FixtureFindingsGateway(), LlmOperationSettings(model="fixture-model", reasoning_effort=None)
    )
    findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await findings_processor.findings(calculations)

    assert len(parsed_lines) == len(transactions) == len(categorizations) == 198
    assert all(not line.issues for line in parsed_lines)
    reconciliation = calculations.reconciliation
    assert reconciliation.is_balanced
    assert reconciliation.source_line_count == 198
    assert len(reconciliation.duplicate_line_numbers) == 1
    assert len(reconciliation.unresolved_line_numbers) == 8
    assert not reconciliation.unparsed_line_numbers
    assert len(calculations.monthly_totals) == 3
    assert len(calculations.budget_comparisons) == 6
    assert reconciliation.classified_spending == Decimal("11111.95")
    assert reconciliation.unresolved_outflow == Decimal("646.73")
    assert reconciliation.observed_outflow == Decimal("11758.68")
    monthly_observed: dict[date, Decimal] = {item.month: item.observed_outflow for item in calculations.monthly_totals}
    assert monthly_observed == {
        date(2026, 6, 1): Decimal("3276.71"),
        date(2026, 7, 1): Decimal("2835.78"),
        date(2026, 8, 1): Decimal("5646.19"),
    }
    shopping: dict[date, Decimal] = {
        item.month: item.amount for item in calculations.monthly_category_totals if item.category == ExpenseCategory.SHOPPING
    }
    assert shopping == {
        date(2026, 6, 1): Decimal("572.48"),
        date(2026, 7, 1): Decimal("526.62"),
        date(2026, 8, 1): Decimal("712.98"),
    }
    assert all(item.coverage_note for item in calculations.budget_comparisons if item.category == ExpenseCategory.SHOPPING)
    assert {finding.evidence_id for finding in findings} == {
        "budget-2026-08-01-dining_coffee",
        "change-2026-07-01-2026-08-01-travel",
        "shopping-coverage",
    }
    assert all(finding.source_line_numbers for finding in findings)
    assert any(item.policy_note is not None and item.category == ExpenseCategory.OTHER for item in categorizations)
