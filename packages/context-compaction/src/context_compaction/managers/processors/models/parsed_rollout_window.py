from dataclasses import dataclass
from pathlib import Path

from context_compaction.managers.models.context_models import Segment


@dataclass(frozen=True)
class ParsedRolloutWindow:
    source: Path
    start_line: int
    cutoff_line: int
    segments: tuple[Segment, ...]
    pending_lines: tuple[int, ...]
    held_out_answer: str | None
    source_sha256: str
