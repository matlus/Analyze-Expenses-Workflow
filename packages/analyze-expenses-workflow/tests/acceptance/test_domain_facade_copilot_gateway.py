import secrets

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_llm_gateway_failure
from acceptance_support.mediator_copilot import TestMediatorCopilot
from acceptance_support.mediator_jev import TestMediatorJev
from acceptance_support.service_locator_testing import create_domain_facade, create_subscription_test_configuration

from analyze_expenses_workflow.domain_facades import (
    DomainFacade,
    LlmClientCleanupFailedException,
    LlmRequestFailedException,
    LlmResponseInvalidException,
)
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    CodingAssistantSubscription,
)


async def test_analyze_expenses_WhenCopilotSessionFails_ThenReportsGatewayFailureAtBoundary() -> None:
    test_mediator_copilot: TestMediatorCopilot = TestMediatorCopilot()
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_request_count: int = 2
    expected_models: list[str] = [expected_model] * expected_request_count
    expected_available_tools: list[list[object]] = [[]] * expected_request_count
    expected_stop_count: int = 1
    expected_jev_request_count: int = 0
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action="RetryActionNeeded",
        reason="Coding assistant request failed",
        message_phrases=("Copilot", "request failed"),
        contextual_fields=(("Operation", "session_request"), ("Model", expected_model), ("ReasoningEffort", "medium")),
        cause_type="RuntimeError",
        forbidden_message_phrases=(expected_expense_line,),
    )
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GITHUB_COPILOT, expected_model
    )

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        copilot_client=test_mediator_copilot.create_client(),
    ) as facade:
        with pytest.raises(LlmRequestFailedException) as raised_exception:
            await facade.analyze_expenses([expected_expense_line])

    actual_llm_gateway_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
    assert (
        test_mediator_copilot.captured_models,
        test_mediator_copilot.captured_available_tools,
        test_mediator_copilot.stop_count,
        len(test_mediator_jev.captured_jev_requests),
    ) == (expected_models, expected_available_tools, expected_stop_count, expected_jev_request_count)


async def test_analyze_expenses_WhenCopilotSessionEmitsError_ThenReportsGatewayFailureAtBoundary() -> None:
    test_mediator_copilot: TestMediatorCopilot = TestMediatorCopilot(emit_session_error=True)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_request_count: int = 2
    expected_models: list[str] = [expected_model] * expected_request_count
    expected_available_tools: list[list[object]] = [[]] * expected_request_count
    expected_stop_count: int = 1
    expected_jev_request_count: int = 0
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action="RetryActionNeeded",
        reason="Coding assistant request failed",
        message_phrases=("Copilot", "request failed"),
        contextual_fields=(("Operation", "session_request"), ("Model", expected_model), ("ReasoningEffort", "medium")),
        cause_type="Exception",
        forbidden_message_phrases=(expected_expense_line,),
    )
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GITHUB_COPILOT, expected_model
    )

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        copilot_client=test_mediator_copilot.create_client(),
    ) as facade:
        with pytest.raises(LlmRequestFailedException) as raised_exception:
            await facade.analyze_expenses([expected_expense_line])

    actual_llm_gateway_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
    assert (
        test_mediator_copilot.captured_models,
        test_mediator_copilot.captured_available_tools,
        test_mediator_copilot.stop_count,
        len(test_mediator_jev.captured_jev_requests),
    ) == (expected_models, expected_available_tools, expected_stop_count, expected_jev_request_count)


async def test_analyze_expenses_WhenCopilotSessionHasNoMessage_ThenReportsInvalidResponseAtBoundary() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseInvalidException,
        action="DeveloperActionRequired",
        reason="Coding assistant response violates its contract",
        message_phrases=("Copilot", "no message"),
        contextual_fields=(("Operation", "response_validation"), ("EventDataType", "None")),
        forbidden_message_phrases=(expected_expense_line,),
    )
    test_mediator_copilot: TestMediatorCopilot = TestMediatorCopilot(return_no_message=True)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GITHUB_COPILOT, expected_model
    )

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        copilot_client=test_mediator_copilot.create_client(),
    ) as domain_facade:
        with pytest.raises(LlmResponseInvalidException) as raised_exception:
            await domain_facade.analyze_expenses([expected_expense_line])

    assert_llm_gateway_failure(raised_exception.value, expected_failure)
    assert (test_mediator_copilot.captured_models, test_mediator_copilot.stop_count, len(test_mediator_jev.captured_jev_requests)) == (
        [expected_model],
        1,
        0,
    )


async def test_close_WhenCopilotClientFailsOnce_ThenFacadeCanRetryCleanup() -> None:
    test_mediator_copilot: TestMediatorCopilot = TestMediatorCopilot(stop_failures_before_success=1)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmClientCleanupFailedException,
        action="RetryActionNeeded",
        reason="Coding assistant client cleanup failed",
        message_phrases=("Copilot", "cleanup failed"),
        contextual_fields=(("Operation", "client_stop"),),
        cause_type="ExceptionGroup",
    )
    expected_copilot_stop_attempt_count: int = 2
    expected_jev_close_attempt_count: int = 1
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GITHUB_COPILOT, expected_model
    )
    domain_facade: DomainFacade = create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        copilot_client=test_mediator_copilot.create_client(),
    )

    with pytest.raises(LlmClientCleanupFailedException) as raised_exception:
        await domain_facade.close()
    actual_llm_gateway_exception: LlmClientCleanupFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_gateway_exception, expected_llm_gateway_failure)
    await domain_facade.close()
    await domain_facade.close()

    assert (test_mediator_copilot.stop_count, test_mediator_jev.captured_close_attempt_count) == (
        expected_copilot_stop_attempt_count,
        expected_jev_close_attempt_count,
    )
