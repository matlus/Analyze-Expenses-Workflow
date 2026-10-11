from __future__ import annotations

import json
from collections.abc import Mapping, Set as AbstractSet
from dataclasses import asdict
from pathlib import Path

from context_compaction.managers.exceptions.context_compaction_exception import (
    CompactionContextEmptyException,
    CompactionThresholdOutOfRangeException,
    ContextPreparationInvalidException,
    JevStateTooLargeException,
    PreparedQuestionsMismatchException,
    PreparedStateMismatchException,
    RetentionProbabilitiesInvalidException,
    RolloutFileNotFoundException,
    RolloutLineRangeInvalidException,
    TrialOptionsInvalidException,
)
from context_compaction.managers.models.context_models import (
    MAXIMUM_TRIAL_RUNS,
    EvidenceMode,
    PreparedCompaction,
    RetentionQuestion,
    Segment,
    ToolSegment,
    TrialCase,
    Variant,
)
from context_compaction.managers.models.records import JevState
from context_compaction.managers.validators.primitive_validators.validator_probability import ValidatorProbability, ValidatorRepeatCount

MAX_STATE_CHARACTERS_BY_EVIDENCE_MODE: dict[EvidenceMode, int] = {"full": 90_000, "excerpt": 24_000}


def _is_tool_segment(segment: Segment) -> bool:
    return isinstance(segment, ToolSegment)


def _tool_segment_ids(segments: tuple[Segment, ...]) -> set[str]:
    return {segment.id for segment in segments if _is_tool_segment(segment)}


def _has_nonempty_segment_text(segments: tuple[Segment, ...]) -> bool:
    return bool(sum(segment.characters for segment in segments))


class ValidatorRolloutWindow:
    @staticmethod
    def validate(source: Path, start_line: int, cutoff_line: int) -> None:
        if start_line < 1 or cutoff_line <= start_line:
            raise RolloutLineRangeInvalidException(start_line, cutoff_line)
        if not source.is_file():
            raise RolloutFileNotFoundException(source)


class ValidatorContextPreparation:
    @staticmethod
    def validate(trial_case: TrialCase, mode: EvidenceMode, variant: Variant) -> None:
        problems: list[str] = ValidatorContextPreparation._option_problems(mode, variant)
        problems.extend(ValidatorContextPreparation._context_problems(trial_case))
        if problems:
            raise ContextPreparationInvalidException(problems)

    @staticmethod
    def _option_problems(mode: EvidenceMode, variant: Variant) -> list[str]:
        problems: list[str] = []
        if mode not in MAX_STATE_CHARACTERS_BY_EVIDENCE_MODE:
            problems.append(f"mode={mode!r}; expected full or excerpt")
        if variant not in {"current", "careful"}:
            problems.append(f"variant={variant!r}; expected current or careful")
        return problems

    @staticmethod
    def _context_problems(trial_case: TrialCase) -> list[str]:
        problems: list[str] = []
        if not trial_case.current_goal.strip() or not trial_case.segments:
            problems.append("A nonempty current goal and context segments are required")
        if not any(_is_tool_segment(segment) for segment in trial_case.segments):
            problems.append("Context must contain at least one completed tool event")
        if not _has_nonempty_segment_text(trial_case.segments):
            problems.append("Context must contain nonempty segment text")
        segment_ids: list[str] = [segment.id for segment in trial_case.segments]
        if any(not identifier for identifier in segment_ids) or len(set(segment_ids)) != len(segment_ids):
            problems.append("Context segment IDs must be nonempty and unique")
        return problems

    @staticmethod
    def validate_request_size(jev_state: JevState, mode: EvidenceMode) -> None:
        state_characters: int = ValidatorContextPreparation._serialized_size(jev_state)
        if state_characters > MAX_STATE_CHARACTERS_BY_EVIDENCE_MODE[mode]:
            raise JevStateTooLargeException(state_characters, mode, MAX_STATE_CHARACTERS_BY_EVIDENCE_MODE[mode])

    @staticmethod
    def _serialized_size(jev_state: JevState) -> int:
        return len(json.dumps(asdict(jev_state), ensure_ascii=False))


class ValidatorContextSelection:
    @staticmethod
    def validate(segments: tuple[Segment, ...], probability_by_tool_id: Mapping[str, float], threshold: float) -> None:
        ValidatorContextSelection.validate_threshold(threshold)
        ValidatorContextSelection._validate_segments(segments)
        ValidatorContextSelection._validate_probabilities(segments, probability_by_tool_id)

    @staticmethod
    def validate_threshold(threshold: float) -> None:
        if ValidatorProbability.collect_problems(threshold):
            raise CompactionThresholdOutOfRangeException(threshold)

    @staticmethod
    def _validate_segments(segments: tuple[Segment, ...]) -> None:
        if not segments or not _has_nonempty_segment_text(segments):
            raise CompactionContextEmptyException

    @staticmethod
    def _validate_probabilities(segments: tuple[Segment, ...], probability_by_tool_id: Mapping[str, float]) -> None:
        expected_tool_ids: set[str] = _tool_segment_ids(segments)
        invalid_probability_by_tool_id: dict[str, float] = {
            tool_id: probability_by_tool_id[tool_id]
            for tool_id in probability_by_tool_id
            if ValidatorProbability.collect_problems(probability_by_tool_id[tool_id])
        }
        if set(probability_by_tool_id) != expected_tool_ids or invalid_probability_by_tool_id:
            raise RetentionProbabilitiesInvalidException(expected_tool_ids, set(probability_by_tool_id), invalid_probability_by_tool_id)


class ValidatorPreparedCompaction:
    @staticmethod
    def validate(
        prepared_compaction: PreparedCompaction, expected_question_by_tool_id: Mapping[str, RetentionQuestion], expected_jev_state: JevState
    ) -> None:
        ValidatorPreparedCompaction._validate_questions(prepared_compaction, expected_question_by_tool_id)
        ValidatorPreparedCompaction._validate_state(prepared_compaction, expected_jev_state)

    @staticmethod
    def _validate_questions(prepared_compaction: PreparedCompaction, expected_question_by_tool_id: Mapping[str, RetentionQuestion]) -> None:
        expected_tool_ids: set[str] = _tool_segment_ids(prepared_compaction.trial_case.segments)
        mismatched_question_ids: set[str] = {
            tool_id
            for tool_id in set(prepared_compaction.questions) & set(expected_question_by_tool_id)
            if prepared_compaction.questions[tool_id] != expected_question_by_tool_id[tool_id]
        }
        if set(prepared_compaction.questions) != expected_tool_ids or mismatched_question_ids:
            raise PreparedQuestionsMismatchException(expected_tool_ids, set(prepared_compaction.questions), mismatched_question_ids)

    @staticmethod
    def _validate_state(prepared_compaction: PreparedCompaction, expected_jev_state: JevState) -> None:
        if prepared_compaction.jev_state != expected_jev_state:
            raise PreparedStateMismatchException(prepared_compaction.trial_case.source, prepared_compaction.mode, prepared_compaction.variant)


class ValidatorTrialOptions:
    @staticmethod
    def validate(runs: int, threshold: float, required_tool_ids: AbstractSet[str], available_tool_ids: AbstractSet[str]) -> None:
        problems: list[str] = ValidatorTrialOptions._repeat_count_problems(runs)
        problems.extend(ValidatorTrialOptions._threshold_problems(threshold))
        problems.extend(ValidatorTrialOptions._tool_id_problems(required_tool_ids, available_tool_ids))
        if problems:
            raise TrialOptionsInvalidException(problems)

    @staticmethod
    def _repeat_count_problems(runs: int) -> list[str]:
        return [f"runs: {problem}" for problem in ValidatorRepeatCount.collect_problems(runs, MAXIMUM_TRIAL_RUNS)]

    @staticmethod
    def _threshold_problems(threshold: float) -> list[str]:
        return [f"threshold: {problem}" for problem in ValidatorProbability.collect_problems(threshold)]

    @staticmethod
    def _tool_id_problems(required_tool_ids: AbstractSet[str], available_tool_ids: AbstractSet[str]) -> list[str]:
        if required_tool_ids <= available_tool_ids:
            return []
        return [f"Required tool IDs must belong to the selected context window; absent={sorted(required_tool_ids - available_tool_ids)}."]
