import asyncio
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from analyze_expenses_workflow.domain_facades import JevRequestFailedException
from analyze_expenses_workflow.models import (
    BudgetComparison,
    CalculationReconciliation,
    ExpenseAnalysisResult,
    ExpenseCalculationResult,
    ExpenseCategorization,
    ExpenseCategory,
    ExpenseFinding,
    ExpenseTransaction,
    MonthCategoryDelta,
    MonthlyCategoryTotal,
    ParsedExpenseLine,
    TransactionTreatment,
)
from expense_analysis_workflow_eval import (
    AcceptedPolicy,
    AmountCheck,
    BucketTotal,
    BudgetOutcome,
    BudgetStatusReader,
    BudgetTargetsCheck,
    ChangeOutcome,
    CheckGrade,
    CheckStatus,
    CompletedJudgment,
    DuplicateExcludedReader,
    ErroredJudgment,
    FactValueCheck,
    GradingReference,
    JudgeCheck,
    LargestIncreaseReader,
    LedgerBucket,
    LedgerCategoryCheck,
    LedgerEntry,
    LedgerLinesCheck,
    LineCategorization,
    LineOutcome,
    ReferenceLedger,
    RefundTreatmentReader,
    WorkflowOutcome,
    describe_workflow_failure,
    grade_amount_check,
    grade_budget_targets_check,
    grade_fact_value_check,
    grade_judge_check,
    grade_ledger_category_check,
    grade_ledger_lines_check,
    project_workflow_outcome,
)

PERIOD: str = "2026-07"
NEXT_PERIOD: str = "2026-08"
UNCATEGORIZED: str = "Uncategorized"
INCLUDED_SPENDING: str = TransactionTreatment.INCLUDED_SPENDING.value


def create_amount() -> Decimal:
    return Decimal(secrets.randbelow(90_000) + 1_000) / 100


def create_merchant() -> str:
    return f"Merchant {secrets.token_hex(4)}"


def create_source_text() -> str:
    return f"{create_merchant()} - ${create_amount()}"


def create_date_in(period: str) -> date:
    return date.fromisoformat(f"{period}-{secrets.randbelow(28) + 1:02d}")


def create_fact_name() -> str:
    return f"fact_{secrets.token_hex(4)}"


def create_check_name() -> str:
    return f"check-{secrets.token_hex(4)}"


def create_check_description() -> str:
    return f"A check {secrets.token_hex(4)}."


def create_ledger_entry(line_number: int, category: str, amount: Decimal, *, period: str = PERIOD, is_counted: bool = True) -> LedgerEntry:
    merchant: str = create_merchant()
    return LedgerEntry(
        line_number=line_number,
        source_text=f"{merchant} - ${amount}",
        merchant=merchant,
        occurred_on=create_date_in(period),
        amount=amount,
        category=category,
        accepted_policy=None,
        is_counted=is_counted,
        note=None if is_counted else f"likely duplicate {secrets.token_hex(4)}",
    )


def create_line_outcome(
    ledger_entry: LedgerEntry, category: str | None = None, treatment: str = INCLUDED_SPENDING, policy_note: str | None = None
) -> LineOutcome:
    line_category: str = category if category is not None else ledger_entry.category
    return LineOutcome(
        line_number=ledger_entry.line_number,
        source_text=ledger_entry.source_text,
        merchant=ledger_entry.merchant,
        occurred_on=ledger_entry.occurred_on,
        amount=ledger_entry.amount,
        treatment=treatment,
        line_categorization=LineCategorization(category=line_category, model_category=ledger_entry.category, policy_note=policy_note),
    )


def create_workflow_outcome(
    line_outcomes: Sequence[LineOutcome], budget_outcomes: Sequence[BudgetOutcome] = (), change_outcomes: Sequence[ChangeOutcome] = ()
) -> WorkflowOutcome:
    """Totals summed from the included lines, by category and month, as the workflow's calculation step sums them."""
    line_outcomes_by_bucket: dict[tuple[str, str], list[LineOutcome]] = {}
    for line_outcome in line_outcomes:
        if line_outcome.treatment == INCLUDED_SPENDING and line_outcome.category is not None and line_outcome.period is not None:
            line_outcomes_by_bucket.setdefault((line_outcome.category, line_outcome.period), []).append(line_outcome)
    bucket_totals: list[BucketTotal] = []
    bucket_key: tuple[str, str]
    bucket_line_outcomes: list[LineOutcome]
    for bucket_key, bucket_line_outcomes in line_outcomes_by_bucket.items():
        bucket_totals.append(
            BucketTotal(
                category=bucket_key[0],
                period=bucket_key[1],
                amount=sum((bucket_line_outcome.amount or Decimal(0) for bucket_line_outcome in bucket_line_outcomes), Decimal(0)),
                line_numbers=tuple(bucket_line_outcome.line_number for bucket_line_outcome in bucket_line_outcomes),
            )
        )
    return WorkflowOutcome(
        line_outcomes=tuple(line_outcomes),
        bucket_totals=tuple(bucket_totals),
        budget_outcomes=tuple(budget_outcomes),
        change_outcomes=tuple(change_outcomes),
        finding_texts=(),
    )


def create_grading_reference(
    ledger_entries: Sequence[LedgerEntry], expected_fact_values_by_fact_name: Mapping[str, tuple[str, ...]]
) -> GradingReference:
    ledger_bucket_by_fact_name: dict[str, LedgerBucket] = {}
    return GradingReference(expected_fact_values_by_fact_name, ReferenceLedger(tuple(ledger_entries), ledger_bucket_by_fact_name), UNCATEGORIZED)


def create_amount_grading_reference(
    ledger_entries: Sequence[LedgerEntry], fact_name: str, ledger_bucket: LedgerBucket, expected_amount: Decimal
) -> GradingReference:
    return GradingReference({fact_name: (str(expected_amount),)}, ReferenceLedger(tuple(ledger_entries), {fact_name: ledger_bucket}), UNCATEGORIZED)


def assert_check_grade(
    scenario: str,
    actual_check_grade: CheckGrade,
    expected_check_status: CheckStatus,
    expected_reason_phrases: Sequence[str],
    expected_cause_phrases: Sequence[str],
    unexpected_cause_phrases: Sequence[str] = (),
    *,
    expected_cause_count: int | None = None,
) -> None:
    mismatches: list[str] = []
    if actual_check_grade.check_status != expected_check_status:
        mismatches.append(f"Expected status {expected_check_status.value}, got {actual_check_grade.check_status.value}")
    mismatches.extend(
        f"Expected the reason to contain {expected_reason_phrase!r}; reason: {actual_check_grade.reason!r}"
        for expected_reason_phrase in expected_reason_phrases
        if expected_reason_phrase not in actual_check_grade.reason
    )
    actual_cause_text: str = "\n".join(f"{cause.summary} | {cause.description}" for cause in actual_check_grade.causes)
    mismatches.extend(
        f"Expected a cause to contain {expected_cause_phrase!r}; causes: {actual_cause_text!r}"
        for expected_cause_phrase in expected_cause_phrases
        if expected_cause_phrase not in actual_cause_text
    )
    mismatches.extend(
        f"Expected no cause to contain {unexpected_cause_phrase!r}; causes: {actual_cause_text!r}"
        for unexpected_cause_phrase in unexpected_cause_phrases
        if unexpected_cause_phrase in actual_cause_text
    )
    if not expected_cause_phrases and actual_check_grade.causes:
        mismatches.append(f"Expected no traced cause, got {actual_cause_text!r}")
    if expected_cause_count is not None and len(actual_check_grade.causes) != expected_cause_count:
        mismatches.append(f"Expected {expected_cause_count} traced causes, got {len(actual_check_grade.causes)}: {actual_cause_text!r}")
    assert not mismatches, f"{scenario}: the check grade has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def assert_workflow_outcome(scenario: str, actual_workflow_outcome: WorkflowOutcome, expected_workflow_outcome: WorkflowOutcome) -> None:
    compared_items_by_field_name: dict[str, tuple[Sequence[object], Sequence[object]]] = {
        "line_outcomes": (expected_workflow_outcome.line_outcomes, actual_workflow_outcome.line_outcomes),
        "bucket_totals": (expected_workflow_outcome.bucket_totals, actual_workflow_outcome.bucket_totals),
        "budget_outcomes": (expected_workflow_outcome.budget_outcomes, actual_workflow_outcome.budget_outcomes),
        "change_outcomes": (expected_workflow_outcome.change_outcomes, actual_workflow_outcome.change_outcomes),
        "finding_texts": (expected_workflow_outcome.finding_texts, actual_workflow_outcome.finding_texts),
    }
    mismatches: list[str] = []
    field_name: str
    expected_field_values: Sequence[object]
    actual_field_values: Sequence[object]
    position: int
    for field_name, (expected_field_values, actual_field_values) in compared_items_by_field_name.items():
        if len(actual_field_values) != len(expected_field_values):
            mismatches.append(f"Expected {len(expected_field_values)} {field_name}, got {len(actual_field_values)}")
        for position in range(max(len(expected_field_values), len(actual_field_values))):
            if position >= len(actual_field_values):
                mismatches.append(f"{field_name} item {position + 1}: expected {expected_field_values[position]!r}, missing")
            elif position >= len(expected_field_values):
                mismatches.append(f"{field_name} item {position + 1}: unexpected {actual_field_values[position]!r}")
            elif actual_field_values[position] != expected_field_values[position]:
                mismatches.append(
                    f"{field_name} item {position + 1}: expected {expected_field_values[position]!r}, got {actual_field_values[position]!r}"
                )
    assert not mismatches, f"{scenario}: the workflow outcome has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def test_AmountCheck_WhenWorkflowTotalMatchesAnAcceptedValue_ThenPassesWithoutCause() -> None:
    reference_category: str = "Shopping"
    fact_name: str = create_fact_name()
    first_ledger_entry: LedgerEntry = create_ledger_entry(10, reference_category, create_amount())
    second_ledger_entry: LedgerEntry = create_ledger_entry(11, reference_category, create_amount())
    expected_total: Decimal = first_ledger_entry.amount + second_ledger_entry.amount
    grading_reference: GradingReference = create_amount_grading_reference(
        (first_ledger_entry, second_ledger_entry), fact_name, LedgerBucket(reference_category, PERIOD), expected_total
    )
    workflow_outcome: WorkflowOutcome = create_workflow_outcome([create_line_outcome(first_ledger_entry), create_line_outcome(second_ledger_entry)])
    amount_check: AmountCheck = AmountCheck(create_check_name(), create_check_description(), 0.8, None, fact_name=fact_name)
    expected_reason_phrases: list[str] = [f"reported {expected_total} for {reference_category} {PERIOD}"]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_amount_check(amount_check, grading_reference, workflow_outcome)

    assert_check_grade("A matching total", actual_check_grade, CheckStatus.PASSED, expected_reason_phrases, expected_cause_phrases)


def test_AmountCheck_WhenWorkflowPolicyPutsAReferenceLineInAnotherCategory_ThenFailsAndTracesTheLineAndTheDecision() -> None:
    reference_category: str = "Shopping"
    workflow_category: str = "Other"
    policy_note: str = f"Merchant-only mixed retailer charge {secrets.token_hex(4)}"
    fact_name: str = create_fact_name()
    kept_ledger_entry: LedgerEntry = create_ledger_entry(10, reference_category, create_amount())
    moved_ledger_entry: LedgerEntry = create_ledger_entry(12, reference_category, create_amount())
    expected_total: Decimal = kept_ledger_entry.amount + moved_ledger_entry.amount
    grading_reference: GradingReference = create_amount_grading_reference(
        (kept_ledger_entry, moved_ledger_entry), fact_name, LedgerBucket(reference_category, PERIOD), expected_total
    )
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [create_line_outcome(kept_ledger_entry), create_line_outcome(moved_ledger_entry, category=workflow_category, policy_note=policy_note)]
    )
    amount_check: AmountCheck = AmountCheck(create_check_name(), create_check_description(), 0.8, None, fact_name=fact_name)
    expected_reason_phrases: list[str] = [f"Expected {expected_total}", f"reported {kept_ledger_entry.amount}"]
    expected_cause_count: int = 1
    expected_cause_phrases: list[str] = [
        f'line {moved_ledger_entry.line_number} "{moved_ledger_entry.source_text}": the workflow put it in {workflow_category} {PERIOD}',
        f"(Jev chose {reference_category}; the workflow's policy decided: {policy_note}); the reference puts it in {reference_category}",
        f"a difference of {-moved_ledger_entry.amount:+}",
    ]

    actual_check_grade: CheckGrade = grade_amount_check(amount_check, grading_reference, workflow_outcome)

    assert_check_grade(
        "A line moved by policy",
        actual_check_grade,
        CheckStatus.FAILED,
        expected_reason_phrases,
        expected_cause_phrases,
        expected_cause_count=expected_cause_count,
    )


def test_AmountCheck_WhenWorkflowCountsALineTheReferenceDoesNotCount_ThenTracesTheExtraLine() -> None:
    reference_category: str = "Groceries"
    fact_name: str = create_fact_name()
    counted_ledger_entry: LedgerEntry = create_ledger_entry(20, reference_category, create_amount())
    duplicate_ledger_entry: LedgerEntry = create_ledger_entry(21, reference_category, counted_ledger_entry.amount, is_counted=False)
    grading_reference: GradingReference = create_amount_grading_reference(
        (counted_ledger_entry, duplicate_ledger_entry), fact_name, LedgerBucket(reference_category, PERIOD), counted_ledger_entry.amount
    )
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [create_line_outcome(counted_ledger_entry), create_line_outcome(duplicate_ledger_entry)]
    )
    amount_check: AmountCheck = AmountCheck(create_check_name(), create_check_description(), 0.8, None, fact_name=fact_name)
    expected_reason_phrases: list[str] = [f"Expected {counted_ledger_entry.amount}"]
    expected_cause_count: int = 1
    expected_cause_phrases: list[str] = [
        f"line {duplicate_ledger_entry.line_number}",
        f"the workflow counted it in {reference_category}; the reference does not count it ({duplicate_ledger_entry.note})",
    ]

    actual_check_grade: CheckGrade = grade_amount_check(amount_check, grading_reference, workflow_outcome)

    assert_check_grade(
        "An uncounted duplicate",
        actual_check_grade,
        CheckStatus.FAILED,
        expected_reason_phrases,
        expected_cause_phrases,
        expected_cause_count=expected_cause_count,
    )


def test_FactValueCheck_WhenRefundIsIncludedAsANegativeAmount_ThenReadsItAsNetted() -> None:
    refund_ledger_entry: LedgerEntry = create_ledger_entry(108, "Shopping", -create_amount())
    fact_name: str = create_fact_name()
    expected_fact_value: str = "netted_against_spending"
    grading_reference: GradingReference = create_grading_reference((refund_ledger_entry,), {fact_name: (expected_fact_value,)})
    workflow_outcome: WorkflowOutcome = create_workflow_outcome([create_line_outcome(refund_ledger_entry)])
    fact_value_check: FactValueCheck = FactValueCheck(
        create_check_name(), create_check_description(), 0.8, None, fact_name, RefundTreatmentReader(refund_ledger_entry.line_number)
    )
    expected_reason_phrases: list[str] = [f"gives {expected_fact_value}", f"amount {refund_ledger_entry.amount}"]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_fact_value_check(fact_value_check, grading_reference, workflow_outcome)

    assert_check_grade("A netted refund", actual_check_grade, CheckStatus.PASSED, expected_reason_phrases, expected_cause_phrases)


def test_FactValueCheck_WhenRefundIsLeftUncategorized_ThenFailsNamingTheLineTreatment() -> None:
    refund_ledger_entry: LedgerEntry = create_ledger_entry(108, "Shopping", -create_amount())
    fact_name: str = create_fact_name()
    uncategorized_credit: str = TransactionTreatment.UNCATEGORIZED_CREDIT.value
    grading_reference: GradingReference = create_grading_reference((refund_ledger_entry,), {fact_name: ("netted_against_spending",)})
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [create_line_outcome(refund_ledger_entry, category=UNCATEGORIZED, treatment=uncategorized_credit)]
    )
    fact_value_check: FactValueCheck = FactValueCheck(
        create_check_name(), create_check_description(), 0.8, None, fact_name, RefundTreatmentReader(refund_ledger_entry.line_number)
    )
    expected_reason_phrases: list[str] = ["gives excluded", f"line {refund_ledger_entry.line_number}", f"{uncategorized_credit} in {UNCATEGORIZED}"]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_fact_value_check(fact_value_check, grading_reference, workflow_outcome)

    assert_check_grade("An uncategorized refund", actual_check_grade, CheckStatus.FAILED, expected_reason_phrases, expected_cause_phrases)


def test_FactValueCheck_WhenConfirmedDuplicateIsExcluded_ThenReadsTrue() -> None:
    duplicate_ledger_entry: LedgerEntry = create_ledger_entry(112, "Groceries", create_amount(), is_counted=False)
    fact_name: str = create_fact_name()
    excluded_duplicate: str = TransactionTreatment.EXCLUDED_DUPLICATE.value
    grading_reference: GradingReference = create_grading_reference((duplicate_ledger_entry,), {fact_name: ("true",)})
    workflow_outcome: WorkflowOutcome = create_workflow_outcome([create_line_outcome(duplicate_ledger_entry, treatment=excluded_duplicate)])
    fact_value_check: FactValueCheck = FactValueCheck(
        create_check_name(), create_check_description(), 0.8, None, fact_name, DuplicateExcludedReader(duplicate_ledger_entry.line_number)
    )
    expected_reason_phrases: list[str] = ["gives true", excluded_duplicate]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_fact_value_check(fact_value_check, grading_reference, workflow_outcome)

    assert_check_grade("An excluded duplicate", actual_check_grade, CheckStatus.PASSED, expected_reason_phrases, expected_cause_phrases)


def test_FactValueCheck_WhenDiningIsOverItsTarget_ThenReadsOver() -> None:
    reference_category: str = "Dining and coffee"
    fact_name: str = create_fact_name()
    target: Decimal = create_amount()
    actual_spending: Decimal = target + create_amount()
    grading_reference: GradingReference = create_grading_reference((), {fact_name: ("over",)})
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [], budget_outcomes=[BudgetOutcome(reference_category, PERIOD, target, actual_spending)]
    )
    fact_value_check: FactValueCheck = FactValueCheck(
        create_check_name(), create_check_description(), 0.8, None, fact_name, BudgetStatusReader(reference_category, PERIOD)
    )
    expected_reason_phrases: list[str] = ["gives over", f"actual {actual_spending} against target {target}"]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_fact_value_check(fact_value_check, grading_reference, workflow_outcome)

    assert_check_grade("A category over its target", actual_check_grade, CheckStatus.PASSED, expected_reason_phrases, expected_cause_phrases)


def test_FactValueCheck_WhenAnotherCategoryRisesMost_ThenTracesTheWrongTotalsButNotTheUncategorizedLines() -> None:
    expected_category: str = "Travel"
    workflow_category: str = "Other"
    fact_name: str = create_fact_name()
    travel_ledger_entry: LedgerEntry = create_ledger_entry(167, expected_category, create_amount() + Decimal(1000), period=NEXT_PERIOD)
    uber_ledger_entry: LedgerEntry = create_ledger_entry(173, expected_category, create_amount(), period=NEXT_PERIOD)
    transfer_ledger_entry: LedgerEntry = create_ledger_entry(150, UNCATEGORIZED, create_amount(), period=NEXT_PERIOD)
    grading_reference: GradingReference = create_grading_reference(
        (travel_ledger_entry, uber_ledger_entry, transfer_ledger_entry), {fact_name: (expected_category.lower(),)}
    )
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [
            create_line_outcome(travel_ledger_entry, category=workflow_category),
            create_line_outcome(uber_ledger_entry),
            create_line_outcome(transfer_ledger_entry, treatment=TransactionTreatment.UNCATEGORIZED_OUTFLOW.value),
        ],
        change_outcomes=[
            ChangeOutcome(workflow_category, PERIOD, NEXT_PERIOD, travel_ledger_entry.amount),
            ChangeOutcome(expected_category, PERIOD, NEXT_PERIOD, uber_ledger_entry.amount),
        ],
    )
    fact_value_check: FactValueCheck = FactValueCheck(
        create_check_name(), create_check_description(), 0.8, None, fact_name, LargestIncreaseReader(PERIOD, NEXT_PERIOD)
    )
    expected_reason_phrases: list[str] = [
        f"gives {workflow_category}",
        f"largest increases: {workflow_category} {travel_ledger_entry.amount:+}, {expected_category} {uber_ledger_entry.amount:+}",
    ]
    expected_cause_phrases: list[str] = [
        f"line {travel_ledger_entry.line_number}",
        f"the workflow put it in {workflow_category} {NEXT_PERIOD}",
        f"the reference puts it in {expected_category}",
    ]
    unexpected_cause_phrases: list[str] = [f"line {transfer_ledger_entry.line_number}"]

    actual_check_grade: CheckGrade = grade_fact_value_check(fact_value_check, grading_reference, workflow_outcome)

    assert_check_grade(
        "A wrong largest increase", actual_check_grade, CheckStatus.FAILED, expected_reason_phrases, expected_cause_phrases, unexpected_cause_phrases
    )


def test_LedgerCategoryCheck_WhenATransferIsForcedIntoASpendingCategory_ThenFailsNamingTheLine() -> None:
    forced_category: str = "Dining and coffee"
    kept_ledger_entry: LedgerEntry = create_ledger_entry(44, UNCATEGORIZED, create_amount())
    forced_ledger_entry: LedgerEntry = create_ledger_entry(48, UNCATEGORIZED, create_amount())
    reference_ledger: ReferenceLedger = ReferenceLedger((kept_ledger_entry, forced_ledger_entry), {})
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [
            create_line_outcome(kept_ledger_entry, treatment=TransactionTreatment.UNCATEGORIZED_OUTFLOW.value),
            create_line_outcome(forced_ledger_entry, category=forced_category),
        ]
    )
    ledger_category_check: LedgerCategoryCheck = LedgerCategoryCheck(create_check_name(), create_check_description(), 0.8, None, UNCATEGORIZED)
    reference_line_numbers: list[int] = [kept_ledger_entry.line_number, forced_ledger_entry.line_number]
    expected_reason_phrases: list[str] = [f"{len(reference_line_numbers)} {UNCATEGORIZED} lines {reference_line_numbers}", "1 lines differ"]
    expected_cause_phrases: list[str] = [
        f'line {forced_ledger_entry.line_number} "{forced_ledger_entry.source_text}": the workflow put it in {forced_category} {PERIOD}',
        f"the reference puts it in {UNCATEGORIZED}",
    ]

    actual_check_grade: CheckGrade = grade_ledger_category_check(ledger_category_check, reference_ledger, workflow_outcome.line_outcomes)

    assert_check_grade("A forced transfer", actual_check_grade, CheckStatus.FAILED, expected_reason_phrases, expected_cause_phrases)


def test_LedgerCategoryCheck_WhenTheWorkflowExcludesTheDuplicateAndUsesAnAcceptedCategory_ThenPasses() -> None:
    reference_category: str = "Travel"
    alternative_category: str = "Transport"
    counted_ledger_entry: LedgerEntry = create_ledger_entry(60, reference_category, create_amount())
    duplicate_ledger_entry: LedgerEntry = create_ledger_entry(61, reference_category, counted_ledger_entry.amount, is_counted=False)
    alternative_ledger_entry: LedgerEntry = replace(
        create_ledger_entry(62, reference_category, create_amount()),
        accepted_policy=AcceptedPolicy(f"policy {secrets.token_hex(4)}", (alternative_category,)),
    )
    reference_ledger: ReferenceLedger = ReferenceLedger((counted_ledger_entry, duplicate_ledger_entry, alternative_ledger_entry), {})
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [
            create_line_outcome(counted_ledger_entry),
            create_line_outcome(duplicate_ledger_entry, treatment=TransactionTreatment.EXCLUDED_DUPLICATE.value),
            create_line_outcome(alternative_ledger_entry, category=alternative_category),
        ]
    )
    ledger_category_check: LedgerCategoryCheck = LedgerCategoryCheck(create_check_name(), create_check_description(), 1.0, None, reference_category)
    expected_reason_phrases: list[str] = ["0 lines differ"]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_ledger_category_check(ledger_category_check, reference_ledger, workflow_outcome.line_outcomes)

    assert_check_grade(
        "An excluded duplicate and an accepted category", actual_check_grade, CheckStatus.PASSED, expected_reason_phrases, expected_cause_phrases
    )


def test_BudgetTargetsCheck_WhenWorkflowComparesAnUnstatedCategory_ThenFailsNamingTheInventedTarget() -> None:
    stated_category: str = "Dining and coffee"
    stated_target: Decimal = Decimal("260.00")
    invented_category: str = "Groceries"
    invented_target: Decimal = create_amount()
    budget_outcomes: list[BudgetOutcome] = [
        BudgetOutcome(stated_category, PERIOD, stated_target, create_amount()),
        BudgetOutcome(invented_category, PERIOD, invented_target, create_amount()),
    ]
    budget_targets_check: BudgetTargetsCheck = BudgetTargetsCheck(
        create_check_name(), create_check_description(), 0.8, None, {stated_category: stated_target}
    )
    expected_reason_phrases: list[str] = [f"invented ['{invented_category} {invented_target}']", "missing []"]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_budget_targets_check(budget_targets_check, budget_outcomes)

    assert_check_grade("An invented budget", actual_check_grade, CheckStatus.FAILED, expected_reason_phrases, expected_cause_phrases)


def test_JudgeCheck_WhenTheJudgeSessionFailed_ThenErrorsInsteadOfFailing() -> None:
    failure_description: str = f"the judge session failed {secrets.token_hex(4)}"
    judge_check: JudgeCheck = JudgeCheck(create_check_name(), create_check_description(), 0.8, None, rubric_text=secrets.token_hex(8))
    expected_reason_phrases: list[str] = [failure_description]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_judge_check(judge_check.description, ErroredJudgment(failure_description))

    assert_check_grade("A failed judge session", actual_check_grade, CheckStatus.ERRORED, expected_reason_phrases, expected_cause_phrases)


def test_DescribeWorkflowFailure_WhenAGatewayWrapsTheProviderError_ThenNamesEveryExceptionInTheChain() -> None:
    gateway_message: str = f"subscription request failed {secrets.token_hex(4)}"
    provider_message: str = f"model not supported {secrets.token_hex(4)}"
    workflow_failure: ValueError = ValueError(gateway_message)
    workflow_failure.__cause__ = RuntimeError(provider_message)
    expected_description: str = f"ValueError: {gateway_message}; caused by RuntimeError: {provider_message}"

    actual_description: str = describe_workflow_failure(workflow_failure)

    assert actual_description == expected_description, f"expected {expected_description!r}; actual {actual_description!r}"


async def test_DescribeWorkflowFailure_WhenATaskGroupCollectsAJevFailure_ThenNamesTheJevExceptionAndItsCause() -> None:
    jev_request_message: str = f"Jev via OpenRouter categorization request failed {secrets.token_hex(4)}"
    provider_message: str = f"provider unavailable {secrets.token_hex(4)}"

    async def fail_jev_request() -> None:
        raise JevRequestFailedException(jev_request_message, {"Operation": "categorization_request"}) from RuntimeError(provider_message)

    with pytest.raises(ExceptionGroup) as raised_exception:
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(fail_jev_request())
    workflow_failure: ExceptionGroup[Exception] = raised_exception.value
    expected_description: str = (
        "ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception); "
        f"including JevRequestFailedException: {jev_request_message}; caused by RuntimeError: {provider_message}"
    )

    actual_description: str = describe_workflow_failure(workflow_failure)

    assert actual_description == expected_description, f"expected {expected_description!r}; actual {actual_description!r}"


def test_DescribeWorkflowFailure_WhenAnExceptionGroupCollectsSeveralFailures_ThenNamesEachFailureAndItsCause() -> None:
    group_message: str = f"unhandled errors {secrets.token_hex(4)}"
    first_message: str = f"first failure {secrets.token_hex(4)}"
    provider_message: str = f"provider overloaded {secrets.token_hex(4)}"
    second_message: str = f"second failure {secrets.token_hex(4)}"
    first_failure: ValueError = ValueError(first_message)
    first_failure.__cause__ = RuntimeError(provider_message)
    workflow_failure: ExceptionGroup[Exception] = ExceptionGroup(group_message, [first_failure, TypeError(second_message)])
    expected_description: str = (
        f"ExceptionGroup: {group_message} (2 sub-exceptions); including ValueError: {first_message}; "
        f"caused by RuntimeError: {provider_message}; including TypeError: {second_message}"
    )

    actual_description: str = describe_workflow_failure(workflow_failure)

    assert actual_description == expected_description, f"expected {expected_description!r}; actual {actual_description!r}"


def test_LedgerLinesCheck_WhenEveryLineMatchesTheReference_ThenPassesWithoutCause() -> None:
    ledger_entries: list[LedgerEntry] = [
        create_ledger_entry(1, "Groceries", create_amount()),
        create_ledger_entry(2, "Dining and coffee", create_amount()),
    ]
    ledger_lines_check: LedgerLinesCheck = LedgerLinesCheck(create_check_name(), create_check_description(), 1.0, None)
    workflow_outcome: WorkflowOutcome = create_workflow_outcome([create_line_outcome(ledger_entry) for ledger_entry in ledger_entries])
    expected_reason_phrases: list[str] = ["Compared the reference's 2 lines with the workflow's 2; 0 lines differ."]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_ledger_lines_check(
        ledger_lines_check, ReferenceLedger(tuple(ledger_entries), {}), workflow_outcome.line_outcomes
    )

    assert_check_grade("Every line matching", actual_check_grade, CheckStatus.PASSED, expected_reason_phrases, expected_cause_phrases)


def test_LedgerLinesCheck_WhenALineIsReadWithTheWrongDateOrMerchant_ThenFailsNamingWhatWasMisread() -> None:
    reference_category: str = "Groceries"
    misdated_ledger_entry: LedgerEntry = create_ledger_entry(1, reference_category, create_amount())
    misnamed_ledger_entry: LedgerEntry = create_ledger_entry(2, reference_category, create_amount())
    misread_date: date = misdated_ledger_entry.occurred_on.replace(day=misdated_ledger_entry.occurred_on.day % 28 + 1)
    misread_merchant: str = f"Return processed - {misnamed_ledger_entry.merchant}"
    ledger_lines_check: LedgerLinesCheck = LedgerLinesCheck(create_check_name(), create_check_description(), 1.0, None)
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [
            replace(create_line_outcome(misdated_ledger_entry), occurred_on=misread_date),
            replace(create_line_outcome(misnamed_ledger_entry), merchant=misread_merchant),
        ]
    )
    expected_reason_phrases: list[str] = ["Compared the reference's 2 lines with the workflow's 2; 2 lines differ."]
    expected_cause_phrases: list[str] = [
        (
            f'line 1 "{misdated_ledger_entry.source_text}": the workflow read the date as {misread_date}; '
            f"the reference reads {misdated_ledger_entry.occurred_on}"
        ),
        (
            f'line 2 "{misnamed_ledger_entry.source_text}": the workflow read the merchant as {misread_merchant!r}; '
            f"the reference reads {misnamed_ledger_entry.merchant!r}"
        ),
    ]

    actual_check_grade: CheckGrade = grade_ledger_lines_check(
        ledger_lines_check, ReferenceLedger((misdated_ledger_entry, misnamed_ledger_entry), {}), workflow_outcome.line_outcomes
    )

    assert_check_grade("A misread date and merchant", actual_check_grade, CheckStatus.FAILED, expected_reason_phrases, expected_cause_phrases)


def test_LedgerLinesCheck_WhenLinesAreMisreadMiscategorizedMiscountedOrMissing_ThenFailsNamingEachLine() -> None:
    reference_category: str = "Groceries"
    workflow_category: str = "Shopping"
    misread_ledger_entry: LedgerEntry = create_ledger_entry(1, reference_category, create_amount())
    miscategorized_ledger_entry: LedgerEntry = create_ledger_entry(2, reference_category, create_amount())
    duplicate_ledger_entry: LedgerEntry = create_ledger_entry(3, reference_category, create_amount(), is_counted=False)
    missing_ledger_entry: LedgerEntry = create_ledger_entry(4, reference_category, create_amount())
    misread_amount: Decimal = -misread_ledger_entry.amount
    ledger_lines_check: LedgerLinesCheck = LedgerLinesCheck(create_check_name(), create_check_description(), 1.0, None)
    reference_ledger: ReferenceLedger = ReferenceLedger(
        (misread_ledger_entry, miscategorized_ledger_entry, duplicate_ledger_entry, missing_ledger_entry), {}
    )
    workflow_outcome: WorkflowOutcome = create_workflow_outcome(
        [
            replace(create_line_outcome(misread_ledger_entry), amount=misread_amount),
            create_line_outcome(miscategorized_ledger_entry, category=workflow_category),
            create_line_outcome(duplicate_ledger_entry),
        ]
    )
    expected_reason_phrases: list[str] = ["Compared the reference's 4 lines with the workflow's 3; 4 lines differ."]
    expected_cause_phrases: list[str] = [
        (
            f'line 1 "{misread_ledger_entry.source_text}": the workflow read the amount as {misread_amount}; '
            f"the reference reads {misread_ledger_entry.amount}"
        ),
        (
            f'line 2 "{miscategorized_ledger_entry.source_text}": the workflow put it in {workflow_category}; '
            f"the reference puts it in {reference_category}"
        ),
        f'line 3 "{duplicate_ledger_entry.source_text}": the workflow counted it; the reference does not',
        f'line 4 "{missing_ledger_entry.source_text}": the workflow did not report it',
    ]

    actual_check_grade: CheckGrade = grade_ledger_lines_check(ledger_lines_check, reference_ledger, workflow_outcome.line_outcomes)

    assert_check_grade("Four wrong lines", actual_check_grade, CheckStatus.FAILED, expected_reason_phrases, expected_cause_phrases)


def test_LedgerLinesCheck_WhenALineIsInACategoryTheKeyAcceptsAsAnAlternative_ThenPasses() -> None:
    alternative_category: str = "Transport"
    ledger_entry: LedgerEntry = replace(
        create_ledger_entry(1, "Travel", create_amount()),
        accepted_policy=AcceptedPolicy(f"policy {secrets.token_hex(4)}", (alternative_category,)),
    )
    ledger_lines_check: LedgerLinesCheck = LedgerLinesCheck(create_check_name(), create_check_description(), 1.0, None)
    workflow_outcome: WorkflowOutcome = create_workflow_outcome([create_line_outcome(ledger_entry, category=alternative_category)])
    expected_reason_phrases: list[str] = ["0 lines differ."]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_ledger_lines_check(
        ledger_lines_check, ReferenceLedger((ledger_entry,), {}), workflow_outcome.line_outcomes
    )

    assert_check_grade("An accepted alternative category", actual_check_grade, CheckStatus.PASSED, expected_reason_phrases, expected_cause_phrases)


def test_JudgeCheck_WhenTheJudgeRejectsTheFindings_ThenFailsQuotingTheJudge() -> None:
    judge_reason: str = f"Finding two contradicts the table {secrets.token_hex(4)}"
    judge_check: JudgeCheck = JudgeCheck(create_check_name(), create_check_description(), 0.8, None, rubric_text=secrets.token_hex(8))
    expected_reason_phrases: list[str] = [f"The judge said: {judge_reason}"]
    expected_cause_phrases: list[str] = []

    actual_check_grade: CheckGrade = grade_judge_check(judge_check.description, CompletedJudgment(passed=False, reason=judge_reason))

    assert_check_grade("A rejected finding", actual_check_grade, CheckStatus.FAILED, expected_reason_phrases, expected_cause_phrases)


def test_WorkflowOutcome_WhenProjectedFromAResult_ThenUsesInputFileLineNumbersReferenceNamesAndDecisionDrivers() -> None:
    other_category_name: str = "Other"
    shopping_category_name: str = "Shopping"
    policy_note: str = f"Merchant-only mixed retailer charge {secrets.token_hex(4)}"
    first_expense_line_number: int = 4
    source_text: str = create_source_text()
    amount: Decimal = create_amount()
    month: date = date(2026, 7, 1)
    previous_month: date = date(2026, 6, 1)
    target: Decimal = create_amount()
    first_finding_text: str = f"finding {secrets.token_hex(4)}"
    second_finding_text: str = f"finding {secrets.token_hex(4)}"
    third_finding_text: str = f"finding {secrets.token_hex(4)}"
    input_line_offset: int = first_expense_line_number - 1
    merchant: str = create_merchant()
    occurred_on: date = date(2026, 7, 13)
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, occurred_on, merchant, amount, ())
    expense_categorization: ExpenseCategorization = ExpenseCategorization(
        1, source_text, ExpenseCategory.OTHER, ExpenseCategory.SHOPPING, policy_note, 0.9, ()
    )
    expense_analysis_result: ExpenseAnalysisResult = ExpenseAnalysisResult(
        categorizations=(expense_categorization,),
        parsed_lines=(parsed_expense_line,),
        transactions=(ExpenseTransaction(parsed_expense_line, expense_categorization, TransactionTreatment.INCLUDED_SPENDING, None),),
        calculations=ExpenseCalculationResult(
            monthly_category_totals=(MonthlyCategoryTotal(month, ExpenseCategory.OTHER, amount, (1,)),),
            monthly_totals=(),
            budget_comparisons=(BudgetComparison(month, ExpenseCategory.SHOPPING, target, Decimal(0), -target, (), None),),
            month_category_deltas=(MonthCategoryDelta(previous_month, month, ExpenseCategory.OTHER, Decimal(0), amount, amount, (1,)),),
            reconciliation=CalculationReconciliation(1, (1,), (), (), (), amount, Decimal(0), amount, is_balanced=True),
        ),
        findings=(
            ExpenseFinding(secrets.token_hex(4), first_finding_text, (1,)),
            ExpenseFinding(secrets.token_hex(4), second_finding_text, (1,)),
            ExpenseFinding(secrets.token_hex(4), third_finding_text, (1,)),
        ),
        method="",
    )
    reference_category_by_workflow_category: dict[ExpenseCategory, str] = {
        ExpenseCategory.OTHER: other_category_name,
        ExpenseCategory.SHOPPING: shopping_category_name,
    }
    expected_workflow_outcome: WorkflowOutcome = WorkflowOutcome(
        line_outcomes=(
            LineOutcome(
                first_expense_line_number,
                source_text,
                merchant,
                occurred_on,
                amount,
                INCLUDED_SPENDING,
                LineCategorization(other_category_name, shopping_category_name, policy_note),
            ),
        ),
        bucket_totals=(BucketTotal(other_category_name, PERIOD, amount, (first_expense_line_number,)),),
        budget_outcomes=(BudgetOutcome(shopping_category_name, PERIOD, target, Decimal(0)),),
        change_outcomes=(ChangeOutcome(other_category_name, f"{previous_month:%Y-%m}", PERIOD, amount),),
        finding_texts=(first_finding_text, second_finding_text, third_finding_text),
    )

    actual_workflow_outcome: WorkflowOutcome = project_workflow_outcome(
        expense_analysis_result, input_line_offset, reference_category_by_workflow_category
    )

    assert_workflow_outcome("A categorized line under a policy", actual_workflow_outcome, expected_workflow_outcome)
