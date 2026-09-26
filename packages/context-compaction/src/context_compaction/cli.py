"""Run repeatable Jev compaction trials on a bounded Codex JSONL conversation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import cast

from context_compaction import (
    Compaction,
    CompactionResult,
    ContextCompactionBusinessError,
    ContextCompactionInputError,
    ContextCompactionTechnicalError,
    ContextEntry,
    DecisionRecord,
    DomainFacade,
    EvidenceMode,
    JevDecisionBatch,
    JevQuestionRecord,
    JevRequestRecord,
    PreparedCompaction,
    RetentionQuestion,
    ToolSegment,
    TrialCase,
    TrialManifest,
    TrialResultRecord,
    Variant,
)

MAXIMUM_RUNS = 5


@dataclass(frozen=True)
class TrialOptions:
    trial_case: TrialCase
    mode: EvidenceMode
    runs: int
    threshold: float
    required_tool_ids: frozenset[str]
    output_dir: Path


@dataclass(frozen=True)
class CompletedTrial:
    compaction: Compaction
    jev_decision_batch: JevDecisionBatch
    variant: Variant
    repeat: int
    required_tool_ids: frozenset[str]
    compacted_file: str


class ValidatorTrialOutputPaths:
    @staticmethod
    def validate(source: Path, output_dir: Path) -> None:
        output_artifact_paths: tuple[Path, ...] = ValidatorTrialOutputPaths._planned_paths(output_dir)
        for output_artifact_path in output_artifact_paths:
            if source.resolve() == output_artifact_path.resolve() or (output_artifact_path.exists() and source.samefile(output_artifact_path)):
                raise ContextCompactionInputError(
                    f"Trial output would overwrite or delete the original rollout; source={source}, output={output_artifact_path}."
                )

    @staticmethod
    def _planned_paths(output_dir: Path) -> tuple[Path, ...]:
        return (
            output_dir / "manifest.json",
            output_dir / "jev_requests.json",
            output_dir / "live_results.json",
            *(output_dir / f"compacted_{variant}_{repeat}.json" for variant in ("current", "careful") for repeat in range(1, MAXIMUM_RUNS + 1)),
        )


def _serialize_record(record: object) -> object:
    if not is_dataclass(record) or isinstance(record, type):
        raise TypeError(f"Cannot serialize {type(record).__name__} as a trial record.")
    serialized_fields: dict[str, object] = asdict(record)
    if isinstance(record, JevRequestRecord):
        serialized_fields["state"] = serialized_fields.pop("jev_state")
    elif isinstance(record, TrialManifest):
        serialized_fields["baselines"] = serialized_fields.pop("trial_baselines")
    return serialized_fields


def _write_json(path: Path, content: object) -> None:
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2, default=_serialize_record) + "\n", encoding="utf-8")


def _reset_trial_outputs(output_dir: Path) -> None:
    for variant in ("current", "careful"):
        for repeat in range(1, MAXIMUM_RUNS + 1):
            compacted_path: Path = output_dir / f"compacted_{variant}_{repeat}.json"
            if compacted_path.is_file():
                compacted_path.unlink()
    _write_json(output_dir / "live_results.json", [])


def _request_record(prepared_compaction: PreparedCompaction) -> JevRequestRecord:
    retention_questions_by_tool_id: Mapping[str, RetentionQuestion] = prepared_compaction.questions
    return JevRequestRecord(
        jev_state=prepared_compaction.jev_state,
        questions={
            question_id: JevQuestionRecord(type="noul", instructions=retention_questions_by_tool_id[question_id].instructions)
            for question_id in retention_questions_by_tool_id
        },
    )


def _context_record(compaction: Compaction) -> tuple[ContextEntry, ...]:
    return compaction.context


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
        required_tools_retained=completed_trial.required_tool_ids <= retained_ids,
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
    domain_facade: DomainFacade,
) -> None:
    live_result_records: list[TrialResultRecord] = []
    for variant in ("current", "careful"):
        selected_variant: Variant = variant
        prepared_compaction: PreparedCompaction = domain_facade.prepare(trial_options.trial_case, trial_options.mode, selected_variant)
        for repeat in range(1, trial_options.runs + 1):
            compaction_result: CompactionResult = await domain_facade.compact(prepared_compaction, trial_options.threshold)
            jev_decision_batch: JevDecisionBatch = compaction_result.jev_decision_batch
            compaction: Compaction = compaction_result.compaction
            compacted_file: str = f"compacted_{selected_variant}_{repeat}.json"
            _write_json(trial_options.output_dir / compacted_file, _context_record(compaction))
            live_result_records.append(
                _result_record(
                    CompletedTrial(
                        compaction=compaction,
                        jev_decision_batch=jev_decision_batch,
                        variant=selected_variant,
                        repeat=repeat,
                        required_tool_ids=trial_options.required_tool_ids,
                        compacted_file=compacted_file,
                    )
                )
            )
            _write_json(trial_options.output_dir / "live_results.json", live_result_records)
            sys.stdout.write(f"{selected_variant} {repeat}/{trial_options.runs}: {compaction.reduction_percent}% reduction\n")


async def _execute_trial(trial_options: TrialOptions, env_file: Path, request_records: Mapping[str, JevRequestRecord]) -> None:
    async with DomainFacade(enable_jev=bool(trial_options.runs), env_file=env_file) as domain_facade:
        output_dir: Path = trial_options.output_dir
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        _reset_trial_outputs(output_dir)
        _write_json(output_dir / "manifest.json", domain_facade.describe_context(trial_options.trial_case, trial_options.mode))
        _write_json(output_dir / "jev_requests.json", request_records)
        if trial_options.runs:
            await _run_live(trial_options, domain_facade)
        else:
            sys.stdout.write(f"Prepared {len(request_records['careful'].questions)} tool events; no Jev calls made.\n")


def main() -> int:
    argument_parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("rollout", type=Path, help="Local Codex rollout JSONL")
    argument_parser.add_argument("--start-line", type=int, required=True)
    argument_parser.add_argument("--cutoff-line", type=int, required=True, help="First line of the held-out answer")
    argument_parser.add_argument("--evidence-mode", choices=("full", "excerpt"), default="full")
    argument_parser.add_argument("--output-dir", type=Path, required=True)
    argument_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    argument_parser.add_argument("--runs", type=int, default=0, help="Live repeats per instruction variant, 0 prepares only")
    argument_parser.add_argument("--threshold", type=float, default=0.5)
    argument_parser.add_argument("--required-tool", action="append", default=[], help="Tool ID needed to support the held-out answer")
    namespace: argparse.Namespace = argument_parser.parse_args()
    try:
        mode: EvidenceMode = cast(EvidenceMode, namespace.evidence_mode)
        preparation_domain_facade: DomainFacade = DomainFacade()
        trial_case: TrialCase = preparation_domain_facade.load_context(namespace.rollout, namespace.start_line, namespace.cutoff_line)
        required_tool_ids: set[str] = set(namespace.required_tool)
        available_tool_ids: set[str] = {segment.id for segment in trial_case.segments if isinstance(segment, ToolSegment)}
        preparation_domain_facade.validate_trial_options(namespace.runs, namespace.threshold, required_tool_ids, available_tool_ids)
        request_records: dict[str, JevRequestRecord] = {
            variant: _request_record(preparation_domain_facade.prepare(trial_case, mode, variant)) for variant in ("current", "careful")
        }
        output_dir: Path = namespace.output_dir.resolve()
        ValidatorTrialOutputPaths.validate(trial_case.source, output_dir)
        trial_options: TrialOptions = TrialOptions(trial_case, mode, namespace.runs, namespace.threshold, frozenset(required_tool_ids), output_dir)
        asyncio.run(_execute_trial(trial_options, namespace.env_file, request_records))
        return 0
    except ContextCompactionBusinessError as error:
        diagnostic_context: str = (
            f"rollout={namespace.rollout}, lines={namespace.start_line}:{namespace.cutoff_line}, "
            f"mode={namespace.evidence_mode}, runs={namespace.runs}, output_dir={namespace.output_dir}"
            f", env_file={namespace.env_file}, threshold={namespace.threshold}, required_tools={namespace.required_tool}"
        )
        sys.stderr.write(f"Context compaction stopped: {error}; {diagnostic_context}\n")
        return 1
    except (ContextCompactionTechnicalError, OSError, TypeError, ValueError) as error:
        diagnostic_context: str = (
            f"rollout={namespace.rollout}, lines={namespace.start_line}:{namespace.cutoff_line}, "
            f"mode={namespace.evidence_mode}, runs={namespace.runs}, output_dir={namespace.output_dir}"
            f", env_file={namespace.env_file}, threshold={namespace.threshold}, required_tools={namespace.required_tool}"
        )
        sys.stderr.write(f"Context compaction technical failure: {error}; {diagnostic_context}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
