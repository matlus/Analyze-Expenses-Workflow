from __future__ import annotations

from context_compaction.managers.models.context_models import (
    EvidenceMode,
    PreparedCompaction,
    Segment,
    ToolSegment,
    TrialCase,
    Variant,
)
from context_compaction.managers.models.records import ToolEventSummary, TrialBaselines, TrialManifest
from context_compaction.managers.processors.extractors.context_baseline_extractor import recency_baseline
from context_compaction.managers.processors.extractors.context_question_extractor import build_questions
from context_compaction.managers.processors.extractors.context_state_extractor import build_state, redact


class ContextPreparationProcessor:
    @staticmethod
    def prepare(trial_case: TrialCase, mode: EvidenceMode, variant: Variant) -> PreparedCompaction:
        return PreparedCompaction(
            trial_case, mode, variant, build_state(trial_case.segments, trial_case.current_goal, mode), build_questions(trial_case.segments, variant)
        )

    @staticmethod
    def describe_context(trial_case: TrialCase, mode: EvidenceMode) -> TrialManifest:
        return TrialManifest(
            source=str(trial_case.source),
            source_sha256=trial_case.source_sha256,
            start_line=trial_case.start_line,
            cutoff_line=trial_case.cutoff_line,
            evidence_mode=mode,
            current_goal=redact(trial_case.current_goal),
            held_out_answer=redact(trial_case.held_out_answer),
            protected_messages=ContextPreparationProcessor._count_protected_messages(trial_case.segments),
            tool_events=ContextPreparationProcessor._summarize_tools(trial_case.segments),
            original_characters=sum(segment.characters for segment in trial_case.segments),
            trial_baselines=ContextPreparationProcessor._describe_baselines(trial_case.segments),
            measurement="Characters of chat and tool text, not model tokens or JSONL bytes.",
        )

    @staticmethod
    def _count_protected_messages(segments: tuple[Segment, ...]) -> int:
        return sum(not isinstance(segment, ToolSegment) for segment in segments)

    @staticmethod
    def _summarize_tools(segments: tuple[Segment, ...]) -> tuple[ToolEventSummary, ...]:
        return tuple(ToolEventSummary(segment.id, len(segment.text), len(segment.result)) for segment in segments if isinstance(segment, ToolSegment))

    @staticmethod
    def _describe_baselines(segments: tuple[Segment, ...]) -> TrialBaselines:
        return TrialBaselines(recency_baseline(segments, 0), recency_baseline(segments, 1), recency_baseline(segments, 3))
