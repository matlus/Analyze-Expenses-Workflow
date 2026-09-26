import secrets

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_codex_requests, assert_llm_gateway_failure
from acceptance_support.mediator_codex import TestMediatorCodex
from acceptance_support.mediator_jev import TestMediatorJev
from acceptance_support.service_locator_testing import create_domain_facade, create_subscription_test_configuration

from analyze_expenses_workflow import DomainFacade
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    CodingAssistantSubscription,
)
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmRequestFailedException,
    LlmResponseInvalidException,
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
        action=ExceptionAction.RETRY_ACTION_NEEDED,
        reason="Coding assistant request failed",
        message_phrases=("Codex", "request failed"),
        contextual_fields=(("Operation", "turn_request"), ("Model", expected_model), ("ReasoningEffort", "medium")),
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

    actual_llm_gateway_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
    assert_codex_requests(
        test_mediator_codex, test_mediator_jev, expected_model, expected_expense_line, expected_request_count, expected_close_count
    )


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


async def test_analyze_expenses_WhenCodexTurnHasNoResponse_ThenReportsInvalidResponseAtBoundary() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseInvalidException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED,
        reason="Coding assistant response violates its contract",
        message_phrases=("Codex", "no completed response"),
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
        with pytest.raises(LlmResponseInvalidException) as raised_exception:
            await domain_facade.analyze_expenses([expected_expense_line])

    assert_llm_gateway_failure(raised_exception.value, expected_failure)
    assert (test_mediator_codex.captured_models, test_mediator_codex.close_count, len(test_mediator_jev.captured_jev_requests)) == (
        [expected_model],
        1,
        0,
    )


async def test_close_WhenCodexClientFailsOnce_ThenFacadeCanRetryCleanup() -> None:
    test_mediator_codex: TestMediatorCodex = TestMediatorCodex(close_failures_before_success=1)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmClientCleanupFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED,
        reason="Coding assistant client cleanup failed",
        message_phrases=("Codex", "cleanup failed"),
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
    actual_llm_gateway_exception: LlmClientCleanupFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
    await domain_facade.close()
    await domain_facade.close()

    assert (test_mediator_codex.close_count, test_mediator_jev.captured_close_attempt_count) == (
        expected_codex_close_attempt_count,
        expected_jev_close_attempt_count,
    )
