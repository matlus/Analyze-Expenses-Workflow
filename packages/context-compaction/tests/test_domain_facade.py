from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import ClassVar, Final, cast, final, override
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from context_compaction import (
    CompactionResult,
    CompactionThresholdOutOfRangeException,
    ContextCompactionTechnicalBaseException,
    DomainFacade,
    EvidenceMode,
    JevAnswerSetMismatchException,
    JevClientCleanupException,
    JevDisabledException,
    JevGatewayBaseException,
    JevGatewayClosedException,
    JevProbabilityOutOfRangeException,
    JevRateLimitRetriesExhaustedException,
    JevRequestFailedException,
    JevRequestRejectedException,
    JevResponseSchemaInvalidException,
    JevServerRetryRequestedRetriesExhaustedException,
    JevSettings,
    JevStateTooLargeException,
    JevTransportCleanupException,
    PreparedCompaction,
    PreparedQuestionsMismatchException,
    PreparedStateMismatchException,
    TrialCase,
    Variant,
)
from context_compaction.managers.configuration_providers.configuration_provider import ConfigurationProvider
from context_compaction.managers.gateways.jev_gateway import JevGateway
from context_compaction.managers.gateways.jev_gateway_protocol import JevGatewayProtocol
from context_compaction.managers.manager_context_compaction import ManagerContextCompaction
from context_compaction.managers.service_locators.service_locator_production import ServiceLocatorProduction
from context_compaction.managers.service_locators.service_locator_protocol import ServiceLocatorProtocol

SCRIPTED_EPOCH_SECONDS: Final[float] = 1_800_000_000.0


def _http_date(epoch_seconds: float) -> str:
    return format_datetime(datetime.fromtimestamp(epoch_seconds, tz=UTC), usegmt=True)


@dataclass(frozen=True)
class ScriptedJevResponse:
    status_code: int
    provider_message: str
    retry_after: str | None = None
    retry_after_milliseconds: str | None = None


class TestMediatorJev:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        answer_by_question_id: Mapping[str, object],
        model: str,
        *,
        close_failures: int = 0,
        scripted_jev_responses: tuple[ScriptedJevResponse, ...] = (),
        retry_jitter_fraction: float = 0.5,
        transport_failure: httpx2.TransportError | None = None,
    ) -> None:
        self.compact_answer_by_question_id: Mapping[str, object] = answer_by_question_id
        self.compact_response_model: str = model
        self.api_key: str = secrets.token_hex(16)
        self.compact_requests: list[dict[str, object]] = []
        self.cleanup_attempt_count: int = 0
        self.remaining_cleanup_failures: int = close_failures
        self.remaining_scripted_jev_responses: list[ScriptedJevResponse] = list(scripted_jev_responses)
        self.retry_jitter_fraction: float = retry_jitter_fraction
        self.transport_failure: httpx2.TransportError | None = transport_failure
        self.retry_delays: list[float] = []
        self.request_count_at_each_retry_wait: list[int] = []

    async def wait_for_retry(self, delay_seconds: float) -> None:
        self.retry_delays.append(delay_seconds)
        self.request_count_at_each_retry_wait.append(len(self.compact_requests))

    def jitter_fraction(self) -> float:
        return self.retry_jitter_fraction

    def current_epoch_seconds(self) -> float:
        return SCRIPTED_EPOCH_SECONDS


@final
class JevTransportSpy(httpx2.AsyncBaseTransport):
    def __init__(self, test_mediator_jev: TestMediatorJev) -> None:
        self._test_mediator_jev: TestMediatorJev = test_mediator_jev

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self._test_mediator_jev.compact_requests.append(json.loads(request.content))
        if self._test_mediator_jev.transport_failure is not None:
            raise self._test_mediator_jev.transport_failure
        if self._test_mediator_jev.remaining_scripted_jev_responses:
            return self._build_scripted_response(self._test_mediator_jev.remaining_scripted_jev_responses.pop(0))
        return self._build_response()

    def _build_response(self) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=json.dumps(
                {
                    "model": self._test_mediator_jev.compact_response_model,
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                    "answers": self._test_mediator_jev.compact_answer_by_question_id,
                }
            ),
            headers={"content-type": "application/json"},
        )

    @staticmethod
    def _build_scripted_response(scripted_jev_response: ScriptedJevResponse) -> httpx2.Response:
        header_value_by_name: dict[str, str] = {"content-type": "application/json"}
        if scripted_jev_response.retry_after is not None:
            header_value_by_name["Retry-After"] = scripted_jev_response.retry_after
        if scripted_jev_response.retry_after_milliseconds is not None:
            header_value_by_name["Retry-After-Ms"] = scripted_jev_response.retry_after_milliseconds
        return httpx2.Response(
            scripted_jev_response.status_code,
            content=json.dumps({"error": {"message": scripted_jev_response.provider_message}}),
            headers=header_value_by_name,
        )

    @override
    async def aclose(self) -> None:
        self._test_mediator_jev.cleanup_attempt_count += 1
        if self._test_mediator_jev.remaining_cleanup_failures:
            self._test_mediator_jev.remaining_cleanup_failures -= 1
            raise OSError("Scripted transport cleanup failure")


class ServiceLocatorTesting(ServiceLocatorProduction):
    def __init__(self, jev_transport_spy: JevTransportSpy, test_mediator_jev: TestMediatorJev) -> None:
        super().__init__()
        self._jev_transport_spy: JevTransportSpy = jev_transport_spy
        self._test_mediator_jev: TestMediatorJev = test_mediator_jev

    @override
    def make_jev_http_transport(self) -> httpx2.AsyncBaseTransport:
        return self._jev_transport_spy

    @override
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider(
            Path(".env"),
            {
                "OPEN_ROUTER_KEY": self._test_mediator_jev.api_key,
                "OPEN_ROUTER_BASE_URL": "https://jev.invalid/api",
                "JEV_MODEL": self._test_mediator_jev.compact_response_model,
            },
        )

    @override
    def create_jev_gateway(self, jev_settings: JevSettings) -> JevGatewayProtocol:
        return JevGateway(
            jev_settings,
            async_base_transport=self.make_jev_http_transport(),
            wait_for_retry=self._test_mediator_jev.wait_for_retry,
            retry_jitter_fraction=self._test_mediator_jev.jitter_fraction,
            current_epoch_seconds=self._test_mediator_jev.current_epoch_seconds,
        )


def _create_domain_facade(test_mediator_jev: TestMediatorJev) -> DomainFacade:
    jev_transport_spy: JevTransportSpy = JevTransportSpy(test_mediator_jev)
    service_locator_testing: ServiceLocatorTesting = ServiceLocatorTesting(jev_transport_spy, test_mediator_jev)
    return DomainFacade.for_live_evaluation(service_locator_testing)


@dataclass(frozen=True)
class ExpectedFacadeTrial:
    retained_ids: tuple[str, ...]
    probability_by_tool_id: Mapping[str, float]
    model: str
    question_ids: frozenset[str]
    request_count: int
    close_attempts: int
    retry_delays: tuple[float, ...] = ()
    request_count_at_each_retry_wait: tuple[int, ...] = ()


@dataclass(frozen=True)
class ExpectedGatewayFailure:
    message_phrase: str
    operation: str
    model: str
    detail_value_by_name: Mapping[str, str]


@dataclass(frozen=True)
class ExpectedJevStatusFailure:
    message_phrases: tuple[str, ...]
    detail_value_by_name: Mapping[str, str]
    request_count: int
    retry_delays: tuple[float, ...]
    request_count_at_each_retry_wait: tuple[int, ...]
    forbidden_phrases: tuple[str, ...] = field(default=())


@final
@dataclass(frozen=True)
class RolloutMessageRecord:
    role: str
    text: str

    def to_jsonl(self) -> str:
        return json.dumps(
            {"type": "response_item", "payload": {"type": "message", "role": self.role, "content": [{"type": "input_text", "text": self.text}]}}
        )


@final
@dataclass(frozen=True)
class RolloutToolCallRecord:
    call_id: str
    command: str

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "call_id": self.call_id, "name": "functions.exec", "input": self.command},
            }
        )


@final
@dataclass(frozen=True)
class RolloutToolOutputRecord:
    call_id: str
    output: str

    def to_jsonl(self) -> str:
        return json.dumps({"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": self.call_id, "output": self.output}})


type RolloutRecord = RolloutMessageRecord | RolloutToolCallRecord | RolloutToolOutputRecord


def _write_rollout(tmp_path: Path) -> Path:
    source: Path = tmp_path / f"rollout-{secrets.token_hex(4)}.jsonl"
    host_name: str = f"host-{secrets.token_hex(4)}.invalid"
    first_call_id: str = secrets.token_hex(5)
    second_call_id: str = secrets.token_hex(5)
    third_call_id: str = secrets.token_hex(5)
    rollout_records: tuple[RolloutRecord, ...] = (
        RolloutMessageRecord("user", f"Please check {host_name} and its www alias {secrets.token_hex(4)}."),
        RolloutMessageRecord("assistant", f"I will check both hosts over HTTPS {secrets.token_hex(4)}."),
        RolloutToolCallRecord(first_call_id, f"curl -Iv https://www.{host_name}"),
        RolloutToolOutputRecord(first_call_id, f"HTTP/2 301\nlocation: https://{host_name}/ {secrets.token_hex(4)}"),
        RolloutToolCallRecord(second_call_id, f"curl -Iv https://{host_name}"),
        RolloutToolOutputRecord(second_call_id, f"HTTP/2 503\ncertificate pending {secrets.token_hex(4)}"),
        RolloutToolCallRecord(third_call_id, f"curl -Iv https://{host_name}"),
        RolloutToolOutputRecord(third_call_id, f"HTTP/2 200\ncertificate verify ok {secrets.token_hex(4)}"),
        RolloutMessageRecord("user", f"Is HTTPS ready for {host_name}? {secrets.token_hex(4)}"),
        RolloutMessageRecord("assistant", f"HTTPS for {host_name} is ready {secrets.token_hex(4)}."),
    )
    source.write_text("\n".join(rollout_record.to_jsonl() for rollout_record in rollout_records) + "\n", encoding="utf-8")
    return source


def _valid_answer_by_question_id() -> dict[str, object]:
    probability_by_tool_id: Mapping[str, float] = {"tool_3": 0.8, "tool_5": 0.1, "tool_7": 0.9}
    answer_by_question_id: dict[str, object] = {}
    tool_id: str
    probability: float
    for tool_id, probability in probability_by_tool_id.items():
        answer_by_question_id[tool_id] = {"type": "noul", "noul": probability}
    return answer_by_question_id


def _retry_wait_mismatches(
    actual_test_mediator_jev: TestMediatorJev,
    expected_retry_delays: tuple[float, ...],
    expected_request_count_at_each_retry_wait: tuple[int, ...],
) -> list[str]:
    mismatches: list[str] = []
    actual_retry_delays: tuple[float, ...] = tuple(actual_test_mediator_jev.retry_delays)
    if actual_retry_delays != pytest.approx(expected_retry_delays):
        mismatches.append(f"retry delays: expected {expected_retry_delays}; got {actual_retry_delays}")
    actual_request_count_at_each_retry_wait: tuple[int, ...] = tuple(actual_test_mediator_jev.request_count_at_each_retry_wait)
    if actual_request_count_at_each_retry_wait != expected_request_count_at_each_retry_wait:
        mismatches.append(
            f"request count at each retry wait: expected {expected_request_count_at_each_retry_wait}; got {actual_request_count_at_each_retry_wait}"
        )
    return mismatches


def assert_facade_trial_matches(
    actual_compaction_result: CompactionResult, actual_test_mediator_jev: TestMediatorJev, expected_facade_trial: ExpectedFacadeTrial
) -> None:
    mismatches: list[str] = []
    actual_retained_ids: tuple[str, ...] = tuple(entry.id for entry in actual_compaction_result.compaction.retained)
    if actual_retained_ids != expected_facade_trial.retained_ids:
        mismatches.append(f"retained IDs: expected {expected_facade_trial.retained_ids}; got {actual_retained_ids}")
    if actual_compaction_result.jev_decision_batch.probabilities != expected_facade_trial.probability_by_tool_id:
        mismatches.append(
            f"gateway probabilities: expected {expected_facade_trial.probability_by_tool_id}; "
            f"got {actual_compaction_result.jev_decision_batch.probabilities}"
        )
    if actual_compaction_result.compaction.probabilities != expected_facade_trial.probability_by_tool_id:
        mismatches.append(
            f"selection probabilities: expected {expected_facade_trial.probability_by_tool_id}; "
            f"got {actual_compaction_result.compaction.probabilities}"
        )
    if len(actual_test_mediator_jev.compact_requests) != expected_facade_trial.request_count:
        mismatches.append(f"requests: expected {expected_facade_trial.request_count}; got {len(actual_test_mediator_jev.compact_requests)}")
    if actual_test_mediator_jev.compact_requests:
        actual_model: object = actual_test_mediator_jev.compact_requests[0]["model"]
        actual_question_ids: frozenset[str] = frozenset(actual_test_mediator_jev.compact_requests[0]["questions"])
        if actual_model != expected_facade_trial.model:
            mismatches.append(f"model: expected {expected_facade_trial.model}; got {actual_model}")
        if actual_question_ids != expected_facade_trial.question_ids:
            mismatches.append(f"question IDs: expected {expected_facade_trial.question_ids}; got {actual_question_ids}")
    if actual_test_mediator_jev.cleanup_attempt_count != expected_facade_trial.close_attempts:
        mismatches.append(f"close attempts: expected {expected_facade_trial.close_attempts}; got {actual_test_mediator_jev.cleanup_attempt_count}")
    mismatches.extend(
        _retry_wait_mismatches(actual_test_mediator_jev, expected_facade_trial.retry_delays, expected_facade_trial.request_count_at_each_retry_wait)
    )
    assert not mismatches, f"Facade trial {expected_facade_trial.model}: {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def assert_gateway_failure_matches(
    actual_jev_gateway_base_exception: JevGatewayBaseException, expected_gateway_failure: ExpectedGatewayFailure
) -> None:
    mismatches: list[str] = []
    if expected_gateway_failure.message_phrase not in str(actual_jev_gateway_base_exception):
        mismatches.append(f"message: expected phrase {expected_gateway_failure.message_phrase!r}; got {str(actual_jev_gateway_base_exception)!r}")
    if actual_jev_gateway_base_exception.operation != expected_gateway_failure.operation:
        mismatches.append(f"operation: expected {expected_gateway_failure.operation}; got {actual_jev_gateway_base_exception.operation}")
    if actual_jev_gateway_base_exception.model != expected_gateway_failure.model:
        mismatches.append(f"model: expected {expected_gateway_failure.model}; got {actual_jev_gateway_base_exception.model}")
    if actual_jev_gateway_base_exception.detail_value_by_name != expected_gateway_failure.detail_value_by_name:
        mismatches.append(
            f"details: expected {expected_gateway_failure.detail_value_by_name}; got {actual_jev_gateway_base_exception.detail_value_by_name}"
        )
    assert not mismatches, f"Gateway failure {expected_gateway_failure.model}: {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def assert_jev_status_failure_matches(
    actual_jev_gateway_base_exception: JevGatewayBaseException,
    actual_test_mediator_jev: TestMediatorJev,
    expected_jev_status_failure: ExpectedJevStatusFailure,
) -> None:
    mismatches: list[str] = []
    actual_message: str = str(actual_jev_gateway_base_exception)
    mismatches.extend(
        f"message: expected phrase {message_phrase!r}; got {actual_message!r}"
        for message_phrase in expected_jev_status_failure.message_phrases
        if message_phrase not in actual_message
    )
    mismatches.extend(
        f"forbidden phrase disclosed in message: length={len(forbidden_phrase)}"
        for forbidden_phrase in expected_jev_status_failure.forbidden_phrases
        if forbidden_phrase in actual_message
    )
    actual_detail_value_by_name: dict[str, str] = {
        detail_name: actual_jev_gateway_base_exception.detail_value_by_name.get(detail_name, "<absent>")
        for detail_name in expected_jev_status_failure.detail_value_by_name
    }
    if actual_detail_value_by_name != expected_jev_status_failure.detail_value_by_name:
        mismatches.append(f"details: expected {expected_jev_status_failure.detail_value_by_name}; got {actual_detail_value_by_name}")
    if len(actual_test_mediator_jev.compact_requests) != expected_jev_status_failure.request_count:
        mismatches.append(f"requests: expected {expected_jev_status_failure.request_count}; got {len(actual_test_mediator_jev.compact_requests)}")
    mismatches.extend(
        _retry_wait_mismatches(
            actual_test_mediator_jev, expected_jev_status_failure.retry_delays, expected_jev_status_failure.request_count_at_each_retry_wait
        )
    )
    assert not mismatches, f"Jev status failure {type(actual_jev_gateway_base_exception).__name__}: {len(mismatches)} mismatches:\n" + "\n".join(
        mismatches
    )


async def test_Compact_WhenTransportReturnsValidDecisions_ThenRetainsUniqueEvidenceAndClosesOnce(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_probability_by_tool_id: Mapping[str, float] = {"tool_3": 0.8, "tool_5": 0.1, "tool_7": 0.9}
    expected_facade_trial: ExpectedFacadeTrial = ExpectedFacadeTrial(
        ("message_1", "message_2", "tool_3", "tool_7", "message_9"),
        expected_probability_by_tool_id,
        model,
        frozenset(expected_probability_by_tool_id),
        1,
        1,
    )
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure("gateway is closed", "system_one", model, {})
    test_mediator_jev: TestMediatorJev = TestMediatorJev(_valid_answer_by_question_id(), model)
    source: Path = _write_rollout(tmp_path)
    domain_facade: DomainFacade = _create_domain_facade(test_mediator_jev)
    async with domain_facade:
        trial_case: TrialCase = domain_facade.load_context(source, 1, 10)
        prepared_compaction: PreparedCompaction = domain_facade.prepare(trial_case)
        compaction_result: CompactionResult = await domain_facade.compact(prepared_compaction)
    await domain_facade.close()
    with pytest.raises(JevGatewayClosedException) as exception_info:
        await domain_facade.compact(prepared_compaction)
    assert_facade_trial_matches(compaction_result, test_mediator_jev, expected_facade_trial)
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)


@pytest.mark.parametrize(
    ("scripted_jev_response", "retry_jitter_fraction", "expected_retry_delay"),
    [
        (ScriptedJevResponse(429, "Rate limit exceeded"), 0.5, 0.5),
        (ScriptedJevResponse(429, "Rate limit exceeded", retry_after="7"), 0.25, 7.25),
        (ScriptedJevResponse(429, "Rate limit exceeded", retry_after="3600"), 0.25, 60.25),
        (ScriptedJevResponse(429, "Rate limit exceeded", retry_after="Wed, 21 Oct 2015 07:28:00 GMT"), 0.25, 0.25),
        (ScriptedJevResponse(429, "Rate limit exceeded", retry_after=_http_date(SCRIPTED_EPOCH_SECONDS + 30)), 0.25, 30.25),
        (ScriptedJevResponse(429, "Rate limit exceeded", retry_after_milliseconds="30000"), 0.25, 30.25),
        (ScriptedJevResponse(429, "Rate limit exceeded", retry_after="7", retry_after_milliseconds="1500"), 0.25, 2.25),
        (ScriptedJevResponse(429, "Rate limit exceeded", retry_after_milliseconds="3600000"), 0.25, 60.25),
        (ScriptedJevResponse(500, "The server had an error processing your request. Please retry your request."), 0.5, 0.5),
        (ScriptedJevResponse(500, "Upstream overloaded; please RETRY", retry_after="3"), 0.5, 3.5),
    ],
)
async def test_Compact_WhenProviderAsksForOneRetry_ThenSucceedsAfterOneJitteredWait(
    tmp_path: Path, scripted_jev_response: ScriptedJevResponse, retry_jitter_fraction: float, expected_retry_delay: float
) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_probability_by_tool_id: Mapping[str, float] = {"tool_3": 0.8, "tool_5": 0.1, "tool_7": 0.9}
    expected_facade_trial: ExpectedFacadeTrial = ExpectedFacadeTrial(
        ("message_1", "message_2", "tool_3", "tool_7", "message_9"),
        expected_probability_by_tool_id,
        model,
        frozenset(expected_probability_by_tool_id),
        2,
        1,
        (expected_retry_delay,),
        (1,),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        _valid_answer_by_question_id(), model, scripted_jev_responses=(scripted_jev_response,), retry_jitter_fraction=retry_jitter_fraction
    )
    source: Path = _write_rollout(tmp_path)

    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        compaction_result: CompactionResult = await domain_facade.compact(prepared_compaction)

    assert_facade_trial_matches(compaction_result, test_mediator_jev, expected_facade_trial)


@pytest.mark.parametrize(
    ("retry_after", "retry_jitter_fraction", "expected_retry_delays", "expected_last_retry_after"),
    [
        ("2", 0.5, (2.5, 2.5, 2.5, 2.5), "2s"),
        (None, 1.0, (1.0, 2.0, 4.0, 5.0), "none"),
        (None, 0.5, (0.5, 1.0, 2.0, 2.5), "none"),
    ],
)
async def test_Compact_WhenEveryAttemptIsRateLimited_ThenRaisesRateLimitRetriesExhaustedAfterFiveAttempts(
    tmp_path: Path, retry_after: str | None, retry_jitter_fraction: float, expected_retry_delays: tuple[float, ...], expected_last_retry_after: str
) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_attempt_count: int = 5
    expected_jev_status_failure: ExpectedJevStatusFailure = ExpectedJevStatusFailure(
        ("HTTP 429", "Jev via OpenRouter", f"after {expected_attempt_count} attempts", f"Retry-After={expected_last_retry_after}"),
        {"attempt_count": str(expected_attempt_count), "http_status": "429", "last_retry_after_seconds": expected_last_retry_after},
        expected_attempt_count,
        expected_retry_delays,
        (1, 2, 3, 4),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        {},
        model,
        scripted_jev_responses=tuple(ScriptedJevResponse(429, "Rate limit exceeded", retry_after) for _ in range(expected_attempt_count)),
        retry_jitter_fraction=retry_jitter_fraction,
    )
    source: Path = _write_rollout(tmp_path)

    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevRateLimitRetriesExhaustedException) as exception_info:
            await domain_facade.compact(prepared_compaction)

    assert_jev_status_failure_matches(exception_info.value, test_mediator_jev, expected_jev_status_failure)


async def test_Compact_WhenEveryAttemptIsA500AskingForRetry_ThenRaisesServerRetryRequestedRetriesExhausted(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    provider_message: str = f"Internal error {secrets.token_hex(4)}. Please retry your request."
    expected_attempt_count: int = 5
    expected_jev_status_failure: ExpectedJevStatusFailure = ExpectedJevStatusFailure(
        ("HTTP 500 responses that asked to be retried", "Jev via OpenRouter", f"after {expected_attempt_count} attempts", provider_message),
        {"attempt_count": str(expected_attempt_count), "http_status": "500"},
        expected_attempt_count,
        (0.5, 1.0, 2.0, 2.5),
        (1, 2, 3, 4),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        {}, model, scripted_jev_responses=tuple(ScriptedJevResponse(500, provider_message) for _ in range(expected_attempt_count))
    )
    source: Path = _write_rollout(tmp_path)

    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevServerRetryRequestedRetriesExhaustedException) as exception_info:
            await domain_facade.compact(prepared_compaction)

    assert_jev_status_failure_matches(exception_info.value, test_mediator_jev, expected_jev_status_failure)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_Compact_WhenProviderRejectsTheRequest_ThenRaisesRejectionWithoutRetrying(tmp_path: Path, status_code: int) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model)
    provider_detail: str = f"rejected {secrets.token_hex(4)}"
    test_mediator_jev.remaining_scripted_jev_responses.append(
        ScriptedJevResponse(status_code, f"{provider_detail} for key {test_mediator_jev.api_key}")
    )
    expected_jev_status_failure: ExpectedJevStatusFailure = ExpectedJevStatusFailure(
        ("Jev via OpenRouter rejected", f"HTTP {status_code}", provider_detail, "[REDACTED]"),
        {"http_status": str(status_code)},
        1,
        (),
        (),
        (test_mediator_jev.api_key,),
    )
    source: Path = _write_rollout(tmp_path)

    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevRequestRejectedException) as exception_info:
            await domain_facade.compact(prepared_compaction)

    assert_jev_status_failure_matches(exception_info.value, test_mediator_jev, expected_jev_status_failure)


@pytest.mark.parametrize(
    "scripted_jev_response",
    [
        ScriptedJevResponse(500, "Internal Server Error"),
        ScriptedJevResponse(502, "Bad gateway"),
        ScriptedJevResponse(503, "Service unavailable; please retry", retry_after="1"),
    ],
)
async def test_Compact_WhenProviderFailsServerSideWithoutAskingFor500Retry_ThenRaisesRequestFailedWithoutRetrying(
    tmp_path: Path, scripted_jev_response: ScriptedJevResponse
) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_jev_status_failure: ExpectedJevStatusFailure = ExpectedJevStatusFailure(
        ("Jev via OpenRouter context-retention request failed", scripted_jev_response.provider_message),
        {"http_status": str(scripted_jev_response.status_code)},
        1,
        (),
        (),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model, scripted_jev_responses=(scripted_jev_response,))
    source: Path = _write_rollout(tmp_path)

    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevRequestFailedException) as exception_info:
            await domain_facade.compact(prepared_compaction)

    assert_jev_status_failure_matches(exception_info.value, test_mediator_jev, expected_jev_status_failure)


async def test_Compact_WhenTransportCannotConnect_ThenRaisesRequestFailedWithoutRetrying(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_jev_status_failure: ExpectedJevStatusFailure = ExpectedJevStatusFailure(
        ("Jev via OpenRouter context-retention request failed",), {}, 1, (), ()
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model, transport_failure=httpx2.ConnectError("Scripted connect failure"))
    source: Path = _write_rollout(tmp_path)

    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevRequestFailedException) as exception_info:
            await domain_facade.compact(prepared_compaction)

    assert_jev_status_failure_matches(exception_info.value, test_mediator_jev, expected_jev_status_failure)


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf")])
async def test_Compact_WhenThresholdIsInvalid_ThenRejectsBeforeTransportRequest(tmp_path: Path, threshold: float) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_requests: list[dict[str, object]] = []
    expected_message_phrase: str = "Threshold must be between zero and one"
    expected_rejected_threshold_text: str = repr(threshold)
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model)
    source: Path = _write_rollout(tmp_path)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(CompactionThresholdOutOfRangeException, match=expected_message_phrase) as exception_info:
            await domain_facade.compact(prepared_compaction, threshold)
    assert (repr(exception_info.value.threshold), test_mediator_jev.compact_requests) == (expected_rejected_threshold_text, expected_requests)


@pytest.mark.parametrize(
    ("out_of_range_answer", "expected_detail_value_by_name"),
    [
        ({"type": "noul", "noul": 1.2}, {"rejected_probabilities": "{'tool_3': 1.2}"}),
        ({"type": "noul", "noul": float("nan")}, {"rejected_probabilities": "{'tool_3': nan}"}),
    ],
)
async def test_Compact_WhenProviderProbabilityIsOutsideZeroToOne_ThenGatewayRaisesProbabilityOutOfRange(
    tmp_path: Path, out_of_range_answer: dict[str, object], expected_detail_value_by_name: Mapping[str, str]
) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure(
        "probability outside", "validate_response", model, expected_detail_value_by_name
    )
    expected_request_count: int = 1
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        {"tool_3": out_of_range_answer, "tool_5": {"type": "noul", "noul": 0.1}, "tool_7": {"type": "noul", "noul": 0.9}}, model
    )
    source: Path = _write_rollout(tmp_path)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevProbabilityOutOfRangeException) as exception_info:
            await domain_facade.compact(prepared_compaction)
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)
    assert len(test_mediator_jev.compact_requests) == expected_request_count


async def test_Compact_WhenProviderAnswerIsNotParsable_ThenGatewayRaisesResponseSchemaInvalid(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure("invalid structured response", "validate_response", model, {})
    expected_request_count: int = 1
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        {"tool_3": {"type": "noul", "noul": "bad"}, "tool_5": {"type": "noul", "noul": 0.1}, "tool_7": {"type": "noul", "noul": 0.9}}, model
    )
    source: Path = _write_rollout(tmp_path)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevResponseSchemaInvalidException) as exception_info:
            await domain_facade.compact(prepared_compaction)
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)
    assert len(test_mediator_jev.compact_requests) == expected_request_count


async def test_Compact_WhenProviderAnswerIsNotANoul_ThenGatewayRaisesAnswerSetMismatch(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure(
        "missing or unexpected",
        "validate_response",
        model,
        {
            "expected_question_ids": "['tool_3', 'tool_5', 'tool_7']",
            "answer_ids": "['tool_3', 'tool_5', 'tool_7']",
            "noul_ids": "['tool_5', 'tool_7']",
        },
    )
    expected_request_count: int = 1
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        {
            "tool_3": {"type": "choice", "choice": "keep", "confidence": 1, "probabilities": {"keep": 1}},
            "tool_5": {"type": "noul", "noul": 0.1},
            "tool_7": {"type": "noul", "noul": 0.9},
        },
        model,
    )
    source: Path = _write_rollout(tmp_path)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevAnswerSetMismatchException) as exception_info:
            await domain_facade.compact(prepared_compaction)
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)
    assert len(test_mediator_jev.compact_requests) == expected_request_count


async def test_Compact_WhenPreparedStateIsChanged_ThenRejectsBeforeTransportRequestNamingTheTrial(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    changed_goal: str = f"unrelated-{secrets.token_hex(8)}"
    expected_message_phrase: str = "Prepared state"
    expected_requests: list[dict[str, object]] = []
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model)
    source: Path = _write_rollout(tmp_path)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        trial_case: TrialCase = domain_facade.load_context(source, 1, 10)
        expected_evidence_mode: EvidenceMode = "excerpt"
        expected_variant: Variant = "current"
        expected_mismatch_context: tuple[Path, str, str, list[dict[str, object]]] = (
            trial_case.source,
            expected_evidence_mode,
            expected_variant,
            expected_requests,
        )
        prepared_compaction: PreparedCompaction = domain_facade.prepare(trial_case, expected_evidence_mode, expected_variant)
        changed_prepared_compaction: PreparedCompaction = replace(
            prepared_compaction, jev_state=replace(prepared_compaction.jev_state, current_goal=changed_goal)
        )
        with pytest.raises(PreparedStateMismatchException, match=expected_message_phrase) as exception_info:
            await domain_facade.compact(changed_prepared_compaction)
    actual_mismatch_context: tuple[Path, str, str, list[dict[str, object]]] = (
        exception_info.value.source,
        exception_info.value.mode,
        exception_info.value.variant,
        test_mediator_jev.compact_requests,
    )
    assert actual_mismatch_context == expected_mismatch_context


async def test_Compact_WhenPreparedQuestionsAreChanged_ThenRejectsBeforeTransportRequest(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_message_phrase: str = "Prepared questions"
    expected_requests: list[dict[str, object]] = []
    expected_tool_id_pair: tuple[tuple[str, ...], tuple[str, ...]] = (("tool_3", "tool_5", "tool_7"), ())
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model)
    source: Path = _write_rollout(tmp_path)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        changed_prepared_compaction: PreparedCompaction = replace(prepared_compaction, questions={})
        with pytest.raises(PreparedQuestionsMismatchException, match=expected_message_phrase) as exception_info:
            await domain_facade.compact(changed_prepared_compaction)
    actual_tool_id_pair: tuple[tuple[str, ...], tuple[str, ...]] = (exception_info.value.expected_tool_ids, exception_info.value.received_tool_ids)
    assert (actual_tool_id_pair, test_mediator_jev.compact_requests) == (expected_tool_id_pair, expected_requests)


async def test_Compact_WhenPreparedQuestionsCarryTheOtherVariantsInstructions_ThenRejectsNamingTheMismatchedQuestions(tmp_path: Path) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_message_phrase: str = "mismatched="
    expected_requests: list[dict[str, object]] = []
    expected_tool_ids: tuple[str, ...] = ("tool_3", "tool_5", "tool_7")
    expected_question_mismatch: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] = (expected_tool_ids, expected_tool_ids, expected_tool_ids)
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model)
    source: Path = _write_rollout(tmp_path)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        trial_case: TrialCase = domain_facade.load_context(source, 1, 10)
        careful_prepared_compaction: PreparedCompaction = domain_facade.prepare(trial_case, variant="careful")
        current_prepared_compaction: PreparedCompaction = domain_facade.prepare(trial_case, variant="current")
        changed_prepared_compaction: PreparedCompaction = replace(careful_prepared_compaction, questions=current_prepared_compaction.questions)
        with pytest.raises(PreparedQuestionsMismatchException, match=expected_message_phrase) as exception_info:
            await domain_facade.compact(changed_prepared_compaction)
    actual_question_mismatch: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] = (
        exception_info.value.expected_tool_ids,
        exception_info.value.received_tool_ids,
        exception_info.value.mismatched_question_ids,
    )
    assert (actual_question_mismatch, test_mediator_jev.compact_requests) == (expected_question_mismatch, expected_requests)


async def test_Compact_WhenFacadeWasBuiltForPreparation_ThenRaisesTechnicalJevDisabledError(tmp_path: Path) -> None:
    expected_message_phrase: str = "for_live_evaluation"
    expected_disabled_context: tuple[bool, str] = (True, "compact")
    source: Path = _write_rollout(tmp_path)
    async with DomainFacade() as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(source, 1, 10))
        with pytest.raises(JevDisabledException, match=expected_message_phrase) as exception_info:
            await domain_facade.compact(prepared_compaction)
    actual_disabled_context: tuple[bool, str] = (
        isinstance(exception_info.value, ContextCompactionTechnicalBaseException),
        exception_info.value.operation,
    )
    assert actual_disabled_context == expected_disabled_context, "a facade composed without Jev is ours to fix"


async def test_Close_WhenTransportCleanupFails_ThenRetryClosesTheRemainingTransport() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure("cleanup failed", "aclose", model, {})
    expected_close_attempts: int = 2
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model, close_failures=1)
    domain_facade: DomainFacade = _create_domain_facade(test_mediator_jev)
    with pytest.raises(JevClientCleanupException) as exception_info:
        await domain_facade.close()
    await domain_facade.close()
    await domain_facade.close()
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)
    assert test_mediator_jev.cleanup_attempt_count == expected_close_attempts


async def test_Close_WhenRetriedTransportCleanupAlsoFails_ThenRaisesTransportCleanupError() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure("transport cleanup failed", "aclose", model, {})
    expected_close_attempts: int = 2
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model, close_failures=2)
    domain_facade: DomainFacade = _create_domain_facade(test_mediator_jev)
    with pytest.raises(JevClientCleanupException):
        await domain_facade.close()
    with pytest.raises(JevTransportCleanupException) as exception_info:
        await domain_facade.close()
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)
    assert test_mediator_jev.cleanup_attempt_count == expected_close_attempts


def test_Prepare_WhenStateIsOversized_ThenValidatorRejectsItBeforeLiveWork(tmp_path: Path) -> None:
    expected_message_phrase: str = "trial limit"
    expected_mode_limit_and_oversize: tuple[str, int, bool] = ("full", 90_000, True)
    domain_facade: DomainFacade = DomainFacade()
    trial_case: TrialCase = domain_facade.load_context(_write_rollout(tmp_path), 1, 10)
    oversized_trial_case: TrialCase = replace(trial_case, current_goal="x" * 90_001)
    with pytest.raises(JevStateTooLargeException, match=expected_message_phrase) as exception_info:
        domain_facade.prepare(oversized_trial_case)
    actual_mode_limit_and_oversize: tuple[str, int, bool] = (
        exception_info.value.mode,
        exception_info.value.maximum_state_characters,
        exception_info.value.state_characters > exception_info.value.maximum_state_characters,
    )
    assert actual_mode_limit_and_oversize == expected_mode_limit_and_oversize


def _service_locator_mock(model: str, jev_gateway_mock: MagicMock) -> ServiceLocatorProtocol:
    service_locator_mock: MagicMock = MagicMock(spec=ServiceLocatorProtocol)
    service_locator_mock.get_configuration_provider.return_value = ConfigurationProvider(
        Path(".env"), {"OPEN_ROUTER_KEY": secrets.token_hex(16), "OPEN_ROUTER_BASE_URL": "https://jev.invalid/api", "JEV_MODEL": model}
    )
    service_locator_mock.create_jev_gateway.return_value = jev_gateway_mock
    return cast(ServiceLocatorProtocol, service_locator_mock)


async def test_ManagerClose_WhenCalledAfterContextExit_ThenDelegatesOwnedReleaseOnce() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_release_calls: int = 1
    jev_gateway_mock: MagicMock = MagicMock(spec=JevGatewayProtocol)
    jev_gateway_mock.close = AsyncMock()
    manager_context_compaction: ManagerContextCompaction = ManagerContextCompaction.for_live_evaluation(
        _service_locator_mock(model, jev_gateway_mock)
    )

    async with manager_context_compaction:
        pass
    await manager_context_compaction.close()

    assert jev_gateway_mock.close.await_count == expected_release_calls


async def test_ManagerClose_WhenOwnedReleaseFails_ThenRetriesBeforeMarkingItReleased() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_release_calls: int = 2
    expected_jev_client_cleanup_exception: JevClientCleanupException = JevClientCleanupException(
        "Scripted cleanup failure", operation="aclose", model=model
    )
    jev_gateway_mock: MagicMock = MagicMock(spec=JevGatewayProtocol)
    jev_gateway_mock.close = AsyncMock(side_effect=[expected_jev_client_cleanup_exception, None])
    manager_context_compaction: ManagerContextCompaction = ManagerContextCompaction.for_live_evaluation(
        _service_locator_mock(model, jev_gateway_mock)
    )

    with pytest.raises(JevClientCleanupException) as exception_info:
        await manager_context_compaction.close()
    await manager_context_compaction.close()
    await manager_context_compaction.close()

    assert exception_info.value is expected_jev_client_cleanup_exception
    assert jev_gateway_mock.close.await_count == expected_release_calls
