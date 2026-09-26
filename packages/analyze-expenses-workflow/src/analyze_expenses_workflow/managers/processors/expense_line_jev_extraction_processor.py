import math
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import ClassVar

from analyze_expenses_workflow.managers.exceptions.configuration_setting_exception import ConfigurationSettingException
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import JevRequestFailedException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion, SystemOneGatewayProtocol
from analyze_expenses_workflow.managers.processors.evidence_parsers.expense_line_evidence_parser import (
    AmountEvidenceOccurrence,
    ExpenseLineEvidenceParser,
)
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class ExpenseLineJevExtractionProcessor:
    _MINIMUM_AMBIGUOUS_CANDIDATES: ClassVar[int] = 2
    _MAXIMUM_CANDIDATES_WITH_NONE_OPTION: ClassVar[int] = 254
    _MULTIPLE_DATES_ISSUE: ClassVar[str] = "multiple_dates"
    _MULTIPLE_AMOUNTS_ISSUE: ClassVar[str] = "multiple_amounts"
    _CANDIDATE_PREFIX: ClassVar[str] = "candidate_"
    _NONE_CHOICE: ClassVar[str] = "none"

    def __init__(self, system_one_gateway_protocol: SystemOneGatewayProtocol, minimum_choice_probability: float) -> None:
        if not math.isfinite(minimum_choice_probability) or not 0 <= minimum_choice_probability <= 1:
            raise ConfigurationSettingException(
                "Jev minimum choice probability must be between zero and one",
                {"MinimumChoiceProbability": str(minimum_choice_probability)},
            )
        self._system_one_gateway_protocol: SystemOneGatewayProtocol = system_one_gateway_protocol
        self._minimum_choice_probability: float = minimum_choice_probability

    async def extract_uncertain_lines(
        self, parsed_expense_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None
    ) -> tuple[ParsedExpenseLine, ...]:
        return await self._extract_lines(parsed_expense_lines, inferred_year)

    async def _extract_lines(self, parsed_expense_lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None) -> tuple[ParsedExpenseLine, ...]:
        return tuple([await self._extract_line(parsed_expense_line, inferred_year) for parsed_expense_line in parsed_expense_lines])

    async def _extract_line(self, parsed_expense_line: ParsedExpenseLine, inferred_year: int | None) -> ParsedExpenseLine:
        if self._MULTIPLE_DATES_ISSUE in parsed_expense_line.issues:
            parsed_expense_line = await self._select_date(parsed_expense_line, inferred_year)
        if self._MULTIPLE_AMOUNTS_ISSUE in parsed_expense_line.issues:
            parsed_expense_line = await self._select_amount(parsed_expense_line)
        return parsed_expense_line

    async def _select_date(self, parsed_expense_line: ParsedExpenseLine, inferred_year: int | None) -> ParsedExpenseLine:
        candidates: tuple[str, ...] = ExpenseLineEvidenceParser.date_candidates(parsed_expense_line.source_text)
        selected_index: int | None = await self._choose_candidate_index(
            parsed_expense_line.source_text,
            candidates,
            "Which candidate is the transaction date for this expense, rather than a reference or posting date?",
        )
        return self._apply_selected_date(parsed_expense_line, candidates, selected_index, inferred_year)

    @staticmethod
    def _apply_selected_date(
        parsed_expense_line: ParsedExpenseLine, candidates: tuple[str, ...], selected_index: int | None, inferred_year: int | None
    ) -> ParsedExpenseLine:
        occurred_on: date | None = ExpenseLineJevExtractionProcessor._selected_date(candidates, selected_index, inferred_year)
        if occurred_on is None:
            return parsed_expense_line
        return ExpenseLineJevExtractionProcessor._with_selected_date(parsed_expense_line, occurred_on)

    @staticmethod
    def _selected_date(candidates: tuple[str, ...], selected_index: int | None, inferred_year: int | None) -> date | None:
        if selected_index is None:
            return None
        return ExpenseLineEvidenceParser.parse_date_evidence(candidates[selected_index], inferred_year)

    @staticmethod
    def _with_selected_date(parsed_expense_line: ParsedExpenseLine, occurred_on: date) -> ParsedExpenseLine:
        return replace(
            parsed_expense_line,
            occurred_on=occurred_on,
            issues=tuple(issue for issue in parsed_expense_line.issues if issue != ExpenseLineJevExtractionProcessor._MULTIPLE_DATES_ISSUE),
        )

    async def _select_amount(self, parsed_expense_line: ParsedExpenseLine) -> ParsedExpenseLine:
        amount_evidence_occurrences: tuple[AmountEvidenceOccurrence, ...] = ExpenseLineEvidenceParser.amount_candidate_occurrences(
            parsed_expense_line.source_text
        )
        candidates: tuple[str, ...] = tuple(occurrence.amount_text for occurrence in amount_evidence_occurrences)
        selected_index: int | None = await self._choose_candidate_index(
            parsed_expense_line.source_text,
            candidates,
            "Which candidate is the signed transaction amount affecting this account, rather than a balance or original price?",
        )
        return self._apply_selected_amount(parsed_expense_line, amount_evidence_occurrences, selected_index)

    @staticmethod
    def _apply_selected_amount(
        parsed_expense_line: ParsedExpenseLine, amount_evidence_occurrences: tuple[AmountEvidenceOccurrence, ...], selected_index: int | None
    ) -> ParsedExpenseLine:
        if selected_index is None:
            return parsed_expense_line
        amount_evidence_occurrence: AmountEvidenceOccurrence = amount_evidence_occurrences[selected_index]
        amount: Decimal | None = ExpenseLineJevExtractionProcessor._selected_amount(parsed_expense_line.source_text, amount_evidence_occurrence)
        if amount is None:
            return parsed_expense_line
        return ExpenseLineJevExtractionProcessor._with_selected_amount(parsed_expense_line, amount)

    @staticmethod
    def _selected_amount(source_text: str, amount_evidence_occurrence: AmountEvidenceOccurrence) -> Decimal | None:
        if ExpenseLineEvidenceParser.amount_occurrence_is_balance(source_text, amount_evidence_occurrence.amount_start_index):
            return None
        return ExpenseLineEvidenceParser.refund_adjusted_amount(
            source_text,
            ExpenseLineEvidenceParser.parse_amount_evidence(amount_evidence_occurrence.amount_text),
            amount_evidence_occurrence.amount_start_index,
        )

    @staticmethod
    def _with_selected_amount(parsed_expense_line: ParsedExpenseLine, amount: Decimal) -> ParsedExpenseLine:
        return replace(
            parsed_expense_line,
            amount=amount,
            issues=tuple(issue for issue in parsed_expense_line.issues if issue != ExpenseLineJevExtractionProcessor._MULTIPLE_AMOUNTS_ISSUE),
        )

    async def _choose_candidate_index(self, source_text: str, candidates: tuple[str, ...], instructions: str) -> int | None:
        if not self._MINIMUM_AMBIGUOUS_CANDIDATES <= len(candidates) <= self._MAXIMUM_CANDIDATES_WITH_NONE_OPTION:
            return None
        candidate_descriptions_by_choice_key: dict[str, str] = self._choice_criteria(candidates)
        try:
            choice_decision: ChoiceDecision = await self._system_one_gateway_protocol.choose(
                source_text, ChoiceQuestion(instructions=instructions, criteria=candidate_descriptions_by_choice_key)
            )
        except JevRequestFailedException:
            return None
        return self._accepted_candidate_index(choice_decision, candidate_descriptions_by_choice_key)

    @staticmethod
    def _choice_criteria(candidates: tuple[str, ...]) -> dict[str, str]:
        candidate_descriptions_by_choice_key: dict[str, str] = {
            f"{ExpenseLineJevExtractionProcessor._CANDIDATE_PREFIX}{index}": f"The source occurrence {index + 1}: {candidate}"
            for index, candidate in enumerate(candidates)
        }
        candidate_descriptions_by_choice_key[ExpenseLineJevExtractionProcessor._NONE_CHOICE] = (
            "None of the candidate values is the transaction field."
        )
        return candidate_descriptions_by_choice_key

    def _accepted_candidate_index(self, choice_decision: ChoiceDecision, candidate_descriptions_by_choice_key: Mapping[str, str]) -> int | None:
        if (
            choice_decision.choice not in candidate_descriptions_by_choice_key
            or choice_decision.probabilities[choice_decision.choice] < self._minimum_choice_probability
        ):
            return None
        if choice_decision.choice == self._NONE_CHOICE:
            return None
        return int(choice_decision.choice.removeprefix(self._CANDIDATE_PREFIX))
