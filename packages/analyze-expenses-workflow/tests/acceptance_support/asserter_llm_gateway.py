from dataclasses import dataclass

from openai_codex import ApprovalMode, Sandbox

from acceptance_support.mediator_codex import TestMediatorCodex
from acceptance_support.mediator_jev import TestMediatorJev
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    ExceptionAction,
    ExceptionValue,
    ExpenseLogEvent,
    Severity,
)
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException


@dataclass(frozen=True, slots=True)
class ExpectedLlmGatewayFailure:
    exception_type: type[LlmGatewayException]
    action: ExceptionAction
    reason: str
    message_phrases: tuple[str, ...]
    contextual_fields: tuple[tuple[str, str], ...]
    cause_type: str | None = None
    forbidden_message_phrases: tuple[str, ...] = ()


def assert_codex_requests(
    test_mediator_codex: TestMediatorCodex,
    test_mediator_jev: TestMediatorJev,
    expected_model: str,
    expected_expense_line: str,
    expected_request_count: int,
    expected_close_count: int,
) -> None:
    mismatches: list[str] = []
    if len(test_mediator_codex.captured_prompts) != expected_request_count:
        mismatches.append(f"Expected {expected_request_count} Codex prompts, got {len(test_mediator_codex.captured_prompts)}")
    if test_mediator_codex.captured_models != [expected_model] * expected_request_count:
        mismatches.append(f"Expected Codex model {expected_model!r} for each request, got {test_mediator_codex.captured_models!r}")
    if test_mediator_codex.captured_sandboxes != [Sandbox.read_only] * expected_request_count:
        mismatches.append(f"Expected read-only Codex sandbox for each request, got {test_mediator_codex.captured_sandboxes!r}")
    if test_mediator_codex.captured_approval_modes != [ApprovalMode.deny_all] * expected_request_count:
        mismatches.append(f"Expected denied Codex approvals for each request, got {test_mediator_codex.captured_approval_modes!r}")
    if test_mediator_codex.close_count != expected_close_count:
        mismatches.append(f"Expected {expected_close_count} Codex closes, got {test_mediator_codex.close_count}")
    if test_mediator_jev.captured_jev_requests:
        mismatches.append(f"Expected no Jev requests, got {len(test_mediator_jev.captured_jev_requests)}")
    if not test_mediator_codex.captured_prompts or expected_expense_line not in test_mediator_codex.captured_prompts[0]:
        mismatches.append(f"Expected the Codex prompt to contain expense line {expected_expense_line!r}")
    assert not mismatches, f"Codex request for expense line {expected_expense_line!r} has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def assert_llm_gateway_failure(
    actual_llm_gateway_exception: LlmGatewayException,
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure,
) -> None:
    mismatches: list[str] = []
    if type(actual_llm_gateway_exception) is not expected_llm_gateway_failure.exception_type:
        mismatches.append(
            f"Expected exception {expected_llm_gateway_failure.exception_type.__name__}, got {type(actual_llm_gateway_exception).__name__}"
        )
    if actual_llm_gateway_exception.action != expected_llm_gateway_failure.action:
        mismatches.append(f"Expected action {expected_llm_gateway_failure.action}, got {actual_llm_gateway_exception.action}")
    if actual_llm_gateway_exception.reason != expected_llm_gateway_failure.reason:
        mismatches.append(f"Expected reason {expected_llm_gateway_failure.reason!r}, got {actual_llm_gateway_exception.reason!r}")
    if actual_llm_gateway_exception.log_event != ExpenseLogEvent.LLM_GATEWAY:
        mismatches.append(f"Expected LLM gateway log event, got {actual_llm_gateway_exception.log_event}")
    if actual_llm_gateway_exception.severity != Severity.ERROR:
        mismatches.append(f"Expected error severity, got {actual_llm_gateway_exception.severity}")
    if actual_llm_gateway_exception.http_status_code != 500:
        mismatches.append(f"Expected HTTP status 500, got {actual_llm_gateway_exception.http_status_code}")
    actual_contextual_field_value_by_name: dict[str, ExceptionValue] = dict(actual_llm_gateway_exception.diagnostics.additional_fields)
    expected_contextual_field: tuple[str, str]
    for expected_contextual_field in expected_llm_gateway_failure.contextual_fields:
        actual_contextual_field_value: ExceptionValue | None = actual_contextual_field_value_by_name.get(expected_contextual_field[0])
        if actual_contextual_field_value != expected_contextual_field[1]:
            mismatches.append(
                f"Expected context {expected_contextual_field[0]}={expected_contextual_field[1]!r}, got {actual_contextual_field_value!r}"
            )
    actual_cause_type: str | None = actual_llm_gateway_exception.cause.exception_type if actual_llm_gateway_exception.cause is not None else None
    if actual_cause_type != expected_llm_gateway_failure.cause_type:
        mismatches.append(f"Expected cause type {expected_llm_gateway_failure.cause_type}, got {actual_cause_type}")
    actual_message: str = actual_llm_gateway_exception.message
    mismatches.extend(
        f"Expected message phrase {expected_message_phrase!r} in {actual_message!r}"
        for expected_message_phrase in expected_llm_gateway_failure.message_phrases
        if expected_message_phrase.casefold() not in actual_message.casefold()
    )
    mismatches.extend(
        "Expense text appeared in gateway diagnostics"
        for forbidden_message_phrase in expected_llm_gateway_failure.forbidden_message_phrases
        if forbidden_message_phrase in actual_llm_gateway_exception.to_string_without_traceback()
    )
    assert not mismatches, (
        f"LLM gateway failure for context {dict(expected_llm_gateway_failure.contextual_fields)!r} "
        f"has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)
    )
