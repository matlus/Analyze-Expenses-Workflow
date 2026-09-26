"""Read a bounded, real Codex task from its local JSONL rollout."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast, final

from context_compaction.errors import ContextCompactionBusinessError

type MessageKind = Literal["user", "assistant"]

POLL_SESSION_ID = re.compile(r"[\"']?session_id[\"']?\s*:\s*(\d+)")
RESULT_SESSION_ID = re.compile(r'["\']session_id["\']\s*:\s*(\d+)')


@final
class RolloutError(ContextCompactionBusinessError):
    """The selected Codex rollout window cannot form a trial case."""


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


def _string_object(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict):
        return None
    candidate_mapping: dict[object, object] = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in candidate_mapping):
        return None
    return cast(dict[str, object], value)


def _message_text(content: object) -> str:
    if not isinstance(content, list):
        raise RolloutError(f"Selected message content must be a list; received {type(content).__name__}.")
    message_text_parts: list[str] = []
    content_parts: list[object] = cast(list[object], content)
    for raw_part in content_parts:
        part: Mapping[str, object] | None = _string_object(raw_part)
        if part is None or part.get("type") not in {None, "text", "input_text", "output_text"} or not isinstance(part.get("text"), str):
            content_kind: object = part.get("type") if part is not None else type(raw_part).__name__
            supported_kinds: str = "text, input_text, output_text"
            raise RolloutError(
                f"Selected conversation contains unsupported nontext content: kind={str(content_kind)[:40]!r}; supported={supported_kinds}."
            )
        message_text_parts.append(cast(str, part["text"]))
    return "\n".join(message_text_parts)


def _result_text(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _message_text(cast(list[object], output))
    return json.dumps(output, ensure_ascii=False)


def _merge_continuations(segments: Sequence[Segment]) -> tuple[Segment, ...]:
    merged_segments: list[Segment] = []
    for candidate_segment in segments:
        if (
            isinstance(candidate_segment, ToolSegment)
            and "tools.write_stdin(" in candidate_segment.text
            and merged_segments
            and isinstance(merged_segments[-1], ToolSegment)
        ):
            poll_match: re.Match[str] | None = POLL_SESSION_ID.search(candidate_segment.text)
            previous_tool_segment: ToolSegment = merged_segments[-1]
            previous_session_ids: set[str] = set(RESULT_SESSION_ID.findall(previous_tool_segment.result))
            if poll_match is not None and poll_match.group(1) in previous_session_ids:
                merged_segments[-1] = replace(
                    previous_tool_segment,
                    text=previous_tool_segment.text + "\n[continued by poll]\n" + candidate_segment.text,
                    result=previous_tool_segment.result + "\n[continued output]\n" + candidate_segment.result,
                )
                continue
        merged_segments.append(candidate_segment)
    return tuple(merged_segments)


def load_case(source: Path, start_line: int, cutoff_line: int) -> TrialCase:
    if start_line < 1 or cutoff_line <= start_line:
        raise RolloutError(f"The start line must be positive and precede the held-out answer; start={start_line}, cutoff={cutoff_line}.")
    if not source.is_file():
        raise RolloutError(f"Codex rollout does not exist: {source}")

    segments: list[Segment] = []
    pending_call_by_id: dict[str, PendingToolCall] = {}
    completed_call_ids: set[str] = set()
    held_out_answer: str | None = None
    digest = sha256()
    with source.open("rb") as stream:
        line_number: int
        raw_line: bytes
        for line_number, raw_line in enumerate(stream, start=1):
            digest.update(raw_line)
            try:
                record: Mapping[str, object] | None = _string_object(json.loads(raw_line))
            except json.JSONDecodeError as error:
                raise RolloutError(f"Invalid JSONL record at line {line_number} in {source}.") from error
            if record is None or record.get("type") != "response_item":
                continue
            payload: Mapping[str, object] | None = _string_object(record.get("payload"))
            if payload is None:
                continue
            item_type: object = payload.get("type")
            if line_number >= cutoff_line:
                if held_out_answer is None and item_type == "message" and payload.get("role") == "assistant":
                    candidate_answer: str = _message_text(payload.get("content"))
                    if candidate_answer:
                        held_out_answer = candidate_answer
                continue
            if line_number < start_line:
                continue
            if item_type == "message" and payload.get("role") in {"user", "assistant"}:
                text: str = _message_text(payload.get("content"))
                if text and not text.startswith("<recommended_plugins>"):
                    role: MessageKind = "user" if payload.get("role") == "user" else "assistant"
                    segments.append(MessageSegment(f"message_{line_number}", role, text))
            elif item_type == "custom_tool_call":
                call_id: object = payload.get("call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise RolloutError(f"Tool call ID at line {line_number} must be a nonempty string; received {type(call_id).__name__}.")
                if call_id in pending_call_by_id or call_id in completed_call_ids:
                    call_fingerprint: str = sha256(call_id.encode("utf-8")).hexdigest()[:12]
                    raise RolloutError(f"Duplicate tool call ID at line {line_number}; fingerprint={call_fingerprint}.")
                tool_name: object = payload.get("name")
                tool_input: object = payload.get("input")
                if not isinstance(tool_name, str) or not isinstance(tool_input, str):
                    received_field_types: str = f"name={type(tool_name).__name__}, input={type(tool_input).__name__}"
                    raise RolloutError(f"Tool call at line {line_number} needs string name and input; received {received_field_types}.")
                pending_call_by_id[call_id] = PendingToolCall(line_number, f"{tool_name}: {tool_input}")
            elif item_type == "custom_tool_call_output":
                call_id = payload.get("call_id")
                if isinstance(call_id, str) and call_id in pending_call_by_id:
                    pending_tool_call: PendingToolCall = pending_call_by_id.pop(call_id)
                    completed_call_ids.add(call_id)
                    segments.append(ToolSegment(f"tool_{pending_tool_call.line_number}", pending_tool_call.text, _result_text(payload.get("output"))))

    logical_segments: tuple[Segment, ...] = _merge_continuations(segments)
    window: str = f"{source}:{start_line}-{cutoff_line}"
    if not held_out_answer:
        raise RolloutError(f"The selected window {window} needs a later assistant answer.")
    if not logical_segments:
        raise RolloutError(f"The selected window {window} needs context segments.")
    if pending_call_by_id:
        pending_lines: list[int] = sorted(pending_call.line_number for pending_call in pending_call_by_id.values())
        raise RolloutError(f"The selected window {window} contains {len(pending_lines)} unfinished tool calls at lines {pending_lines}.")
    user_messages: list[str] = [segment.text for segment in logical_segments if segment.kind == "user"]
    if not user_messages:
        raise RolloutError(f"The selected window {window} needs a user message.")
    if not any(segment.kind == "tool" for segment in logical_segments):
        raise RolloutError(f"The selected window {window} needs a completed tool call.")
    return TrialCase(source.resolve(), digest.hexdigest(), start_line, cutoff_line, logical_segments, user_messages[-1], held_out_answer)
