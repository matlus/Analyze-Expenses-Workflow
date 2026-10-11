import asyncio
import secrets
from collections.abc import Mapping
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from acceptance_support.asserter_jev import ExpectedJevGatewayFailure, assert_jev_gateway_failure
from acceptance_support.mediator_jev import ScriptedJevResponse, TestMediatorJev
from pydantic import HttpUrl, SecretStr, ValidationError
from typesafe_sdk import (
    AsyncTypeSafeClient,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeInternalServerError,
    TypeSafeRateLimitError,
)

from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import (
    JevChoiceCategoriesMismatchException,
    JevChoiceResponseMalformedException,
    JevClientCleanupFailedException,
    JevGatewayClosedException,
    JevRateLimitRetriesExhaustedException,
    JevRequestFailedException,
    JevRequestRejectedException,
    JevServerRetryRequestedRetriesExhaustedException,
    JevTransportCleanupFailedException,
)
from analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev import SystemOneGatewayOpenRouterJev
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion


class _RetryWaitRecorder:
    def __init__(self) -> None:
        self.captured_retry_delays: list[float] = []

    async def wait_for_retry(self, delay_seconds: float) -> None:
        self.captured_retry_delays.append(delay_seconds)


def _create_jev_settings(open_router_key: str, jev_model: str) -> JevSettings:
    return JevSettings(
        open_router_key=SecretStr(open_router_key),
        open_router_base_url=HttpUrl("https://openrouter.ai/api/v1"),
        jev_model=jev_model,
    )


def _create_random_jev_settings() -> JevSettings:
    return _create_jev_settings(secrets.token_hex(16), f"typesafe/jev-{secrets.token_hex(4)}")


def _create_choice_question(expected_choice: str) -> ChoiceQuestion:
    return ChoiceQuestion(instructions=f"Classify expense {secrets.token_hex(8)}", criterion_by_choice_key={expected_choice: secrets.token_hex(8)})


def _create_choice_response(jev_model: str, expected_choice: str) -> ScriptedJevResponse:
    return ScriptedJevResponse(
        status_code=200,
        jev_response_body={
            "model": jev_model,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "answers": {"category": {"type": "choice", "choice": expected_choice, "confidence": 0.9, "probabilities": {expected_choice: 0.9}}},
        },
    )


def _create_error_response(status_code: int, provider_message: str, retry_after: str | None = None) -> ScriptedJevResponse:
    return ScriptedJevResponse(
        status_code=status_code,
        jev_response_body={"error": {"message": provider_message}},
        header_value_by_name={"Retry-After": retry_after} if retry_after is not None else {},
    )


def _create_gateway_over_mediator(
    jev_settings: JevSettings, test_mediator_jev: TestMediatorJev, retry_wait_recorder: _RetryWaitRecorder, jitter_fraction: float
) -> SystemOneGatewayOpenRouterJev:
    return SystemOneGatewayOpenRouterJev(
        jev_settings,
        async_base_transport=test_mediator_jev.create_http_transport(),
        wait_for_retry=retry_wait_recorder.wait_for_retry,
        retry_jitter_fraction=lambda: jitter_fraction,
    )


def _create_async_type_safe_client_mock(choice_answer_by_question_name: Mapping[str, MagicMock]) -> MagicMock:
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.system_one = AsyncMock(return_value=MagicMock(choices=choice_answer_by_question_name))
    async_type_safe_client_mock.aclose = AsyncMock()
    return async_type_safe_client_mock


@pytest.mark.asyncio
async def test_choose_WhenJevRequestsAreConcurrent_ThenSharesOneClient() -> None:
    expected_choice: str = secrets.token_hex(8)
    expected_request_count: int = 8
    expected_client_creation_count: int = 1
    expected_client_close_count: int = 1
    expense_description: str = f"{secrets.token_hex(8)} $10"
    choice_answer_mock: MagicMock = MagicMock()
    choice_answer_mock.model_dump.return_value = {
        "choice": expected_choice,
        "confidence": 0.9,
        "probabilities": {expected_choice: 0.9},
    }
    system_one_response_mock: MagicMock = MagicMock(choices={"category": choice_answer_mock})
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.system_one = AsyncMock(return_value=system_one_response_mock)
    async_type_safe_client_mock.aclose = AsyncMock()
    choice_question: ChoiceQuestion = _create_choice_question(expected_choice)

    with patch(
        "analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev.AsyncTypeSafeClient",
        return_value=async_type_safe_client_mock,
    ) as async_type_safe_client_factory_mock:
        system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(_create_random_jev_settings())
        actual_choice_decisions: list[ChoiceDecision] = list(
            await asyncio.gather(
                *(system_one_gateway_open_router_jev.choose(expense_description, choice_question) for _ in range(expected_request_count))
            )
        )
        await system_one_gateway_open_router_jev.close()

    assert (
        len(actual_choice_decisions),
        [choice_decision.choice for choice_decision in actual_choice_decisions],
        async_type_safe_client_factory_mock.call_count,
        async_type_safe_client_mock.system_one.await_count,
        async_type_safe_client_mock.aclose.await_count,
    ) == (
        expected_request_count,
        [expected_choice] * expected_request_count,
        expected_client_creation_count,
        expected_request_count,
        expected_client_close_count,
    )


@pytest.mark.asyncio
async def test_close_WhenJevGatewayIsUnused_ThenClosesItsClient() -> None:
    with patch(
        "analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev.AsyncTypeSafeClient"
    ) as async_type_safe_client_factory_mock:
        async_type_safe_client_mock: MagicMock = async_type_safe_client_factory_mock.return_value
        async_type_safe_client_mock.aclose = AsyncMock()
        system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(_create_random_jev_settings())
        await system_one_gateway_open_router_jev.close()

    async_type_safe_client_factory_mock.assert_called_once()
    assert async_type_safe_client_factory_mock.call_args.kwargs["transport"] is None
    async_type_safe_client_mock.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_WhenJevClientIsInjected_ThenClosesThatClient() -> None:
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.aclose = AsyncMock()
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _create_random_jev_settings(), cast("AsyncTypeSafeClient", async_type_safe_client_mock)
    )

    await system_one_gateway_open_router_jev.close()

    async_type_safe_client_mock.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_WhenPendingTransportFailsAfterClientCloses_ThenRetriesOnlyTransport() -> None:
    expected_client_close_count: int = 2
    expected_transport_close_count: int = 2
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.aclose = AsyncMock(side_effect=[OSError(secrets.token_hex(8)), None])
    async_base_transport_mock: MagicMock = MagicMock(spec=httpx2.AsyncBaseTransport)
    async_base_transport_mock.aclose = AsyncMock(side_effect=[OSError(secrets.token_hex(8)), None])

    with patch(
        "analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev.AsyncTypeSafeClient",
        return_value=async_type_safe_client_mock,
    ):
        system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
            _create_random_jev_settings(), async_base_transport=cast("httpx2.AsyncBaseTransport", async_base_transport_mock)
        )
        with pytest.raises(JevClientCleanupFailedException):
            await system_one_gateway_open_router_jev.close()
        with pytest.raises(JevTransportCleanupFailedException):
            await system_one_gateway_open_router_jev.close()
        await system_one_gateway_open_router_jev.close()
        await system_one_gateway_open_router_jev.close()

    assert (async_type_safe_client_mock.aclose.await_count, async_base_transport_mock.aclose.await_count) == (
        expected_client_close_count,
        expected_transport_close_count,
    )


@pytest.mark.asyncio
async def test_close_WhenTheJevClientCloseFailsWithASuppliedTransport_ThenTheNextCloseClosesThePendingTransportOnce() -> None:
    expected_close_counts: tuple[int, int] = (2, 1)
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.aclose = AsyncMock(side_effect=[OSError(secrets.token_hex(8)), None])
    async_base_transport_mock: MagicMock = MagicMock(spec=httpx2.AsyncBaseTransport)
    async_base_transport_mock.aclose = AsyncMock()

    with patch(
        "analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev.AsyncTypeSafeClient",
        return_value=async_type_safe_client_mock,
    ):
        system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
            _create_random_jev_settings(), async_base_transport=cast("httpx2.AsyncBaseTransport", async_base_transport_mock)
        )
        with pytest.raises(JevClientCleanupFailedException):
            await system_one_gateway_open_router_jev.close()
        await system_one_gateway_open_router_jev.close()
        await system_one_gateway_open_router_jev.close()

    actual_close_counts: tuple[int, int] = (async_type_safe_client_mock.aclose.await_count, async_base_transport_mock.aclose.await_count)
    assert actual_close_counts == expected_close_counts, (
        "After a failed Jev client close, the next close must close the client again and the supplied transport exactly once; "
        f"expected (client closes, transport closes) {expected_close_counts}; actual {actual_close_counts}"
    )


@pytest.mark.asyncio
async def test_choose_WhenJevOmitsTheCategoryAnswer_ThenReportsAMalformedChoiceResponse() -> None:
    expected_category: str = secrets.token_hex(8)
    test_open_router_key: str = secrets.token_hex(16)
    expected_jev_model: str = f"typesafe/jev-{secrets.token_hex(4)}"
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevChoiceResponseMalformedException,
        action="DeveloperActionRequired",
        reason="Jev choice response does not have the expected shape",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "does not have the expected shape", "no 'category' choice answer"),
        contextual_fields=(("Operation", "choice_response_validation"), ("Model", expected_jev_model)),
        forbidden_message_phrases=(test_open_router_key,),
    )
    async_type_safe_client_mock: MagicMock = _create_async_type_safe_client_mock({})
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _create_jev_settings(test_open_router_key, expected_jev_model), cast("AsyncTypeSafeClient", async_type_safe_client_mock)
    )

    with pytest.raises(JevChoiceResponseMalformedException) as raised_exception:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(expected_category))

    actual_jev_choice_response_malformed_exception: JevChoiceResponseMalformedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_response_malformed_exception, expected_jev_gateway_failure)


@pytest.mark.asyncio
async def test_choose_WhenJevAnswersSuccessWithAnInvalidBody_ThenReportsAMalformedChoiceResponse() -> None:
    expected_category: str = secrets.token_hex(8)
    test_open_router_key: str = secrets.token_hex(16)
    expected_jev_model: str = f"typesafe/jev-{secrets.token_hex(4)}"
    expected_request_count: int = 1
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevChoiceResponseMalformedException,
        action="DeveloperActionRequired",
        reason="Jev choice response does not have the expected shape",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "does not have the expected shape", "could not read the response body"),
        contextual_fields=(("Operation", "choice_response_validation"), ("Model", expected_jev_model)),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=TypeSafeAPIResponseValidationError,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[ScriptedJevResponse(status_code=200, jev_response_body={"answers": {}})]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(
        _create_jev_settings(test_open_router_key, expected_jev_model), test_mediator_jev, retry_wait_recorder, 0.5
    ) as system_one_gateway_open_router_jev:
        with pytest.raises(JevChoiceResponseMalformedException) as raised_exception:
            await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(expected_category))

    actual_jev_choice_response_malformed_exception: JevChoiceResponseMalformedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_response_malformed_exception, expected_jev_gateway_failure)
    assert len(test_mediator_jev.captured_jev_requests) == expected_request_count


@pytest.mark.asyncio
async def test_choose_WhenTheChoiceAnswerIsNotAValidDecision_ThenReportsAMalformedChoiceResponse() -> None:
    expected_category: str = secrets.token_hex(8)
    test_open_router_key: str = secrets.token_hex(16)
    expected_jev_model: str = f"typesafe/jev-{secrets.token_hex(4)}"
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevChoiceResponseMalformedException,
        action="DeveloperActionRequired",
        reason="Jev choice response does not have the expected shape",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "does not have the expected shape", "not a valid choice decision"),
        contextual_fields=(("Operation", "choice_response_validation"), ("Model", expected_jev_model)),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=ValidationError,
    )
    choice_answer_mock: MagicMock = MagicMock()
    choice_answer_mock.model_dump.return_value = {"choice": expected_category, "confidence": 1.5, "probabilities": {expected_category: 0.9}}
    async_type_safe_client_mock: MagicMock = _create_async_type_safe_client_mock({"category": choice_answer_mock})
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _create_jev_settings(test_open_router_key, expected_jev_model), cast("AsyncTypeSafeClient", async_type_safe_client_mock)
    )

    with pytest.raises(JevChoiceResponseMalformedException) as raised_exception:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(expected_category))

    actual_jev_choice_response_malformed_exception: JevChoiceResponseMalformedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_response_malformed_exception, expected_jev_gateway_failure)


@pytest.mark.asyncio
async def test_choose_WhenTheJevClientCallRaisesATypeError_ThenTheErrorIsNotReportedAsAMalformedResponse() -> None:
    expected_type_error_message: str = f"unexpected keyword argument {secrets.token_hex(4)}"
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.system_one = AsyncMock(side_effect=TypeError(expected_type_error_message))
    async_type_safe_client_mock.aclose = AsyncMock()
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _create_random_jev_settings(), cast("AsyncTypeSafeClient", async_type_safe_client_mock)
    )

    # A TypeError from the call is a defect in how the gateway calls the SDK, not a response with the wrong shape.
    with pytest.raises(TypeError, match=expected_type_error_message):
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))


@pytest.mark.asyncio
async def test_choose_WhenJevScoresCategoriesThatWereNotAsked_ThenReportsTheCategoryMismatch() -> None:
    expected_category: str = f"asked-{secrets.token_hex(4)}"
    unexpected_category: str = f"unasked-{secrets.token_hex(4)}"
    test_open_router_key: str = secrets.token_hex(16)
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevChoiceCategoriesMismatchException,
        action="DeveloperActionRequired",
        reason="Jev returned probabilities for categories other than those asked about",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "categories other than those asked about"),
        contextual_fields=(
            ("Operation", "choice_category_validation"),
            ("ExpectedCategories", expected_category),
            ("ActualCategories", unexpected_category),
        ),
        forbidden_message_phrases=(test_open_router_key,),
    )
    choice_answer_mock: MagicMock = MagicMock()
    choice_answer_mock.model_dump.return_value = {"choice": unexpected_category, "confidence": 0.9, "probabilities": {unexpected_category: 0.9}}
    async_type_safe_client_mock: MagicMock = _create_async_type_safe_client_mock({"category": choice_answer_mock})
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}"), cast("AsyncTypeSafeClient", async_type_safe_client_mock)
    )

    with pytest.raises(JevChoiceCategoriesMismatchException) as raised_exception:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(expected_category))

    actual_jev_choice_categories_mismatch_exception: JevChoiceCategoriesMismatchException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_categories_mismatch_exception, expected_jev_gateway_failure)


@pytest.mark.asyncio
async def test_choose_WhenJevRateLimitsOnceWithoutRetryAfter_ThenRetriesAfterOneJitteredBackoff() -> None:
    expected_choice: str = secrets.token_hex(8)
    jev_settings: JevSettings = _create_random_jev_settings()
    jitter_fraction: float = 0.5
    expected_result: tuple[str, list[float], int] = (expected_choice, [0.5], 2)
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[
            _create_error_response(429, secrets.token_hex(8)),
            _create_choice_response(jev_settings.jev_model, expected_choice),
        ]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(
        jev_settings, test_mediator_jev, retry_wait_recorder, jitter_fraction
    ) as system_one_gateway_open_router_jev:
        actual_choice_decision: ChoiceDecision = await system_one_gateway_open_router_jev.choose(
            f"{secrets.token_hex(8)} $10", _create_choice_question(expected_choice)
        )

    actual_result: tuple[str, list[float], int] = (
        actual_choice_decision.choice,
        retry_wait_recorder.captured_retry_delays,
        len(test_mediator_jev.captured_jev_requests),
    )
    assert actual_result == expected_result


@pytest.mark.parametrize("retry_after_seconds", [1, 30])
@pytest.mark.parametrize("jitter_fraction", [0.0, 0.5, 1.0])
@pytest.mark.asyncio
async def test_choose_WhenJevRateLimitsWithRetryAfter_ThenWaitsTheFullInstructionPlusJitter(retry_after_seconds: int, jitter_fraction: float) -> None:
    expected_choice: str = secrets.token_hex(8)
    jev_settings: JevSettings = _create_random_jev_settings()
    expected_retry_delays: list[float] = [retry_after_seconds + jitter_fraction * 0.5]
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[
            _create_error_response(429, secrets.token_hex(8), str(retry_after_seconds)),
            _create_choice_response(jev_settings.jev_model, expected_choice),
        ]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(
        jev_settings, test_mediator_jev, retry_wait_recorder, jitter_fraction
    ) as system_one_gateway_open_router_jev:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(expected_choice))

    assert retry_wait_recorder.captured_retry_delays == pytest.approx(expected_retry_delays)


@pytest.mark.asyncio
async def test_choose_WhenRetryAfterExceedsTheMaximum_ThenWaitsTheMaximumPlusJitter() -> None:
    expected_choice: str = secrets.token_hex(8)
    jev_settings: JevSettings = _create_random_jev_settings()
    jitter_fraction: float = 1.0
    expected_retry_delays: list[float] = [60.5]
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[
            _create_error_response(429, secrets.token_hex(8), "86400"),
            _create_choice_response(jev_settings.jev_model, expected_choice),
        ]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(
        jev_settings, test_mediator_jev, retry_wait_recorder, jitter_fraction
    ) as system_one_gateway_open_router_jev:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(expected_choice))

    assert retry_wait_recorder.captured_retry_delays == pytest.approx(expected_retry_delays)


@pytest.mark.asyncio
async def test_choose_WhenRetryAfterIsNotDelaySeconds_ThenFallsBackToJitteredBackoff() -> None:
    expected_choice: str = secrets.token_hex(8)
    jev_settings: JevSettings = _create_random_jev_settings()
    jitter_fraction: float = 0.25
    expected_retry_delays: list[float] = [0.25]
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[
            _create_error_response(429, secrets.token_hex(8), "Wed, 21 Oct 2026 07:28:00 GMT"),
            _create_choice_response(jev_settings.jev_model, expected_choice),
        ]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(
        jev_settings, test_mediator_jev, retry_wait_recorder, jitter_fraction
    ) as system_one_gateway_open_router_jev:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(expected_choice))

    assert retry_wait_recorder.captured_retry_delays == pytest.approx(expected_retry_delays)


@pytest.mark.parametrize("jitter_fraction", [0.0, 0.5, 1.0])
@pytest.mark.asyncio
async def test_choose_WhenJevRateLimitsEveryAttempt_ThenBacksOffWithCappedJitterAndReportsRetriesExhausted(jitter_fraction: float) -> None:
    test_open_router_key: str = secrets.token_hex(16)
    jev_settings: JevSettings = _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}")
    expected_request_count: int = 5
    expected_backoff_ceilings: tuple[float, ...] = (1.0, 2.0, 4.0, 4.0)
    expected_retry_delays: list[float] = [backoff_ceiling * jitter_fraction for backoff_ceiling in expected_backoff_ceilings]
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevRateLimitRetriesExhaustedException,
        action="RetryActionNeeded",
        reason="Jev kept rate limiting the request through every retry attempt",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "HTTP 429", "rate limiting", f"after {expected_request_count} attempts", "Retry-After: not provided"),
        contextual_fields=(
            ("Operation", "categorization_request"),
            ("Model", jev_settings.jev_model),
            ("RequestAttemptCount", expected_request_count),
            ("ProviderHttpStatusCode", 429),
        ),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=TypeSafeRateLimitError,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[_create_error_response(429, secrets.token_hex(8)) for _ in range(expected_request_count)]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(
        jev_settings, test_mediator_jev, retry_wait_recorder, jitter_fraction
    ) as system_one_gateway_open_router_jev:
        with pytest.raises(JevRateLimitRetriesExhaustedException) as raised_exception:
            await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_rate_limit_retries_exhausted_exception: JevRateLimitRetriesExhaustedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_rate_limit_retries_exhausted_exception, expected_jev_gateway_failure)
    assert (retry_wait_recorder.captured_retry_delays, len(test_mediator_jev.captured_jev_requests)) == (
        pytest.approx(expected_retry_delays),
        expected_request_count,
    )


@pytest.mark.asyncio
async def test_choose_WhenJevRateLimitsEveryAttemptWithRetryAfter_ThenReportsTheLastRetryAfter() -> None:
    test_open_router_key: str = secrets.token_hex(16)
    jev_settings: JevSettings = _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}")
    expected_request_count: int = 5
    expected_last_retry_after_seconds: int = 7
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevRateLimitRetriesExhaustedException,
        action="RetryActionNeeded",
        reason="Jev kept rate limiting the request through every retry attempt",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "HTTP 429", f"Retry-After: {expected_last_retry_after_seconds} seconds"),
        contextual_fields=(
            ("RequestAttemptCount", expected_request_count),
            ("ProviderHttpStatusCode", 429),
            ("LastRetryAfterSeconds", expected_last_retry_after_seconds),
        ),
        forbidden_message_phrases=(test_open_router_key,),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[
            *(_create_error_response(429, secrets.token_hex(8), "1") for _ in range(expected_request_count - 1)),
            _create_error_response(429, secrets.token_hex(8), str(expected_last_retry_after_seconds)),
        ]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(jev_settings, test_mediator_jev, retry_wait_recorder, 0.5) as system_one_gateway_open_router_jev:
        with pytest.raises(JevRateLimitRetriesExhaustedException) as raised_exception:
            await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_rate_limit_retries_exhausted_exception: JevRateLimitRetriesExhaustedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_rate_limit_retries_exhausted_exception, expected_jev_gateway_failure)


@pytest.mark.parametrize("rejected_status_code", [400, 401, 403, 404, 408, 422])
@pytest.mark.asyncio
async def test_choose_WhenJevRejectsTheRequest_ThenReportsTheRejectionWithoutRetrying(rejected_status_code: int) -> None:
    test_open_router_key: str = secrets.token_hex(16)
    jev_settings: JevSettings = _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}")
    expected_request_count: int = 1
    expected_retry_delays: list[float] = []
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevRequestRejectedException,
        action="DeveloperActionRequired",
        reason="Jev rejected the request and will reject it again unless the request changes",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", f"HTTP {rejected_status_code}", "not retried"),
        contextual_fields=(
            ("Operation", "categorization_request"),
            ("Model", jev_settings.jev_model),
            ("ProviderHttpStatusCode", rejected_status_code),
        ),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=TypeSafeAPIError,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[_create_error_response(rejected_status_code, "Please retry with a valid request")]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(jev_settings, test_mediator_jev, retry_wait_recorder, 0.5) as system_one_gateway_open_router_jev:
        with pytest.raises(JevRequestRejectedException) as raised_exception:
            await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_request_rejected_exception: JevRequestRejectedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_request_rejected_exception, expected_jev_gateway_failure)
    assert (retry_wait_recorder.captured_retry_delays, len(test_mediator_jev.captured_jev_requests)) == (
        expected_retry_delays,
        expected_request_count,
    )


@pytest.mark.asyncio
async def test_choose_WhenJevServerErrorAsksForRetryOnce_ThenRetriesAfterOneJitteredBackoff() -> None:
    expected_choice: str = secrets.token_hex(8)
    jev_settings: JevSettings = _create_random_jev_settings()
    jitter_fraction: float = 0.5
    expected_result: tuple[str, list[float], int] = (expected_choice, [0.5], 2)
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[
            _create_error_response(500, "The server had an error while processing your request. Please retry your request."),
            _create_choice_response(jev_settings.jev_model, expected_choice),
        ]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(
        jev_settings, test_mediator_jev, retry_wait_recorder, jitter_fraction
    ) as system_one_gateway_open_router_jev:
        actual_choice_decision: ChoiceDecision = await system_one_gateway_open_router_jev.choose(
            f"{secrets.token_hex(8)} $10", _create_choice_question(expected_choice)
        )

    actual_result: tuple[str, list[float], int] = (
        actual_choice_decision.choice,
        retry_wait_recorder.captured_retry_delays,
        len(test_mediator_jev.captured_jev_requests),
    )
    assert actual_result == expected_result


@pytest.mark.asyncio
async def test_choose_WhenJevServerErrorAsksForRetryOnEveryAttempt_ThenReportsServerRetriesExhausted() -> None:
    test_open_router_key: str = secrets.token_hex(16)
    jev_settings: JevSettings = _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}")
    expected_request_count: int = 5
    expected_provider_message: str = f"Upstream failure {secrets.token_hex(4)}. Please retry."
    expected_retry_delays: list[float] = [1.0, 2.0, 4.0, 4.0]
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevServerRetryRequestedRetriesExhaustedException,
        action="RetryActionNeeded",
        reason="Jev kept failing with a server error that asked for a retry through every retry attempt",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=(
            "Jev via OpenRouter",
            "HTTP 500",
            "asked to be retried",
            f"after {expected_request_count} attempts",
            expected_provider_message,
        ),
        contextual_fields=(
            ("Operation", "categorization_request"),
            ("Model", jev_settings.jev_model),
            ("RequestAttemptCount", expected_request_count),
            ("ProviderHttpStatusCode", 500),
        ),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=TypeSafeInternalServerError,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[_create_error_response(500, expected_provider_message) for _ in range(expected_request_count)]
    )
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(jev_settings, test_mediator_jev, retry_wait_recorder, 1.0) as system_one_gateway_open_router_jev:
        with pytest.raises(JevServerRetryRequestedRetriesExhaustedException) as raised_exception:
            await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_server_retry_requested_retries_exhausted_exception: JevServerRetryRequestedRetriesExhaustedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_server_retry_requested_retries_exhausted_exception, expected_jev_gateway_failure)
    assert (retry_wait_recorder.captured_retry_delays, len(test_mediator_jev.captured_jev_requests)) == (
        pytest.approx(expected_retry_delays),
        expected_request_count,
    )


@pytest.mark.parametrize(
    ("server_error_status_code", "provider_message"),
    [(500, "The server had an internal error"), (502, "Bad gateway, please retry"), (503, "Service unavailable, please retry")],
)
@pytest.mark.asyncio
async def test_choose_WhenJevServerErrorIsNotARetryableFiveHundred_ThenReportsRequestFailedWithoutRetrying(
    server_error_status_code: int, provider_message: str
) -> None:
    test_open_router_key: str = secrets.token_hex(16)
    jev_settings: JevSettings = _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}")
    expected_request_count: int = 1
    expected_retry_delays: list[float] = []
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevRequestFailedException,
        action="RetryActionNeeded",
        reason="Jev gateway request failed",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "request failed"),
        contextual_fields=(
            ("Operation", "categorization_request"),
            ("Model", jev_settings.jev_model),
            ("ProviderHttpStatusCode", server_error_status_code),
        ),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=TypeSafeInternalServerError,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(scripted_jev_responses=[_create_error_response(server_error_status_code, provider_message)])
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()

    async with _create_gateway_over_mediator(jev_settings, test_mediator_jev, retry_wait_recorder, 0.5) as system_one_gateway_open_router_jev:
        with pytest.raises(JevRequestFailedException) as raised_exception:
            await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_request_failed_exception: JevRequestFailedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_request_failed_exception, expected_jev_gateway_failure)
    assert (retry_wait_recorder.captured_retry_delays, len(test_mediator_jev.captured_jev_requests)) == (
        expected_retry_delays,
        expected_request_count,
    )


@pytest.mark.asyncio
async def test_choose_WhenJevCannotBeReached_ThenReportsRequestFailedWithoutRetrying() -> None:
    test_open_router_key: str = secrets.token_hex(16)
    expected_jev_model: str = f"typesafe/jev-{secrets.token_hex(4)}"
    expected_request_count: int = 1
    expected_retry_delays: list[float] = []
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevRequestFailedException,
        action="RetryActionNeeded",
        reason="Jev gateway request failed",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "request failed"),
        contextual_fields=(("Operation", "categorization_request"), ("Model", expected_jev_model)),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=TypeSafeAPIConnectionError,
    )
    async_type_safe_client_mock: MagicMock = MagicMock()
    async_type_safe_client_mock.system_one = AsyncMock(side_effect=TypeSafeAPIConnectionError(secrets.token_hex(8)))
    async_type_safe_client_mock.aclose = AsyncMock()
    retry_wait_recorder: _RetryWaitRecorder = _RetryWaitRecorder()
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _create_jev_settings(test_open_router_key, expected_jev_model),
        cast("AsyncTypeSafeClient", async_type_safe_client_mock),
        wait_for_retry=retry_wait_recorder.wait_for_retry,
    )

    with pytest.raises(JevRequestFailedException) as raised_exception:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_request_failed_exception: JevRequestFailedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_request_failed_exception, expected_jev_gateway_failure)
    assert (retry_wait_recorder.captured_retry_delays, async_type_safe_client_mock.system_one.await_count) == (
        expected_retry_delays,
        expected_request_count,
    )


def _create_expected_jev_gateway_closed_failure(test_open_router_key: str) -> ExpectedJevGatewayFailure:
    return ExpectedJevGatewayFailure(
        exception_type=JevGatewayClosedException,
        action="DeveloperActionRequired",
        reason="Jev gateway was used after closing",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "gateway is closed"),
        contextual_fields=(("Operation", "gateway_open_state_check"), ("GatewayState", "closed")),
        forbidden_message_phrases=(test_open_router_key,),
    )


@pytest.mark.asyncio
async def test_choose_WhenGatewayIsClosed_ThenReportsTheClosedGatewayWithoutARequest() -> None:
    test_open_router_key: str = secrets.token_hex(16)
    expected_request_count: int = 0
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _create_expected_jev_gateway_closed_failure(test_open_router_key)
    test_mediator_jev: TestMediatorJev = TestMediatorJev(scripted_jev_responses=[_create_error_response(500, secrets.token_hex(8))])
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = _create_gateway_over_mediator(
        _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}"), test_mediator_jev, _RetryWaitRecorder(), 0.5
    )
    await system_one_gateway_open_router_jev.close()

    with pytest.raises(JevGatewayClosedException) as raised_exception:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_gateway_closed_exception: JevGatewayClosedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_gateway_closed_exception, expected_jev_gateway_failure)
    assert len(test_mediator_jev.captured_jev_requests) == expected_request_count, (
        f"A closed Jev gateway must not send a request; expected {expected_request_count} requests; "
        f"actual {len(test_mediator_jev.captured_jev_requests)}"
    )


@pytest.mark.asyncio
async def test_choose_WhenGatewayClosesDuringARetryWait_ThenReportsTheClosedGatewayInsteadOfRetrying() -> None:
    test_open_router_key: str = secrets.token_hex(16)
    expected_request_count: int = 1
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _create_expected_jev_gateway_closed_failure(test_open_router_key)
    test_mediator_jev: TestMediatorJev = TestMediatorJev(
        scripted_jev_responses=[_create_error_response(429, secrets.token_hex(8)), _create_error_response(429, secrets.token_hex(8))]
    )

    async def close_gateway_while_waiting(delay_seconds: float) -> None:
        await system_one_gateway_open_router_jev.close()

    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(
        _create_jev_settings(test_open_router_key, f"typesafe/jev-{secrets.token_hex(4)}"),
        async_base_transport=test_mediator_jev.create_http_transport(),
        wait_for_retry=close_gateway_while_waiting,
        retry_jitter_fraction=lambda: 0.5,
    )

    with pytest.raises(JevGatewayClosedException) as raised_exception:
        await system_one_gateway_open_router_jev.choose(f"{secrets.token_hex(8)} $10", _create_choice_question(secrets.token_hex(8)))

    actual_jev_gateway_closed_exception: JevGatewayClosedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_gateway_closed_exception, expected_jev_gateway_failure)
    assert len(test_mediator_jev.captured_jev_requests) == expected_request_count, (
        f"A Jev gateway closed during a retry wait must not retry; expected {expected_request_count} request; "
        f"actual {len(test_mediator_jev.captured_jev_requests)}"
    )


def _create_jev_request_failed_with_cause(cause_text: str) -> JevRequestFailedException:
    jev_request_failed_exception: JevRequestFailedException = JevRequestFailedException(
        "Jev via OpenRouter categorization request failed", {"Operation": "categorization_request"}
    )
    jev_request_failed_exception.__cause__ = OSError(cause_text)
    return jev_request_failed_exception


@pytest.mark.parametrize("credential_location", ["context", "cause"])
def test_assert_jev_gateway_failure_WhenTheCredentialAppearsOutsideTheMessage_ThenReportsTheCredentialInTheDiagnostics(
    credential_location: str,
) -> None:
    test_open_router_key: str = secrets.token_hex(16)
    expected_failure_phrase: str = "Configured credential appeared in gateway diagnostics"
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevRequestFailedException,
        action="RetryActionNeeded",
        reason="Jev gateway request failed",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev via OpenRouter", "request failed"),
        forbidden_message_phrases=(test_open_router_key,),
    )
    leaking_jev_request_failed_exception: JevRequestFailedException = (
        JevRequestFailedException("Jev via OpenRouter categorization request failed", {"ProviderMessage": f"key {test_open_router_key}"})
        if credential_location == "context"
        else _create_jev_request_failed_with_cause(f"key {test_open_router_key}")
    )

    with pytest.raises(AssertionError) as raised_assertion:
        assert_jev_gateway_failure(leaking_jev_request_failed_exception, expected_jev_gateway_failure)

    actual_assertion_text: str = str(raised_assertion.value)
    assert (expected_failure_phrase in actual_assertion_text, test_open_router_key in actual_assertion_text) == (True, False), (
        f"A credential in the exception {credential_location} must fail the Jev gateway asserter without being repeated; "
        f"actual assertion text {actual_assertion_text.replace(test_open_router_key, '<redacted>')!r}"
    )
