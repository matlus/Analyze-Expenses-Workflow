import secrets
from typing import cast

import pytest
from acceptance_support.mediator_jev import TestMediatorJev
from acceptance_support.service_locator_testing import create_domain_facade

from analyze_expenses_workflow import ExpenseInputException
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction, ExpenseLogEvent, Severity


async def test_analyze_expenses_WhenExpenseLinesAreOneString_ThenRejectsInputBeforeJevRequest() -> None:
    invalid_expense_lines: str = secrets.token_hex(8)
    expected_action: ExceptionAction = ExceptionAction.USER_ACTION_REQUIRED
    expected_log_event: ExpenseLogEvent = ExpenseLogEvent.INPUT_VALIDATION
    expected_message_phrase: str = "received str"
    expected_reason: str = "Expense input failed validation"
    expected_severity: Severity = Severity.ERROR
    expected_http_status_code: int = 400
    test_mediator_jev: TestMediatorJev = TestMediatorJev()

    async with create_domain_facade(test_mediator_jev) as facade:
        with pytest.raises(ExpenseInputException) as raised_expense_input_exception:
            await facade.analyze_expenses(cast("list[str]", invalid_expense_lines))

    actual_expense_input_exception: ExpenseInputException = raised_expense_input_exception.value
    mismatches: list[str] = []
    if actual_expense_input_exception.action != expected_action:
        mismatches.append(f"Expected action {expected_action}, got {actual_expense_input_exception.action}")
    if actual_expense_input_exception.log_event != expected_log_event:
        mismatches.append(f"Expected log event {expected_log_event}, got {actual_expense_input_exception.log_event}")
    if actual_expense_input_exception.reason != expected_reason:
        mismatches.append(f"Expected reason {expected_reason!r}, got {actual_expense_input_exception.reason!r}")
    if actual_expense_input_exception.severity != expected_severity:
        mismatches.append(f"Expected severity {expected_severity}, got {actual_expense_input_exception.severity}")
    if actual_expense_input_exception.http_status_code != expected_http_status_code:
        mismatches.append(
            f"Expected HTTP status {expected_http_status_code}, got {actual_expense_input_exception.http_status_code}"
        )
    if expected_message_phrase not in actual_expense_input_exception.message:
        mismatches.append(
            f"Expected message phrase {expected_message_phrase!r} in {actual_expense_input_exception.message!r}"
        )
    if test_mediator_jev.captured_jev_requests:
        mismatches.append(f"Expected no Jev requests, got {len(test_mediator_jev.captured_jev_requests)}")
    assert not mismatches, f"Invalid expense-line input has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)
