import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar, final, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)
from analyze_expenses_workflow.managers.exceptions.expense_findings_exception import ExpenseFindingsException
from analyze_expenses_workflow.managers.gateways.llm_gateway_protocol import LlmGatewayProtocol
from analyze_expenses_workflow.managers.llm_processors.clients.llm_request_client import (
    LlmRequestClient,
    ProcessingEventCallback,
)
from analyze_expenses_workflow.models.expense_calculation_result import (
    BudgetComparison,
    CalculationReconciliation,
    ExpenseCalculationResult,
    MonthCategoryDelta,
)
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_finding import ExpenseFinding


@dataclass(frozen=True, slots=True)
class _FindingEvidence:
    evidence_id: str
    statement: str
    source_line_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _ReconciliationFindingEvidence:
    source_line_count: int
    duplicate_line_numbers: tuple[int, ...]
    unparsed_line_numbers: tuple[int, ...]
    classified_spending: Decimal
    included_line_numbers: tuple[int, ...]
    unresolved_outflow: Decimal
    unresolved_line_numbers: tuple[int, ...]


class _ProposedFinding(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)


class _FindingsResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    proposed_findings: tuple[_ProposedFinding, _ProposedFinding, _ProposedFinding] = Field(alias="findings")


@final
class _FindingsValidationError(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_RESPONSE_VALIDATION, contextual_data_by_name)

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Proposed expense findings failed evidence validation"


@dataclass(frozen=True, slots=True)
class _SelectionSuccess:
    expense_findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]


@dataclass(frozen=True, slots=True)
class _SelectionFailure:
    retry_prompt: str
    error: ValidationError | _FindingsValidationError


type _SelectionAttempt = _SelectionSuccess | _SelectionFailure


@final
# code-review: override[pwi.llm-based-processor-design.base-class-inheritance] - Composes LlmRequestClient; empty base adds no polymorphic contract.
class ExpenseFindingsLlmProcessor:
    _MAX_SEMANTIC_ATTEMPTS: ClassVar[int] = 2
    _FINDING_COUNT: ClassVar[int] = 3

    def __init__(
        self,
        llm_gateway_protocol: LlmGatewayProtocol,
        llm_operation_settings: LlmOperationSettings,
        event_callback: ProcessingEventCallback | None = None,
    ) -> None:
        self._llm_request_client: LlmRequestClient = LlmRequestClient(llm_gateway_protocol, llm_operation_settings, event_callback)

    async def findings(self, expense_calculation_result: ExpenseCalculationResult) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        await self._llm_request_client.publish("progress", "Selecting evidence-based expense findings")
        finding_evidences: tuple[_FindingEvidence, ...] = self._build_evidence(expense_calculation_result)
        required_ids: tuple[str, ...] = self._required_evidence_ids(expense_calculation_result, finding_evidences)
        expense_findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await self._select_findings(finding_evidences, required_ids)
        await self._llm_request_client.publish("complete", "Three expense findings selected")
        return expense_findings

    async def _select_findings(
        self, evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]
    ) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        prompt: str = self._build_prompt(evidence, required_ids)
        return await self._retry_selection(prompt, evidence, required_ids)

    async def _retry_selection(
        self, prompt: str, evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]
    ) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        selection_attempt: _SelectionAttempt = await self._selection_attempt(prompt, evidence, required_ids)
        for _attempt in range(self._MAX_SEMANTIC_ATTEMPTS - 1):
            if isinstance(selection_attempt, _SelectionSuccess):
                return selection_attempt.expense_findings
            prompt = selection_attempt.retry_prompt
            selection_attempt = await self._selection_attempt(prompt, evidence, required_ids)
        if isinstance(selection_attempt, _SelectionSuccess):
            return selection_attempt.expense_findings

        raise ExpenseFindingsException(
            "The LLM did not return three grounded expense findings",
            {
                "Operation": "findings_selection",
                "AttemptCount": self._MAX_SEMANTIC_ATTEMPTS,
                "RequiredEvidenceIds": str(required_ids),
            },
        ) from selection_attempt.error

    async def _selection_attempt(self, prompt: str, evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]) -> _SelectionAttempt:
        try:
            expense_findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await self._attempt_selection(prompt, evidence, required_ids)
            return _SelectionSuccess(expense_findings)
        except (ValidationError, _FindingsValidationError) as exc:
            retry_prompt: str = f"{prompt}\nThe previous response failed validation: {exc}. Return corrected JSON only."
            return _SelectionFailure(retry_prompt, exc)

    async def _attempt_selection(
        self, prompt: str, evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]
    ) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        response_text: str = await self._llm_request_client.complete(prompt)
        findings_response: _FindingsResponse = _FindingsResponse.model_validate_json(LlmRequestClient.json_response_text(response_text))
        return self._validated_findings(findings_response, evidence, required_ids)

    @classmethod
    def _build_evidence(cls, expense_calculation_result: ExpenseCalculationResult) -> tuple[_FindingEvidence, ...]:
        budget_evidence: tuple[_FindingEvidence, ...] = cls._ranked_budget_evidence(expense_calculation_result.budget_comparisons)
        delta_evidence: tuple[_FindingEvidence, ...] = cls._ranked_change_evidence(expense_calculation_result.month_category_deltas)
        coverage_evidence: tuple[_FindingEvidence, ...] = cls._shopping_coverage_evidence(expense_calculation_result.budget_comparisons)
        reconciliation_finding_evidence: _ReconciliationFindingEvidence = cls._reconciliation_finding_evidence(
            expense_calculation_result.reconciliation
        )
        fallback_evidence: tuple[_FindingEvidence, ...] = cls._fallback_evidence(reconciliation_finding_evidence)
        return (*budget_evidence, *delta_evidence, *coverage_evidence, *fallback_evidence)

    @staticmethod
    def _reconciliation_finding_evidence(calculation_reconciliation: CalculationReconciliation) -> _ReconciliationFindingEvidence:
        return _ReconciliationFindingEvidence(
            source_line_count=calculation_reconciliation.source_line_count,
            duplicate_line_numbers=calculation_reconciliation.duplicate_line_numbers,
            unparsed_line_numbers=calculation_reconciliation.unparsed_line_numbers,
            classified_spending=calculation_reconciliation.classified_spending,
            included_line_numbers=calculation_reconciliation.included_line_numbers,
            unresolved_outflow=calculation_reconciliation.unresolved_outflow,
            unresolved_line_numbers=calculation_reconciliation.unresolved_line_numbers,
        )

    @classmethod
    def _ranked_budget_evidence(cls, budget_comparisons: tuple[BudgetComparison, ...]) -> tuple[_FindingEvidence, ...]:
        ranked_budget_comparisons: list[BudgetComparison] = sorted(
            budget_comparisons,
            key=lambda budget_comparison: (-budget_comparison.variance, budget_comparison.month, budget_comparison.category.value),
        )
        return tuple(cls._budget_evidence(budget_comparison) for budget_comparison in ranked_budget_comparisons if budget_comparison.variance > 0)

    @classmethod
    def _ranked_change_evidence(cls, month_category_deltas: tuple[MonthCategoryDelta, ...]) -> tuple[_FindingEvidence, ...]:
        ranked_month_category_deltas: list[MonthCategoryDelta] = sorted(
            month_category_deltas,
            key=lambda month_category_delta: (-abs(month_category_delta.change), month_category_delta.to_month, month_category_delta.category.value),
        )
        return tuple(cls._delta_evidence(month_category_delta) for month_category_delta in ranked_month_category_deltas[:5])

    @classmethod
    def _required_evidence_ids(cls, expense_calculation_result: ExpenseCalculationResult, evidence: tuple[_FindingEvidence, ...]) -> tuple[str, ...]:
        candidates: tuple[str | None, ...] = (
            cls._required_dining_evidence_id(expense_calculation_result.budget_comparisons),
            cls._required_change_evidence_id(expense_calculation_result.month_category_deltas),
            "shopping-coverage" if any(finding_evidence.evidence_id == "shopping-coverage" for finding_evidence in evidence) else None,
        )
        return tuple(dict.fromkeys(candidate for candidate in candidates if candidate is not None))[:3]

    @classmethod
    def _required_dining_evidence_id(cls, budget_comparisons: tuple[BudgetComparison, ...]) -> str | None:
        dining_breaches: tuple[BudgetComparison, ...] = tuple(
            budget_comparison
            for budget_comparison in budget_comparisons
            if budget_comparison.category == ExpenseCategory.DINING_COFFEE and budget_comparison.variance > 0
        )
        if not dining_breaches:
            return None
        budget_comparison: BudgetComparison = max(
            dining_breaches, key=lambda budget_comparison: (budget_comparison.variance, budget_comparison.month)
        )
        return cls._budget_evidence(budget_comparison).evidence_id

    @classmethod
    def _required_change_evidence_id(cls, month_category_deltas: tuple[MonthCategoryDelta, ...]) -> str | None:
        if not month_category_deltas:
            return None
        month_category_delta: MonthCategoryDelta = min(
            month_category_deltas,
            key=lambda month_category_delta: (-abs(month_category_delta.change), month_category_delta.to_month, month_category_delta.category.value),
        )
        return cls._delta_evidence(month_category_delta).evidence_id

    @classmethod
    def _budget_evidence(cls, budget_comparison: BudgetComparison) -> _FindingEvidence:
        direction: str = "over" if budget_comparison.variance > 0 else "under"
        statement: str = (
            f"In {cls._month_name(budget_comparison.month)}, {cls._category_name(budget_comparison.category)} was "
            f"{cls._money(budget_comparison.actual)} against a {cls._money(budget_comparison.target)} monthly target, "
            f"{cls._money(abs(budget_comparison.variance))} {direction} target."
        )
        return _FindingEvidence(
            evidence_id=f"budget-{budget_comparison.month.isoformat()}-{budget_comparison.category.value}",
            statement=statement,
            source_line_numbers=budget_comparison.source_line_numbers,
        )

    @classmethod
    def _delta_evidence(cls, month_category_delta: MonthCategoryDelta) -> _FindingEvidence:
        direction: str = "rose" if month_category_delta.change > 0 else "fell" if month_category_delta.change < 0 else "did not change"
        statement: str = (
            f"From {cls._month_name(month_category_delta.from_month)} to {cls._month_name(month_category_delta.to_month)}, "
            f"{cls._category_name(month_category_delta.category)} {direction} by {cls._money(abs(month_category_delta.change))}, "
            f"from {cls._money(month_category_delta.previous_amount)} to {cls._money(month_category_delta.current_amount)}."
        )
        return _FindingEvidence(
            evidence_id=f"change-{month_category_delta.from_month.isoformat()}-{month_category_delta.to_month.isoformat()}-{month_category_delta.category.value}",
            statement=statement,
            source_line_numbers=month_category_delta.source_line_numbers,
        )

    @classmethod
    def _shopping_coverage_evidence(cls, budget_comparisons: tuple[BudgetComparison, ...]) -> tuple[_FindingEvidence, ...]:
        shopping_budget_comparisons: tuple[BudgetComparison, ...] = tuple(
            budget_comparison for budget_comparison in budget_comparisons if budget_comparison.category == ExpenseCategory.SHOPPING
        )
        affected_budget_comparisons: tuple[BudgetComparison, ...] = tuple(
            budget_comparison for budget_comparison in shopping_budget_comparisons if budget_comparison.coverage_note is not None
        )
        if not affected_budget_comparisons:
            return ()
        under_count: int = sum(budget_comparison.variance < 0 for budget_comparison in shopping_budget_comparisons)
        statement: str = (
            f"Shopping was below its stated monthly target in {under_count} of {len(shopping_budget_comparisons)} reported months. "
            "These Shopping totals exclude merchant-only mixed-retailer charges assigned to Other, "
            "so the comparison does not cover all shopping-like purchases."
        )
        source_line_numbers: tuple[int, ...] = tuple(
            sorted(
                {
                    number
                    for budget_comparison in shopping_budget_comparisons
                    for number in (*budget_comparison.source_line_numbers, *budget_comparison.coverage_source_line_numbers)
                }
            )
        )
        return (_FindingEvidence("shopping-coverage", statement, source_line_numbers),)

    @classmethod
    def _fallback_evidence(cls, reconciliation_evidence: _ReconciliationFindingEvidence) -> tuple[_FindingEvidence, ...]:
        coverage_statement: str = " ".join(
            (
                f"The analysis accounted for {reconciliation_evidence.source_line_count} source lines, including",
                f"{len(reconciliation_evidence.duplicate_line_numbers)} excluded duplicates and",
                f"{len(reconciliation_evidence.unparsed_line_numbers)} unparsed lines.",
            )
        )
        return (
            _FindingEvidence(
                "reconciliation-spending",
                f"Classified spending totaled {cls._money(reconciliation_evidence.classified_spending)} across the reported period.",
                reconciliation_evidence.included_line_numbers,
            ),
            _FindingEvidence(
                "reconciliation-unresolved",
                f"Unresolved outflows totaled {cls._money(reconciliation_evidence.unresolved_outflow)} "
                "and were kept separate from classified spending.",
                reconciliation_evidence.unresolved_line_numbers,
            ),
            _FindingEvidence(
                "reconciliation-coverage",
                coverage_statement,
                tuple(range(1, reconciliation_evidence.source_line_count + 1)),
            ),
        )

    @staticmethod
    def _build_prompt(evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]) -> str:
        evidence_items: list[dict[str, object]] = [
            {
                "evidence_id": finding_evidence.evidence_id,
                "statement": finding_evidence.statement,
                "source_line_numbers": finding_evidence.source_line_numbers,
            }
            for finding_evidence in evidence
        ]
        return (
            "Choose exactly three distinct evidence IDs from the supplied calculated evidence. "
            "Include every required evidence_id. Prefer material budget breaches, the largest month-to-month change, "
            "and category coverage limitations. Do not write findings or calculate numbers. "
            "The application will render the exact evidence statements. Return one JSON object only.\n"
            f"schema: {json.dumps(_FindingsResponse.model_json_schema())}\n"
            f"required_evidence_ids: {json.dumps(required_ids)}\n"
            f"candidate_evidence: {json.dumps(evidence_items)}"
        )

    @classmethod
    def _validated_findings(
        cls,
        findings_response: _FindingsResponse,
        evidence: tuple[_FindingEvidence, ...],
        required_ids: tuple[str, ...],
    ) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        finding_evidence_by_evidence_id: dict[str, _FindingEvidence] = {
            finding_evidence.evidence_id: finding_evidence for finding_evidence in evidence
        }
        selected_evidence_ids: tuple[str, ...] = tuple(proposed_finding.evidence_id for proposed_finding in findings_response.proposed_findings)
        if len(set(selected_evidence_ids)) != cls._FINDING_COUNT:
            raise _FindingsValidationError(
                f"Finding evidence IDs must be distinct; received: {selected_evidence_ids}",
                {"ValidationStage": "distinct_evidence_ids", "ReceivedEvidenceIds": str(selected_evidence_ids)},
            )
        unsupported_evidence_ids: tuple[str, ...] = tuple(
            evidence_id for evidence_id in selected_evidence_ids if evidence_id not in finding_evidence_by_evidence_id
        )
        if unsupported_evidence_ids:
            raise _FindingsValidationError(
                f"Unsupported finding evidence IDs: {unsupported_evidence_ids}; supplied IDs: {tuple(finding_evidence_by_evidence_id)}",
                {
                    "ValidationStage": "supported_evidence_ids",
                    "UnsupportedEvidenceIds": str(unsupported_evidence_ids),
                    "SuppliedEvidenceIds": str(tuple(finding_evidence_by_evidence_id)),
                },
            )
        omitted_evidence_ids: tuple[str, ...] = tuple(evidence_id for evidence_id in required_ids if evidence_id not in selected_evidence_ids)
        if omitted_evidence_ids:
            raise _FindingsValidationError(
                f"Required finding evidence IDs omitted: {omitted_evidence_ids}; received: {selected_evidence_ids}",
                {
                    "ValidationStage": "required_evidence_ids",
                    "OmittedEvidenceIds": str(omitted_evidence_ids),
                    "ReceivedEvidenceIds": str(selected_evidence_ids),
                },
            )

        expense_findings: list[ExpenseFinding] = []
        for proposed_finding in findings_response.proposed_findings:
            finding_evidence: _FindingEvidence = finding_evidence_by_evidence_id[proposed_finding.evidence_id]
            expense_findings.append(
                ExpenseFinding(
                    evidence_id=finding_evidence.evidence_id,
                    text=finding_evidence.statement,
                    source_line_numbers=finding_evidence.source_line_numbers,
                )
            )
        return expense_findings[0], expense_findings[1], expense_findings[2]

    @staticmethod
    def _category_name(category: ExpenseCategory) -> str:
        if category == ExpenseCategory.DINING_COFFEE:
            return "Dining & Coffee"
        return category.value.replace("_", " ").title()

    @staticmethod
    def _month_name(month: date) -> str:
        return month.strftime("%B %Y")

    @staticmethod
    def _money(amount: Decimal) -> str:
        return f"${amount:,.2f}"
