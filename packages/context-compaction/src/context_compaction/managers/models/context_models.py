from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from context_compaction.managers.models.records import ContextEntry, JevState

type MessageKind = Literal["user", "assistant"]
type EvidenceMode = Literal["full", "excerpt"]
type Variant = Literal["current", "careful"]


@dataclass(frozen=True)
class MessageSegment:
    id: str
    kind: MessageKind
    text: str

    @property
    def characters(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class ToolSegment:
    id: str
    text: str
    result: str
    kind: Literal["tool"] = "tool"

    @property
    def characters(self) -> int:
        return len(self.text) + len(self.result)


type Segment = MessageSegment | ToolSegment


@dataclass(frozen=True)
class PendingToolCall:
    line_number: int
    text: str


@dataclass(frozen=True)
class TrialCase:
    source: Path
    source_sha256: str
    start_line: int
    cutoff_line: int
    segments: tuple[Segment, ...]
    current_goal: str
    held_out_answer: str


@dataclass(frozen=True)
class Compaction:
    retained: tuple[Segment, ...]
    probabilities: Mapping[str, float]
    original_characters: int
    retained_characters: int
    context: tuple[ContextEntry, ...] = ()

    @property
    def reduction_percent(self) -> float:
        return round(100 * (self.original_characters - self.retained_characters) / self.original_characters, 1)


@dataclass(frozen=True)
class JevSettings:
    api_key: SecretStr
    base_url: str
    model: str


@dataclass(frozen=True)
class JevDecisionBatch:
    model: str
    usage: dict[str, int | None]
    probabilities: dict[str, float]


@dataclass(frozen=True)
class RetentionQuestion:
    instructions: str


@dataclass(frozen=True)
class PreparedCompaction:
    trial_case: TrialCase
    mode: EvidenceMode
    variant: Variant
    jev_state: JevState
    questions: Mapping[str, RetentionQuestion]


@dataclass(frozen=True)
class CompactionResult:
    compaction: Compaction
    jev_decision_batch: JevDecisionBatch
