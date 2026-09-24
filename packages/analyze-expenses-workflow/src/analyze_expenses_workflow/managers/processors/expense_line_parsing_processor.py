import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar

from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


@dataclass(frozen=True)
class AmountEvidenceOccurrence:
    text: str
    start: int


class ExpenseLineParsingProcessor:
    _SHORT_YEAR_DIGITS: ClassVar[int] = 2
    _YEAR_ROLLOVER_MONTH_GAP: ClassVar[int] = 6
    _MONTH_NUMBERS: ClassVar[dict[str, int]] = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    _DATE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"""
        (?<![\w/.-])(?:
            (?P<iso_year>\d{4})-(?P<iso_month>\d{1,2})-(?P<iso_day>\d{1,2})
            |(?P<name_month>January|February|March|April|May|June|July|August|September|October|November|December|
                Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(?P<name_day>\d{1,2})
                (?:,?\s+(?P<name_year>\d{4}|\d{2}))?
            |(?P<numeric_month>\d{1,2})[/-](?P<numeric_day>\d{1,2})(?:[/-](?P<numeric_year>\d{4}|\d{2}))?
        )(?!(?:[\w/-]|\.\d))
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    _AMOUNT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(?<![\w.,])(?P<amount>\(\s*\$?\s*\d+(?:,\d{3})*\.\d{2}\s*\)|(?:-\$|\$-|[-$])?\d+(?:,\d{3})*\.\d{2})(?!(?:[\w.]|,\d))"
    )
    _REFUND_DESCRIPTION_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\brefund\b|\breturn processed\b", re.IGNORECASE)
    _NEGATED_REFUND_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\b(?:no|not|without|never)\s+refund\b", re.IGNORECASE)
    _BALANCE_FIELD_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\bbalance\s*(?:of\s*)?:?\s*$", re.IGNORECASE)
    _DESCRIPTION_EDGES: ClassVar[str] = " \t,|:-()"

    async def parse(self, expense_lines: list[str]) -> tuple[ParsedExpenseLine, ...]:
        inferred_year: int | None = self.infer_year(expense_lines)
        return tuple(self._parse_line(line_number, source_text, inferred_year) for line_number, source_text in enumerate(expense_lines, start=1))

    def infer_year(self, expense_lines: list[str]) -> int | None:
        explicit_years: set[int] = set()
        previous_month: int | None = None
        crosses_year_boundary: bool = False
        for source_text in expense_lines:
            date_matches: list[re.Match[str]] = list(self._DATE_PATTERN.finditer(source_text))
            if len(date_matches) == 1:
                matched_date: re.Match[str] = date_matches[0]
                month: int = self._date_month(matched_date)
                if previous_month is not None and previous_month - month >= self._YEAR_ROLLOVER_MONTH_GAP:
                    crosses_year_boundary = True
                previous_month = month
                explicit_year: int | None = self._explicit_year(matched_date)
                if explicit_year is not None:
                    explicit_years.add(explicit_year)
        return next(iter(explicit_years)) if len(explicit_years) == 1 and not crosses_year_boundary else None

    @classmethod
    def _date_month(cls, matched_date: re.Match[str]) -> int:
        if matched_date.group("iso_month") is not None:
            return int(matched_date.group("iso_month"))
        if matched_date.group("name_month") is not None:
            return cls._MONTH_NUMBERS[matched_date.group("name_month")[:3].lower()]
        return int(matched_date.group("numeric_month"))

    @classmethod
    def parse_date_evidence(cls, evidence: str, inferred_year: int | None) -> date | None:
        matched_date: re.Match[str] | None = cls._DATE_PATTERN.fullmatch(evidence.strip(" (),."))
        if matched_date is None:
            return None
        issues: list[str] = []
        return cls._parse_date(matched_date, inferred_year, issues)

    @classmethod
    def date_candidates(cls, source_text: str) -> tuple[str, ...]:
        return tuple(match.group() for match in cls._DATE_PATTERN.finditer(source_text))

    @classmethod
    def amount_candidates(cls, source_text: str) -> tuple[str, ...]:
        return tuple(match.group("amount") for match in cls._AMOUNT_PATTERN.finditer(source_text))

    @classmethod
    def amount_candidate_occurrences(cls, source_text: str) -> tuple[AmountEvidenceOccurrence, ...]:
        return tuple(AmountEvidenceOccurrence(match.group("amount"), match.start("amount")) for match in cls._AMOUNT_PATTERN.finditer(source_text))

    def _parse_line(self, line_number: int, source_text: str, inferred_year: int | None) -> ParsedExpenseLine:
        issues: list[str] = []
        occurred_on: date | None = self._parse_line_date(source_text, inferred_year, issues)
        amount: Decimal | None = self._parse_line_amount(source_text, issues)
        description: str | None = self._parse_line_description(source_text, issues)

        return ParsedExpenseLine(
            line_number=line_number,
            source_text=source_text,
            occurred_on=occurred_on,
            description=description,
            amount=amount,
            issues=tuple(issues),
        )

    def _parse_line_date(self, source_text: str, inferred_year: int | None, issues: list[str]) -> date | None:
        date_matches: list[re.Match[str]] = list(self._DATE_PATTERN.finditer(source_text))
        if len(date_matches) != 1:
            issues.append("missing_date" if not date_matches else "multiple_dates")
            return None
        return self._parse_date(date_matches[0], inferred_year, issues)

    def _parse_line_amount(self, source_text: str, issues: list[str]) -> Decimal | None:
        amount_matches: list[re.Match[str]] = list(self._AMOUNT_PATTERN.finditer(source_text))
        if len(amount_matches) != 1:
            issues.append("missing_amount" if not amount_matches else "multiple_amounts")
            return None
        if self.amount_occurrence_is_balance(source_text, amount_matches[0].start("amount")):
            issues.append("nontransaction_amount")
            return None
        amount: Decimal = self.refund_adjusted_amount(source_text, self.parse_amount_evidence(amount_matches[0].group("amount")))
        return amount

    def _parse_line_description(
        self,
        source_text: str,
        issues: list[str],
    ) -> str | None:
        description_text: str = source_text
        removal_spans: list[tuple[int, int]] = [
            match.span() for pattern in (self._DATE_PATTERN, self._AMOUNT_PATTERN) for match in pattern.finditer(source_text)
        ]
        for start, end in sorted(removal_spans, reverse=True):
            description_text = description_text[:start] + description_text[end:]
        description_text = " ".join(description_text.strip(self._DESCRIPTION_EDGES).split())
        description: str | None = description_text.strip(self._DESCRIPTION_EDGES) or None
        if description is None:
            issues.append("missing_description")
        return description

    @classmethod
    def _parse_date(cls, matched_date: re.Match[str], inferred_year: int | None, issues: list[str]) -> date | None:
        explicit_year: int | None = cls._explicit_year(matched_date)
        year: int | None = explicit_year if explicit_year is not None else inferred_year
        if year is None:
            issues.append("year_not_inferable")
            return None

        month: int
        day: int
        if matched_date.group("iso_month") is not None:
            month = int(matched_date.group("iso_month"))
            day = int(matched_date.group("iso_day"))
        elif matched_date.group("name_month") is not None:
            month = cls._MONTH_NUMBERS[matched_date.group("name_month")[:3].lower()]
            day = int(matched_date.group("name_day"))
        else:
            month = int(matched_date.group("numeric_month"))
            day = int(matched_date.group("numeric_day"))
        try:
            return date(year, month, day)
        except ValueError:
            issues.append("invalid_date")
            return None

    @classmethod
    def _explicit_year(cls, matched_date: re.Match[str]) -> int | None:
        year_text: str | None = matched_date.group("iso_year") or matched_date.group("name_year") or matched_date.group("numeric_year")
        if year_text is None:
            return None
        year: int = int(year_text)
        return 2000 + year if len(year_text) == cls._SHORT_YEAR_DIGITS else year

    @staticmethod
    def parse_amount_evidence(amount_text: str) -> Decimal:
        if amount_text.startswith("("):
            return -Decimal("".join(amount_text[1:-1].replace("$", "").replace(",", "").split()))
        return Decimal(amount_text.replace("$", "").replace(",", ""))

    @classmethod
    def amount_evidence_matches_source(cls, source_text: str, amount_text: str) -> bool:
        return any(match.group("amount") == amount_text for match in cls._AMOUNT_PATTERN.finditer(source_text))

    @classmethod
    def amount_evidence_is_balance(cls, source_text: str, amount_text: str) -> bool:
        return any(
            cls.amount_occurrence_is_balance(source_text, match.start("amount"))
            for match in cls._AMOUNT_PATTERN.finditer(source_text)
            if match.group("amount") == amount_text
        )

    @classmethod
    def amount_occurrence_is_balance(cls, source_text: str, start: int) -> bool:
        return cls._BALANCE_FIELD_PATTERN.search(source_text[:start]) is not None

    @classmethod
    def refund_adjusted_amount(cls, source_text: str, amount: Decimal) -> Decimal:
        if amount > 0 and cls._REFUND_DESCRIPTION_PATTERN.search(source_text) and not cls._NEGATED_REFUND_PATTERN.search(source_text):
            return -amount
        return amount

    @classmethod
    def has_descriptive_text(cls, evidence: str) -> bool:
        matched_spans: list[tuple[int, int]] = [
            match.span() for pattern in (cls._DATE_PATTERN, cls._AMOUNT_PATTERN) for match in pattern.finditer(evidence)
        ]
        remainder: str = evidence
        for start, end in sorted(matched_spans, reverse=True):
            remainder = remainder[:start] + remainder[end:]
        return any(character.isalpha() for character in remainder)
