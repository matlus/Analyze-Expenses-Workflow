from datetime import date
from decimal import Decimal
from secrets import randbelow, token_hex

import pytest
from acceptance_support.asserter_expense_categorization import ExpenseCategorizationAsserter
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

from analyze_expenses_workflow.models.expense_analysis_result import CategoryProbability, ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory


def create_expense_processing_outcome(source_text: str, evidence_id: str) -> ExpenseProcessingOutcome:
    return ExpenseProcessingOutcome(
        parsing_stage_outcome=ParsingStageOutcome(parsed_line_count=1, line_numbers_with_issues=()),
        categorization_stage_outcome=CategorizationStageOutcome(
            categorization_count=1,
            expense_categorization_decision_by_source_text={
                source_text: ExpenseCategorizationDecision.create_certain(ExpenseCategory.SHOPPING, tuple(ExpenseCategory))
            },
            has_policy_noted_other_categorization=True,
        ),
        reconciliation_stage_outcome=ReconciliationStageOutcome(
            transaction_count=1,
            is_balanced=True,
            source_line_count=1,
            duplicate_line_count=0,
            unresolved_line_count=0,
            unparsed_line_numbers=(),
            classified_spending=Decimal("25.00"),
            unresolved_outflow=Decimal("0.00"),
            observed_outflow=Decimal("25.00"),
        ),
        calculation_stage_outcome=CalculationStageOutcome(
            monthly_total_count=1,
            budget_comparison_count=1,
            observed_outflow_by_month={date(2026, 6, 1): Decimal("25.00")},
            shopping_amount_by_month={date(2026, 6, 1): Decimal("25.00")},
            shopping_budget_months_without_coverage_note=(),
        ),
        findings_stage_outcome=FindingsStageOutcome(evidence_ids=frozenset({evidence_id}), evidence_ids_without_source_lines=()),
    )


def test_ExpenseCategorizationAsserter_WhenSeveralPropertiesDiffer_ThenReportsEveryMismatchInOneFailure() -> None:
    source_line_number: int = randbelow(500) + 1
    source_text: str = f"June 1 {token_hex(4)} $25.00"
    expected_expense_categorization: ExpenseCategorization = ExpenseCategorization(
        source_line_number=source_line_number,
        source_text=source_text,
        category=ExpenseCategory.OTHER,
        model_category=ExpenseCategory.SHOPPING,
        policy_note="Merchant-only mixed retailer charge; item type is not stated",
        confidence=1.0,
        probabilities=(CategoryProbability(category=ExpenseCategory.SHOPPING, probability=1.0),),
    )
    differing_expense_categorization: ExpenseCategorization = ExpenseCategorization(
        source_line_number=source_line_number,
        source_text=source_text,
        category=ExpenseCategory.SHOPPING,
        model_category=ExpenseCategory.SHOPPING,
        policy_note=None,
        confidence=0.5,
        probabilities=(CategoryProbability(category=ExpenseCategory.SHOPPING, probability=0.5),),
    )
    expected_report_phrases: tuple[str, ...] = (
        "4 mismatch(es)",
        "Category: expected other, got shopping",
        "Policy note: expected 'Merchant-only mixed retailer charge; item type is not stated', got None",
        "Confidence: expected 1.0, got 0.5",
        "Probabilities: expected",
        "Business rule: Mixed retailer charges are Other",
    )

    with pytest.raises(AssertionError) as raised_assertion_error:
        ExpenseCategorizationAsserter.assert_expected_and_actual_are_equal(
            expected_expense_categorization, differing_expense_categorization, "Mixed retailer charges are Other"
        )

    actual_report: str = str(raised_assertion_error.value)
    missing_report_phrases: list[str] = [
        expected_report_phrase for expected_report_phrase in expected_report_phrases if expected_report_phrase not in actual_report
    ]
    assert not missing_report_phrases, (
        f"The categorization asserter should report every mismatched property at once; missing {missing_report_phrases} in:\n{actual_report}"
    )


def test_ExpenseProcessingOutcomeAsserter_WhenSeveralStagesDiffer_ThenReportsEveryFailingStageInOneFailure() -> None:
    source_text: str = f"June 1 {token_hex(4)} $25.00"
    expected_evidence_id: str = f"evidence-{token_hex(4)}"
    unexpected_evidence_id: str = f"evidence-{token_hex(4)}"
    expected_expense_processing_outcome: ExpenseProcessingOutcome = create_expense_processing_outcome(source_text, expected_evidence_id)
    differing_expense_processing_outcome: ExpenseProcessingOutcome = ExpenseProcessingOutcome(
        parsing_stage_outcome=ParsingStageOutcome(parsed_line_count=2, line_numbers_with_issues=(2,)),
        categorization_stage_outcome=CategorizationStageOutcome(
            categorization_count=1,
            expense_categorization_decision_by_source_text={
                source_text: ExpenseCategorizationDecision.create_certain(ExpenseCategory.OTHER, tuple(ExpenseCategory))
            },
            has_policy_noted_other_categorization=True,
        ),
        reconciliation_stage_outcome=expected_expense_processing_outcome.reconciliation_stage_outcome,
        calculation_stage_outcome=CalculationStageOutcome(
            monthly_total_count=1,
            budget_comparison_count=1,
            observed_outflow_by_month={date(2026, 6, 1): Decimal("25.00")},
            shopping_amount_by_month={date(2026, 6, 1): Decimal("30.00")},
            shopping_budget_months_without_coverage_note=(),
        ),
        findings_stage_outcome=FindingsStageOutcome(
            evidence_ids=frozenset({unexpected_evidence_id}), evidence_ids_without_source_lines=(unexpected_evidence_id,)
        ),
    )
    expected_report_phrases: tuple[str, ...] = (
        "4 of 5 stages failed",
        "Parsing stage, 2 mismatch(es)",
        "Categorization stage, 1 mismatch(es)",
        "Calculation stage, 1 mismatch(es)",
        "Findings stage, 3 mismatch(es)",
        source_text,
        expected_evidence_id,
        unexpected_evidence_id,
    )

    with pytest.raises(AssertionError) as raised_assertion_error:
        ExpenseProcessingOutcomeAsserter.assert_expected_and_actual_are_equal(
            expected_expense_processing_outcome, differing_expense_processing_outcome, "Every stage matches the fixture"
        )

    actual_report: str = str(raised_assertion_error.value)
    missing_report_phrases: list[str] = [
        expected_report_phrase for expected_report_phrase in expected_report_phrases if expected_report_phrase not in actual_report
    ]
    assert not missing_report_phrases, (
        f"The processing asserter should report every failing stage at once; missing {missing_report_phrases} in:\n{actual_report}"
    )
