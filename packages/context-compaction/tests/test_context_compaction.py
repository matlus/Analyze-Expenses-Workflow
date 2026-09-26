from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NoReturn, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from context_compaction import (
    Compaction,
    CompactionDecisionError,
    ContextCompactionConfigurationReadError,
    DomainFacade,
    JevCleanupError,
    JevDecisionBatch,
    JevGatewayError,
    JevRequestError,
    JevResponseError,
    JevSettings,
    MessageSegment,
    PreparedCompaction,
    RetentionQuestion,
    RolloutError,
    ToolSegment,
    TrialCase,
)
from context_compaction.cli import main
from context_compaction.managers.configuration_providers import configuration_provider as compaction_configuration
from context_compaction.managers.gateways.jev_gateway import JevGateway
from context_compaction.managers.models.context_models import Variant
from context_compaction.managers.models.records import JevState, TrialManifest
from context_compaction.managers.processors.extractors.context_question_extractor import build_questions
from context_compaction.managers.processors.extractors.context_state_extractor import build_state, redact
from context_compaction.managers.service_locators.service_locator_production import ServiceLocatorProduction
from pydantic import SecretStr
from typesafe_sdk import AsyncTypeSafeClient, Noul, SystemOneResponse

load_case = DomainFacade().load_context
apply_decisions = DomainFacade().apply_saved_decisions


@dataclass(frozen=True)
class RolloutScenario:
    user_text: str
    first_result: str
    continued_result: str
    answer_text: str
    tool_id: str


@dataclass(frozen=True)
class JevFailureExpectation:
    operation: str
    model: str
    detail_value_by_name: Mapping[str, str]
    message_phrase: str
    secret_key: str
    cause: BaseException | None


@dataclass(frozen=True)
class ExpectedCompaction:
    retained_ids: tuple[str, ...]
    retained_characters: int
    reduction_percent: float
    probability_by_tool_id: Mapping[str, float]


def assert_cli_diagnostic_matches(actual_text: str, expected_phrases: tuple[str, ...], expected_forbidden_phrases: tuple[str, ...]) -> None:
    mismatches: list[str] = [f"missing expected phrase {phrase!r}" for phrase in expected_phrases if phrase not in actual_text]
    mismatches.extend(f"forbidden phrase disclosed: length={len(phrase)}" for phrase in expected_forbidden_phrases if phrase in actual_text)
    assert not mismatches, f"CLI diagnostic: {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def _write_case(path: Path) -> RolloutScenario:
    scenario_suffix: str = secrets.token_hex(4)
    session_id: int = secrets.randbelow(900_000) + 100_000
    first_call_id: str = secrets.token_hex(5)
    poll_call_id: str = secrets.token_hex(5)
    user_text: str = f"Does HTTPS endpoint {scenario_suffix} work?"
    first_result: str = f'200 OK for {scenario_suffix} "session_id":{session_id}'
    continued_result: str = f"certificate approved for {scenario_suffix}"
    answer_text: str = f"HTTPS endpoint {scenario_suffix} works."
    records: list[dict[str, object]] = [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": user_text}]}},
        {
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "call_id": first_call_id, "name": "exec", "input": f"curl {scenario_suffix}"},
        },
        {
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": first_call_id, "output": [{"text": first_result}]},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": poll_call_id,
                "name": "exec",
                "input": f'tools.write_stdin({{"session_id":{session_id}}})',
            },
        },
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": poll_call_id, "output": continued_result}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": answer_text}]}},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return RolloutScenario(user_text, first_result, continued_result, answer_text, "tool_2")


def assert_trial_case_matches(
    actual_segments: tuple[MessageSegment | ToolSegment, ...], actual_held_out_answer: str, expected_rollout_scenario: RolloutScenario
) -> None:
    mismatches: list[str] = []
    if len(actual_segments) != 2:
        mismatches.append(f"expected 2 segments; got {len(actual_segments)}")
    else:
        actual_message_segment: MessageSegment = cast(MessageSegment, actual_segments[0])
        actual_tool_segment: ToolSegment = cast(ToolSegment, actual_segments[1])
        if actual_message_segment.text != expected_rollout_scenario.user_text:
            mismatches.append(f"user text: expected {expected_rollout_scenario.user_text!r}; got {actual_message_segment.text!r}")
        if actual_tool_segment.id != expected_rollout_scenario.tool_id:
            mismatches.append(f"expected tool ID {expected_rollout_scenario.tool_id}; got {actual_tool_segment.id}")
        mismatches.extend(
            f"tool result: expected phrase {expected_phrase!r}; got {actual_tool_segment.result!r}"
            for expected_phrase in (expected_rollout_scenario.first_result, expected_rollout_scenario.continued_result)
            if expected_phrase not in actual_tool_segment.result
        )
    if actual_held_out_answer != expected_rollout_scenario.answer_text:
        mismatches.append(f"held-out answer: expected {expected_rollout_scenario.answer_text!r}; got {actual_held_out_answer!r}")
    assert not mismatches, f"Trial case {expected_rollout_scenario.tool_id}: {len(mismatches)} property mismatches:\n" + "\n".join(mismatches)


def assert_compaction_matches(actual_compaction: Compaction, expected_compaction: ExpectedCompaction) -> None:
    mismatches: list[str] = []
    actual_retained_ids: tuple[str, ...] = tuple(segment.id for segment in actual_compaction.retained)
    if actual_retained_ids != expected_compaction.retained_ids:
        mismatches.append(f"expected retained IDs {expected_compaction.retained_ids}; got {actual_retained_ids}")
    if actual_compaction.retained_characters != expected_compaction.retained_characters:
        mismatches.append(f"expected {expected_compaction.retained_characters} characters; got {actual_compaction.retained_characters}")
    if actual_compaction.reduction_percent != expected_compaction.reduction_percent:
        mismatches.append(f"expected {expected_compaction.reduction_percent}% reduction; got {actual_compaction.reduction_percent}%")
    if actual_compaction.probabilities != expected_compaction.probability_by_tool_id:
        mismatches.append(f"expected probabilities {expected_compaction.probability_by_tool_id}; got {actual_compaction.probabilities}")
    assert not mismatches, f"Compaction for {expected_compaction.retained_ids}: {len(mismatches)} property mismatches:\n" + "\n".join(mismatches)


def assert_jev_decision_batch_matches(
    actual_jev_decision_batch: JevDecisionBatch, *, expected_probability_by_tool_id: Mapping[str, float], expected_input_tokens: int
) -> None:
    mismatches: list[str] = []
    if actual_jev_decision_batch.probabilities != expected_probability_by_tool_id:
        mismatches.append(f"expected probabilities {expected_probability_by_tool_id}; got {actual_jev_decision_batch.probabilities}")
    if actual_jev_decision_batch.usage["input_tokens"] != expected_input_tokens:
        mismatches.append(f"expected {expected_input_tokens} input tokens; got {actual_jev_decision_batch.usage['input_tokens']}")
    assert not mismatches, f"Jev decision for {sorted(expected_probability_by_tool_id)}: {len(mismatches)} property mismatches:\n" + "\n".join(
        mismatches
    )


def assert_secret_is_redacted(actual_text: str, expected_secret_key: str, expected_redaction_marker: str) -> None:
    mismatches: list[str] = []
    if expected_secret_key in actual_text:
        mismatches.append("secret absent: expected True; got False")
    if expected_redaction_marker not in actual_text:
        mismatches.append(f"marker {expected_redaction_marker!r} present: expected True; got False")
    assert not mismatches, f"Redaction of secret length {len(expected_secret_key)}: {len(mismatches)} property mismatches:\n" + "\n".join(mismatches)


def assert_jev_failure_matches(actual_jev_gateway_error: JevGatewayError, expected_jev_failure_expectation: JevFailureExpectation) -> None:
    mismatches: list[str] = []
    if expected_jev_failure_expectation.message_phrase not in str(actual_jev_gateway_error):
        mismatches.append(f"expected message phrase {expected_jev_failure_expectation.message_phrase!r}; got no matching phrase")
    if actual_jev_gateway_error.operation != expected_jev_failure_expectation.operation:
        mismatches.append(f"expected operation {expected_jev_failure_expectation.operation}; got {actual_jev_gateway_error.operation}")
    if actual_jev_gateway_error.model != expected_jev_failure_expectation.model:
        mismatches.append(f"expected model {expected_jev_failure_expectation.model}; got {actual_jev_gateway_error.model}")
    if actual_jev_gateway_error.detail_value_by_name != expected_jev_failure_expectation.detail_value_by_name:
        mismatches.append(
            f"expected details {expected_jev_failure_expectation.detail_value_by_name}; got {actual_jev_gateway_error.detail_value_by_name}"
        )
    if expected_jev_failure_expectation.secret_key in str(actual_jev_gateway_error):
        mismatches.append("secret absent: expected True; got False")
    if actual_jev_gateway_error.__cause__ is not expected_jev_failure_expectation.cause:
        mismatches.append("expected cause identity preserved; got a different cause")
    failure_summary: str = f"Jev failure for {expected_jev_failure_expectation.model}: {len(mismatches)} property mismatches:\n"
    assert not mismatches, failure_summary + "\n".join(mismatches)


def test_Rollout_WhenQuotedPollCompletes_ThenResultJoinsAndAnswerIsHeldOut(tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    expected_rollout_scenario: RolloutScenario = _write_case(source)
    expected_question_ids: set[str] = {expected_rollout_scenario.tool_id}

    actual_trial_case: TrialCase = load_case(source, 1, 6)
    jev_state: JevState = build_state(actual_trial_case.segments, actual_trial_case.current_goal, "full")
    retention_questions_by_tool_id: Mapping[str, RetentionQuestion] = build_questions(actual_trial_case.segments, "current")

    assert_trial_case_matches(actual_trial_case.segments, actual_trial_case.held_out_answer, expected_rollout_scenario)
    assert expected_rollout_scenario.answer_text not in json.dumps(asdict(jev_state))
    assert set(retention_questions_by_tool_id) == expected_question_ids


def test_Compaction_WhenToolIsDropped_ThenUserRequestRemainsAndReductionIsMeasured(tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    rollout_scenario: RolloutScenario = _write_case(source)
    trial_case: TrialCase = load_case(source, 1, 6)
    expected_retained_ids: list[str] = [trial_case.segments[0].id]
    expected_retained_characters: int = len(rollout_scenario.user_text)
    original_characters: int = sum(segment.characters for segment in trial_case.segments)
    expected_reduction_percent: float = round(100 * (original_characters - expected_retained_characters) / original_characters, 1)
    expected_probability_by_tool_id: dict[str, float] = {rollout_scenario.tool_id: 0.1}

    expected_compaction: ExpectedCompaction = ExpectedCompaction(
        tuple(expected_retained_ids), expected_retained_characters, expected_reduction_percent, expected_probability_by_tool_id
    )

    actual_compaction: Compaction = apply_decisions(trial_case.segments, {rollout_scenario.tool_id: 0.1}, 0.5)

    assert_compaction_matches(actual_compaction, expected_compaction)


def test_Compaction_WhenToolDecisionIsMissing_ThenDomainErrorNamesExpectedTool(tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    rollout_scenario: RolloutScenario = _write_case(source)
    trial_case: TrialCase = load_case(source, 1, 6)
    expected_message_phrase: str = "complete tool-event"

    with pytest.raises(CompactionDecisionError, match=expected_message_phrase) as error:
        apply_decisions(trial_case.segments, {}, 0.5)

    assert rollout_scenario.tool_id in str(error.value)


def test_Compaction_WhenProbabilityIsInvalid_ThenDiagnosticNamesRejectedValue(tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    rollout_scenario: RolloutScenario = _write_case(source)
    trial_case: TrialCase = load_case(source, 1, 6)
    invalid_probability: float = 1.2
    expected_diagnostic: str = repr({rollout_scenario.tool_id: invalid_probability})
    expected_message_phrase: str = "probabilities do not match"

    with pytest.raises(CompactionDecisionError, match=expected_message_phrase) as error:
        apply_decisions(trial_case.segments, {rollout_scenario.tool_id: invalid_probability}, 0.5)

    assert expected_diagnostic in str(error.value)


def test_Rollout_WhenPollIsInterleaved_ThenItDoesNotAttachToWrongCommand(tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    session_id: int = secrets.randbelow(900_000) + 100_000
    first_session_result: str = f'"session_id":{session_id}'
    first_call_id: str = secrets.token_hex(5)
    second_call_id: str = secrets.token_hex(5)
    poll_call_id: str = secrets.token_hex(5)
    second_result: str = f"second command {secrets.token_hex(4)}"
    first_continuation: str = f"first continuation {secrets.token_hex(4)}"
    user_text: str = f"Check {secrets.token_hex(4)}"
    first_command: str = f"command {secrets.token_hex(4)}"
    second_command: str = f"command {secrets.token_hex(4)}"
    answer_text: str = f"Finished {secrets.token_hex(4)}"
    expected_tool_events: list[tuple[str, str]] = [
        ("tool_2", first_session_result),
        ("tool_4", second_result),
        ("tool_6", first_continuation),
    ]
    records: list[dict[str, object]] = [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": user_text}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": first_call_id, "name": "exec", "input": first_command}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": first_call_id, "output": first_session_result}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": second_call_id, "name": "exec", "input": second_command}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": second_call_id, "output": second_result}},
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": poll_call_id,
                "name": "exec",
                "input": f"tools.write_stdin({{session_id:{session_id}}})",
            },
        },
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": poll_call_id, "output": first_continuation}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": answer_text}]}},
    ]
    source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    trial_case: TrialCase = load_case(source, 1, 8)
    actual_tool_events: list[tuple[str, str]] = [(segment.id, segment.result) for segment in trial_case.segments if isinstance(segment, ToolSegment)]

    assert actual_tool_events == expected_tool_events


def test_Redaction_WhenOpenRouterKeyAppears_ThenJevStateHidesIt(tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    trial_case: TrialCase = load_case(source, 1, 6)
    secret_key: str = "sk-or-v1-" + secrets.token_hex(12)
    expected_redaction_marker: str = "[REDACTED]"
    changed_trial_case: TrialCase = replace(
        trial_case,
        segments=(replace(trial_case.segments[0], text=f"OPEN_ROUTER_KEY={secret_key}"), trial_case.segments[1]),
    )

    rendered_state: str = json.dumps(asdict(build_state(changed_trial_case.segments, changed_trial_case.current_goal, "full")))

    assert_secret_is_redacted(rendered_state, secret_key, expected_redaction_marker)


def test_Redaction_WhenExcerptCutsCredentialLabel_ThenTheValueIsStillHidden() -> None:
    secret_key: str = secrets.token_hex(32)
    tool_result: str = "unrelated evidence " * 40 + f"\nOPEN_ROUTER_KEY={secret_key}"
    tool_id: str = f"tool_{secrets.token_hex(4)}"
    tool_segment: ToolSegment = ToolSegment(id=tool_id, text=secrets.token_hex(4), result=tool_result)
    expected_redaction_marker: str = "[REDACTED]"

    jev_state: JevState = build_state((tool_segment,), "check retained evidence", "excerpt")
    rendered_state: str = json.dumps(asdict(jev_state))

    assert_secret_is_redacted(rendered_state, secret_key, expected_redaction_marker)


@pytest.mark.parametrize("prefix", ["sk-proj-", "github_pat_"])
def test_Redaction_WhenBareKeyAppears_ThenRequestAndContextHideIt(tmp_path: Path, prefix: str) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    trial_case: TrialCase = load_case(source, 1, 6)
    secret_key: str = prefix + secrets.token_hex(12)
    changed_trial_case: TrialCase = replace(
        trial_case,
        segments=(replace(trial_case.segments[0], text=f"Check {secret_key}"), replace(trial_case.segments[1], result=f"Result {secret_key}")),
    )

    rendered_request: str = json.dumps(asdict(build_state(changed_trial_case.segments, changed_trial_case.current_goal, "full")))
    compaction: Compaction = apply_decisions(changed_trial_case.segments, {"tool_2": 0.9}, 0.5)
    saved_text: str = json.dumps(
        [redact(segment.text) + (redact(segment.result) if isinstance(segment, ToolSegment) else "") for segment in compaction.retained]
    )

    assert secret_key not in rendered_request
    assert secret_key not in saved_text


def test_Rollout_WhenImageIsSelected_ThenItRaisesRolloutError(tmp_path: Path) -> None:
    FILE_ENCODING: str = "utf-8"
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    records: list[dict[str, object]] = [json.loads(line) for line in source.read_text(encoding=FILE_ENCODING).splitlines()]
    first: dict[str, object] = cast(dict[str, object], records[0]["payload"])
    first["content"] = [{"type": "input_text", "text": "Does HTTPS work?"}, {"type": "input_image", "image_url": "data:ignored"}]
    source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding=FILE_ENCODING)
    expected_message_phrase: str = "unsupported nontext content"

    with pytest.raises(RolloutError, match=expected_message_phrase):
        load_case(source, 1, 6)


@pytest.mark.parametrize(("field_name", "invalid_field"), [("name", None), ("input", 7)])
def test_Rollout_WhenToolCallFieldsAreMalformed_ThenItRejectsTheRecord(tmp_path: Path, field_name: str, invalid_field: object) -> None:
    FILE_ENCODING: str = "utf-8"
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    records: list[dict[str, object]] = [json.loads(line) for line in source.read_text(encoding=FILE_ENCODING).splitlines()]
    call_payload: dict[str, object] = cast(dict[str, object], records[1]["payload"])
    call_payload[field_name] = invalid_field
    source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding=FILE_ENCODING)
    expected_message_phrase: str = "needs string name and input"

    with pytest.raises(RolloutError, match=expected_message_phrase):
        load_case(source, 1, 6)


def test_Excerpt_WhenGoalTermsTie_ThenDifferentHashSeedsProduceTheSameState() -> None:
    script: str = (
        "import json; from dataclasses import asdict; "
        "from context_compaction import ToolSegment; "
        "from context_compaction.managers.processors.extractors.context_state_extractor import build_state; "
        "result = 'alpha bravo ' + 'x' * 600; "
        "print(json.dumps(asdict(build_state((ToolSegment('tool_1', 'call', result),), 'alpha bravo', 'excerpt')), sort_keys=True))"
    )
    rendered_states: list[str] = []
    for hash_seed in ("1", "4"):
        process_environment: dict[str, str] = dict(os.environ)
        process_environment["PYTHONHASHSEED"] = hash_seed
        completed_process: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603 - fixed local interpreter and script
            [sys.executable, "-c", script], capture_output=True, check=True, text=True, env=process_environment
        )
        rendered_states.append(completed_process.stdout)

    assert rendered_states[0] == rendered_states[1]


@pytest.mark.asyncio
async def test_JevGateway_WhenEveryAnswerIsPresent_ThenItReturnsProbabilities() -> None:
    model: str = f"jev-{secrets.token_hex(4)}"
    question_id: str = f"tool_{secrets.token_hex(4)}"
    expected_probability: float = 0.9
    expected_input_tokens: int = 12
    expected_probability_by_tool_id: dict[str, float] = {question_id: expected_probability}
    goal_text: str = f"HTTPS {secrets.token_hex(4)}"
    instructions: str = f"Keep evidence {secrets.token_hex(4)}?"
    system_one_response: SystemOneResponse = SystemOneResponse.model_validate(
        {
            "model": model,
            "usage": {"input_tokens": expected_input_tokens, "output_tokens": 1},
            "answers": {question_id: {"type": "noul", "noul": expected_probability}},
        }
    )
    client: MagicMock = MagicMock()
    client.system_one = AsyncMock(return_value=system_one_response)
    client.aclose = AsyncMock()
    api_key: str = f"test-only-{secrets.token_hex(8)}"
    jev_settings: JevSettings = JevSettings(SecretStr(api_key), f"https://{secrets.token_hex(4)}.invalid", model)
    expected_question_ids: set[str] = {question_id}
    jev_state: JevState = JevState(current_goal=goal_text, timeline=())
    expected_call_properties: tuple[set[str], bool, str, object, str] = (expected_question_ids, True, model, asdict(jev_state), instructions)

    async with JevGateway(jev_settings, cast("AsyncTypeSafeClient", client)) as jev_gateway:
        actual_jev_decision_batch: JevDecisionBatch = await jev_gateway.decide(jev_state, {question_id: RetentionQuestion(instructions)})

    assert_jev_decision_batch_matches(
        actual_jev_decision_batch, expected_probability_by_tool_id=expected_probability_by_tool_id, expected_input_tokens=expected_input_tokens
    )
    sent_questions: dict[str, object] = client.system_one.await_args.kwargs["questions"]
    actual_call_properties: tuple[set[str], bool, str, object, str] = (
        set(sent_questions),
        isinstance(sent_questions[question_id], Noul),
        client.system_one.await_args.kwargs["model"],
        client.system_one.await_args.kwargs["state"],
        cast(Noul, sent_questions[question_id]).instructions,
    )
    assert actual_call_properties == expected_call_properties
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_JevGateway_WhenAnswerIsMissing_ThenItRaisesDomainError() -> None:
    model: str = f"jev-{secrets.token_hex(4)}"
    requested_question_id: str = f"tool_{secrets.token_hex(4)}"
    returned_question_id: str = f"tool_{secrets.token_hex(4)}"
    goal_text: str = f"HTTPS {secrets.token_hex(4)}"
    instructions: str = f"Keep evidence {secrets.token_hex(4)}?"
    system_one_response: SystemOneResponse = SystemOneResponse.model_validate(
        {"model": model, "usage": {"input_tokens": 12, "output_tokens": 1}, "answers": {returned_question_id: {"type": "noul", "noul": 0.9}}}
    )
    client: MagicMock = MagicMock()
    client.system_one = AsyncMock(return_value=system_one_response)
    client.aclose = AsyncMock()
    api_key: str = f"test-only-{secrets.token_hex(8)}"
    jev_settings: JevSettings = JevSettings(SecretStr(api_key), f"https://{secrets.token_hex(4)}.invalid", model)
    jev_state: JevState = JevState(current_goal=goal_text, timeline=())
    expected_context: dict[str, str] = {
        "expected_question_ids": repr([requested_question_id]),
        "answer_ids": repr([returned_question_id]),
        "noul_ids": repr([returned_question_id]),
    }
    expected_jev_failure_expectation: JevFailureExpectation = JevFailureExpectation(
        operation="validate_response",
        model=model,
        detail_value_by_name=expected_context,
        message_phrase="missing or unexpected",
        secret_key=api_key,
        cause=None,
    )

    async with JevGateway(jev_settings, cast("AsyncTypeSafeClient", client)) as jev_gateway:
        with pytest.raises(JevResponseError) as error:
            await jev_gateway.decide(jev_state, {requested_question_id: RetentionQuestion(instructions)})

    assert_jev_failure_matches(error.value, expected_jev_failure_expectation)


@pytest.mark.asyncio
async def test_JevGateway_WhenProviderRequestFails_ThenItRaisesRequestErrorWithSafeContext() -> None:
    api_key: str = f"test-only-{secrets.token_hex(8)}"
    model: str = f"jev-{secrets.token_hex(4)}"
    question_id: str = f"tool_{secrets.token_hex(4)}"
    runtime_error: RuntimeError = RuntimeError(f"provider failure containing {api_key}")
    client: MagicMock = MagicMock()
    client.system_one = AsyncMock(side_effect=runtime_error)
    client.aclose = AsyncMock()
    jev_settings: JevSettings = JevSettings(SecretStr(api_key), f"https://{secrets.token_hex(4)}.invalid", model)
    jev_state: JevState = JevState(current_goal=secrets.token_hex(4), timeline=())
    retention_questions: dict[str, RetentionQuestion] = {question_id: RetentionQuestion(secrets.token_hex(4))}
    expected_jev_failure_expectation: JevFailureExpectation = JevFailureExpectation(
        operation="system_one", model=model, detail_value_by_name={}, message_phrase="request failed", secret_key=api_key, cause=runtime_error
    )

    async with JevGateway(jev_settings, cast("AsyncTypeSafeClient", client)) as jev_gateway:
        with pytest.raises(JevRequestError) as error:
            await jev_gateway.decide(jev_state, retention_questions)

    assert_jev_failure_matches(error.value, expected_jev_failure_expectation)


def test_Rollout_WhenOutputsArriveOutOfOrder_ThenTimelineTracksCompletion(tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    first_call_id: str = secrets.token_hex(5)
    second_call_id: str = secrets.token_hex(5)
    first_result: str = f"first result {secrets.token_hex(4)}"
    second_result: str = f"second result {secrets.token_hex(4)}"
    user_text: str = f"Compare {secrets.token_hex(4)}"
    first_command: str = f"first {secrets.token_hex(4)}"
    second_command: str = f"second {secrets.token_hex(4)}"
    answer_text: str = f"Finished {secrets.token_hex(4)}"
    expected_tool_events: list[tuple[str, str]] = [("tool_3", second_result), ("tool_2", first_result)]
    records: list[dict[str, object]] = [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": user_text}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": first_call_id, "name": "exec", "input": first_command}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": second_call_id, "name": "exec", "input": second_command}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": second_call_id, "output": second_result}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": first_call_id, "output": first_result}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": answer_text}]}},
    ]
    source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    trial_case: TrialCase = load_case(source, 1, 6)
    actual_tool_events: list[tuple[str, str]] = [(segment.id, segment.result) for segment in trial_case.segments if isinstance(segment, ToolSegment)]

    assert actual_tool_events == expected_tool_events


@pytest.mark.asyncio
async def test_JevGateway_WhenClosedTwice_ThenClientIsReleasedOnce() -> None:
    client: MagicMock = MagicMock()
    client.aclose = AsyncMock()
    jev_settings: JevSettings = JevSettings(
        SecretStr(f"test-only-{secrets.token_hex(8)}"), f"https://{secrets.token_hex(4)}.invalid", f"jev-{secrets.token_hex(4)}"
    )
    jev_gateway: JevGateway = JevGateway(jev_settings, cast("AsyncTypeSafeClient", client))

    await jev_gateway.close()
    await jev_gateway.close()

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_JevGateway_WhenCleanupFails_ThenDomainErrorIsRaisedAndCloseCanRetry() -> None:
    client: MagicMock = MagicMock()
    runtime_error: RuntimeError = RuntimeError(f"transport {secrets.token_hex(4)}")
    client.aclose = AsyncMock(side_effect=[runtime_error, None])
    api_key: str = f"test-only-{secrets.token_hex(8)}"
    model: str = f"jev-{secrets.token_hex(4)}"
    expected_close_calls: int = 2
    jev_settings: JevSettings = JevSettings(SecretStr(api_key), f"https://{secrets.token_hex(4)}.invalid", model)
    expected_jev_failure_expectation: JevFailureExpectation = JevFailureExpectation(
        operation="aclose", model=model, detail_value_by_name={}, message_phrase="cleanup failed", secret_key=api_key, cause=runtime_error
    )
    jev_gateway: JevGateway = JevGateway(jev_settings, cast("AsyncTypeSafeClient", client))

    with pytest.raises(JevCleanupError) as error:
        await jev_gateway.close()
    await jev_gateway.close()

    assert_jev_failure_matches(error.value, expected_jev_failure_expectation)
    assert client.aclose.await_count == expected_close_calls


def test_Cli_WhenPreparingExistingDirectory_ThenStaleLiveArtifactsAreRemoved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FILE_ENCODING: str = "utf-8"
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    output_dir: Path = tmp_path / "trial"
    output_dir.mkdir()
    stale_compacted_file: Path = output_dir / "compacted_current_1.json"
    live_results_file: Path = output_dir / "live_results.json"
    stale_compacted_file.write_text(secrets.token_hex(6), encoding=FILE_ENCODING)
    live_results_file.write_text(secrets.token_hex(6), encoding=FILE_ENCODING)
    expected_live_results: list[object] = []
    expected_exit_code: int = 0
    monkeypatch.setattr(
        sys,
        "argv",
        ["context-compaction", str(source), "--start-line", "1", "--cutoff-line", "6", "--output-dir", str(output_dir), "--runs", "0"],
    )

    actual_exit_code: int = main()
    actual_live_results: list[object] = json.loads(live_results_file.read_text(encoding=FILE_ENCODING))

    assert actual_exit_code == expected_exit_code
    assert actual_live_results == expected_live_results
    assert not stale_compacted_file.exists()


def test_Cli_WhenSettingsParserFails_ThenDomainErrorLeavesEarlierTrialFilesIntact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    FILE_ENCODING: str = "utf-8"
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    env_file: Path = tmp_path / "broken.env"
    env_file.write_text(secrets.token_hex(6), encoding=FILE_ENCODING)
    output_dir: Path = tmp_path / "trial"
    output_dir.mkdir()
    prior_results_file: Path = output_dir / "live_results.json"
    prior_result_text: str = f"prior result {secrets.token_hex(4)}"
    prior_results_file.write_text(prior_result_text, encoding=FILE_ENCODING)
    expected_exit_code: int = 1
    expected_diagnostic: str = "Could not read Jev settings"
    expected_diagnostic_phrases: tuple[str, ...] = (expected_diagnostic, str(env_file))
    parser_failure_detail: str = secrets.token_hex(8)
    forbidden_diagnostic_phrases: tuple[str, ...] = (parser_failure_detail,)

    def fail_to_parse(_: Path) -> NoReturn:
        raise ValueError(parser_failure_detail)

    monkeypatch.setattr(compaction_configuration, "dotenv_values", fail_to_parse)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "context-compaction",
            str(source),
            "--start-line",
            "1",
            "--cutoff-line",
            "6",
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
            "--runs",
            "1",
        ],
    )

    actual_exit_code: int = main()
    error_text: str = capsys.readouterr().err

    assert actual_exit_code == expected_exit_code
    assert_cli_diagnostic_matches(error_text, expected_diagnostic_phrases, forbidden_diagnostic_phrases)
    assert prior_results_file.read_text(encoding=FILE_ENCODING) == prior_result_text


@pytest.mark.parametrize("artifact_name", ["manifest.json", "live_results.json", "compacted_current_1.json"])
def test_Cli_WhenSourceIsAnOutputPath_ThenRejectsBeforeChangingTheSource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], artifact_name: str
) -> None:
    source: Path = tmp_path / artifact_name
    _write_case(source)
    expected_source_bytes: bytes = source.read_bytes()
    expected_exit_code: int = 1
    expected_phrases: tuple[str, ...] = ("original rollout", str(source))
    forbidden_phrases: tuple[str, ...] = ()
    monkeypatch.setattr(
        sys, "argv", ["context-compaction", str(source), "--start-line", "1", "--cutoff-line", "6", "--output-dir", str(tmp_path), "--runs", "0"]
    )

    actual_exit_code: int = main()
    actual_source_bytes: bytes = source.read_bytes()
    actual_diagnostic: str = capsys.readouterr().err

    assert actual_exit_code == expected_exit_code
    assert actual_source_bytes == expected_source_bytes
    assert_cli_diagnostic_matches(actual_diagnostic, expected_phrases, forbidden_phrases)


@pytest.mark.parametrize("link_kind", ["hard", "symbolic"])
def test_Cli_WhenAnOutputAliasesTheSource_ThenRejectsBeforeChangingTheSource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], link_kind: str
) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    aliased_output: Path = tmp_path / "manifest.json"
    if link_kind == "hard":
        aliased_output.hardlink_to(source)
    else:
        try:
            aliased_output.symlink_to(source)
        except OSError:
            pytest.skip("This Windows host does not permit creating symbolic links.")
    expected_source_bytes: bytes = source.read_bytes()
    expected_exit_code: int = 1
    expected_phrases: tuple[str, ...] = ("original rollout", str(aliased_output))
    forbidden_phrases: tuple[str, ...] = ()
    monkeypatch.setattr(
        sys, "argv", ["context-compaction", str(source), "--start-line", "1", "--cutoff-line", "6", "--output-dir", str(tmp_path), "--runs", "0"]
    )

    actual_exit_code: int = main()
    actual_diagnostic: str = capsys.readouterr().err

    assert actual_exit_code == expected_exit_code
    assert source.read_bytes() == expected_source_bytes
    assert_cli_diagnostic_matches(actual_diagnostic, expected_phrases, forbidden_phrases)


@pytest.mark.parametrize("embedded_json", [False, True])
def test_Compaction_WhenToolContainsAQuotedCredential_ThenRequestAndRetainedContextAreRedacted(tmp_path: Path, embedded_json: bool) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    rollout_scenario: RolloutScenario = _write_case(source)
    trial_case: TrialCase = load_case(source, 1, 6)
    secret_key: str = f"opaque-{secrets.token_hex(16)}"
    credential_json: str = json.dumps({"TYPESAFE_API_KEY": secret_key})
    credential_text: str = json.dumps(credential_json) if embedded_json else credential_json
    tool_segment: ToolSegment = cast(ToolSegment, trial_case.segments[1])
    credential_trial_case: TrialCase = replace(trial_case, segments=(trial_case.segments[0], replace(tool_segment, result=credential_text)))
    expected_redaction_marker: str = "[REDACTED]"
    probability_by_tool_id: Mapping[str, float] = {rollout_scenario.tool_id: 0.9}
    domain_facade: DomainFacade = DomainFacade()

    prepared_compaction: PreparedCompaction = domain_facade.prepare(credential_trial_case)
    compaction: Compaction = domain_facade.apply_saved_decisions(credential_trial_case.segments, probability_by_tool_id)
    actual_state_json: str = json.dumps(asdict(prepared_compaction.jev_state))
    actual_context_json: str = json.dumps([asdict(entry) for entry in compaction.context])

    assert_secret_is_redacted(actual_state_json, secret_key, expected_redaction_marker)
    assert_secret_is_redacted(actual_context_json, secret_key, expected_redaction_marker)


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"type": "message", "content": [{"text": "shape-control"}]},
        {"type": "message", "role": {"wrong": "shape"}, "content": [{"text": "shape-control"}]},
        {"type": "message", "role": "user", "content": [{"text": 7}]},
    ],
)
def test_Rollout_WhenMessageSchemaIsInvalid_ThenRejectsBeforeReturningDomainContext(tmp_path: Path, invalid_payload: dict[str, object]) -> None:
    FILE_ENCODING: str = "utf-8"
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    rollout_records: list[dict[str, object]] = [json.loads(line) for line in source.read_text(encoding=FILE_ENCODING).splitlines()]
    rollout_records[0]["payload"] = invalid_payload
    source.write_text("\n".join(json.dumps(record) for record in rollout_records) + "\n", encoding=FILE_ENCODING)
    expected_message_phrase: str = "invalid message shape"

    with pytest.raises(RolloutError, match=expected_message_phrase):
        load_case(source, 1, 6)


def test_Configuration_WhenFileReaderFails_ThenTechnicalErrorPreservesSourceAndCause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file: Path = tmp_path / "settings.env"
    env_file.write_text(secrets.token_hex(8), encoding="utf-8")
    value_error: ValueError = ValueError(secrets.token_hex(8))
    expected_operation: str = "read_jev_settings"
    expected_message_phrase: str = "Could not read Jev settings"
    expected_diagnostic: tuple[str, Path, BaseException] = (expected_operation, env_file, value_error)
    configuration_provider: compaction_configuration.ConfigurationProvider = compaction_configuration.ConfigurationProvider(env_file)

    def fail_to_read(_: Path) -> NoReturn:
        raise value_error

    monkeypatch.setattr(compaction_configuration, "dotenv_values", fail_to_read)

    with pytest.raises(ContextCompactionConfigurationReadError, match=expected_message_phrase) as exception_info:
        configuration_provider.get_jev_settings()

    assert (exception_info.value.operation, exception_info.value.env_file, exception_info.value.__cause__) == expected_diagnostic


@dataclass(frozen=True)
class RejectedTrialObservation:
    exit_code: int
    diagnostic: str
    artifact_text_by_name: Mapping[str, str]


def assert_rejected_trial_matches(
    actual_rejected_trial_observation: RejectedTrialObservation, expected_rejected_trial_observation: RejectedTrialObservation
) -> None:
    mismatches: list[str] = []
    if actual_rejected_trial_observation.exit_code != expected_rejected_trial_observation.exit_code:
        mismatches.append(f"exit code: expected {expected_rejected_trial_observation.exit_code}; got {actual_rejected_trial_observation.exit_code}")
    if expected_rejected_trial_observation.diagnostic not in actual_rejected_trial_observation.diagnostic:
        mismatches.append(
            f"diagnostic: expected phrase {expected_rejected_trial_observation.diagnostic!r}; got {actual_rejected_trial_observation.diagnostic!r}"
        )
    if actual_rejected_trial_observation.artifact_text_by_name != expected_rejected_trial_observation.artifact_text_by_name:
        mismatches.append("previous artifacts must retain their complete original content")
    assert not mismatches, "Rejected trial: " + "\n".join(mismatches)


@pytest.mark.parametrize("port_text", ["garbage", "65536"])
def test_Cli_WhenUrlPortIsInvalid_ThenRejectsBeforeTransportCreationAndPreservesArtifacts(
    port_text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    FILE_ENCODING: str = "utf-8"
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    env_file: Path = tmp_path / "jev.env"
    api_key: str = secrets.token_hex(16)
    model: str = f"jev-{secrets.token_hex(8)}"
    env_file.write_text(
        f"OPEN_ROUTER_KEY={api_key}\nOPEN_ROUTER_BASE_URL=https://jev.invalid:{port_text}/api\nJEV_MODEL={model}\n", encoding=FILE_ENCODING
    )
    output_dir: Path = tmp_path / "trial"
    output_dir.mkdir()
    expected_artifact_text_by_name: Mapping[str, str] = {
        "live_results.json": secrets.token_hex(12),
        "compacted_current_1.json": secrets.token_hex(12),
    }
    artifact_name: str
    expected_artifact_text: str
    for artifact_name, expected_artifact_text in expected_artifact_text_by_name.items():
        (output_dir / artifact_name).write_text(expected_artifact_text, encoding=FILE_ENCODING)
    expected_rejected_trial_observation: RejectedTrialObservation = RejectedTrialObservation(1, "valid HTTP(S) URL", expected_artifact_text_by_name)

    def reject_transport_creation(_: ServiceLocatorProduction) -> NoReturn:
        raise AssertionError("Invalid configuration must be rejected before transport creation")

    monkeypatch.setattr(ServiceLocatorProduction, "make_jev_http_transport", reject_transport_creation)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "context-compaction",
            str(source),
            "--start-line",
            "1",
            "--cutoff-line",
            "6",
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
            "--runs",
            "1",
        ],
    )
    actual_exit_code: int = main()
    actual_diagnostic: str = capsys.readouterr().err
    actual_artifact_text_by_name: Mapping[str, str] = {
        artifact_name: (output_dir / artifact_name).read_text(encoding=FILE_ENCODING) for artifact_name in expected_artifact_text_by_name
    }
    actual_rejected_trial_observation: RejectedTrialObservation = RejectedTrialObservation(
        actual_exit_code, actual_diagnostic, actual_artifact_text_by_name
    )
    assert_rejected_trial_matches(actual_rejected_trial_observation, expected_rejected_trial_observation)


@pytest.mark.parametrize("operation_name", ["prepare", "describe_context"])
def test_Preparation_WhenContextHasZeroCharacters_ThenRejectsWithDomainDiagnostic(operation_name: str, tmp_path: Path) -> None:
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    expected_message_phrase: str = "nonempty segment text"
    tool_id: str = secrets.token_hex(8)
    domain_facade: DomainFacade = DomainFacade()
    trial_case: TrialCase = replace(domain_facade.load_context(source, 1, 6), segments=(ToolSegment(tool_id, "", ""),))
    preparation_operation: Callable[[TrialCase], object] = domain_facade.prepare if operation_name == "prepare" else domain_facade.describe_context
    with pytest.raises(CompactionDecisionError, match=expected_message_phrase):
        preparation_operation(trial_case)


def test_Cli_WhenPreparingTrial_ThenJsonRetainsExternalFieldNames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FILE_ENCODING: str = "utf-8"
    source: Path = tmp_path / "rollout.jsonl"
    _write_case(source)
    output_dir: Path = tmp_path / "trial"
    domain_facade: DomainFacade = DomainFacade()
    trial_case: TrialCase = domain_facade.load_context(source, 1, 6)
    prepared_compaction: PreparedCompaction = domain_facade.prepare(trial_case)
    trial_manifest: TrialManifest = domain_facade.describe_context(trial_case)
    variants: tuple[Variant, ...] = ("current", "careful")
    expected_requests: dict[str, object] = {
        variant: {
            "state": json.loads(json.dumps(asdict(prepared_compaction.jev_state))),
            "questions": {
                question_id: {"type": "noul", "instructions": retention_question.instructions}
                for question_id, retention_question in domain_facade.prepare(trial_case, variant=variant).questions.items()
            },
        }
        for variant in variants
    }
    expected_manifest: dict[str, object] = {
        "source": str(source),
        "source_sha256": trial_case.source_sha256,
        "start_line": 1,
        "cutoff_line": 6,
        "evidence_mode": "full",
        "current_goal": trial_case.current_goal,
        "held_out_answer": trial_case.held_out_answer,
        "protected_messages": 1,
        "tool_events": [asdict(summary) for summary in trial_manifest.tool_events],
        "original_characters": sum(segment.characters for segment in trial_case.segments),
        "baselines": asdict(trial_manifest.trial_baselines),
        "measurement": "Characters of chat and tool text, not model tokens or JSONL bytes.",
    }
    expected_exit_code: int = 0
    monkeypatch.setattr(
        sys,
        "argv",
        ["context-compaction", str(source), "--start-line", "1", "--cutoff-line", "6", "--output-dir", str(output_dir), "--runs", "0"],
    )

    actual_exit_code: int = main()
    actual_requests: object = json.loads((output_dir / "jev_requests.json").read_text(encoding=FILE_ENCODING))
    actual_manifest: object = json.loads((output_dir / "manifest.json").read_text(encoding=FILE_ENCODING))

    assert (actual_exit_code, actual_requests, actual_manifest) == (expected_exit_code, expected_requests, expected_manifest)
