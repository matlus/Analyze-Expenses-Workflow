"""Run repeatable Jev compaction trials on a bounded Codex JSONL conversation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Set as AbstractSet
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import cast, final

from dotenv import dotenv_values
from pydantic import SecretStr

from context_compaction.errors import ContextCompactionBusinessError, ContextCompactionTechnicalError
from context_compaction.gateway import JevDecisionBatch, JevGateway, JevSettings
from context_compaction.questions import RetentionQuestion, Variant, build_questions
from context_compaction.records import (
    ContextEntry,
    DecisionRecord,
    JevQuestionRecord,
    JevRequestRecord,
    JevState,
    MessageContextEntry,
    ToolContextEntry,
    ToolEventSummary,
    TrialBaselines,
    TrialManifest,
    TrialResultRecord,
)
from context_compaction.rollout import ToolSegment, TrialCase, load_case
from context_compaction.selection import (
    Compaction,
    EvidenceMode,
    apply_decisions,
    build_state,
    recency_baseline,
    redact,
)

MAXIMUM_RUNS = 5


@final
class ContextCompactionConfigurationError(ContextCompactionBusinessError):
    """A required Jev setting is missing or cannot be read."""


@final
class ContextCompactionInputError(ContextCompactionBusinessError):
    """The caller selected an invalid trial option."""


@dataclass(frozen=True)
class TrialOptions:
    trial_case: TrialCase
    mode: EvidenceMode
    runs: int
    threshold: float
    required_tools: frozenset[str]
    output_dir: Path


@dataclass(frozen=True)
class CompletedTrial:
    compaction: Compaction
    jev_decision_batch: JevDecisionBatch
    variant: Variant
    repeat: int
    required_tools: frozenset[str]
    compacted_file: str


def _validate_arguments(runs: int, threshold: float, required_tools: AbstractSet[str], available_tools: AbstractSet[str]) -> None:
    if not 0 <= runs <= MAXIMUM_RUNS or not 0 <= threshold <= 1:
        raise ContextCompactionInputError(f"Use 0-5 runs and a threshold from zero to one; received runs={runs}, threshold={threshold}.")
    if not required_tools <= available_tools:
        absent_tool_ids: list[str] = sorted(required_tools - available_tools)
        raise ContextCompactionInputError(f"Required tool IDs must belong to the selected context window; absent={absent_tool_ids}.")


def _settings(env_file: Path) -> JevSettings:
    try:
        values: dict[str, str | None] = dict(dotenv_values(env_file)) if env_file.is_file() else {}
    except Exception as error:
        raise ContextCompactionConfigurationError(f"Could not read Jev settings from {env_file}.") from error

    def setting(name: str) -> str:
        setting_value: str | None = os.getenv(name) or values.get(name)
        if not setting_value:
            raise ContextCompactionConfigurationError(f"Set {name} in the environment or {env_file}; no Jev call was made.")
        return setting_value

    return JevSettings(
        api_key=SecretStr(setting("OPEN_ROUTER_KEY")),
        base_url=setting("OPEN_ROUTER_BASE_URL"),
        model=setting("JEV_MODEL"),
    )


def _serialize_record(record: object) -> object:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    raise TypeError(f"Cannot serialize {type(record).__name__} as a trial record.")


def _write_json(path: Path, content: object) -> None:
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2, default=_serialize_record) + "\n", encoding="utf-8")


def _reset_trial_outputs(output_dir: Path) -> None:
    for variant in ("current", "careful"):
        for repeat in range(1, MAXIMUM_RUNS + 1):
            compacted_path: Path = output_dir / f"compacted_{variant}_{repeat}.json"
            if compacted_path.is_file():
                compacted_path.unlink()
    _write_json(output_dir / "live_results.json", [])


def _manifest(trial_case: TrialCase, mode: EvidenceMode) -> TrialManifest:
    return TrialManifest(
        source=str(trial_case.source),
        source_sha256=trial_case.source_sha256,
        start_line=trial_case.start_line,
        cutoff_line=trial_case.cutoff_line,
        evidence_mode=mode,
        current_goal=redact(trial_case.current_goal),
        held_out_answer=redact(trial_case.held_out_answer),
        protected_messages=sum(not isinstance(segment, ToolSegment) for segment in trial_case.segments),
        tool_events=tuple(
            ToolEventSummary(id=segment.id, call_characters=len(segment.text), result_characters=len(segment.result))
            for segment in trial_case.segments
            if isinstance(segment, ToolSegment)
        ),
        original_characters=sum(segment.characters for segment in trial_case.segments),
        baselines=TrialBaselines(
            drop_all_tools=recency_baseline(trial_case.segments, 0),
            keep_newest_tool=recency_baseline(trial_case.segments, 1),
            keep_newest_three_tools=recency_baseline(trial_case.segments, 3),
        ),
        measurement="Characters of chat and tool text, not model tokens or JSONL bytes.",
    )


def _request_record(trial_case: TrialCase, mode: EvidenceMode, variant: Variant) -> JevRequestRecord:
    retention_questions_by_tool_id: Mapping[str, RetentionQuestion] = build_questions(trial_case.segments, variant)
    return JevRequestRecord(
        state=build_state(trial_case.segments, trial_case.current_goal, mode),
        questions={
            question_id: JevQuestionRecord(type="noul", instructions=retention_questions_by_tool_id[question_id].instructions)
            for question_id in retention_questions_by_tool_id
        },
    )


def _context_record(compaction: Compaction) -> tuple[ContextEntry, ...]:
    return tuple(
        ToolContextEntry(id=segment.id, kind="tool", text=redact(segment.text), result=redact(segment.result))
        if isinstance(segment, ToolSegment)
        else MessageContextEntry(id=segment.id, kind=segment.kind, text=redact(segment.text))
        for segment in compaction.retained
    )


def _result_record(completed_trial: CompletedTrial) -> TrialResultRecord:
    compaction: Compaction = completed_trial.compaction
    jev_decision_batch: JevDecisionBatch = completed_trial.jev_decision_batch
    retained_ids: set[str] = {segment.id for segment in compaction.retained}
    return TrialResultRecord(
        variant=completed_trial.variant,
        repeat=completed_trial.repeat,
        model=jev_decision_batch.model,
        usage=jev_decision_batch.usage,
        reduction_percent=compaction.reduction_percent,
        original_characters=compaction.original_characters,
        retained_characters=compaction.retained_characters,
        required_tools_retained=completed_trial.required_tools <= retained_ids,
        decisions=tuple(
            DecisionRecord(
                id=identifier,
                keep_probability=compaction.probabilities[identifier],
                action="keep" if identifier in retained_ids else "proposed_drop",
            )
            for identifier in compaction.probabilities
        ),
        compacted_file=completed_trial.compacted_file,
    )


async def _run_live(
    trial_options: TrialOptions,
    jev_settings: JevSettings,
) -> None:
    live_result_records: list[TrialResultRecord] = []
    async with JevGateway(jev_settings) as jev_gateway:
        for variant in ("current", "careful"):
            selected_variant: Variant = variant
            jev_state: JevState = build_state(trial_options.trial_case.segments, trial_options.trial_case.current_goal, trial_options.mode)
            questions: Mapping[str, RetentionQuestion] = build_questions(trial_options.trial_case.segments, selected_variant)
            for repeat in range(1, trial_options.runs + 1):
                jev_decision_batch: JevDecisionBatch = await jev_gateway.decide(jev_state, questions)
                compaction: Compaction = apply_decisions(trial_options.trial_case.segments, jev_decision_batch.probabilities, trial_options.threshold)
                compacted_file: str = f"compacted_{selected_variant}_{repeat}.json"
                _write_json(trial_options.output_dir / compacted_file, _context_record(compaction))
                live_result_records.append(
                    _result_record(
                        CompletedTrial(
                            compaction=compaction,
                            jev_decision_batch=jev_decision_batch,
                            variant=selected_variant,
                            repeat=repeat,
                            required_tools=trial_options.required_tools,
                            compacted_file=compacted_file,
                        )
                    )
                )
                _write_json(trial_options.output_dir / "live_results.json", live_result_records)
                sys.stdout.write(f"{selected_variant} {repeat}/{trial_options.runs}: {compaction.reduction_percent}% reduction\n")


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout", type=Path, help="Local Codex rollout JSONL")
    parser.add_argument("--start-line", type=int, required=True)
    parser.add_argument("--cutoff-line", type=int, required=True, help="First line of the held-out answer")
    parser.add_argument("--evidence-mode", choices=("full", "excerpt"), default="full")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--runs", type=int, default=0, help="Live repeats per instruction variant, 0 prepares only")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--required-tool", action="append", default=[], help="Tool ID needed to support the held-out answer")
    arguments: argparse.Namespace = parser.parse_args()
    try:
        mode: EvidenceMode = cast(EvidenceMode, arguments.evidence_mode)
        trial_case: TrialCase = load_case(arguments.rollout, arguments.start_line, arguments.cutoff_line)
        required_tools: set[str] = set(arguments.required_tool)
        available_tools: set[str] = {segment.id for segment in trial_case.segments if isinstance(segment, ToolSegment)}
        _validate_arguments(arguments.runs, arguments.threshold, required_tools, available_tools)
        request_records: dict[str, JevRequestRecord] = {variant: _request_record(trial_case, mode, variant) for variant in ("current", "careful")}
        jev_settings: JevSettings | None = _settings(arguments.env_file) if arguments.runs else None
        output_dir: Path = arguments.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        _reset_trial_outputs(output_dir)
        _write_json(output_dir / "manifest.json", _manifest(trial_case, mode))
        _write_json(output_dir / "jev_requests.json", request_records)
        if not arguments.runs:
            sys.stdout.write(f"Prepared {len(available_tools)} tool events; no Jev calls made.\n")
            return 0
        trial_options: TrialOptions = TrialOptions(trial_case, mode, arguments.runs, arguments.threshold, frozenset(required_tools), output_dir)
        asyncio.run(_run_live(trial_options, cast(JevSettings, jev_settings)))
        return 0
    except ContextCompactionBusinessError as error:
        diagnostic_context: str = (
            f"rollout={arguments.rollout}, lines={arguments.start_line}:{arguments.cutoff_line}, "
            f"mode={arguments.evidence_mode}, runs={arguments.runs}, output_dir={arguments.output_dir}"
            f", threshold={arguments.threshold}, required_tools={arguments.required_tool}"
        )
        sys.stderr.write(f"Context compaction stopped: {error}; {diagnostic_context}\n")
        return 1
    except (ContextCompactionTechnicalError, OSError, TypeError, ValueError) as error:
        diagnostic_context: str = (
            f"rollout={arguments.rollout}, lines={arguments.start_line}:{arguments.cutoff_line}, "
            f"mode={arguments.evidence_mode}, runs={arguments.runs}, output_dir={arguments.output_dir}"
            f", threshold={arguments.threshold}, required_tools={arguments.required_tool}"
        )
        sys.stderr.write(f"Context compaction technical failure: {error}; {diagnostic_context}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
