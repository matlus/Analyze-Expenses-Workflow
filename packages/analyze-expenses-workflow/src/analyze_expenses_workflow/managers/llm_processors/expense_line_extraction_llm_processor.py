import json
from datetime import date
from decimal import Decimal
from typing import ClassVar, final, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExpenseLogEvent,
)
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException
from analyze_expenses_workflow.managers.llm_processors.bases.llm_processor_base import LlmProcessorBase
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class _ExtractedLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    line_number: int = Field(strict=True, ge=1)
    source_text: str
    occurred_on: date | None
    date_evidence: str | None
    description: str | None
    description_evidence: str | None
    amount: str | None = Field(pattern=r"^-?\d+\.\d{2}$")
    amount_evidence: str | None


class _ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lines: tuple[_ExtractedLine, ...]


@final
class _ExtractionValidationError(AnalyzeExpensesTechnicalException):
    def __init__(self, message: str) -> None:
        super().__init__(message, ExpenseLogEvent.LLM_RESPONSE_VALIDATION)

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.RETRY_ACTION_NEEDED

    @property
    @override
    def reason(self) -> str:
        return "Extracted expense line failed evidence validation"


@final
class ExpenseLineExtractionLlmProcessor(LlmProcessorBase):
    _MAX_SEMANTIC_ATTEMPTS: ClassVar[int] = 2

    async def extract_uncertain_lines(self, lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None) -> tuple[ParsedExpenseLine, ...]:
        await self._publish("progress", "Extracting uncertain expense lines")
        uncertain_lines: tuple[ParsedExpenseLine, ...] = self._uncertain_lines(lines)
        if not uncertain_lines:
            await self._publish("complete", "No uncertain expense lines required extraction")
            return lines

        resolved_lines: tuple[ParsedExpenseLine, ...] = await self._resolve_uncertain_lines(uncertain_lines, inferred_year)
        result: tuple[ParsedExpenseLine, ...] = self._replace_lines(lines, resolved_lines)
        await self._publish("complete", "Uncertain expense line extraction complete")
        return result

    def _uncertain_lines(self, lines: tuple[ParsedExpenseLine, ...]) -> tuple[ParsedExpenseLine, ...]:
        return tuple(line for line in lines if line.issues)

    async def _resolve_uncertain_lines(
        self, uncertain_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None
    ) -> tuple[ParsedExpenseLine, ...]:
        pending_lines: tuple[ParsedExpenseLine, ...] = uncertain_lines
        resolved_by_number: dict[int, ParsedExpenseLine] = {}
        retry_problem: str | None = None
        for _attempt in range(self._MAX_SEMANTIC_ATTEMPTS):
            prompt: str = self._build_prompt(pending_lines, inferred_year)
            if retry_problem is not None:
                prompt = self._build_retry_prompt(prompt, retry_problem)
            try:
                response: _ExtractionResponse = await self._attempt_extraction(prompt)
                resolved_lines, pending_lines, retry_problem = self._merge_response(pending_lines, response, inferred_year)
                resolved_by_number.update({line.line_number: line for line in resolved_lines})
                if not pending_lines:
                    break
            except (ValidationError, _ExtractionValidationError) as exc:
                retry_problem = str(exc)
            except LlmGatewayException:
                break
        return tuple(
            resolved_by_number[line.line_number] if line.line_number in resolved_by_number else self._mark_failed(line)
            for line in uncertain_lines
        )

    async def _attempt_extraction(
        self, prompt: str
    ) -> _ExtractionResponse:
        response_text: str = await self._complete(prompt)
        return _ExtractionResponse.model_validate_json(self._json_response_text(response_text))

    def _replace_lines(
        self, lines: tuple[ParsedExpenseLine, ...], resolved_lines: tuple[ParsedExpenseLine, ...]
    ) -> tuple[ParsedExpenseLine, ...]:
        replacements_by_number: dict[int, ParsedExpenseLine] = {line.line_number: line for line in resolved_lines}
        return tuple(replacements_by_number.get(line.line_number, line) for line in lines)

    def _build_prompt(self, lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None) -> str:
        input_lines: list[dict[str, object]] = [
            {
                "line_number": line.line_number,
                "source_text": line.source_text,
                "occurred_on": line.occurred_on.isoformat() if line.occurred_on is not None else None,
                "description": line.description,
                "amount": str(line.amount) if line.amount is not None else None,
                "issues": line.issues,
            }
            for line in lines
        ]
        return (
            "Extract expense fields only from the supplied source lines. Return one JSON object matching the schema, with one entry "
            "for every input line, in the same order. Copy line_number and source_text exactly. "
            "Return null for fields that already have a known value; the application preserves them. "
            "Use null for a field you cannot establish. For every newly supplied date, amount, or description, give an exact "
            "verbatim substring from source_text in its matching evidence field. Do not infer a date year unless inferred_year "
            "is supplied. When several values are present, distinguish the transaction amount from a balance or original price "
            "and the transaction date from a posting or reference date. Use null if the role is still unclear. "
            "A refund must have a negative amount when the source amount is negative. Return JSON only.\n"
            f"inferred_year: {json.dumps(inferred_year)}\n"
            f"schema: {json.dumps(_ExtractionResponse.model_json_schema())}\n"
            f"input_lines: {json.dumps(input_lines)}"
        )

    def _build_retry_prompt(self, original_prompt: str, problem: str) -> str:
        return f"{original_prompt}\nThe previous response failed schema or evidence validation: {problem}. Return a corrected JSON object only."

    def _merge_response(
        self, uncertain_lines: tuple[ParsedExpenseLine, ...], response: _ExtractionResponse, inferred_year: int | None
    ) -> tuple[tuple[ParsedExpenseLine, ...], tuple[ParsedExpenseLine, ...], str | None]:
        expected_numbers: tuple[int, ...] = tuple(line.line_number for line in uncertain_lines)
        received_numbers: tuple[int, ...] = tuple(line.line_number for line in response.lines)
        if received_numbers != expected_numbers:
            raise _ExtractionValidationError("Response line numbers must match the requested lines in order")
        resolved_lines: list[ParsedExpenseLine] = []
        pending_lines: list[ParsedExpenseLine] = []
        validation_errors: list[str] = []
        for original, extracted in zip(uncertain_lines, response.lines, strict=True):
            try:
                resolved_lines.append(self._merge_line(original, extracted, inferred_year))
            except _ExtractionValidationError as exc:
                pending_lines.append(original)
                validation_errors.append(str(exc))
        retry_problem: str | None = "; ".join(validation_errors) if validation_errors else None
        return tuple(resolved_lines), tuple(pending_lines), retry_problem

    def _merge_line(self, original: ParsedExpenseLine, extracted: _ExtractedLine, inferred_year: int | None) -> ParsedExpenseLine:
        self._require_matching_source(original.line_number, original.source_text, extracted.source_text)
        occurred_on: date | None = self._validated_date(original, extracted.occurred_on, extracted.date_evidence, inferred_year)
        amount: Decimal | None = self._validated_amount(original, extracted.amount, extracted.amount_evidence)
        description: str | None = self._validated_description(original, extracted.description, extracted.description_evidence)
        issues: tuple[str, ...] = self._remaining_issues(original.issues, occurred_on, amount, description)
        return ParsedExpenseLine(
            line_number=original.line_number,
            source_text=original.source_text,
            occurred_on=occurred_on,
            description=description,
            amount=amount,
            issues=issues,
        )

    def _require_matching_source(self, line_number: int, source_text: str, extracted_source_text: str) -> None:
        if extracted_source_text != source_text:
            raise _ExtractionValidationError(f"Line {line_number} source_text differs from input")

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
        self, original: ParsedExpenseLine, extracted_date: date | None, date_evidence: str | None, inferred_year: int | None
    ) -> date | None:
        if original.occurred_on is not None:
            if extracted_date is not None and extracted_date != original.occurred_on:
                raise _ExtractionValidationError(f"Line {original.line_number} changed a known date")
            return original.occurred_on
        if extracted_date is None:
            return None
        return self._validated_new_date(original.line_number, original.source_text, extracted_date, date_evidence, inferred_year)

    def _validated_new_date(
        self, line_number: int, source_text: str, extracted_date: date, date_evidence: str | None, inferred_year: int | None
    ) -> date:
        if date_evidence is None or date_evidence not in source_text:
            raise _ExtractionValidationError(f"Line {line_number} date lacks source evidence")
        evidence_date: date | None = self._date_from_evidence(date_evidence, inferred_year)
        if evidence_date != extracted_date:
            raise _ExtractionValidationError(f"Line {line_number} date does not match source evidence")
        return extracted_date

    def _date_from_evidence(self, evidence: str, inferred_year: int | None) -> date | None:
        return ExpenseLineParsingProcessor.parse_date_evidence(evidence, inferred_year)

    def _validated_amount(self, original: ParsedExpenseLine, extracted_amount: str | None, amount_evidence: str | None) -> Decimal | None:
        if original.amount is not None:
            if extracted_amount is not None and Decimal(extracted_amount) != original.amount:
                raise _ExtractionValidationError(f"Line {original.line_number} changed a known amount")
            return original.amount
        if extracted_amount is None:
            return None
        evidence: str | None = amount_evidence
        if (
            evidence is None
            or not ExpenseLineParsingProcessor.amount_evidence_matches_source(original.source_text, evidence)
            or ExpenseLineParsingProcessor.amount_evidence_is_balance(original.source_text, evidence)
        ):
            raise _ExtractionValidationError(f"Line {original.line_number} amount lacks source evidence")
        evidence_amount: Decimal = ExpenseLineParsingProcessor.refund_adjusted_amount(
            original.source_text, ExpenseLineParsingProcessor.parse_amount_evidence(evidence)
        )
        if evidence_amount != Decimal(extracted_amount):
            raise _ExtractionValidationError(f"Line {original.line_number} amount does not match source evidence")
        return evidence_amount

    def _validated_description(
        self, original: ParsedExpenseLine, extracted_description: str | None, description_evidence: str | None
    ) -> str | None:
        if original.description is not None:
            if extracted_description is not None and extracted_description != original.description:
                raise _ExtractionValidationError(f"Line {original.line_number} changed a known description")
            return original.description
        if extracted_description is None:
            return None
        evidence: str | None = description_evidence
        if (
            evidence is None
            or not extracted_description.strip()
            or evidence not in original.source_text
            or evidence.strip() != extracted_description
            or not ExpenseLineParsingProcessor.has_descriptive_text(evidence)
        ):
            raise _ExtractionValidationError(f"Line {original.line_number} description lacks source evidence")
        return extracted_description

    def _mark_failed(self, line: ParsedExpenseLine) -> ParsedExpenseLine:
        return ParsedExpenseLine(
            line_number=line.line_number,
            source_text=line.source_text,
            occurred_on=line.occurred_on,
            description=line.description,
            amount=line.amount,
            issues=(*line.issues, "llm_extraction_failed"),
        )
