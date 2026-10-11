from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from context_compaction.managers.exceptions.context_compaction_exception import (
    RolloutRecordInvalidJsonException,
    RolloutResponsePayloadNotObjectException,
    RolloutToolCallIdDuplicateException,
    RolloutWindowIncompleteException,
)
from context_compaction.managers.models.context_models import MessageSegment, PendingToolCall, Segment, ToolSegment, TrialCase
from context_compaction.managers.processors.models.parsed_rollout_window import ParsedRolloutWindow
from context_compaction.managers.processors.models.rollout_resources import (
    RolloutMessageResource,
    RolloutResponseResource,
    RolloutTextResource,
    RolloutToolCallResource,
    RolloutToolOutputResource,
)
from context_compaction.managers.processors.models.rollout_scan_state import RolloutScanState, RolloutWindowSelection
from context_compaction.managers.processors.validators.validator_rollout_response import ValidatorRolloutResponse

POLL_SESSION_ID = re.compile(r"[\"']?session_id[\"']?\s*:\s*(\d+)")
RESULT_SESSION_ID = re.compile(r'["\']session_id["\']\s*:\s*(\d+)')


def _string_object(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict):
        return None
    candidate_mapping: dict[object, object] = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in candidate_mapping):
        return None
    return cast(dict[str, object], value)


def _tool_output_text(content: object) -> str:
    rollout_text_resources: list[RolloutTextResource] = ValidatorRolloutResponse.validate_tool_output_text(content)
    return "\n".join(rollout_text_resource.text for rollout_text_resource in rollout_text_resources)


def _message_text(rollout_message_resource: RolloutMessageResource) -> str:
    return "\n".join(rollout_text_resource.text for rollout_text_resource in rollout_message_resource.content)


def _result_text(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _tool_output_text(cast(list[object], output))
    return json.dumps(output, ensure_ascii=False)


def _merge_continuations(segments: Iterable[Segment]) -> tuple[Segment, ...]:
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


class ContextRolloutProcessor:
    USER_MESSAGE_KIND: Final[str] = "user"

    @staticmethod
    def load_context(source: Path, start_line: int, cutoff_line: int) -> TrialCase:
        unmerged_parsed_rollout_window: ParsedRolloutWindow = ContextRolloutProcessor._read_window(source, start_line, cutoff_line)
        logical_parsed_rollout_window: ParsedRolloutWindow = replace(
            unmerged_parsed_rollout_window, segments=_merge_continuations(unmerged_parsed_rollout_window.segments)
        )
        ContextRolloutProcessor._validate_window(logical_parsed_rollout_window)
        return ContextRolloutProcessor._build_case(logical_parsed_rollout_window)

    @staticmethod
    def _read_window(source: Path, start_line: int, cutoff_line: int) -> ParsedRolloutWindow:
        rollout_window_selection: RolloutWindowSelection = RolloutWindowSelection(source, start_line, cutoff_line)
        digest = sha256()
        with source.open("rb") as stream:
            rollout_scan_state: RolloutScanState = ContextRolloutProcessor._scan_records(stream, rollout_window_selection, digest.update)
        return ContextRolloutProcessor._build_window(rollout_window_selection, rollout_scan_state, digest.hexdigest())

    @staticmethod
    def _scan_records(
        raw_lines: Iterable[bytes], rollout_window_selection: RolloutWindowSelection, record_raw_line: Callable[[bytes], None]
    ) -> RolloutScanState:
        rollout_scan_state: RolloutScanState = RolloutScanState((), MappingProxyType({}), frozenset(), None)
        line_number: int
        raw_line: bytes
        for line_number, raw_line in enumerate(raw_lines, start=1):
            record_raw_line(raw_line)
            rollout_value_by_field_name: Mapping[str, object] | None = ContextRolloutProcessor._parse_record(
                raw_line, rollout_window_selection.source, line_number
            )
            response_value_by_field_name: Mapping[str, object] | None = ContextRolloutProcessor._selected_payload(
                rollout_value_by_field_name, rollout_window_selection, line_number, rollout_scan_state.held_out_answer
            )
            rollout_response_resource: RolloutResponseResource | None = (
                ValidatorRolloutResponse.validate(response_value_by_field_name) if response_value_by_field_name is not None else None
            )
            rollout_scan_state = ContextRolloutProcessor._apply_record(
                rollout_response_resource, line_number, rollout_window_selection.cutoff_line, rollout_scan_state
            )
        return rollout_scan_state

    @staticmethod
    def _apply_record(
        rollout_response_resource: RolloutResponseResource | None, line_number: int, cutoff_line: int, rollout_scan_state: RolloutScanState
    ) -> RolloutScanState:
        if line_number >= cutoff_line:
            return ContextRolloutProcessor._capture_answer(rollout_response_resource, rollout_scan_state)
        if isinstance(rollout_response_resource, RolloutMessageResource):
            return ContextRolloutProcessor._retain_message(rollout_response_resource, line_number, rollout_scan_state)
        if isinstance(rollout_response_resource, RolloutToolCallResource):
            return ContextRolloutProcessor._register_call(rollout_response_resource, line_number, rollout_scan_state)
        if isinstance(rollout_response_resource, RolloutToolOutputResource):
            return ContextRolloutProcessor._complete_call(rollout_response_resource, rollout_scan_state)
        return rollout_scan_state

    @staticmethod
    def _build_window(
        rollout_window_selection: RolloutWindowSelection, rollout_scan_state: RolloutScanState, source_sha256: str
    ) -> ParsedRolloutWindow:
        return ParsedRolloutWindow(
            rollout_window_selection.source,
            rollout_window_selection.start_line,
            rollout_window_selection.cutoff_line,
            rollout_scan_state.segments,
            tuple(sorted(pending_tool_call.line_number for pending_tool_call in rollout_scan_state.pending_call_by_id.values())),
            rollout_scan_state.held_out_answer,
            source_sha256,
        )

    @staticmethod
    def _parse_record(raw_line: bytes, source: Path, line_number: int) -> Mapping[str, object] | None:
        try:
            return _string_object(json.loads(raw_line))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RolloutRecordInvalidJsonException(source, line_number) from error

    @staticmethod
    def _selected_payload(
        rollout_value_by_field_name: Mapping[str, object] | None,
        rollout_window_selection: RolloutWindowSelection,
        line_number: int,
        held_out_answer: str | None,
    ) -> Mapping[str, object] | None:
        if (
            rollout_value_by_field_name is None
            or rollout_value_by_field_name.get("type") != "response_item"
            or line_number < rollout_window_selection.start_line
        ):
            return None
        response_value_by_field_name: Mapping[str, object] | None = _string_object(rollout_value_by_field_name.get("payload"))
        if response_value_by_field_name is None:
            if line_number < rollout_window_selection.cutoff_line:
                raise RolloutResponsePayloadNotObjectException(rollout_window_selection.source, line_number)
            return None
        if line_number >= rollout_window_selection.cutoff_line and (
            held_out_answer is not None
            or response_value_by_field_name.get("type") != "message"
            or response_value_by_field_name.get("role") != "assistant"
        ):
            return None
        return response_value_by_field_name

    @staticmethod
    def _capture_answer(rollout_response_resource: RolloutResponseResource | None, rollout_scan_state: RolloutScanState) -> RolloutScanState:
        if isinstance(rollout_response_resource, RolloutMessageResource):
            candidate_answer: str = _message_text(rollout_response_resource)
            if candidate_answer:
                return replace(rollout_scan_state, held_out_answer=candidate_answer)
        return rollout_scan_state

    @staticmethod
    def _retain_message(rollout_message_resource: RolloutMessageResource, line_number: int, rollout_scan_state: RolloutScanState) -> RolloutScanState:
        message_text: str = _message_text(rollout_message_resource)
        if message_text and not message_text.startswith("<recommended_plugins>"):
            message_segment: MessageSegment = MessageSegment(f"message_{line_number}", rollout_message_resource.role, message_text)
            return replace(rollout_scan_state, segments=(*rollout_scan_state.segments, message_segment))
        return rollout_scan_state

    @staticmethod
    def _register_call(
        rollout_tool_call_resource: RolloutToolCallResource, line_number: int, rollout_scan_state: RolloutScanState
    ) -> RolloutScanState:
        call_id: str = rollout_tool_call_resource.call_id
        if call_id in rollout_scan_state.pending_call_by_id or call_id in rollout_scan_state.completed_call_ids:
            call_fingerprint: str = sha256(call_id.encode("utf-8")).hexdigest()[:12]
            raise RolloutToolCallIdDuplicateException(line_number, call_fingerprint)
        pending_call_by_id: dict[str, PendingToolCall] = dict(rollout_scan_state.pending_call_by_id)
        pending_call_by_id[call_id] = PendingToolCall(line_number, f"{rollout_tool_call_resource.name}: {rollout_tool_call_resource.input}")
        return replace(rollout_scan_state, pending_call_by_id=MappingProxyType(pending_call_by_id))

    @staticmethod
    def _complete_call(rollout_tool_output_resource: RolloutToolOutputResource, rollout_scan_state: RolloutScanState) -> RolloutScanState:
        if rollout_tool_output_resource.call_id not in rollout_scan_state.pending_call_by_id:
            return rollout_scan_state
        pending_call_by_id: dict[str, PendingToolCall] = dict(rollout_scan_state.pending_call_by_id)
        pending_tool_call: PendingToolCall = pending_call_by_id.pop(rollout_tool_output_resource.call_id)
        tool_segment: ToolSegment = ContextRolloutProcessor._tool_segment(pending_tool_call, rollout_tool_output_resource)
        return replace(
            rollout_scan_state,
            pending_call_by_id=MappingProxyType(pending_call_by_id),
            completed_call_ids=rollout_scan_state.completed_call_ids | {rollout_tool_output_resource.call_id},
            segments=(*rollout_scan_state.segments, tool_segment),
        )

    @staticmethod
    def _tool_segment(pending_tool_call: PendingToolCall, rollout_tool_output_resource: RolloutToolOutputResource) -> ToolSegment:
        tool_result_text: str = _result_text(rollout_tool_output_resource.output)
        return ToolSegment(f"tool_{pending_tool_call.line_number}", pending_tool_call.text, tool_result_text)

    @staticmethod
    def _validate_window(parsed_rollout_window: ParsedRolloutWindow) -> None:
        problems: list[str] = []
        if not parsed_rollout_window.held_out_answer:
            problems.append("needs a later assistant answer")
        if parsed_rollout_window.pending_lines:
            problems.append(
                f"contains {len(parsed_rollout_window.pending_lines)} unfinished tool calls at lines {list(parsed_rollout_window.pending_lines)}"
            )
        if not any(segment.kind == ContextRolloutProcessor.USER_MESSAGE_KIND for segment in parsed_rollout_window.segments):
            problems.append("needs a user message")
        if not any(segment.kind == "tool" for segment in parsed_rollout_window.segments):
            problems.append("needs a completed tool call")
        if problems:
            window: str = f"{parsed_rollout_window.source}:{parsed_rollout_window.start_line}-{parsed_rollout_window.cutoff_line}"
            raise RolloutWindowIncompleteException(window, problems)

    @staticmethod
    def _build_case(parsed_rollout_window: ParsedRolloutWindow) -> TrialCase:
        user_messages: list[str] = [
            segment.text for segment in parsed_rollout_window.segments if segment.kind == ContextRolloutProcessor.USER_MESSAGE_KIND
        ]
        return TrialCase(
            parsed_rollout_window.source.resolve(),
            parsed_rollout_window.source_sha256,
            parsed_rollout_window.start_line,
            parsed_rollout_window.cutoff_line,
            parsed_rollout_window.segments,
            user_messages[-1],
            cast(str, parsed_rollout_window.held_out_answer),
        )
