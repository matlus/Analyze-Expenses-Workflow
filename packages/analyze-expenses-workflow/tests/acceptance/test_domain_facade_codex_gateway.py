import json
import secrets

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_codex_requests, assert_llm_gateway_failure
from acceptance_support.mediator_codex import TestMediatorCodex
from acceptance_support.mediator_jev import TestMediatorJev
from acceptance_support.service_locator_testing import create_domain_facade, create_subscription_test_configuration

from analyze_expenses_workflow.domain_facades import (
    DomainFacade,
    LlmClientCleanupFailedException,
    LlmRequestFailedException,
    LlmRequestRejectedException,
    LlmRequestTimedOutException,
    LlmResponseEmptyException,
)
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    CodingAssistantSubscription,
)


async def test_analyze_expenses_WhenCodexTurnFails_ThenReportsGatewayFailureAtBoundary() -> None:
    test_mediator_codex: TestMediatorCodex = TestMediatorCodex()
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_request_count: int = 2
    expected_close_count: int = 1
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action="RetryActionNeeded",
        reason="Coding assistant request failed",
        message_phrases=("Codex CLI", "request failed", TestMediatorCodex.SCRIPTED_TURN_FAILURE_MESSAGE),
        contextual_fields=(
            ("Operation", "turn_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "medium"),
            ("ProviderStatusCode", "unknown"),
            ("ProviderError", TestMediatorCodex.SCRIPTED_TURN_FAILURE_MESSAGE),
        ),
        cause_type="RuntimeError",
        forbidden_message_phrases=(expected_expense_line,),
    )
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GPT_CODEX, expected_model
    )

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        async_codex=test_mediator_codex.create_client(),
    ) as domain_facade:
        with pytest.raises(LlmRequestFailedException) as raised_exception:
            await domain_facade.analyze_expenses([expected_expense_line])

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)
    assert_codex_requests(test_mediator_codex, test_mediator_jev, expected_model, expected_expense_line, expected_request_count, expected_close_count)


async def test_analyze_expenses_WhenCodexTurnTimesOut_ThenReportsATimedOutRequestAtBoundary() -> None:
    test_mediator_codex: TestMediatorCodex = TestMediatorCodex(time_out_turn=True)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_request_count: int = 2
    expected_close_count: int = 1
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestTimedOutException,
        action="RetryActionNeeded",
        reason="Coding assistant request did not finish within its time limit",
        message_phrases=("Codex CLI request timed out", TestMediatorCodex.SCRIPTED_TURN_FAILURE_MESSAGE),
        contextual_fields=(
            ("Operation", "turn_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "medium"),
            ("ProviderStatusCode", "unknown"),
            ("ProviderError", TestMediatorCodex.SCRIPTED_TURN_FAILURE_MESSAGE),
        ),
        cause_type="TimeoutError",
        forbidden_message_phrases=(expected_expense_line,),
    )
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GPT_CODEX, expected_model
    )

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        async_codex=test_mediator_codex.create_client(),
    ) as domain_facade:
        with pytest.raises(LlmRequestTimedOutException) as raised_exception:
            await domain_facade.analyze_expenses([expected_expense_line])

    actual_llm_request_timed_out_exception: LlmRequestTimedOutException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_timed_out_exception, expected_llm_gateway_failure)
    assert_codex_requests(test_mediator_codex, test_mediator_jev, expected_model, expected_expense_line, expected_request_count, expected_close_count)


async def test_analyze_expenses_WhenCodexRejectsTheRequest_ThenReportsTheProviderReasonAsANonRetryableRejection() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_provider_reason: str = f"The {expected_model!r} model is not supported {secrets.token_hex(4)}"
    expected_reasoning_effort: str = "high"
    provider_error_document: str = json.dumps(
        {"type": "error", "status": 400, "error": {"type": "invalid_request_error", "message": expected_provider_reason}}
    )
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestRejectedException,
        action="DeveloperActionRequired",
        reason="Coding assistant provider rejected the request",
        message_phrases=("Codex CLI rejected the request", expected_provider_reason),
        contextual_fields=(
            ("Operation", "turn_request"),
            ("Model", expected_model),
            ("ReasoningEffort", expected_reasoning_effort),
            ("ProviderStatusCode", 400),
            ("ProviderError", expected_provider_reason),
        ),
        cause_type="RuntimeError",
        forbidden_message_phrases=(expected_expense_line,),
    )
    test_mediator_codex: TestMediatorCodex = TestMediatorCodex(turn_failure_message=provider_error_document)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    # The rejected request is the first one, the expense line extraction, and a rejection is not retried.
    override_by_environment_variable_name: dict[str, str] = {
        **create_subscription_test_configuration(CodingAssistantSubscription.GPT_CODEX, expected_model),
        "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT": expected_reasoning_effort,
    }

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        async_codex=test_mediator_codex.create_client(),
    ) as domain_facade:
        with pytest.raises(LlmRequestRejectedException) as raised_exception:
            await domain_facade.analyze_expenses([expected_expense_line])

    actual_llm_request_rejected_exception: LlmRequestRejectedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_rejected_exception, expected_llm_gateway_failure)


async def test_close_WhenCalledTwice_ThenOwnedGatewaysCloseOnce() -> None:
    test_mediator_codex: TestMediatorCodex = TestMediatorCodex()
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_close_count: int = 1
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GPT_CODEX, expected_model
    )
    domain_facade: DomainFacade = create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        async_codex=test_mediator_codex.create_client(),
    )

    await domain_facade.close()
    await domain_facade.close()

    assert (test_mediator_codex.close_count, test_mediator_jev.captured_close_attempt_count) == (
        expected_close_count,
        expected_close_count,
    )


async def test_analyze_expenses_WhenCodexTurnHasNoResponse_ThenReportsEmptyResponseAtBoundary() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_close_count: int = 1
    expected_jev_request_count: int = 0
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseEmptyException,
        action="DeveloperActionRequired",
        reason="Coding assistant returned no response text",
        message_phrases=("Codex CLI", "no completed response"),
        contextual_fields=(("Operation", "response_validation"), ("TurnStatus", "completed")),
        forbidden_message_phrases=(expected_expense_line,),
    )
    test_mediator_codex: TestMediatorCodex = TestMediatorCodex(return_incomplete_turn=True)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GPT_CODEX, expected_model
    )

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        async_codex=test_mediator_codex.create_client(),
    ) as domain_facade:
        with pytest.raises(LlmResponseEmptyException) as raised_exception:
            await domain_facade.analyze_expenses([expected_expense_line])

    actual_llm_response_empty_exception: LlmResponseEmptyException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_empty_exception, expected_llm_gateway_failure)
    assert (test_mediator_codex.captured_models, test_mediator_codex.close_count, len(test_mediator_jev.captured_jev_requests)) == (
        [expected_model],
        expected_close_count,
        expected_jev_request_count,
    )


async def test_close_WhenCodexClientFailsOnce_ThenFacadeCanRetryCleanup() -> None:
    test_mediator_codex: TestMediatorCodex = TestMediatorCodex(close_failures_before_success=1)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmClientCleanupFailedException,
        action="RetryActionNeeded",
        reason="Coding assistant client cleanup failed",
        message_phrases=("Codex CLI", "client cleanup failed"),
        contextual_fields=(("Operation", "client_close"),),
        cause_type="RuntimeError",
    )
    expected_codex_close_attempt_count: int = 2
    expected_jev_close_attempt_count: int = 1
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GPT_CODEX, expected_model
    )
    domain_facade: DomainFacade = create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        async_codex=test_mediator_codex.create_client(),
    )

    with pytest.raises(LlmClientCleanupFailedException) as raised_exception:
        await domain_facade.close()
    actual_llm_client_cleanup_failed_exception: LlmClientCleanupFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_client_cleanup_failed_exception, expected_llm_gateway_failure)
    await domain_facade.close()
    await domain_facade.close()

    assert (test_mediator_codex.close_count, test_mediator_jev.captured_close_attempt_count) == (
        expected_codex_close_attempt_count,
        expected_jev_close_attempt_count,
    )
