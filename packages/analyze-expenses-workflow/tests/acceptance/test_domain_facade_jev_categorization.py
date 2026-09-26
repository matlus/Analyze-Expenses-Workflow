import os
import secrets
from typing import cast

import pytest
from acceptance_support.asserter_jev import (
    ExpectedJevCategorization,
    ExpectedJevGatewayFailure,
    assert_jev_categorization,
    assert_jev_gateway_failure,
    assert_jev_requests,
)
from acceptance_support.mediator_jev import JevTransportCloseError, TestMediatorJev
from acceptance_support.service_locator_testing import create_domain_facade

from analyze_expenses_workflow import DomainFacade, ExpenseAnalysisResult, ExpenseCategory
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction, ExpenseLogEvent, Severity
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import (
    JevRequestFailedException,
    JevResourceCleanupFailedException,
)


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_TESTS") != "1", reason="Real Jev calls are opt-in")
async def test_analyze_expenses_WhenGroceryPurchaseIsClear_ThenCategorizesAndSendsJevRequest() -> None:
    expected_expense_line: str = "2026-08-14 WHOLE FOODS MARKET grocery purchase -$42.18"
    expected_jev_request_method: str = "POST"
    expected_jev_request_path: str = "/api/v1/systemone"
    expected_jev_categorization: ExpectedJevCategorization = ExpectedJevCategorization(
        source_line_number=1,
        source_text=expected_expense_line,
        category=ExpenseCategory.GROCERIES,
        model_category=ExpenseCategory.GROCERIES,
        policy_note=None,
        probability_categories=frozenset(ExpenseCategory),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev()

    async with create_domain_facade(test_mediator_jev) as facade:
        actual_expense_analysis_result: ExpenseAnalysisResult = await facade.analyze_expenses([expected_expense_line])

    assert_jev_categorization(actual_expense_analysis_result, expected_jev_categorization)
    assert_jev_requests(test_mediator_jev.captured_jev_requests, expected_jev_request_method, expected_jev_request_path, expected_expense_line)


async def test_analyze_expenses_WhenJevServiceRejectsRequest_ThenReportsGatewayFailure() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)} -$42.18"
    expected_jev_request_method: str = "POST"
    expected_jev_request_path: str = "/api/v1/systemone"
    expected_jev_model: str = "typesafe/jev"
    test_open_router_key: str = secrets.token_hex(16)
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED,
        reason="Jev gateway request failed",
        log_event=ExpenseLogEvent.JEV_GATEWAY,
        severity=Severity.ERROR,
        http_status_code=500,
        message_phrases=("Jev", "request failed"),
        contextual_fields=(("Operation", "categorization_request"), ("Model", expected_jev_model)),
        forbidden_message_phrases=(test_open_router_key,),
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(scripted_status_code=401)
    override_by_environment_variable_name: dict[str, str] = {
        "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
        "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
        "OPEN_ROUTER_KEY": test_open_router_key,
        "JEV_MODEL": expected_jev_model,
    }

    async with create_domain_facade(test_mediator_jev, override_by_environment_variable_name) as domain_facade:
        with pytest.RaisesGroup(JevRequestFailedException) as raised_exception_group:
            await domain_facade.analyze_expenses([expected_expense_line])

    actual_system_one_gateway_exception: JevRequestFailedException = cast(
        "JevRequestFailedException", raised_exception_group.value.exceptions[0]
    )
    assert_jev_gateway_failure(actual_system_one_gateway_exception, expected_jev_gateway_failure)
    assert_jev_requests(test_mediator_jev.captured_jev_requests, expected_jev_request_method, expected_jev_request_path, expected_expense_line)


async def test_close_WhenJevTransportCloseFailsOnce_ThenFacadeCanRetryCleanup() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)} -$42.18"
    expected_close_attempt_count: int = 2
    expected_jev_model: str = "typesafe/jev"
    test_open_router_key: str = secrets.token_hex(16)
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevResourceCleanupFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED,
        reason="Jev gateway resource cleanup failed",
        log_event=ExpenseLogEvent.JEV_GATEWAY,
        severity=Severity.ERROR,
        http_status_code=500,
        message_phrases=("Jev", "client cleanup failed"),
        contextual_fields=(("Operation", "client_close"), ("Model", expected_jev_model)),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=JevTransportCloseError,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(scripted_status_code=401, close_failures_before_success=1)
    override_by_environment_variable_name: dict[str, str] = {
        "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
        "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
        "OPEN_ROUTER_KEY": test_open_router_key,
        "JEV_MODEL": expected_jev_model,
    }
    domain_facade: DomainFacade = create_domain_facade(test_mediator_jev, override_by_environment_variable_name)

    with pytest.RaisesGroup(JevRequestFailedException):
        await domain_facade.analyze_expenses([expected_expense_line])

    with pytest.raises(JevResourceCleanupFailedException) as raised_exception:
        await domain_facade.close()
    actual_system_one_gateway_exception: JevResourceCleanupFailedException = raised_exception.value
    assert_jev_gateway_failure(actual_system_one_gateway_exception, expected_jev_gateway_failure)

    await domain_facade.close()
    await domain_facade.close()

    assert test_mediator_jev.captured_close_attempt_count == expected_close_attempt_count


async def test_close_WhenJevClientAndPendingTransportCloseFail_ThenFacadeRetriesEachResource() -> None:
    expected_expense_line: str = f"2026-08-14 {secrets.token_hex(8)} -$42.18"
    expected_jev_model: str = "typesafe/jev"
    test_open_router_key: str = secrets.token_hex(16)
    expected_transport_failure: ExpectedJevGatewayFailure = ExpectedJevGatewayFailure(
        exception_type=JevResourceCleanupFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED,
        reason="Jev gateway resource cleanup failed",
        log_event=ExpenseLogEvent.JEV_GATEWAY,
        severity=Severity.ERROR,
        http_status_code=500,
        message_phrases=("Jev", "transport cleanup failed"),
        contextual_fields=(("Operation", "transport_close"), ("Model", expected_jev_model)),
        forbidden_message_phrases=(test_open_router_key,),
        cause_exception_type=JevTransportCloseError,
    )
    test_mediator_jev: TestMediatorJev = TestMediatorJev(scripted_status_code=401, close_failures_before_success=2)
    override_by_environment_variable_name: dict[str, str] = {
        "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
        "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
        "OPEN_ROUTER_KEY": test_open_router_key,
        "JEV_MODEL": expected_jev_model,
    }
    domain_facade: DomainFacade = create_domain_facade(test_mediator_jev, override_by_environment_variable_name)

    with pytest.RaisesGroup(JevRequestFailedException):
        await domain_facade.analyze_expenses([expected_expense_line])
    with pytest.raises(JevResourceCleanupFailedException):
        await domain_facade.close()
    with pytest.raises(JevResourceCleanupFailedException) as raised_exception:
        await domain_facade.close()

    assert_jev_gateway_failure(raised_exception.value, expected_transport_failure)
    await domain_facade.close()
    await domain_facade.close()
    assert test_mediator_jev.captured_close_attempt_count == 3
