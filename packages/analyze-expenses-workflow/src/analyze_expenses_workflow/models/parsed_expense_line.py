from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ParsedExpenseLine:
    line_number: int
    source_text: str
    occurred_on: date | None
    description: str | None
    amount: Decimal | None
    issues: tuple[str, ...]
