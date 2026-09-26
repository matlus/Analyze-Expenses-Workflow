from __future__ import annotations

from collections.abc import Mapping

from context_compaction.managers.models.context_models import Compaction, Segment, ToolSegment
from context_compaction.managers.models.records import ContextEntry, MessageContextEntry, ToolContextEntry
from context_compaction.managers.processors.extractors.context_state_extractor import redact


class ContextSelectionProcessor:
    @staticmethod
    def select(segments: tuple[Segment, ...], probability_by_tool_id: Mapping[str, float], threshold: float) -> Compaction:
        retained_segments: tuple[Segment, ...] = ContextSelectionProcessor._retain_segments(segments, probability_by_tool_id, threshold)
        context: tuple[ContextEntry, ...] = ContextSelectionProcessor._describe_context(retained_segments)
        return Compaction(
            retained_segments,
            probability_by_tool_id,
            sum(segment.characters for segment in segments),
            sum(segment.characters for segment in retained_segments),
            context,
        )

    @staticmethod
    def _retain_segments(segments: tuple[Segment, ...], probability_by_tool_id: Mapping[str, float], threshold: float) -> tuple[Segment, ...]:
        return tuple(segment for segment in segments if not isinstance(segment, ToolSegment) or probability_by_tool_id[segment.id] >= threshold)

    @staticmethod
    def _describe_context(segments: tuple[Segment, ...]) -> tuple[ContextEntry, ...]:
        return tuple(ContextSelectionProcessor._describe_segment(segment) for segment in segments)

    @staticmethod
    def _describe_segment(segment: Segment) -> ContextEntry:
        if isinstance(segment, ToolSegment):
            return ToolContextEntry(segment.id, "tool", redact(segment.text), redact(segment.result))
        return MessageContextEntry(segment.id, segment.kind, redact(segment.text))
