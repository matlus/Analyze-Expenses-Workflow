import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar

from analyze_expenses_workflow.managers.processors.evidence_parsers.expense_line_evidence_parser import ExpenseLineEvidenceParser
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


@dataclass(frozen=True, slots=True)
class _LineYearEvidence:
    month: int
    explicit_year: int | None


class ExpenseLineParsingProcessor:
    _YEAR_ROLLOVER_MONTH_GAP: ClassVar[int] = 6
    _DESCRIPTION_EDGES: ClassVar[str] = " \t,|:-()"

    async def parse(self, expense_lines: Sequence[str]) -> tuple[ParsedExpenseLine, ...]:
        inferred_year: int | None = self.infer_year(expense_lines)
        return self._parse_lines(expense_lines, inferred_year)

    def _parse_lines(self, expense_lines: Sequence[str], inferred_year: int | None) -> tuple[ParsedExpenseLine, ...]:
        parsed_expense_lines: list[ParsedExpenseLine] = []
        for line_number in range(1, len(expense_lines) + 1):
            source_text: str = expense_lines[line_number - 1]
            issues: list[str] = []
            occurred_on: date | None = self._parse_line_date(source_text, inferred_year, issues)
            amount: Decimal | None = self._parse_line_amount(source_text, issues)
            description: str | None = self._parse_line_description(source_text, issues)
            parsed_expense_lines.append(ParsedExpenseLine(line_number, source_text, occurred_on, description, amount, tuple(issues)))
        return tuple(parsed_expense_lines)

    def infer_year(self, expense_lines: Iterable[str]) -> int | None:
        year_evidence: tuple[set[int], bool] = self._scan_year_evidence(expense_lines)
        explicit_years: set[int] = year_evidence[0]
        crosses_year_boundary: bool = year_evidence[1]
        return next(iter(explicit_years)) if len(explicit_years) == 1 and not crosses_year_boundary else None

    def _scan_year_evidence(self, expense_lines: Iterable[str]) -> tuple[set[int], bool]:
        line_year_evidence: tuple[_LineYearEvidence, ...] = self._collect_year_evidence(expense_lines)
        return self._summarize_year_evidence(line_year_evidence)

    def _collect_year_evidence(self, expense_lines: Iterable[str]) -> tuple[_LineYearEvidence, ...]:
        return tuple(line_year_evidence for source_text in expense_lines if (line_year_evidence := self._line_year_evidence(source_text)) is not None)

    @staticmethod
    def _line_year_evidence(source_text: str) -> _LineYearEvidence | None:
        matched_date: re.Match[str] | None = ExpenseLineParsingProcessor._single_date_match(source_text)
        if matched_date is None:
            return None
        explicit_year: int | None = ExpenseLineEvidenceParser.explicit_year(matched_date)
        validation_issues: list[str] = []
        if ExpenseLineEvidenceParser.parse_date(matched_date, explicit_year if explicit_year is not None else 2000, validation_issues) is None:
            return None
        return _LineYearEvidence(ExpenseLineEvidenceParser.date_month(matched_date), explicit_year)

    @staticmethod
    def _single_date_match(source_text: str) -> re.Match[str] | None:
        date_matches: list[re.Match[str]] = list(ExpenseLineEvidenceParser.DATE_PATTERN.finditer(source_text))
        return date_matches[0] if len(date_matches) == 1 else None

    def _summarize_year_evidence(self, line_year_evidence: tuple[_LineYearEvidence, ...]) -> tuple[set[int], bool]:
        explicit_years: set[int] = set()
        previous_line_year_evidence: _LineYearEvidence | None = None
        crosses_year_boundary: bool = False
        for line_year_evidence_item in line_year_evidence:
            if previous_line_year_evidence is not None and self._crosses_year_boundary(previous_line_year_evidence, line_year_evidence_item):
                crosses_year_boundary = True
            previous_line_year_evidence = line_year_evidence_item
            if line_year_evidence_item.explicit_year is not None:
                explicit_years.add(line_year_evidence_item.explicit_year)
        return explicit_years, crosses_year_boundary

    def _crosses_year_boundary(self, previous: _LineYearEvidence, current: _LineYearEvidence) -> bool:
        if previous.explicit_year is not None and previous.explicit_year == current.explicit_year:
            return False
        return abs(previous.month - current.month) >= self._YEAR_ROLLOVER_MONTH_GAP

    def _parse_line_date(self, source_text: str, inferred_year: int | None, issues: list[str]) -> date | None:
        matched_date: re.Match[str] | None = self._one_date_match(source_text, issues)
        if matched_date is None:
            return None
        return ExpenseLineEvidenceParser.parse_date(matched_date, inferred_year, issues)

    @staticmethod
    def _one_date_match(source_text: str, issues: list[str]) -> re.Match[str] | None:
        date_matches: list[re.Match[str]] = list(ExpenseLineEvidenceParser.DATE_PATTERN.finditer(source_text))
        if len(date_matches) != 1:
            issues.append("missing_date" if not date_matches else "multiple_dates")
            return None
        return date_matches[0]

    def _parse_line_amount(self, source_text: str, issues: list[str]) -> Decimal | None:
        matched_amount: re.Match[str] | None = self._one_transaction_amount_match(source_text, issues)
        if matched_amount is None:
            return None
        return ExpenseLineEvidenceParser.refund_adjusted_amount(
            source_text,
            ExpenseLineEvidenceParser.parse_amount_evidence(matched_amount.group("amount")),
            matched_amount.start("amount"),
        )

    @staticmethod
    def _one_transaction_amount_match(source_text: str, issues: list[str]) -> re.Match[str] | None:
        matched_amount: re.Match[str] | None = ExpenseLineParsingProcessor._single_amount_match(source_text, issues)
        if matched_amount is None:
            return None
        if ExpenseLineEvidenceParser.amount_occurrence_is_balance(source_text, matched_amount.start("amount")):
            issues.append("nontransaction_amount")
            return None
        return matched_amount

    @staticmethod
    def _single_amount_match(source_text: str, issues: list[str]) -> re.Match[str] | None:
        amount_matches: list[re.Match[str]] = list(ExpenseLineEvidenceParser.AMOUNT_PATTERN.finditer(source_text))
        if len(amount_matches) != 1:
            issues.append("missing_amount" if not amount_matches else "multiple_amounts")
            return None
        return amount_matches[0]

    def _parse_line_description(self, source_text: str, issues: list[str]) -> str | None:
        description_text: str = source_text
        removal_spans: list[tuple[int, int]] = [
            match.span()
            for pattern in (ExpenseLineEvidenceParser.DATE_PATTERN, ExpenseLineEvidenceParser.AMOUNT_PATTERN)
            for match in pattern.finditer(source_text)
        ]
        for removal_span in sorted(removal_spans, reverse=True):
            span_start_index: int = removal_span[0]
            span_end_index: int = removal_span[1]
            description_text = description_text[:span_start_index] + description_text[span_end_index:]
        description_text = " ".join(description_text.strip(self._DESCRIPTION_EDGES).split())
        description: str | None = description_text.strip(self._DESCRIPTION_EDGES) or None
        if description is None:
            issues.append("missing_description")
        return description
