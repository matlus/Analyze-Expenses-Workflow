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
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException
from analyze_expenses_workflow.managers.gateways.llm_gateway_protocol import LlmGatewayProtocol
from analyze_expenses_workflow.managers.llm_processors.clients.llm_request_client import (
    LlmRequestClient,
    ProcessingEventCallback,
)
from analyze_expenses_workflow.managers.processors.evidence_parsers.expense_line_evidence_parser import (
    AmountEvidenceOccurrence,
    ExpenseLineEvidenceParser,
)
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class _ExtractedLine(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    line_number: int = Field(strict=True, ge=1)
    source_text: str
    occurred_on: date | None
    date_evidence: str | None
    description: str | None
    description_evidence: str | None
    amount: str | None = Field(pattern=r"^-?\d+\.\d{2}$")
    amount_evidence: str | None
    amount_evidence_index: int | None = Field(default=None, ge=0)


class _ExtractionResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    extracted_lines: tuple[_ExtractedLine, ...] = Field(alias="lines")


@dataclass(frozen=True, slots=True)
class _NewDateEvidence:
    line_number: int
    source_text: str
    extracted_date: date
    date_evidence: str | None
    inferred_year: int | None


@dataclass(frozen=True, slots=True)
class _AmountValidationEvidence:
    source_text: str
    line_number: int
    extracted_amount: str
    amount_evidence: str | None
    amount_evidence_index: int | None


@dataclass(frozen=True, slots=True)
class _ExtractionAttempt:
    resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    pending_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    retry_problem: str | None
    gateway_failed: bool = False


@dataclass(frozen=True, slots=True)
class _ExtractionRetryState:
    pending_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    retry_problem: str | None
    finished: bool


@dataclass(frozen=True, slots=True)
class _LineMergeSuccess:
    original_parsed_expense_line: ParsedExpenseLine
    merged_parsed_expense_line: ParsedExpenseLine


@dataclass(frozen=True, slots=True)
class _LineMergeFailure:
    original_parsed_expense_line: ParsedExpenseLine
    validation_error: str


type _LineMergeOutcome = _LineMergeSuccess | _LineMergeFailure


@final
class _ExtractionValidationError(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_RESPONSE_VALIDATION, contextual_data_by_name)

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Extracted expense line failed evidence validation"


@final
# code-review: override[pwi.llm-based-processor-design.base-class-inheritance] - Composes LlmRequestClient; empty base adds no polymorphic contract.
class ExpenseLineExtractionLlmProcessor:
    _MAX_SEMANTIC_ATTEMPTS: ClassVar[int] = 2

    def __init__(
        self,
        llm_gateway_protocol: LlmGatewayProtocol,
        llm_operation_settings: LlmOperationSettings,
        event_callback: ProcessingEventCallback | None = None,
    ) -> None:
        self._llm_request_client: LlmRequestClient = LlmRequestClient(llm_gateway_protocol, llm_operation_settings, event_callback)

    async def extract_uncertain_lines(
        self, parsed_expense_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None
    ) -> tuple[ParsedExpenseLine, ...]:
        await self._llm_request_client.publish("progress", "Extracting uncertain expense lines")
        uncertain_lines: tuple[ParsedExpenseLine, ...] = self._uncertain_lines(parsed_expense_lines)
        if not uncertain_lines:
            await self._llm_request_client.publish("complete", "No uncertain expense lines required extraction")
            return parsed_expense_lines

        resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await self._resolve_uncertain_lines(uncertain_lines, inferred_year)
        updated_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = self._replace_lines(parsed_expense_lines, resolved_parsed_expense_lines)
        await self._llm_request_client.publish("complete", "Uncertain expense line extraction complete")
        return updated_parsed_expense_lines

    def _uncertain_lines(self, parsed_expense_lines: tuple[ParsedExpenseLine, ...]) -> tuple[ParsedExpenseLine, ...]:
        return tuple(parsed_expense_line for parsed_expense_line in parsed_expense_lines if parsed_expense_line.issues)

    async def _resolve_uncertain_lines(
        self, uncertain_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None
    ) -> tuple[ParsedExpenseLine, ...]:
        resolved_parsed_expense_line_by_line_number: dict[int, ParsedExpenseLine] = await self._retry_uncertain_lines(uncertain_lines, inferred_year)
        return self._reconstruct_uncertain_lines(uncertain_lines, resolved_parsed_expense_line_by_line_number)

    def _reconstruct_uncertain_lines(
        self, uncertain_lines: tuple[ParsedExpenseLine, ...], resolved_parsed_expense_line_by_line_number: dict[int, ParsedExpenseLine]
    ) -> tuple[ParsedExpenseLine, ...]:
        return tuple(
            resolved_parsed_expense_line_by_line_number[parsed_expense_line.line_number]
            if parsed_expense_line.line_number in resolved_parsed_expense_line_by_line_number
            else self._mark_failed(parsed_expense_line)
            for parsed_expense_line in uncertain_lines
        )

    async def _retry_uncertain_lines(self, uncertain_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None) -> dict[int, ParsedExpenseLine]:
        retry_state: _ExtractionRetryState = _ExtractionRetryState(uncertain_lines, (), None, finished=False)
        for _attempt in range(self._MAX_SEMANTIC_ATTEMPTS):
            extraction_attempt: _ExtractionAttempt = await self._attempt_pending_lines(
                retry_state.pending_parsed_expense_lines, inferred_year, retry_state.retry_problem
            )
            retry_state = self._advance_retry_state(retry_state, extraction_attempt)
            if retry_state.finished:
                break
        return self._resolved_lines_by_number(retry_state)

    def _resolved_lines_by_number(self, retry_state: _ExtractionRetryState) -> dict[int, ParsedExpenseLine]:
        return {
            parsed_expense_line.line_number: parsed_expense_line
            for parsed_expense_line in (
                *retry_state.resolved_parsed_expense_lines,
                *(self._mark_failed(parsed_expense_line) for parsed_expense_line in retry_state.pending_parsed_expense_lines),
            )
        }

    @staticmethod
    def _advance_retry_state(retry_state: _ExtractionRetryState, extraction_attempt: _ExtractionAttempt) -> _ExtractionRetryState:
        return _ExtractionRetryState(
            pending_parsed_expense_lines=extraction_attempt.pending_parsed_expense_lines,
            resolved_parsed_expense_lines=(*retry_state.resolved_parsed_expense_lines, *extraction_attempt.resolved_parsed_expense_lines),
            retry_problem=extraction_attempt.retry_problem,
            finished=extraction_attempt.gateway_failed or not extraction_attempt.pending_parsed_expense_lines,
        )

    async def _attempt_pending_lines(
        self, pending_parsed_expense_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None, retry_problem: str | None
    ) -> _ExtractionAttempt:
        prompt: str = self._build_prompt(pending_parsed_expense_lines, inferred_year)
        if retry_problem is not None:
            prompt = self._build_retry_prompt(prompt, retry_problem)
        try:
            extraction_response: _ExtractionResponse = await self._attempt_extraction(prompt)
            merged_lines: tuple[tuple[ParsedExpenseLine, ...], tuple[ParsedExpenseLine, ...], str | None] = self._merge_response(
                pending_parsed_expense_lines, extraction_response, inferred_year
            )
            return _ExtractionAttempt(merged_lines[0], merged_lines[1], merged_lines[2])
        except (ValidationError, _ExtractionValidationError) as exc:
            return _ExtractionAttempt((), pending_parsed_expense_lines, str(exc))
        except LlmGatewayException:
            return _ExtractionAttempt((), pending_parsed_expense_lines, retry_problem, gateway_failed=True)

    async def _attempt_extraction(self, prompt: str) -> _ExtractionResponse:
        response_text: str = await self._llm_request_client.complete(prompt)
        return _ExtractionResponse.model_validate_json(LlmRequestClient.json_response_text(response_text))

    def _replace_lines(
        self, parsed_expense_lines: tuple[ParsedExpenseLine, ...], resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    ) -> tuple[ParsedExpenseLine, ...]:
        replacement_parsed_expense_line_by_line_number: dict[int, ParsedExpenseLine] = {
            parsed_expense_line.line_number: parsed_expense_line for parsed_expense_line in resolved_parsed_expense_lines
        }
        return tuple(
            replacement_parsed_expense_line_by_line_number.get(parsed_expense_line.line_number, parsed_expense_line)
            for parsed_expense_line in parsed_expense_lines
        )

    def _build_prompt(self, parsed_expense_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None) -> str:
        input_lines: list[dict[str, object]] = [
            {
                "line_number": parsed_expense_line.line_number,
                "source_text": parsed_expense_line.source_text,
                "occurred_on": parsed_expense_line.occurred_on.isoformat() if parsed_expense_line.occurred_on is not None else None,
                "description": parsed_expense_line.description,
                "amount": str(parsed_expense_line.amount) if parsed_expense_line.amount is not None else None,
                "issues": parsed_expense_line.issues,
            }
            for parsed_expense_line in parsed_expense_lines
        ]
        return (
            "Extract expense fields only from the supplied source lines. Return one JSON object matching the schema, with one entry "
            "for every input line, in the same order. Copy line_number and source_text exactly. "
            "Return null for fields that already have a known value; the application preserves them. "
            "Use null for a field you cannot establish. For every newly supplied date, amount, or description, give an exact "
            "verbatim substring from source_text in its matching evidence field. Do not infer a date year unless inferred_year "
            "is supplied. When several values are present, distinguish the transaction amount from a balance or original price "
            "and the transaction date from a posting or reference date. For a new amount, set amount_evidence_index to the "
            "zero-based occurrence among all amount values in source_text when the same amount text occurs more than once. "
            "Use null if the role is still unclear. "
            "A refund must have a negative amount when the source amount is negative. Return JSON only.\n"
            f"inferred_year: {json.dumps(inferred_year)}\n"
            f"schema: {json.dumps(_ExtractionResponse.model_json_schema())}\n"
            f"input_lines: {json.dumps(input_lines)}"
        )

    def _build_retry_prompt(self, original_prompt: str, response_validation_problem: str) -> str:
        return (
            f"{original_prompt}\nThe previous response failed schema or evidence validation: "
            f"{response_validation_problem}. Return a corrected JSON object only."
        )

    def _merge_response(
        self, uncertain_lines: tuple[ParsedExpenseLine, ...], extraction_response: _ExtractionResponse, inferred_year: int | None
    ) -> tuple[tuple[ParsedExpenseLine, ...], tuple[ParsedExpenseLine, ...], str | None]:
        self._verify_response_line_numbers(uncertain_lines, extraction_response)
        return self._collect_merged_lines(uncertain_lines, extraction_response, inferred_year)

    def _verify_response_line_numbers(self, uncertain_lines: tuple[ParsedExpenseLine, ...], extraction_response: _ExtractionResponse) -> None:
        expected_numbers: tuple[int, ...] = tuple(parsed_expense_line.line_number for parsed_expense_line in uncertain_lines)
        received_numbers: tuple[int, ...] = tuple(extracted_line.line_number for extracted_line in extraction_response.extracted_lines)
        if received_numbers != expected_numbers:
            raise _ExtractionValidationError(
                f"Response line numbers {received_numbers} must match requested lines {expected_numbers} in order",
                {
                    "ValidationStage": "response_line_numbers",
                    "ExpectedLineNumbers": str(expected_numbers),
                    "ReceivedLineNumbers": str(received_numbers),
                },
            )

    def _collect_merged_lines(
        self, uncertain_lines: tuple[ParsedExpenseLine, ...], extraction_response: _ExtractionResponse, inferred_year: int | None
    ) -> tuple[tuple[ParsedExpenseLine, ...], tuple[ParsedExpenseLine, ...], str | None]:
        merge_outcomes: tuple[_LineMergeOutcome, ...] = tuple(
            self._merge_one_line(line_pair[0], line_pair[1], inferred_year)
            for line_pair in zip(uncertain_lines, extraction_response.extracted_lines, strict=True)
        )
        return self._partition_merge_outcomes(merge_outcomes)

    @staticmethod
    def _partition_merge_outcomes(
        merge_outcomes: tuple[_LineMergeOutcome, ...],
    ) -> tuple[tuple[ParsedExpenseLine, ...], tuple[ParsedExpenseLine, ...], str | None]:
        resolved_parsed_expense_lines: list[ParsedExpenseLine] = []
        pending_parsed_expense_lines: list[ParsedExpenseLine] = []
        validation_errors: list[str] = []
        for merge_outcome in merge_outcomes:
            if isinstance(merge_outcome, _LineMergeFailure):
                pending_parsed_expense_lines.append(merge_outcome.original_parsed_expense_line)
                validation_errors.append(merge_outcome.validation_error)
            elif merge_outcome.merged_parsed_expense_line.issues:
                pending_parsed_expense_lines.append(merge_outcome.merged_parsed_expense_line)
            else:
                resolved_parsed_expense_lines.append(merge_outcome.merged_parsed_expense_line)
        retry_problem: str | None = "; ".join(validation_errors) if validation_errors else None
        return tuple(resolved_parsed_expense_lines), tuple(pending_parsed_expense_lines), retry_problem

    def _merge_one_line(
        self, original_parsed_expense_line: ParsedExpenseLine, extracted_line: _ExtractedLine, inferred_year: int | None
    ) -> _LineMergeOutcome:
        try:
            return _LineMergeSuccess(original_parsed_expense_line, self._merge_line(original_parsed_expense_line, extracted_line, inferred_year))
        except _ExtractionValidationError as exc:
            return _LineMergeFailure(original_parsed_expense_line, str(exc))

    def _merge_line(
        self, original_parsed_expense_line: ParsedExpenseLine, extracted_line: _ExtractedLine, inferred_year: int | None
    ) -> ParsedExpenseLine:
        self._require_matching_source(original_parsed_expense_line.line_number, original_parsed_expense_line.source_text, extracted_line.source_text)
        occurred_on: date | None = self._validated_date(
            original_parsed_expense_line, extracted_line.occurred_on, extracted_line.date_evidence, inferred_year
        )
        amount: Decimal | None = self._validated_amount(
            original_parsed_expense_line, extracted_line.amount, extracted_line.amount_evidence, extracted_line.amount_evidence_index
        )
        description: str | None = self._validated_description(
            original_parsed_expense_line, extracted_line.description, extracted_line.description_evidence
        )
        issues: tuple[str, ...] = self._remaining_issues(original_parsed_expense_line.issues, occurred_on, amount, description)
        return ParsedExpenseLine(
            line_number=original_parsed_expense_line.line_number,
            source_text=original_parsed_expense_line.source_text,
            occurred_on=occurred_on,
            description=description,
            amount=amount,
            issues=issues,
        )

    def _require_matching_source(self, line_number: int, source_text: str, extracted_source_text: str) -> None:
        if extracted_source_text != source_text:
            raise _ExtractionValidationError(
                f"Line {line_number} source_text differs from input", {"LineNumber": line_number, "ValidationStage": "source_copy"}
            )

    def _remaining_issues(
        self, original_issues: tuple[str, ...], occurred_on: date | None, amount: Decimal | None, description: str | None
    ) -> tuple[str, ...]:
        issues: tuple[str, ...] = tuple(
            issue
            for issue in original_issues
            if not (
                (occurred_on is not None and issue in {"missing_date", "multiple_dates", "invalid_date", "year_not_inferable"})
                or (amount is not None and issue in {"missing_amount", "multiple_amounts"})
                or (description is not None and issue == "missing_description")
            )
        )
        return issues

    def _validated_date(
        self, original_parsed_expense_line: ParsedExpenseLine, extracted_date: date | None, date_evidence: str | None, inferred_year: int | None
    ) -> date | None:
        if original_parsed_expense_line.occurred_on is not None:
            return self._validated_known_date(original_parsed_expense_line.line_number, original_parsed_expense_line.occurred_on, extracted_date)
        if extracted_date is None:
            return None
        new_date_evidence: _NewDateEvidence = _NewDateEvidence(
            original_parsed_expense_line.line_number, original_parsed_expense_line.source_text, extracted_date, date_evidence, inferred_year
        )
        return self._validated_new_date(new_date_evidence)

    @staticmethod
    def _validated_known_date(line_number: int, known_date: date, extracted_date: date | None) -> date:
        if extracted_date is not None and extracted_date != known_date:
            raise _ExtractionValidationError(
                f"Line {line_number} changed a known date",
                {
                    "LineNumber": line_number,
                    "ValidationStage": "known_date",
                    "KnownDate": known_date.isoformat(),
                    "ExtractedDate": extracted_date.isoformat(),
                },
            )
        return known_date

    def _validated_new_date(self, new_date_evidence: _NewDateEvidence) -> date:
        date_evidence: str = self._required_date_evidence(new_date_evidence)
        evidence_date: date | None = ExpenseLineEvidenceParser.parse_date_evidence(date_evidence, new_date_evidence.inferred_year)
        self._require_matching_date_evidence(new_date_evidence, evidence_date)
        return new_date_evidence.extracted_date

    @staticmethod
    def _required_date_evidence(new_date_evidence: _NewDateEvidence) -> str:
        date_evidence: str | None = new_date_evidence.date_evidence
        if date_evidence is None or date_evidence not in ExpenseLineEvidenceParser.date_candidates(new_date_evidence.source_text):
            raise _ExtractionValidationError(
                f"Line {new_date_evidence.line_number} extracted date {new_date_evidence.extracted_date.isoformat()} lacks source evidence",
                {
                    "LineNumber": new_date_evidence.line_number,
                    "ValidationStage": "date_source_evidence",
                    "ExtractedDate": new_date_evidence.extracted_date.isoformat(),
                },
            )
        return date_evidence

    @staticmethod
    def _require_matching_date_evidence(new_date_evidence: _NewDateEvidence, evidence_date: date | None) -> None:
        if evidence_date != new_date_evidence.extracted_date:
            raise _ExtractionValidationError(
                f"Line {new_date_evidence.line_number} date does not match source evidence",
                {
                    "LineNumber": new_date_evidence.line_number,
                    "ValidationStage": "date_evidence_comparison",
                    "ExtractedDate": new_date_evidence.extracted_date.isoformat(),
                    "EvidenceDate": evidence_date.isoformat() if evidence_date is not None else "unavailable",
                },
            )

    def _validated_amount(
        self,
        original_parsed_expense_line: ParsedExpenseLine,
        extracted_amount: str | None,
        amount_evidence: str | None,
        amount_evidence_index: int | None,
    ) -> Decimal | None:
        if original_parsed_expense_line.amount is not None:
            return self._validated_known_amount(original_parsed_expense_line.line_number, original_parsed_expense_line.amount, extracted_amount)
        if extracted_amount is None:
            return None
        amount_validation_evidence: _AmountValidationEvidence = _AmountValidationEvidence(
            original_parsed_expense_line.source_text,
            original_parsed_expense_line.line_number,
            extracted_amount,
            amount_evidence,
            amount_evidence_index,
        )
        amount_evidence_occurrence: AmountEvidenceOccurrence = self._required_amount_occurrence(amount_validation_evidence)
        evidence_amount: Decimal = self._amount_from_occurrence(original_parsed_expense_line.source_text, amount_evidence_occurrence)
        self._require_matching_amount_evidence(original_parsed_expense_line.line_number, extracted_amount, evidence_amount)
        return evidence_amount

    @staticmethod
    def _validated_known_amount(line_number: int, known_amount: Decimal, extracted_amount: str | None) -> Decimal:
        if extracted_amount is not None and Decimal(extracted_amount) != known_amount:
            raise _ExtractionValidationError(
                f"Line {line_number} changed a known amount",
                {
                    "LineNumber": line_number,
                    "ValidationStage": "known_amount",
                    "KnownAmount": str(known_amount),
                    "ExtractedAmount": extracted_amount,
                },
            )
        return known_amount

    def _required_amount_occurrence(self, amount_validation_evidence: _AmountValidationEvidence) -> AmountEvidenceOccurrence:
        amount_evidence_occurrence: AmountEvidenceOccurrence | None = self._selected_amount_occurrence(
            amount_validation_evidence.source_text,
            amount_validation_evidence.amount_evidence,
            amount_validation_evidence.amount_evidence_index,
        )
        return self._require_supported_amount_occurrence(amount_validation_evidence, amount_evidence_occurrence)

    @staticmethod
    def _require_supported_amount_occurrence(
        amount_validation_evidence: _AmountValidationEvidence, amount_evidence_occurrence: AmountEvidenceOccurrence | None
    ) -> AmountEvidenceOccurrence:
        if amount_evidence_occurrence is None or ExpenseLineEvidenceParser.amount_occurrence_is_balance(
            amount_validation_evidence.source_text, amount_evidence_occurrence.amount_start_index
        ):
            raise _ExtractionValidationError(
                f"Line {amount_validation_evidence.line_number} extracted amount {amount_validation_evidence.extracted_amount} lacks source evidence",
                {
                    "LineNumber": amount_validation_evidence.line_number,
                    "ValidationStage": "amount_source_evidence",
                    "ExtractedAmount": amount_validation_evidence.extracted_amount,
                },
            )
        return amount_evidence_occurrence

    @staticmethod
    def _amount_from_occurrence(source_text: str, amount_evidence_occurrence: AmountEvidenceOccurrence) -> Decimal:
        return ExpenseLineEvidenceParser.refund_adjusted_amount(
            source_text,
            ExpenseLineEvidenceParser.parse_amount_evidence(amount_evidence_occurrence.amount_text),
            amount_evidence_occurrence.amount_start_index,
        )

    @staticmethod
    def _require_matching_amount_evidence(line_number: int, extracted_amount: str, evidence_amount: Decimal) -> None:
        if evidence_amount != Decimal(extracted_amount):
            raise _ExtractionValidationError(
                f"Line {line_number} amount does not match source evidence",
                {
                    "LineNumber": line_number,
                    "ValidationStage": "amount_evidence_comparison",
                    "ExtractedAmount": extracted_amount,
                    "EvidenceAmount": str(evidence_amount),
                },
            )

    @staticmethod
    def _selected_amount_occurrence(
        source_text: str, amount_evidence: str | None, amount_evidence_index: int | None
    ) -> AmountEvidenceOccurrence | None:
        if amount_evidence is None:
            return None
        amount_evidence_occurrences: tuple[AmountEvidenceOccurrence, ...] = ExpenseLineEvidenceParser.amount_candidate_occurrences(source_text)
        if amount_evidence_index is not None:
            return ExpenseLineExtractionLlmProcessor._indexed_amount_occurrence(amount_evidence_occurrences, amount_evidence, amount_evidence_index)
        return ExpenseLineExtractionLlmProcessor._unique_amount_occurrence(amount_evidence_occurrences, amount_evidence)

    @staticmethod
    def _indexed_amount_occurrence(
        amount_evidence_occurrences: tuple[AmountEvidenceOccurrence, ...], amount_evidence: str, amount_evidence_index: int
    ) -> AmountEvidenceOccurrence | None:
        if amount_evidence_index >= len(amount_evidence_occurrences):
            return None
        amount_evidence_occurrence: AmountEvidenceOccurrence = amount_evidence_occurrences[amount_evidence_index]
        return amount_evidence_occurrence if amount_evidence_occurrence.amount_text == amount_evidence else None

    @staticmethod
    def _unique_amount_occurrence(
        amount_evidence_occurrences: tuple[AmountEvidenceOccurrence, ...], amount_evidence: str
    ) -> AmountEvidenceOccurrence | None:
        matching_occurrences: tuple[AmountEvidenceOccurrence, ...] = tuple(
            amount_evidence_occurrence
            for amount_evidence_occurrence in amount_evidence_occurrences
            if amount_evidence_occurrence.amount_text == amount_evidence
        )
        return matching_occurrences[0] if len(matching_occurrences) == 1 else None

    def _validated_description(
        self, original_parsed_expense_line: ParsedExpenseLine, extracted_description: str | None, description_evidence: str | None
    ) -> str | None:
        if original_parsed_expense_line.description is not None:
            if extracted_description is not None and extracted_description != original_parsed_expense_line.description:
                raise _ExtractionValidationError(
                    f"Line {original_parsed_expense_line.line_number} changed a known description",
                    {"LineNumber": original_parsed_expense_line.line_number, "ValidationStage": "known_description"},
                )
            return original_parsed_expense_line.description
        if extracted_description is None:
            return None
        if (
            description_evidence is None
            or not extracted_description.strip()
            or description_evidence not in original_parsed_expense_line.source_text
            or description_evidence.strip() != extracted_description
            or not ExpenseLineEvidenceParser.has_descriptive_text(description_evidence)
        ):
            raise _ExtractionValidationError(
                f"Line {original_parsed_expense_line.line_number} description lacks source evidence",
                {"LineNumber": original_parsed_expense_line.line_number, "ValidationStage": "description_source_evidence"},
            )
        return extracted_description

    def _mark_failed(self, parsed_expense_line: ParsedExpenseLine) -> ParsedExpenseLine:
        return ParsedExpenseLine(
            line_number=parsed_expense_line.line_number,
            source_text=parsed_expense_line.source_text,
            occurred_on=parsed_expense_line.occurred_on,
            description=parsed_expense_line.description,
            amount=parsed_expense_line.amount,
            issues=(*parsed_expense_line.issues, "llm_extraction_failed"),
        )
