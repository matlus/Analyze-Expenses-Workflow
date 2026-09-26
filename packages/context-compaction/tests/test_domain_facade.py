from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, cast, final, override
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from context_compaction import (
    CompactionDecisionError,
    CompactionResult,
    DomainFacade,
    JevCleanupError,
    JevGatewayClosedError,
    JevGatewayError,
    JevResponseError,
    PreparedCompaction,
    TrialCase,
)
from context_compaction.managers.configuration_providers.configuration_provider import ConfigurationProvider
from context_compaction.managers.gateways.jev_gateway_protocol import JevGatewayProtocol
from context_compaction.managers.manager_context_compaction import ManagerContextCompaction
from context_compaction.managers.service_locators.service_locator_production import ServiceLocatorProduction
from context_compaction.managers.service_locators.service_locator_protocol import ServiceLocatorProtocol


class TestMediatorJev:
    __test__: ClassVar[bool] = False

    def __init__(self, answer_by_question_id: Mapping[str, object], model: str, *, close_failures: int = 0) -> None:
        self.compact_answer_by_question_id: Mapping[str, object] = answer_by_question_id
        self.compact_response_model: str = model
        self.compact_requests: list[dict[str, object]] = []
        self.cleanup_attempt_count: int = 0
        self.remaining_cleanup_failures: int = close_failures


@final
class JevTransportSpy(httpx2.AsyncBaseTransport):
    def __init__(self, test_mediator_jev: TestMediatorJev) -> None:
        self._test_mediator_jev: TestMediatorJev = test_mediator_jev

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self._test_mediator_jev.compact_requests.append(json.loads(request.content))
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

    @override
    async def aclose(self) -> None:
        self._test_mediator_jev.cleanup_attempt_count += 1
        if self._test_mediator_jev.remaining_cleanup_failures:
            self._test_mediator_jev.remaining_cleanup_failures -= 1
            raise OSError("Scripted transport cleanup failure")


class ServiceLocatorTesting(ServiceLocatorProduction):
    def __init__(self, jev_transport_spy: JevTransportSpy, model: str) -> None:
        super().__init__()
        self._jev_transport_spy: JevTransportSpy = jev_transport_spy
        self._model: str = model

    @override
    def make_jev_http_transport(self) -> httpx2.AsyncBaseTransport:
        return self._jev_transport_spy

    @override
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider(
            Path(".env"), {"OPEN_ROUTER_KEY": secrets.token_hex(16), "OPEN_ROUTER_BASE_URL": "https://jev.invalid/api", "JEV_MODEL": self._model}
        )


def _create_domain_facade(test_mediator_jev: TestMediatorJev) -> DomainFacade:
    jev_transport_spy: JevTransportSpy = JevTransportSpy(test_mediator_jev)
    service_locator_testing: ServiceLocatorTesting = ServiceLocatorTesting(jev_transport_spy, test_mediator_jev.compact_response_model)
    return DomainFacade(service_locator_testing, enable_jev=True)


@dataclass(frozen=True)
class ExpectedFacadeTrial:
    retained_ids: tuple[str, ...]
    probability_by_tool_id: Mapping[str, float]
    model: str
    question_ids: frozenset[str]
    request_count: int
    close_attempts: int


@dataclass(frozen=True)
class ExpectedGatewayFailure:
    message_phrase: str
    operation: str
    model: str
    detail_value_by_name: Mapping[str, str]


def example_context_path() -> Path:
    return Path(__file__).resolve().parents[3] / "docs/context-compaction/examples/rollout.jsonl"


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
    assert not mismatches, f"Facade trial {expected_facade_trial.model}: {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def assert_gateway_failure_matches(actual_jev_gateway_error: JevGatewayError, expected_gateway_failure: ExpectedGatewayFailure) -> None:
    mismatches: list[str] = []
    if expected_gateway_failure.message_phrase not in str(actual_jev_gateway_error):
        mismatches.append(f"message: expected phrase {expected_gateway_failure.message_phrase!r}; got {str(actual_jev_gateway_error)!r}")
    if actual_jev_gateway_error.operation != expected_gateway_failure.operation:
        mismatches.append(f"operation: expected {expected_gateway_failure.operation}; got {actual_jev_gateway_error.operation}")
    if actual_jev_gateway_error.model != expected_gateway_failure.model:
        mismatches.append(f"model: expected {expected_gateway_failure.model}; got {actual_jev_gateway_error.model}")
    if actual_jev_gateway_error.detail_value_by_name != expected_gateway_failure.detail_value_by_name:
        mismatches.append(f"details: expected {expected_gateway_failure.detail_value_by_name}; got {actual_jev_gateway_error.detail_value_by_name}")
    assert not mismatches, f"Gateway failure {expected_gateway_failure.model}: {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


async def test_Compact_WhenTransportReturnsValidDecisions_ThenRetainsUniqueEvidenceAndClosesOnce() -> None:
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
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        {tool_id: {"type": "noul", "noul": probability} for tool_id, probability in expected_probability_by_tool_id.items()}, model
    )
    domain_facade: DomainFacade = _create_domain_facade(test_mediator_jev)
    async with domain_facade:
        trial_case: TrialCase = domain_facade.load_context(example_context_path(), 1, 10)
        prepared_compaction: PreparedCompaction = domain_facade.prepare(trial_case)
        compaction_result: CompactionResult = await domain_facade.compact(prepared_compaction)
    await domain_facade.close()
    with pytest.raises(JevGatewayClosedError) as exception_info:
        await domain_facade.compact(prepared_compaction)
    assert_facade_trial_matches(compaction_result, test_mediator_jev, expected_facade_trial)
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf")])
async def test_Compact_WhenThresholdIsInvalid_ThenRejectsBeforeTransportRequest(threshold: float) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_requests: list[dict[str, object]] = []
    expected_message_phrase: str = "Threshold must be between zero and one"
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(example_context_path(), 1, 10))
        with pytest.raises(CompactionDecisionError, match=expected_message_phrase):
            await domain_facade.compact(prepared_compaction, threshold)
    assert test_mediator_jev.compact_requests == expected_requests


@pytest.mark.parametrize(
    ("answer", "expected_message_phrase", "expected_detail_value_by_name"),
    [
        ({"type": "noul", "noul": 1.2}, "probability outside", {"rejected_probabilities": "{'tool_3': 1.2}"}),
        ({"type": "noul", "noul": "bad"}, "invalid structured response", {}),
        ({"type": "noul", "noul": float("nan")}, "probability outside", {"rejected_probabilities": "{'tool_3': nan}"}),
        (
            {"type": "choice", "choice": "keep", "confidence": 1, "probabilities": {"keep": 1}},
            "missing or unexpected",
            {
                "expected_question_ids": "['tool_3', 'tool_5', 'tool_7']",
                "answer_ids": "['tool_3', 'tool_5', 'tool_7']",
                "noul_ids": "['tool_5', 'tool_7']",
            },
        ),
    ],
)
async def test_Compact_WhenProviderAnswerIsInvalid_ThenGatewayRaisesDomainResponseError(
    answer: dict[str, object], expected_message_phrase: str, expected_detail_value_by_name: Mapping[str, str]
) -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure(
        expected_message_phrase, "validate_response", model, expected_detail_value_by_name
    )
    expected_request_count: int = 1
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        {"tool_3": answer, "tool_5": {"type": "noul", "noul": 0.1}, "tool_7": {"type": "noul", "noul": 0.9}}, model
    )
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(example_context_path(), 1, 10))
        with pytest.raises(JevResponseError) as exception_info:
            await domain_facade.compact(prepared_compaction)
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)
    assert len(test_mediator_jev.compact_requests) == expected_request_count


async def test_Compact_WhenPreparedStateIsChanged_ThenRejectsBeforeTransportRequest() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    changed_goal: str = f"unrelated-{secrets.token_hex(8)}"
    expected_message_phrase: str = "Prepared state"
    expected_requests: list[dict[str, object]] = []
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model)
    async with _create_domain_facade(test_mediator_jev) as domain_facade:
        prepared_compaction: PreparedCompaction = domain_facade.prepare(domain_facade.load_context(example_context_path(), 1, 10))
        changed_prepared_compaction: PreparedCompaction = replace(
            prepared_compaction, jev_state=replace(prepared_compaction.jev_state, current_goal=changed_goal)
        )
        with pytest.raises(CompactionDecisionError, match=expected_message_phrase):
            await domain_facade.compact(changed_prepared_compaction)
    assert test_mediator_jev.compact_requests == expected_requests


async def test_Close_WhenTransportCleanupFails_ThenRetryClosesTheRemainingTransport() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_gateway_failure: ExpectedGatewayFailure = ExpectedGatewayFailure("cleanup failed", "aclose", model, {})
    expected_close_attempts: int = 2
    test_mediator_jev: TestMediatorJev = TestMediatorJev({}, model, close_failures=1)
    domain_facade: DomainFacade = _create_domain_facade(test_mediator_jev)
    with pytest.raises(JevCleanupError) as exception_info:
        await domain_facade.close()
    await domain_facade.close()
    await domain_facade.close()
    assert_gateway_failure_matches(exception_info.value, expected_gateway_failure)
    assert test_mediator_jev.cleanup_attempt_count == expected_close_attempts


def test_Prepare_WhenStateIsOversized_ThenValidatorRejectsItBeforeLiveWork() -> None:
    expected_message_phrase: str = "trial limit"
    domain_facade: DomainFacade = DomainFacade()
    trial_case: TrialCase = domain_facade.load_context(example_context_path(), 1, 10)
    oversized_trial_case: TrialCase = replace(trial_case, current_goal="x" * 90_001)
    with pytest.raises(CompactionDecisionError, match=expected_message_phrase):
        domain_facade.prepare(oversized_trial_case)


async def test_ManagerClose_WhenCalledAfterContextExit_ThenDelegatesOwnedReleaseOnce() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_release_calls: int = 1
    jev_gateway_mock: MagicMock = MagicMock(spec=JevGatewayProtocol)
    jev_gateway_mock.close = AsyncMock()
    service_locator_mock: MagicMock = MagicMock(spec=ServiceLocatorProtocol)
    service_locator_mock.get_configuration_provider.return_value = ConfigurationProvider(
        Path(".env"), {"OPEN_ROUTER_KEY": secrets.token_hex(16), "OPEN_ROUTER_BASE_URL": "https://jev.invalid/api", "JEV_MODEL": model}
    )
    service_locator_mock.create_jev_gateway.return_value = jev_gateway_mock
    manager_context_compaction: ManagerContextCompaction = ManagerContextCompaction(
        cast(ServiceLocatorProtocol, service_locator_mock), enable_jev=True
    )

    async with manager_context_compaction:
        pass
    await manager_context_compaction.close()

    assert jev_gateway_mock.close.await_count == expected_release_calls


async def test_ManagerClose_WhenOwnedReleaseFails_ThenRetriesBeforeMarkingItReleased() -> None:
    model: str = f"jev-{secrets.token_hex(8)}"
    expected_release_calls: int = 2
    expected_jev_cleanup_error: JevCleanupError = JevCleanupError("Scripted cleanup failure", operation="aclose", model=model)
    jev_gateway_mock: MagicMock = MagicMock(spec=JevGatewayProtocol)
    jev_gateway_mock.close = AsyncMock(side_effect=[expected_jev_cleanup_error, None])
    service_locator_mock: MagicMock = MagicMock(spec=ServiceLocatorProtocol)
    service_locator_mock.get_configuration_provider.return_value = ConfigurationProvider(
        Path(".env"), {"OPEN_ROUTER_KEY": secrets.token_hex(16), "OPEN_ROUTER_BASE_URL": "https://jev.invalid/api", "JEV_MODEL": model}
    )
    service_locator_mock.create_jev_gateway.return_value = jev_gateway_mock
    manager_context_compaction: ManagerContextCompaction = ManagerContextCompaction(
        cast(ServiceLocatorProtocol, service_locator_mock), enable_jev=True
    )

    with pytest.raises(JevCleanupError) as exception_info:
        await manager_context_compaction.close()
    await manager_context_compaction.close()
    await manager_context_compaction.close()

    assert exception_info.value is expected_jev_cleanup_error
    assert jev_gateway_mock.close.await_count == expected_release_calls
