import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class AmountEvidenceOccurrence:
    amount_text: str
    amount_start_index: int


class ExpenseLineEvidenceParser:
    _SHORT_YEAR_DIGITS: ClassVar[int] = 2
    _ISO_MONTH_GROUP: ClassVar[str] = "iso_month"
    _NAME_MONTH_GROUP: ClassVar[str] = "name_month"
    _NUMERIC_MONTH_GROUP: ClassVar[str] = "numeric_month"
    _AMOUNT_GROUP: ClassVar[str] = "amount"
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
    DATE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
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
    AMOUNT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(?<![\w.,])(?P<amount>\(\s*\$?\s*\d+(?:,\d{3})*\.\d{2}\s*\)|(?:-\$|\$-|[-$])?\d+(?:,\d{3})*\.\d{2})(?!(?:[\w.]|,\d))"
    )
    _REFUND_DESCRIPTION_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\brefund\b|\breturn processed\b", re.IGNORECASE)
    _NEGATED_REFUND_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\b(?:no|not|without|never)\s+refund\b", re.IGNORECASE)
    _BALANCE_FIELD_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\bbalance\s*(?:of\s*)?:?\s*$", re.IGNORECASE)

    @classmethod
    def date_month(cls, matched_date: re.Match[str]) -> int:
        if matched_date.group(cls._ISO_MONTH_GROUP) is not None:
            return int(matched_date.group(cls._ISO_MONTH_GROUP))
        if matched_date.group(cls._NAME_MONTH_GROUP) is not None:
            return cls._MONTH_NUMBERS[matched_date.group(cls._NAME_MONTH_GROUP)[:3].lower()]
        return int(matched_date.group(cls._NUMERIC_MONTH_GROUP))

    @staticmethod
    def date_day(matched_date: re.Match[str]) -> int:
        return int(matched_date.group("iso_day") or matched_date.group("name_day") or matched_date.group("numeric_day"))

    @classmethod
    def parse_date_evidence(cls, evidence: str, inferred_year: int | None) -> date | None:
        matched_date: re.Match[str] | None = cls._matched_date_evidence(evidence)
        if matched_date is None:
            return None
        issues: list[str] = []
        return cls.parse_date(matched_date, inferred_year, issues)

    @classmethod
    def _matched_date_evidence(cls, evidence: str) -> re.Match[str] | None:
        return cls.DATE_PATTERN.fullmatch(evidence.strip(" (),."))

    @classmethod
    def date_candidates(cls, source_text: str) -> tuple[str, ...]:
        return tuple(match.group() for match in cls.DATE_PATTERN.finditer(source_text))

    @classmethod
    def amount_candidates(cls, source_text: str) -> tuple[str, ...]:
        return tuple(match.group(cls._AMOUNT_GROUP) for match in cls.AMOUNT_PATTERN.finditer(source_text))

    @classmethod
    def amount_candidate_occurrences(cls, source_text: str) -> tuple[AmountEvidenceOccurrence, ...]:
        return tuple(
            AmountEvidenceOccurrence(match.group(cls._AMOUNT_GROUP), match.start(cls._AMOUNT_GROUP))
            for match in cls.AMOUNT_PATTERN.finditer(source_text)
        )

    @classmethod
    def parse_date(cls, matched_date: re.Match[str], inferred_year: int | None, issues: list[str]) -> date | None:
        year: int | None = cls._resolved_year(matched_date, inferred_year)
        if year is None:
            issues.append("year_not_inferable")
            return None
        return cls._validated_date(matched_date, year, issues)

    @classmethod
    def _resolved_year(cls, matched_date: re.Match[str], inferred_year: int | None) -> int | None:
        explicit_year: int | None = cls.explicit_year(matched_date)
        return explicit_year if explicit_year is not None else inferred_year

    @classmethod
    def _validated_date(cls, matched_date: re.Match[str], year: int, issues: list[str]) -> date | None:
        date_components: tuple[int, int] = cls._date_components(matched_date)
        month: int = date_components[0]
        day: int = date_components[1]
        return cls._constructed_date(year, month, day, issues)

    @classmethod
    def _date_components(cls, matched_date: re.Match[str]) -> tuple[int, int]:
        return cls.date_month(matched_date), cls.date_day(matched_date)

    @staticmethod
    def _constructed_date(year: int, month: int, day: int, issues: list[str]) -> date | None:
        try:
            return date(year, month, day)
        except ValueError:
            issues.append("invalid_date")
            return None

    @classmethod
    def explicit_year(cls, matched_date: re.Match[str]) -> int | None:
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
        return any(match.group(cls._AMOUNT_GROUP) == amount_text for match in cls.AMOUNT_PATTERN.finditer(source_text))

    @classmethod
    def amount_evidence_is_balance(cls, source_text: str, amount_text: str) -> bool:
        return any(
            cls.amount_occurrence_is_balance(source_text, match.start(cls._AMOUNT_GROUP))
            for match in cls.AMOUNT_PATTERN.finditer(source_text)
            if match.group(cls._AMOUNT_GROUP) == amount_text
        )

    @classmethod
    def amount_occurrence_is_balance(cls, source_text: str, amount_start_index: int) -> bool:
        return cls._BALANCE_FIELD_PATTERN.search(source_text[:amount_start_index]) is not None

    @classmethod
    def refund_adjusted_amount(cls, source_text: str, amount: Decimal, amount_start_index: int) -> Decimal:
        amount_context: str = cls._preceding_amount_context(source_text, amount_start_index)
        if amount > 0 and cls._is_refund_context(amount_context):
            return -amount
        return amount

    @classmethod
    def _preceding_amount_context(cls, source_text: str, amount_start_index: int) -> str:
        preceding_amount_end_index: int = 0
        for matched_amount in cls.AMOUNT_PATTERN.finditer(source_text):
            if matched_amount.start(cls._AMOUNT_GROUP) >= amount_start_index:
                break
            preceding_amount_end_index = matched_amount.end(cls._AMOUNT_GROUP)
        return source_text[preceding_amount_end_index:amount_start_index]

    @classmethod
    def _is_refund_context(cls, amount_context: str) -> bool:
        return cls._REFUND_DESCRIPTION_PATTERN.search(amount_context) is not None and cls._NEGATED_REFUND_PATTERN.search(amount_context) is None

    @classmethod
    def has_descriptive_text(cls, evidence: str) -> bool:
        remainder: str = cls._text_without_field_evidence(evidence)
        return any(character.isalpha() for character in remainder)

    @classmethod
    def _text_without_field_evidence(cls, evidence: str) -> str:
        matched_spans: list[tuple[int, int]] = [
            match.span() for pattern in (cls.DATE_PATTERN, cls.AMOUNT_PATTERN) for match in pattern.finditer(evidence)
        ]
        remainder: str = evidence
        for matched_span in sorted(matched_spans, reverse=True):
            span_start_index: int = matched_span[0]
            span_end_index: int = matched_span[1]
            remainder = remainder[:span_start_index] + remainder[span_end_index:]
        return remainder
