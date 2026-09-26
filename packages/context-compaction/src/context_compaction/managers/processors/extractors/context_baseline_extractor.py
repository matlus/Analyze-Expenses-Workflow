from context_compaction.managers.models.context_models import Segment, ToolSegment
from context_compaction.managers.models.records import BaselineMetrics


def recency_baseline(segments: tuple[Segment, ...], keep_recent: int) -> BaselineMetrics:
    tools: tuple[ToolSegment, ...] = tuple(segment for segment in segments if isinstance(segment, ToolSegment))
    kept: set[str] = {segment.id for segment in tools[-keep_recent:]} if keep_recent else set()
    original: int = sum(segment.characters for segment in segments)
    retained: int = sum(segment.characters for segment in segments if not isinstance(segment, ToolSegment) or segment.id in kept)
    return BaselineMetrics(retained_characters=retained, reduction_percent=round(100 * (original - retained) / original, 1))
