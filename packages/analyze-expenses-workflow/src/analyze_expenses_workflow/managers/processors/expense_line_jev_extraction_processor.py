from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import ClassVar

from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import SystemOneGatewayException
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion, SystemOneGatewayProtocol
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import AmountEvidenceOccurrence, ExpenseLineParsingProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class ExpenseLineJevExtractionProcessor:
    _MINIMUM_AMBIGUOUS_CANDIDATES: ClassVar[int] = 2
    _MAXIMUM_CANDIDATES_WITH_NONE_OPTION: ClassVar[int] = 254

    def __init__(self, gateway: SystemOneGatewayProtocol, minimum_choice_probability: float) -> None:
        if not 0 <= minimum_choice_probability <= 1:
            raise ValueError("Minimum Jev parsing probability must be between zero and one")
        self._gateway: SystemOneGatewayProtocol = gateway
        self._minimum_choice_probability: float = minimum_choice_probability

    async def extract_uncertain_lines(self, lines: tuple[ParsedExpenseLine, ...], inferred_year: int | None) -> tuple[ParsedExpenseLine, ...]:
        return tuple([await self._extract_line(line, inferred_year) for line in lines])

    async def _extract_line(self, line: ParsedExpenseLine, inferred_year: int | None) -> ParsedExpenseLine:
        result: ParsedExpenseLine = line
        if "multiple_dates" in result.issues:
            result = await self._select_date(result, inferred_year)
        if "multiple_amounts" in result.issues:
            result = await self._select_amount(result)
        return result

    async def _select_date(self, line: ParsedExpenseLine, inferred_year: int | None) -> ParsedExpenseLine:
        candidates: tuple[str, ...] = ExpenseLineParsingProcessor.date_candidates(line.source_text)
        selected_index: int | None = await self._choose_candidate_index(
            line.source_text, candidates, "Which candidate is the transaction date for this expense, rather than a reference or posting date?"
        )
        selected: str | None = candidates[selected_index] if selected_index is not None else None
        occurred_on: date | None = ExpenseLineParsingProcessor.parse_date_evidence(selected, inferred_year) if selected is not None else None
        if occurred_on is None:
            return line
        return replace(line, occurred_on=occurred_on, issues=tuple(issue for issue in line.issues if issue != "multiple_dates"))

    async def _select_amount(self, line: ParsedExpenseLine) -> ParsedExpenseLine:
        occurrences: tuple[AmountEvidenceOccurrence, ...] = ExpenseLineParsingProcessor.amount_candidate_occurrences(line.source_text)
        candidates: tuple[str, ...] = tuple(occurrence.text for occurrence in occurrences)
        selected_index: int | None = await self._choose_candidate_index(
            line.source_text,
            candidates,
            "Which candidate is the signed transaction amount affecting this account, rather than a balance or original price?",
        )
        if selected_index is None:
            return line
        selected_occurrence: AmountEvidenceOccurrence = occurrences[selected_index]
        if ExpenseLineParsingProcessor.amount_occurrence_is_balance(line.source_text, selected_occurrence.start):
            return line
        amount: Decimal = ExpenseLineParsingProcessor.refund_adjusted_amount(
            line.source_text, ExpenseLineParsingProcessor.parse_amount_evidence(selected_occurrence.text)
        )
        return replace(line, amount=amount, issues=tuple(issue for issue in line.issues if issue != "multiple_amounts"))

    async def _choose_candidate_index(self, source_text: str, candidates: tuple[str, ...], instructions: str) -> int | None:
        if not self._MINIMUM_AMBIGUOUS_CANDIDATES <= len(candidates) <= self._MAXIMUM_CANDIDATES_WITH_NONE_OPTION:
            return None
        criteria: dict[str, str] = {
            f"candidate_{index}": f"The source occurrence {index + 1}: {candidate}" for index, candidate in enumerate(candidates)
        }
        criteria["none"] = "None of the candidate values is the transaction field."
        try:
            decision: ChoiceDecision = await self._gateway.choose(source_text, ChoiceQuestion(instructions=instructions, criteria=criteria))
        except SystemOneGatewayException:
            return None
        if decision.choice not in criteria or decision.probabilities[decision.choice] < self._minimum_choice_probability:
            return None
        if decision.choice == "none":
            return None
        return int(decision.choice.removeprefix("candidate_"))
