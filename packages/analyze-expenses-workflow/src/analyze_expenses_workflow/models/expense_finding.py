from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExpenseFinding:
    evidence_id: str
    text: str
    source_line_numbers: tuple[int, ...]
