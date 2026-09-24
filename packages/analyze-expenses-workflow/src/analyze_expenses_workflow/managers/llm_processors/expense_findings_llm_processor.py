import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar, final, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExpenseLogEvent,
)
from analyze_expenses_workflow.managers.exceptions.expense_findings_exception import ExpenseFindingsException
from analyze_expenses_workflow.managers.llm_processors.bases.llm_processor_base import LlmProcessorBase
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


class _ProposedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)


class _FindingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: tuple[_ProposedFinding, _ProposedFinding, _ProposedFinding]


@final
class _FindingsValidationError(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_RESPONSE_VALIDATION)

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Proposed expense findings failed evidence validation"


@final
class ExpenseFindingsLlmProcessor(LlmProcessorBase):
    _MAX_SEMANTIC_ATTEMPTS: ClassVar[int] = 2
    _FINDING_COUNT: ClassVar[int] = 3

    async def findings(self, calculations: ExpenseCalculationResult) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        await self._publish("progress", "Selecting evidence-based expense findings")
        evidence: tuple[_FindingEvidence, ...] = self._build_evidence(calculations)
        required_ids: tuple[str, ...] = self._required_evidence_ids(calculations, evidence)
        findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await self._select_findings(evidence, required_ids)
        await self._publish("complete", "Three expense findings selected")
        return findings

    async def _select_findings(
        self, evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]
    ) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        prompt: str = self._build_prompt(evidence, required_ids)
        last_error: ValidationError | _FindingsValidationError | None = None

        for _attempt in range(self._MAX_SEMANTIC_ATTEMPTS):
            try:
                return await self._attempt_selection(prompt, evidence, required_ids)
            except (ValidationError, _FindingsValidationError) as exc:
                last_error = exc
                prompt = f"{prompt}\nThe previous response failed validation: {exc}. Return corrected JSON only."

        raise ExpenseFindingsException("The LLM did not return three grounded expense findings") from last_error

    async def _attempt_selection(
        self, prompt: str, evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]
    ) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        response_text: str = await self._complete(prompt)
        response: _FindingsResponse = _FindingsResponse.model_validate_json(self._json_response_text(response_text))
        return self._validated_findings(response, evidence, required_ids)

    @classmethod
    def _build_evidence(cls, calculations: ExpenseCalculationResult) -> tuple[_FindingEvidence, ...]:
        budget_evidence: tuple[_FindingEvidence, ...] = cls._ranked_budget_evidence(calculations.budget_comparisons)
        delta_evidence: tuple[_FindingEvidence, ...] = cls._ranked_change_evidence(calculations.month_category_deltas)
        coverage_evidence: tuple[_FindingEvidence, ...] = cls._shopping_coverage_evidence(calculations.budget_comparisons)
        fallback_evidence: tuple[_FindingEvidence, ...] = cls._fallback_evidence(calculations.reconciliation)
        return (*budget_evidence, *delta_evidence, *coverage_evidence, *fallback_evidence)

    @classmethod
    def _ranked_budget_evidence(cls, budget_comparisons: tuple[BudgetComparison, ...]) -> tuple[_FindingEvidence, ...]:
        ranked: list[BudgetComparison] = sorted(
            budget_comparisons,
            key=lambda item: (-item.variance, item.month, item.category.value),
        )
        return tuple(cls._budget_evidence(comparison) for comparison in ranked if comparison.variance > 0)

    @classmethod
    def _ranked_change_evidence(cls, month_category_deltas: tuple[MonthCategoryDelta, ...]) -> tuple[_FindingEvidence, ...]:
        ranked: list[MonthCategoryDelta] = sorted(
            month_category_deltas,
            key=lambda item: (-abs(item.change), item.to_month, item.category.value),
        )
        return tuple(cls._delta_evidence(delta) for delta in ranked[:5])

    @classmethod
    def _required_evidence_ids(
        cls, calculations: ExpenseCalculationResult, evidence: tuple[_FindingEvidence, ...]
    ) -> tuple[str, ...]:
        candidates: tuple[str | None, ...] = (
            cls._required_dining_evidence_id(calculations.budget_comparisons),
            cls._required_change_evidence_id(calculations.month_category_deltas),
            "shopping-coverage" if any(item.evidence_id == "shopping-coverage" for item in evidence) else None,
        )
        return tuple(dict.fromkeys(candidate for candidate in candidates if candidate is not None))[:3]

    @classmethod
    def _required_dining_evidence_id(cls, budget_comparisons: tuple[BudgetComparison, ...]) -> str | None:
        dining_breaches: tuple[BudgetComparison, ...] = tuple(
            comparison
            for comparison in budget_comparisons
            if comparison.category == ExpenseCategory.DINING_COFFEE and comparison.variance > 0
        )
        if not dining_breaches:
            return None
        breach: BudgetComparison = max(dining_breaches, key=lambda item: (item.variance, item.month))
        return cls._budget_evidence(breach).evidence_id

    @classmethod
    def _required_change_evidence_id(cls, month_category_deltas: tuple[MonthCategoryDelta, ...]) -> str | None:
        if not month_category_deltas:
            return None
        largest_change: MonthCategoryDelta = min(
            month_category_deltas,
            key=lambda item: (-abs(item.change), item.to_month, item.category.value),
        )
        return cls._delta_evidence(largest_change).evidence_id

    @classmethod
    def _budget_evidence(cls, comparison: BudgetComparison) -> _FindingEvidence:
        direction: str = "over" if comparison.variance > 0 else "under"
        statement: str = (
            f"In {cls._month_name(comparison.month)}, {cls._category_name(comparison.category)} was "
            f"{cls._money(comparison.actual)} against a {cls._money(comparison.target)} monthly target, "
            f"{cls._money(abs(comparison.variance))} {direction} target."
        )
        return _FindingEvidence(
            evidence_id=f"budget-{comparison.month.isoformat()}-{comparison.category.value}",
            statement=statement,
            source_line_numbers=comparison.source_line_numbers,
        )

    @classmethod
    def _delta_evidence(cls, delta: MonthCategoryDelta) -> _FindingEvidence:
        direction: str = "rose" if delta.change > 0 else "fell" if delta.change < 0 else "did not change"
        statement: str = (
            f"From {cls._month_name(delta.from_month)} to {cls._month_name(delta.to_month)}, "
            f"{cls._category_name(delta.category)} {direction} by {cls._money(abs(delta.change))}, "
            f"from {cls._money(delta.previous_amount)} to {cls._money(delta.current_amount)}."
        )
        return _FindingEvidence(
            evidence_id=f"change-{delta.from_month.isoformat()}-{delta.to_month.isoformat()}-{delta.category.value}",
            statement=statement,
            source_line_numbers=delta.source_line_numbers,
        )

    @classmethod
    def _shopping_coverage_evidence(cls, budget_comparisons: tuple[BudgetComparison, ...]) -> tuple[_FindingEvidence, ...]:
        comparisons: tuple[BudgetComparison, ...] = tuple(
            comparison for comparison in budget_comparisons if comparison.category == ExpenseCategory.SHOPPING
        )
        affected: tuple[BudgetComparison, ...] = tuple(comparison for comparison in comparisons if comparison.coverage_note is not None)
        if not affected:
            return ()
        under_count: int = sum(comparison.variance < 0 for comparison in comparisons)
        statement: str = (
            f"Shopping was below its stated monthly target in {under_count} of {len(comparisons)} reported months. "
            "These Shopping totals exclude merchant-only mixed-retailer charges assigned to Other, "
            "so the comparison does not cover all shopping-like purchases."
        )
        source_line_numbers: tuple[int, ...] = tuple(
            sorted(
                {
                    number
                    for comparison in affected
                    for number in (*comparison.source_line_numbers, *comparison.coverage_source_line_numbers)
                }
            )
        )
        return (_FindingEvidence("shopping-coverage", statement, source_line_numbers),)

    @classmethod
    def _fallback_evidence(cls, reconciliation: CalculationReconciliation) -> tuple[_FindingEvidence, ...]:
        coverage_statement: str = " ".join(
            (
                f"The analysis accounted for {reconciliation.source_line_count} source lines, including",
                f"{len(reconciliation.duplicate_line_numbers)} excluded duplicates and",
                f"{len(reconciliation.unparsed_line_numbers)} unparsed lines.",
            )
        )
        return (
            _FindingEvidence(
                "reconciliation-spending",
                f"Classified spending totaled {cls._money(reconciliation.classified_spending)} across the reported period.",
                reconciliation.included_line_numbers,
            ),
            _FindingEvidence(
                "reconciliation-unresolved",
                f"Unresolved outflows totaled {cls._money(reconciliation.unresolved_outflow)} and were kept separate from classified spending.",
                reconciliation.unresolved_line_numbers,
            ),
            _FindingEvidence(
                "reconciliation-coverage",
                coverage_statement,
                tuple(range(1, reconciliation.source_line_count + 1)),
            ),
        )

    @staticmethod
    def _build_prompt(evidence: tuple[_FindingEvidence, ...], required_ids: tuple[str, ...]) -> str:
        evidence_items: list[dict[str, object]] = [
            {"evidence_id": item.evidence_id, "statement": item.statement, "source_line_numbers": item.source_line_numbers}
            for item in evidence
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
        response: _FindingsResponse,
        evidence: tuple[_FindingEvidence, ...],
        required_ids: tuple[str, ...],
    ) -> tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]:
        evidence_by_id: dict[str, _FindingEvidence] = {item.evidence_id: item for item in evidence}
        selected_ids: tuple[str, ...] = tuple(item.evidence_id for item in response.findings)
        if len(set(selected_ids)) != cls._FINDING_COUNT:
            raise _FindingsValidationError("Finding evidence IDs must be distinct")
        if any(evidence_id not in evidence_by_id for evidence_id in selected_ids):
            raise _FindingsValidationError("A finding cites evidence that was not supplied")
        if not set(required_ids).issubset(selected_ids):
            raise _FindingsValidationError("Required evidence was omitted")

        findings: list[ExpenseFinding] = []
        for proposal in response.findings:
            item: _FindingEvidence = evidence_by_id[proposal.evidence_id]
            findings.append(
                ExpenseFinding(
                    evidence_id=item.evidence_id,
                    text=item.statement,
                    source_line_numbers=item.source_line_numbers,
                )
            )
        return findings[0], findings[1], findings[2]

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
