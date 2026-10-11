from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Self

from analyze_expenses_workflow.models.expense_analysis_result import CategoryProbability, ExpenseCategorization
from analyze_expenses_workflow.models.expense_calculation_result import CalculationReconciliation, ExpenseCalculationResult
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_finding import ExpenseFinding
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


@dataclass(frozen=True, slots=True)
class ExpenseCategorizationDecision:
    """The decision drivers behind one categorization: what the model chose and how sure it was of every category."""

    model_category: ExpenseCategory
    confidence: float
    probabilities: tuple[CategoryProbability, ...]

    @classmethod
    def create_certain(cls, model_category: ExpenseCategory, candidate_categories: Sequence[ExpenseCategory]) -> Self:
        return cls(
            model_category=model_category,
            confidence=1.0,
            probabilities=tuple(
                CategoryProbability(category=candidate_category, probability=1.0 if candidate_category is model_category else 0.0)
                for candidate_category in candidate_categories
            ),
        )

    @classmethod
    def from_expense_categorization(cls, expense_categorization: ExpenseCategorization) -> Self:
        return cls(
            model_category=expense_categorization.model_category,
            confidence=expense_categorization.confidence,
            probabilities=expense_categorization.probabilities,
        )


@dataclass(frozen=True, slots=True)
class ParsingStageOutcome:
    parsed_line_count: int
    line_numbers_with_issues: tuple[int, ...]

    @classmethod
    def from_parsed_expense_lines(cls, parsed_expense_lines: Sequence[ParsedExpenseLine]) -> Self:
        return cls(
            parsed_line_count=len(parsed_expense_lines),
            line_numbers_with_issues=tuple(
                parsed_expense_line.line_number for parsed_expense_line in parsed_expense_lines if parsed_expense_line.issues
            ),
        )


@dataclass(frozen=True, slots=True)
class CategorizationStageOutcome:
    categorization_count: int
    expense_categorization_decision_by_source_text: Mapping[str, ExpenseCategorizationDecision]
    has_policy_noted_other_categorization: bool

    @classmethod
    def from_expense_categorizations(cls, expense_categorizations: Sequence[ExpenseCategorization]) -> Self:
        return cls(
            categorization_count=len(expense_categorizations),
            expense_categorization_decision_by_source_text={
                expense_categorization.source_text: ExpenseCategorizationDecision.from_expense_categorization(expense_categorization)
                for expense_categorization in expense_categorizations
            },
            has_policy_noted_other_categorization=any(
                expense_categorization.policy_note is not None and expense_categorization.category is ExpenseCategory.OTHER
                for expense_categorization in expense_categorizations
            ),
        )


@dataclass(frozen=True, slots=True)
class ReconciliationStageOutcome:
    transaction_count: int
    is_balanced: bool
    source_line_count: int
    duplicate_line_count: int
    unresolved_line_count: int
    unparsed_line_numbers: tuple[int, ...]
    classified_spending: Decimal
    unresolved_outflow: Decimal
    observed_outflow: Decimal

    @classmethod
    def from_reconciled_expenses(
        cls, expense_transactions: Sequence[ExpenseTransaction], calculation_reconciliation: CalculationReconciliation
    ) -> Self:
        return cls(
            transaction_count=len(expense_transactions),
            is_balanced=calculation_reconciliation.is_balanced,
            source_line_count=calculation_reconciliation.source_line_count,
            duplicate_line_count=len(calculation_reconciliation.duplicate_line_numbers),
            unresolved_line_count=len(calculation_reconciliation.unresolved_line_numbers),
            unparsed_line_numbers=calculation_reconciliation.unparsed_line_numbers,
            classified_spending=calculation_reconciliation.classified_spending,
            unresolved_outflow=calculation_reconciliation.unresolved_outflow,
            observed_outflow=calculation_reconciliation.observed_outflow,
        )


@dataclass(frozen=True, slots=True)
class CalculationStageOutcome:
    monthly_total_count: int
    budget_comparison_count: int
    observed_outflow_by_month: Mapping[date, Decimal]
    shopping_amount_by_month: Mapping[date, Decimal]
    shopping_budget_months_without_coverage_note: tuple[date, ...]

    @classmethod
    def from_expense_calculation_result(cls, expense_calculation_result: ExpenseCalculationResult) -> Self:
        return cls(
            monthly_total_count=len(expense_calculation_result.monthly_totals),
            budget_comparison_count=len(expense_calculation_result.budget_comparisons),
            observed_outflow_by_month={
                monthly_total.month: monthly_total.observed_outflow for monthly_total in expense_calculation_result.monthly_totals
            },
            shopping_amount_by_month={
                monthly_category_total.month: monthly_category_total.amount
                for monthly_category_total in expense_calculation_result.monthly_category_totals
                if monthly_category_total.category is ExpenseCategory.SHOPPING
            },
            shopping_budget_months_without_coverage_note=tuple(
                budget_comparison.month
                for budget_comparison in expense_calculation_result.budget_comparisons
                if budget_comparison.category is ExpenseCategory.SHOPPING and not budget_comparison.coverage_note
            ),
        )


@dataclass(frozen=True, slots=True)
class FindingsStageOutcome:
    evidence_ids: frozenset[str]
    evidence_ids_without_source_lines: tuple[str, ...]

    @classmethod
    def from_expense_findings(cls, expense_findings: Sequence[ExpenseFinding]) -> Self:
        return cls(
            evidence_ids=frozenset(expense_finding.evidence_id for expense_finding in expense_findings),
            evidence_ids_without_source_lines=tuple(
                expense_finding.evidence_id for expense_finding in expense_findings if not expense_finding.source_line_numbers
            ),
        )


@dataclass(frozen=True, slots=True)
class ExpenseProcessingOutcome:
    """What every processor stage produced for one expense file, as one comparable observation."""

    parsing_stage_outcome: ParsingStageOutcome
    categorization_stage_outcome: CategorizationStageOutcome
    reconciliation_stage_outcome: ReconciliationStageOutcome
    calculation_stage_outcome: CalculationStageOutcome
    findings_stage_outcome: FindingsStageOutcome


class ExpenseProcessingOutcomeAsserter:
    """Compares every processor stage and reports each failing stage, with all of its mismatches, in one failure."""

    @staticmethod
    def assert_expected_and_actual_are_equal(
        expected_expense_processing_outcome: ExpenseProcessingOutcome,
        actual_expense_processing_outcome: ExpenseProcessingOutcome,
        business_rule_description: str,
    ) -> None:
        stage_failure_reports: tuple[str | None, ...] = (
            ExpenseProcessingOutcomeAsserter._get_stage_failure(
                "Parsing",
                ExpenseProcessingOutcomeAsserter._find_parsing_stage_mismatches(
                    expected_expense_processing_outcome.parsing_stage_outcome, actual_expense_processing_outcome.parsing_stage_outcome
                ),
            ),
            ExpenseProcessingOutcomeAsserter._get_stage_failure(
                "Categorization",
                ExpenseProcessingOutcomeAsserter._find_categorization_stage_mismatches(
                    expected_expense_processing_outcome.categorization_stage_outcome,
                    actual_expense_processing_outcome.categorization_stage_outcome,
                ),
            ),
            ExpenseProcessingOutcomeAsserter._get_stage_failure(
                "Reconciliation",
                ExpenseProcessingOutcomeAsserter._find_reconciliation_stage_mismatches(
                    expected_expense_processing_outcome.reconciliation_stage_outcome,
                    actual_expense_processing_outcome.reconciliation_stage_outcome,
                ),
            ),
            ExpenseProcessingOutcomeAsserter._get_stage_failure(
                "Calculation",
                ExpenseProcessingOutcomeAsserter._find_calculation_stage_mismatches(
                    expected_expense_processing_outcome.calculation_stage_outcome, actual_expense_processing_outcome.calculation_stage_outcome
                ),
            ),
            ExpenseProcessingOutcomeAsserter._get_stage_failure(
                "Findings",
                ExpenseProcessingOutcomeAsserter._find_findings_stage_mismatches(
                    expected_expense_processing_outcome.findings_stage_outcome, actual_expense_processing_outcome.findings_stage_outcome
                ),
            ),
        )
        failed_stage_reports: tuple[str, ...] = tuple(
            stage_failure_report for stage_failure_report in stage_failure_reports if stage_failure_report is not None
        )
        assert not failed_stage_reports, (
            f"EXPENSE PROCESSING ASSERTION FAILED: {len(failed_stage_reports)} of {len(stage_failure_reports)} stages failed\n\n"
            + "\n\n".join(failed_stage_reports)
            + f"\n\nBusiness rule: {business_rule_description}"
        )

    @staticmethod
    def _get_stage_failure(stage_name: str, stage_mismatches: list[str]) -> str | None:
        if not stage_mismatches:
            return None
        return f"{stage_name} stage, {len(stage_mismatches)} mismatch(es):\n" + "\n".join(
            f"  {stage_mismatch}" for stage_mismatch in stage_mismatches
        )

    @staticmethod
    def _find_parsing_stage_mismatches(
        expected_parsing_stage_outcome: ParsingStageOutcome, actual_parsing_stage_outcome: ParsingStageOutcome
    ) -> list[str]:
        mismatches: list[str] = []
        if actual_parsing_stage_outcome.parsed_line_count != expected_parsing_stage_outcome.parsed_line_count:
            mismatches.append(
                f"Parsed line count: expected {expected_parsing_stage_outcome.parsed_line_count}, "
                f"got {actual_parsing_stage_outcome.parsed_line_count}"
            )
        if actual_parsing_stage_outcome.line_numbers_with_issues != expected_parsing_stage_outcome.line_numbers_with_issues:
            mismatches.append(
                f"Lines with parse issues: expected {expected_parsing_stage_outcome.line_numbers_with_issues}, "
                f"got {actual_parsing_stage_outcome.line_numbers_with_issues}"
            )
        return mismatches

    @staticmethod
    def _find_categorization_stage_mismatches(
        expected_categorization_stage_outcome: CategorizationStageOutcome, actual_categorization_stage_outcome: CategorizationStageOutcome
    ) -> list[str]:
        mismatches: list[str] = []
        if actual_categorization_stage_outcome.categorization_count != expected_categorization_stage_outcome.categorization_count:
            mismatches.append(
                f"Categorization count: expected {expected_categorization_stage_outcome.categorization_count}, "
                f"got {actual_categorization_stage_outcome.categorization_count}"
            )
        mismatches.extend(
            ExpenseProcessingOutcomeAsserter._find_mapping_mismatches(
                "Decision drivers for",
                expected_categorization_stage_outcome.expense_categorization_decision_by_source_text,
                actual_categorization_stage_outcome.expense_categorization_decision_by_source_text,
            )
        )
        if (
            actual_categorization_stage_outcome.has_policy_noted_other_categorization
            is not expected_categorization_stage_outcome.has_policy_noted_other_categorization
        ):
            mismatches.append(
                f"Policy-noted Other categorization present: expected {expected_categorization_stage_outcome.has_policy_noted_other_categorization}, "
                f"got {actual_categorization_stage_outcome.has_policy_noted_other_categorization}"
            )
        return mismatches

    @staticmethod
    def _find_reconciliation_stage_mismatches(
        expected_reconciliation_stage_outcome: ReconciliationStageOutcome, actual_reconciliation_stage_outcome: ReconciliationStageOutcome
    ) -> list[str]:
        compared_values: tuple[tuple[str, object, object], ...] = (
            ("Transaction count", expected_reconciliation_stage_outcome.transaction_count, actual_reconciliation_stage_outcome.transaction_count),
            ("Balanced", expected_reconciliation_stage_outcome.is_balanced, actual_reconciliation_stage_outcome.is_balanced),
            ("Source line count", expected_reconciliation_stage_outcome.source_line_count, actual_reconciliation_stage_outcome.source_line_count),
            (
                "Duplicate line count",
                expected_reconciliation_stage_outcome.duplicate_line_count,
                actual_reconciliation_stage_outcome.duplicate_line_count,
            ),
            (
                "Unresolved line count",
                expected_reconciliation_stage_outcome.unresolved_line_count,
                actual_reconciliation_stage_outcome.unresolved_line_count,
            ),
            (
                "Unparsed line numbers",
                expected_reconciliation_stage_outcome.unparsed_line_numbers,
                actual_reconciliation_stage_outcome.unparsed_line_numbers,
            ),
            (
                "Classified spending",
                expected_reconciliation_stage_outcome.classified_spending,
                actual_reconciliation_stage_outcome.classified_spending,
            ),
            ("Unresolved outflow", expected_reconciliation_stage_outcome.unresolved_outflow, actual_reconciliation_stage_outcome.unresolved_outflow),
            ("Observed outflow", expected_reconciliation_stage_outcome.observed_outflow, actual_reconciliation_stage_outcome.observed_outflow),
        )
        return [
            f"{compared_value[0]}: expected {compared_value[1]!r}, got {compared_value[2]!r}"
            for compared_value in compared_values
            if compared_value[1] != compared_value[2]
        ]

    @staticmethod
    def _find_calculation_stage_mismatches(
        expected_calculation_stage_outcome: CalculationStageOutcome, actual_calculation_stage_outcome: CalculationStageOutcome
    ) -> list[str]:
        mismatches: list[str] = []
        if actual_calculation_stage_outcome.monthly_total_count != expected_calculation_stage_outcome.monthly_total_count:
            mismatches.append(
                f"Monthly total count: expected {expected_calculation_stage_outcome.monthly_total_count}, "
                f"got {actual_calculation_stage_outcome.monthly_total_count}"
            )
        if actual_calculation_stage_outcome.budget_comparison_count != expected_calculation_stage_outcome.budget_comparison_count:
            mismatches.append(
                f"Budget comparison count: expected {expected_calculation_stage_outcome.budget_comparison_count}, "
                f"got {actual_calculation_stage_outcome.budget_comparison_count}"
            )
        mismatches.extend(
            ExpenseProcessingOutcomeAsserter._find_mapping_mismatches(
                "Observed outflow for",
                expected_calculation_stage_outcome.observed_outflow_by_month,
                actual_calculation_stage_outcome.observed_outflow_by_month,
            )
        )
        mismatches.extend(
            ExpenseProcessingOutcomeAsserter._find_mapping_mismatches(
                "Shopping amount for",
                expected_calculation_stage_outcome.shopping_amount_by_month,
                actual_calculation_stage_outcome.shopping_amount_by_month,
            )
        )
        if (
            actual_calculation_stage_outcome.shopping_budget_months_without_coverage_note
            != expected_calculation_stage_outcome.shopping_budget_months_without_coverage_note
        ):
            mismatches.append(
                "Shopping budget months without a coverage note: "
                f"expected {expected_calculation_stage_outcome.shopping_budget_months_without_coverage_note}, "
                f"got {actual_calculation_stage_outcome.shopping_budget_months_without_coverage_note}"
            )
        return mismatches

    @staticmethod
    def _find_findings_stage_mismatches(
        expected_findings_stage_outcome: FindingsStageOutcome, actual_findings_stage_outcome: FindingsStageOutcome
    ) -> list[str]:
        mismatches: list[str] = []
        missing_evidence_ids: frozenset[str] = expected_findings_stage_outcome.evidence_ids - actual_findings_stage_outcome.evidence_ids
        unexpected_evidence_ids: frozenset[str] = actual_findings_stage_outcome.evidence_ids - expected_findings_stage_outcome.evidence_ids
        if missing_evidence_ids:
            mismatches.append(f"Missing finding evidence ids: {sorted(missing_evidence_ids)}")
        if unexpected_evidence_ids:
            mismatches.append(f"Unexpected finding evidence ids: {sorted(unexpected_evidence_ids)}")
        if actual_findings_stage_outcome.evidence_ids_without_source_lines != expected_findings_stage_outcome.evidence_ids_without_source_lines:
            mismatches.append(
                f"Findings without source lines: expected {expected_findings_stage_outcome.evidence_ids_without_source_lines}, "
                f"got {actual_findings_stage_outcome.evidence_ids_without_source_lines}"
            )
        return mismatches

    @staticmethod
    def _find_mapping_mismatches[KeyT, ValueT](
        entry_label: str, expected_value_by_key: Mapping[KeyT, ValueT], actual_value_by_key: Mapping[KeyT, ValueT]
    ) -> list[str]:
        mismatches: list[str] = []
        expected_key: KeyT
        for expected_key in expected_value_by_key:
            if expected_key not in actual_value_by_key:
                mismatches.append(f"{entry_label} {expected_key!r}: expected {expected_value_by_key[expected_key]!r}, got nothing")
            elif actual_value_by_key[expected_key] != expected_value_by_key[expected_key]:
                mismatches.append(
                    f"{entry_label} {expected_key!r}: expected {expected_value_by_key[expected_key]!r}, got {actual_value_by_key[expected_key]!r}"
                )
        mismatches.extend(
            f"{entry_label} {actual_key!r}: expected nothing, got {actual_value_by_key[actual_key]!r}"
            for actual_key in actual_value_by_key
            if actual_key not in expected_value_by_key
        )
        return mismatches
