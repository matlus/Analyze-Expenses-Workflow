from __future__ import annotations

import json
from collections.abc import Mapping, Set as AbstractSet
from dataclasses import asdict
from pathlib import Path

from context_compaction.managers.exceptions.context_compaction_error import CompactionDecisionError, ContextCompactionInputError, RolloutError
from context_compaction.managers.models.context_models import (
    EvidenceMode,
    PreparedCompaction,
    RetentionQuestion,
    Segment,
    ToolSegment,
    TrialCase,
    Variant,
)
from context_compaction.managers.models.records import JevState
from context_compaction.managers.processors.extractors.context_question_extractor import build_questions
from context_compaction.managers.processors.extractors.context_state_extractor import build_state
from context_compaction.managers.validators.primitive_validators.validator_probability import ValidatorProbability, ValidatorRepeatCount

MAXIMUM_RUNS = 5
MAX_STATE_CHARACTERS_BY_EVIDENCE_MODE: dict[EvidenceMode, int] = {"full": 90_000, "excerpt": 24_000}


class ValidatorRolloutWindow:
    @staticmethod
    def validate(source: Path, start_line: int, cutoff_line: int) -> None:
        if start_line < 1 or cutoff_line <= start_line:
            raise RolloutError(f"The start line must be positive and precede the held-out answer; start={start_line}, cutoff={cutoff_line}.")
        if not source.is_file():
            raise RolloutError(f"Codex rollout does not exist: {source}")


class ValidatorContextPreparation:
    @staticmethod
    def validate(trial_case: TrialCase, mode: EvidenceMode, variant: Variant) -> None:
        problems: list[str] = ValidatorContextPreparation._option_problems(mode, variant)
        problems.extend(ValidatorContextPreparation._context_problems(trial_case))
        if problems:
            raise CompactionDecisionError("; ".join(problems))
        ValidatorContextPreparation._validate_request_size(trial_case, mode)

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
        if not any(isinstance(segment, ToolSegment) for segment in trial_case.segments):
            problems.append("Context must contain at least one completed tool event")
        if not sum(segment.characters for segment in trial_case.segments):
            problems.append("Context must contain nonempty segment text")
        segment_ids: list[str] = [segment.id for segment in trial_case.segments]
        if any(not identifier for identifier in segment_ids) or len(set(segment_ids)) != len(segment_ids):
            problems.append("Context segment IDs must be nonempty and unique")
        return problems

    @staticmethod
    def _validate_request_size(trial_case: TrialCase, mode: EvidenceMode) -> None:
        state_characters: int = ValidatorContextPreparation._request_size(trial_case, mode)
        if state_characters > MAX_STATE_CHARACTERS_BY_EVIDENCE_MODE[mode]:
            raise CompactionDecisionError(
                f"Jev state has {state_characters} characters; {mode} trial limit is {MAX_STATE_CHARACTERS_BY_EVIDENCE_MODE[mode]}."
            )

    @staticmethod
    def _request_size(trial_case: TrialCase, mode: EvidenceMode) -> int:
        jev_state: JevState = build_state(trial_case.segments, trial_case.current_goal, mode)
        return ValidatorContextPreparation._serialized_size(jev_state)

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
            raise CompactionDecisionError(f"Threshold must be between zero and one; received {threshold}.")

    @staticmethod
    def _validate_segments(segments: tuple[Segment, ...]) -> None:
        if not segments or not sum(segment.characters for segment in segments):
            raise CompactionDecisionError("Context must contain nonempty segment text.")

    @staticmethod
    def _validate_probabilities(segments: tuple[Segment, ...], probability_by_tool_id: Mapping[str, float]) -> None:
        expected_tool_ids: set[str] = {segment.id for segment in segments if isinstance(segment, ToolSegment)}
        invalid_probability_by_tool_id: dict[str, float] = {
            tool_id: probability_by_tool_id[tool_id]
            for tool_id in probability_by_tool_id
            if ValidatorProbability.collect_problems(probability_by_tool_id[tool_id])
        }
        if set(probability_by_tool_id) != expected_tool_ids or invalid_probability_by_tool_id:
            raise CompactionDecisionError(
                "".join(
                    (
                        "Jev probabilities do not match the complete tool-event set; ",
                        f"expected={sorted(expected_tool_ids)}, received={sorted(probability_by_tool_id)}, invalid={invalid_probability_by_tool_id}.",
                    )
                )
            )


class ValidatorPreparedCompaction:
    @staticmethod
    def validate(prepared_compaction: PreparedCompaction) -> None:
        ValidatorPreparedCompaction._validate_questions(prepared_compaction)
        ValidatorPreparedCompaction._validate_state(prepared_compaction)

    @staticmethod
    def _validate_questions(prepared_compaction: PreparedCompaction) -> None:
        problem: str | None = ValidatorPreparedCompaction._question_problem(prepared_compaction)
        if problem is not None:
            raise CompactionDecisionError(problem)

    @staticmethod
    def _question_problem(prepared_compaction: PreparedCompaction) -> str | None:
        expected_tool_ids: set[str] = ValidatorPreparedCompaction._tool_ids(prepared_compaction.trial_case.segments)
        expected_question_by_tool_id: Mapping[str, RetentionQuestion] = build_questions(
            prepared_compaction.trial_case.segments, prepared_compaction.variant
        )
        if set(prepared_compaction.questions) != expected_tool_ids or prepared_compaction.questions != expected_question_by_tool_id:
            return (
                "Prepared questions must match the complete tool-event set; "
                f"expected={sorted(expected_tool_ids)}, received={sorted(prepared_compaction.questions)}."
            )
        return None

    @staticmethod
    def _tool_ids(segments: tuple[Segment, ...]) -> set[str]:
        return {segment.id for segment in segments if isinstance(segment, ToolSegment)}

    @staticmethod
    def _validate_state(prepared_compaction: PreparedCompaction) -> None:
        if not ValidatorPreparedCompaction._state_matches_source(prepared_compaction):
            raise CompactionDecisionError("Prepared state must match the redacted source context and current goal.")

    @staticmethod
    def _state_matches_source(prepared_compaction: PreparedCompaction) -> bool:
        expected_jev_state: JevState = build_state(
            prepared_compaction.trial_case.segments, prepared_compaction.trial_case.current_goal, prepared_compaction.mode
        )
        return prepared_compaction.jev_state == expected_jev_state


class ValidatorTrialOptions:
    @staticmethod
    def validate(runs: int, threshold: float, required_tool_ids: AbstractSet[str], available_tool_ids: AbstractSet[str]) -> None:
        if ValidatorRepeatCount.collect_problems(runs, MAXIMUM_RUNS) or ValidatorProbability.collect_problems(threshold):
            raise ContextCompactionInputError(f"Use 0-5 runs and a threshold from zero to one; received runs={runs}, threshold={threshold}.")
        if not required_tool_ids <= available_tool_ids:
            raise ContextCompactionInputError(
                f"Required tool IDs must belong to the selected context window; absent={sorted(required_tool_ids - available_tool_ids)}."
            )
