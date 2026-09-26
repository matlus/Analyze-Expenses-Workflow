"""Build Jev decisions and apply them without changing source evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal, final

from context_compaction.errors import ContextCompactionBusinessError
from context_compaction.records import BaselineMetrics, JevState, MessageStateEntry, ToolStateEntry
from context_compaction.rollout import Segment, ToolSegment

type EvidenceMode = Literal["full", "excerpt"]

MAX_STATE_CHARACTERS: dict[EvidenceMode, int] = {"full": 90_000, "excerpt": 24_000}
SHORT_RESULT_CHARACTERS = 500
MINIMUM_EXCERPT_SPACING = 140
MAXIMUM_EXCERPT_WINDOWS = 2
REDACTIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:OPEN_ROUTER_KEY|TYPESAFE_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\b(?:sk|ghp|gho|ghu|ghs)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}"),
)


@final
class CompactionDecisionError(ContextCompactionBusinessError):
    """A caller supplied an invalid retention threshold or decision set."""


@dataclass(frozen=True)
class Compaction:
    retained: tuple[Segment, ...]
    probabilities: Mapping[str, float]
    original_characters: int
    retained_characters: int

    @property
    def reduction_percent(self) -> float:
        return round(100 * (self.original_characters - self.retained_characters) / self.original_characters, 1)


def redact(source_text: str) -> str:
    for pattern in REDACTIONS:
        source_text = pattern.sub("[REDACTED]", source_text)
    return source_text


def _excerpt(tool_result: str, goal: str) -> str:
    if len(tool_result) <= SHORT_RESULT_CHARACTERS:
        return tool_result
    normalized: str = tool_result.replace("\r\n", "\n").replace("\\n", "\n")
    terms: set[str] = {
        term
        for term in re.findall(r"[a-z][a-z0-9.-]{4,}", goal.lower())
        if term not in {"about", "because", "could", "there", "their", "these", "would"}
    }
    windows: list[tuple[int, int, str]] = []
    lowered: str = normalized.lower()
    for term in sorted(terms):
        for match in list(re.finditer(re.escape(term), lowered))[:6]:
            position: int = max(0, match.start() - 65)
            snippet: str = normalized[position : min(len(normalized), match.end() + 90)].replace("\n", " ")
            windows.append((sum(other in snippet.lower() for other in terms), position, snippet))
    picked_excerpt_windows: list[tuple[int, str]] = []
    for window in sorted(windows, key=lambda row: (-row[0], row[1])):
        position: int = window[1]
        snippet: str = window[2]
        if all(abs(position - picked_window[0]) > MINIMUM_EXCERPT_SPACING for picked_window in picked_excerpt_windows):
            picked_excerpt_windows.append((position, snippet))
        if len(picked_excerpt_windows) == MAXIMUM_EXCERPT_WINDOWS:
            break
    selected: str = "\n".join(picked_window[1] for picked_window in sorted(picked_excerpt_windows))
    return normalized[:120] + "\n[Relevant source excerpts]\n" + selected[:360] + "\n[End]\n" + normalized[-70:]


def build_state(segments: tuple[Segment, ...], current_goal: str, mode: EvidenceMode) -> JevState:
    timeline: list[MessageStateEntry | ToolStateEntry] = []
    for segment in segments:
        if isinstance(segment, ToolSegment):
            result: str = segment.result
            redacted_tool_call: str = redact(segment.text)
            redacted_tool_result: str = redact(result)
            timeline.append(
                ToolStateEntry(
                    id=segment.id,
                    kind="tool_call_and_result",
                    call=redacted_tool_call if mode == "full" else redacted_tool_call[:280],
                    result=redacted_tool_result if mode == "full" else _excerpt(redacted_tool_result, current_goal),
                    full_result_characters=str(len(result)),
                )
            )
        else:
            timeline.append(MessageStateEntry(id=segment.id, kind=segment.kind, text=redact(segment.text)))
    jev_state: JevState = JevState(current_goal=redact(current_goal), timeline=tuple(timeline))
    length: int = len(json.dumps(asdict(jev_state), ensure_ascii=False))
    if length > MAX_STATE_CHARACTERS[mode]:
        raise CompactionDecisionError(f"Jev state has {length} characters; {mode} trial limit is {MAX_STATE_CHARACTERS[mode]}.")
    return jev_state


def apply_decisions(segments: tuple[Segment, ...], probabilities: Mapping[str, float], threshold: float) -> Compaction:
    if not 0 <= threshold <= 1:
        raise CompactionDecisionError(f"Threshold must be between zero and one; received {threshold}.")
    expected_tool_ids: set[str] = {segment.id for segment in segments if isinstance(segment, ToolSegment)}
    invalid_probabilities: dict[str, float] = {tool_id: probabilities[tool_id] for tool_id in probabilities if not 0 <= probabilities[tool_id] <= 1}
    if set(probabilities) != expected_tool_ids or invalid_probabilities:
        expected_and_received_ids: str = f"expected={sorted(expected_tool_ids)}, received={sorted(probabilities)}"
        raise CompactionDecisionError(
            f"Jev probabilities do not match the complete tool-event set; {expected_and_received_ids}, invalid={invalid_probabilities}."
        )
    retained: tuple[Segment, ...] = tuple(
        segment for segment in segments if not isinstance(segment, ToolSegment) or probabilities[segment.id] >= threshold
    )
    return Compaction(
        retained=retained,
        probabilities=probabilities,
        original_characters=sum(segment.characters for segment in segments),
        retained_characters=sum(segment.characters for segment in retained),
    )


def recency_baseline(segments: tuple[Segment, ...], keep_recent: int) -> BaselineMetrics:
    tools: tuple[ToolSegment, ...] = tuple(segment for segment in segments if isinstance(segment, ToolSegment))
    kept: set[str] = {segment.id for segment in tools[-keep_recent:]} if keep_recent else set()
    original: int = sum(segment.characters for segment in segments)
    retained: int = sum(segment.characters for segment in segments if not isinstance(segment, ToolSegment) or segment.id in kept)
    return BaselineMetrics(retained_characters=retained, reduction_percent=round(100 * (original - retained) / original, 1))
