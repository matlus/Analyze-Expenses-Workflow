from __future__ import annotations

import re

from context_compaction.managers.models.context_models import EvidenceMode, Segment, ToolSegment
from context_compaction.managers.models.records import JevState, MessageStateEntry, ToolStateEntry

SHORT_RESULT_CHARACTERS = 500
MINIMUM_EXCERPT_SPACING = 140
MAXIMUM_EXCERPT_WINDOWS = 2
REDACTIONS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:OPEN_ROUTER_KEY|TYPESAFE_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN)(?:\\?[\"'])?\s*[:=]\s*(?:\\\".*?\\\"|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"
    ),
    re.compile(r"\bsk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\b(?:sk|ghp|gho|ghu|ghs)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}"),
)


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
            tool_result: str = segment.result
            redacted_tool_call: str = redact(segment.text)
            redacted_tool_result: str = redact(tool_result)
            timeline.append(
                ToolStateEntry(
                    id=segment.id,
                    kind="tool_call_and_result",
                    call=redacted_tool_call if mode == "full" else redacted_tool_call[:280],
                    result=redacted_tool_result if mode == "full" else _excerpt(redacted_tool_result, current_goal),
                    full_result_characters=str(len(tool_result)),
                )
            )
        else:
            timeline.append(MessageStateEntry(id=segment.id, kind=segment.kind, text=redact(segment.text)))
    jev_state: JevState = JevState(current_goal=redact(current_goal), timeline=tuple(timeline))
    return jev_state
