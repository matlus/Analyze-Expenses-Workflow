from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from context_compaction.managers.models.context_models import PendingToolCall, Segment


@dataclass(frozen=True)
class RolloutScanState:
    segments: tuple[Segment, ...]
    pending_call_by_id: Mapping[str, PendingToolCall]
    completed_call_ids: frozenset[str]
    held_out_answer: str | None


@dataclass(frozen=True)
class RolloutWindowSelection:
    source: Path
    start_line: int
    cutoff_line: int
