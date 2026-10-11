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
    LlmRequestTimedOutException,
    LlmResponseEmptyException,
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
        message_phrases=("Copilot CLI", "request failed", "scripted Copilot session failure"),
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

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)
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
    expected_session_timeouts: list[float] = [900.0] * expected_request_count
    expected_deleted_session_ids: list[str] = [test_mediator_copilot.session_id] * expected_request_count
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action="RetryActionNeeded",
        reason="Coding assistant request failed",
        message_phrases=("Copilot CLI", "request failed", "scripted Copilot session failure"),
        contextual_fields=(
            ("Operation", "session_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "medium"),
            ("ProviderError", "Session error: scripted Copilot session failure"),
        ),
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

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)
    assert (
        test_mediator_copilot.captured_models,
        test_mediator_copilot.captured_available_tools,
        test_mediator_copilot.captured_timeouts,
        test_mediator_copilot.deleted_session_ids,
        test_mediator_copilot.stop_count,
        len(test_mediator_jev.captured_jev_requests),
    ) == (
        expected_models,
        expected_available_tools,
        expected_session_timeouts,
        expected_deleted_session_ids,
        expected_stop_count,
        expected_jev_request_count,
    )


async def test_analyze_expenses_WhenCopilotSessionTimesOut_ThenReportsATimedOutRequestAtBoundary() -> None:
    test_mediator_copilot: TestMediatorCopilot = TestMediatorCopilot(time_out_session=True)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_request_count: int = 2
    expected_session_timeouts: list[float] = [900.0] * expected_request_count
    expected_deleted_session_ids: list[str] = [test_mediator_copilot.session_id] * expected_request_count
    expected_stop_count: int = 1
    expected_jev_request_count: int = 0
    expected_timeout_message: str = "Timeout after 900.0s waiting for session.idle"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestTimedOutException,
        action="RetryActionNeeded",
        reason="Coding assistant request did not finish within its time limit",
        message_phrases=("Copilot CLI request timed out", expected_timeout_message),
        contextual_fields=(
            ("Operation", "session_request"),
            ("Model", expected_model),
            ("ReasoningEffort", "medium"),
            ("ProviderError", expected_timeout_message),
        ),
        cause_type="TimeoutError",
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
        with pytest.raises(LlmRequestTimedOutException) as raised_exception:
            await facade.analyze_expenses([expected_expense_line])

    actual_llm_request_timed_out_exception: LlmRequestTimedOutException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_timed_out_exception, expected_llm_gateway_failure)
    assert (
        test_mediator_copilot.captured_timeouts,
        test_mediator_copilot.deleted_session_ids,
        test_mediator_copilot.stop_count,
        len(test_mediator_jev.captured_jev_requests),
    ) == (expected_session_timeouts, expected_deleted_session_ids, expected_stop_count, expected_jev_request_count)


async def test_analyze_expenses_WhenCopilotSessionHasNoMessage_ThenReportsEmptyResponseAtBoundary() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)}"
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_stop_count: int = 1
    expected_jev_request_count: int = 0
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseEmptyException,
        action="DeveloperActionRequired",
        reason="Coding assistant returned no response text",
        message_phrases=("Copilot CLI", "no message"),
        contextual_fields=(("Operation", "response_validation"), ("EventDataType", "None")),
        forbidden_message_phrases=(expected_expense_line,),
    )
    test_mediator_copilot: TestMediatorCopilot = TestMediatorCopilot(return_no_message=True)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    # The prompt carrying the expense line was sent, so its session must still be removed from the user's history.
    expected_copilot_session_outcome: tuple[list[str], list[str], int, int] = (
        [expected_model],
        [test_mediator_copilot.session_id],
        expected_stop_count,
        expected_jev_request_count,
    )
    override_by_environment_variable_name: dict[str, str] = create_subscription_test_configuration(
        CodingAssistantSubscription.GITHUB_COPILOT, expected_model
    )

    async with create_domain_facade(
        test_mediator_jev,
        override_by_environment_variable_name,
        copilot_client=test_mediator_copilot.create_client(),
    ) as domain_facade:
        with pytest.raises(LlmResponseEmptyException) as raised_exception:
            await domain_facade.analyze_expenses([expected_expense_line])

    actual_llm_response_empty_exception: LlmResponseEmptyException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_empty_exception, expected_llm_gateway_failure)
    actual_copilot_session_outcome: tuple[list[str], list[str], int, int] = (
        test_mediator_copilot.captured_models,
        test_mediator_copilot.deleted_session_ids,
        test_mediator_copilot.stop_count,
        len(test_mediator_jev.captured_jev_requests),
    )
    assert actual_copilot_session_outcome == expected_copilot_session_outcome, (
        f"A Copilot session for model {expected_model!r} that answered with no message must still be deleted from the user's history; "
        f"expected (models, deleted session ids, client stops, Jev requests) {expected_copilot_session_outcome}; "
        f"actual {actual_copilot_session_outcome}"
    )


async def test_close_WhenCopilotClientFailsOnce_ThenFacadeCanRetryCleanup() -> None:
    test_mediator_copilot: TestMediatorCopilot = TestMediatorCopilot(stop_failures_before_success=1)
    test_mediator_jev: TestMediatorJev = TestMediatorJev()
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmClientCleanupFailedException,
        action="RetryActionNeeded",
        reason="Coding assistant client cleanup failed",
        message_phrases=("Copilot CLI", "client cleanup failed"),
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
    actual_llm_client_cleanup_failed_exception: LlmClientCleanupFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_client_cleanup_failed_exception, expected_llm_gateway_failure)
    await domain_facade.close()
    await domain_facade.close()

    assert (test_mediator_copilot.stop_count, test_mediator_jev.captured_close_attempt_count) == (
        expected_copilot_stop_attempt_count,
        expected_jev_close_attempt_count,
    )
