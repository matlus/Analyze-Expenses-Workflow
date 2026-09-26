"""Typed records for trial evidence and persisted results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MessageStateEntry:
    id: str
    kind: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class ToolStateEntry:
    id: str
    kind: Literal["tool_call_and_result"]
    call: str
    result: str
    full_result_characters: str


@dataclass(frozen=True)
class JevState:
    current_goal: str
    timeline: tuple[MessageStateEntry | ToolStateEntry, ...]


@dataclass(frozen=True)
class JevQuestionRecord:
    type: Literal["noul"]
    instructions: str


@dataclass(frozen=True)
class JevRequestRecord:
    state: JevState
    questions: dict[str, JevQuestionRecord]


@dataclass(frozen=True)
class BaselineMetrics:
    retained_characters: int
    reduction_percent: float


@dataclass(frozen=True)
class TrialBaselines:
    drop_all_tools: BaselineMetrics
    keep_newest_tool: BaselineMetrics
    keep_newest_three_tools: BaselineMetrics


@dataclass(frozen=True)
class ToolEventSummary:
    id: str
    call_characters: int
    result_characters: int


@dataclass(frozen=True)
class TrialManifest:
    source: str
    source_sha256: str
    start_line: int
    cutoff_line: int
    evidence_mode: Literal["full", "excerpt"]
    current_goal: str
    held_out_answer: str
    protected_messages: int
    tool_events: tuple[ToolEventSummary, ...]
    original_characters: int
    baselines: TrialBaselines
    measurement: str


@dataclass(frozen=True)
class MessageContextEntry:
    id: str
    kind: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class ToolContextEntry:
    id: str
    kind: Literal["tool"]
    text: str
    result: str


type ContextEntry = MessageContextEntry | ToolContextEntry


@dataclass(frozen=True)
class DecisionRecord:
    id: str
    keep_probability: float
    action: Literal["keep", "proposed_drop"]


@dataclass(frozen=True)
class TrialResultRecord:
    variant: Literal["current", "careful"]
    repeat: int
    model: str
    usage: dict[str, int | None]
    reduction_percent: float
    original_characters: int
    retained_characters: int
    required_tools_retained: bool
    decisions: tuple[DecisionRecord, ...]
    compacted_file: str
