from collections.abc import Sequence
from dataclasses import dataclass

from acceptance_support.mediator_jev import CapturedJevRequest
from analyze_expenses_workflow.domain_facades import ExpenseAnalysisResult, ExpenseCategorization, ExpenseCategory, SystemOneGatewayException


@dataclass(frozen=True, slots=True)
class ExpectedJevCategorization:
    source_line_number: int
    source_text: str
    category: ExpenseCategory
    model_category: ExpenseCategory
    policy_note: str | None
    probability_categories: frozenset[ExpenseCategory]


@dataclass(frozen=True, slots=True)
class ExpectedJevGatewayFailure:
    exception_type: type[SystemOneGatewayException]
    action: str
    reason: str
    log_event: str
    severity: str
    http_status_code: int
    message_phrases: tuple[str, ...]
    contextual_fields: tuple[tuple[str, str], ...] = ()
    forbidden_message_phrases: tuple[str, ...] = ()
    cause_exception_type: type[BaseException] | None = None


def assert_jev_categorization(
    actual_expense_analysis_result: ExpenseAnalysisResult,
    expected_jev_categorization: ExpectedJevCategorization,
) -> None:
    mismatches: list[str] = []
    if len(actual_expense_analysis_result.categorizations) != 1:
        mismatches.append(f"Expected one categorization, got {len(actual_expense_analysis_result.categorizations)}")
    if actual_expense_analysis_result.categorizations:
        actual_expense_categorization = actual_expense_analysis_result.categorizations[0]
        if actual_expense_categorization.source_line_number != expected_jev_categorization.source_line_number:
            mismatches.append(
                f"Expected source line {expected_jev_categorization.source_line_number}, got {actual_expense_categorization.source_line_number}"
            )
        if actual_expense_categorization.source_text != expected_jev_categorization.source_text:
            mismatches.append(f"Expected source text {expected_jev_categorization.source_text!r}, got {actual_expense_categorization.source_text!r}")
        if actual_expense_categorization.category != expected_jev_categorization.category:
            mismatches.append(f"Expected category {expected_jev_categorization.category}, got {actual_expense_categorization.category}")
        if actual_expense_categorization.model_category != expected_jev_categorization.model_category:
            mismatches.append(
                f"Expected model category {expected_jev_categorization.model_category}, got {actual_expense_categorization.model_category}"
            )
        if actual_expense_categorization.policy_note != expected_jev_categorization.policy_note:
            mismatches.append(f"Expected policy note {expected_jev_categorization.policy_note!r}, got {actual_expense_categorization.policy_note!r}")
        if not 0.0 <= actual_expense_categorization.confidence <= 1.0:
            mismatches.append(f"Confidence outside [0, 1]: {actual_expense_categorization.confidence}")
        actual_probability_categories: set[ExpenseCategory] = {
            category_probability.category for category_probability in actual_expense_categorization.probabilities
        }
        if actual_probability_categories != expected_jev_categorization.probability_categories:
            mismatches.append(
                f"Expected probability categories {expected_jev_categorization.probability_categories}, got {actual_probability_categories}"
            )
        if actual_expense_categorization.model_category not in actual_probability_categories:
            mismatches.append(f"Model category {actual_expense_categorization.model_category} is absent from probabilities")
    position: int
    unexpected_expense_categorization: ExpenseCategorization
    for position, unexpected_expense_categorization in enumerate(actual_expense_analysis_result.categorizations[1:], start=2):
        mismatches.append(
            f"Unexpected categorization at position {position}: source line {unexpected_expense_categorization.source_line_number}, "
            f"source text {unexpected_expense_categorization.source_text!r}, category {unexpected_expense_categorization.category}"
        )
    assert not mismatches, (
        f"Jev categorization for expense line {expected_jev_categorization.source_text!r} has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)
    )


def assert_jev_requests(
    actual_captured_jev_requests: Sequence[CapturedJevRequest],
    expected_method: str,
    expected_path: str,
    expected_state: str,
) -> None:
    mismatches: list[str] = []
    if len(actual_captured_jev_requests) != 1:
        mismatches.append(f"Expected one Jev request, got {len(actual_captured_jev_requests)}")
    if actual_captured_jev_requests:
        actual_captured_jev_request: CapturedJevRequest = actual_captured_jev_requests[0]
        if actual_captured_jev_request.method != expected_method:
            mismatches.append(f"Expected method {expected_method}, got {actual_captured_jev_request.method}")
        if actual_captured_jev_request.path != expected_path:
            mismatches.append(f"Expected path {expected_path}, got {actual_captured_jev_request.path}")
        if actual_captured_jev_request.jev_request_field_value_by_name.get("state") != expected_state:
            mismatches.append(
                f"Expected request state {expected_state!r}, got {actual_captured_jev_request.jev_request_field_value_by_name.get('state')!r}"
            )
    position: int
    unexpected_captured_jev_request: CapturedJevRequest
    for position, unexpected_captured_jev_request in enumerate(actual_captured_jev_requests[1:], start=2):
        mismatches.append(
            f"Unexpected Jev request at position {position}: method {unexpected_captured_jev_request.method}, "
            f"path {unexpected_captured_jev_request.path}, "
            f"state {unexpected_captured_jev_request.jev_request_field_value_by_name.get('state')!r}"
        )
    assert not mismatches, f"Jev request to {expected_path} for state {expected_state!r} has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def assert_jev_gateway_failure(
    actual_system_one_gateway_exception: SystemOneGatewayException,
    expected_jev_gateway_failure: ExpectedJevGatewayFailure,
) -> None:
    mismatches: list[str] = []
    if type(actual_system_one_gateway_exception) is not expected_jev_gateway_failure.exception_type:
        mismatches.append(
            f"Expected exception {expected_jev_gateway_failure.exception_type.__name__}, got {type(actual_system_one_gateway_exception).__name__}"
        )
    if actual_system_one_gateway_exception.action.value != expected_jev_gateway_failure.action:
        mismatches.append(f"Expected action {expected_jev_gateway_failure.action}, got {actual_system_one_gateway_exception.action}")
    if actual_system_one_gateway_exception.reason != expected_jev_gateway_failure.reason:
        mismatches.append(f"Expected reason {expected_jev_gateway_failure.reason!r}, got {actual_system_one_gateway_exception.reason!r}")
    if actual_system_one_gateway_exception.log_event.value != expected_jev_gateway_failure.log_event:
        mismatches.append(f"Expected log event {expected_jev_gateway_failure.log_event}, got {actual_system_one_gateway_exception.log_event}")
    if actual_system_one_gateway_exception.severity.name != expected_jev_gateway_failure.severity:
        mismatches.append(f"Expected severity {expected_jev_gateway_failure.severity}, got {actual_system_one_gateway_exception.severity}")
    if actual_system_one_gateway_exception.http_status_code != expected_jev_gateway_failure.http_status_code:
        mismatches.append(
            f"Expected HTTP status {expected_jev_gateway_failure.http_status_code}, got {actual_system_one_gateway_exception.http_status_code}"
        )
    if expected_jev_gateway_failure.cause_exception_type is not None and not isinstance(
        actual_system_one_gateway_exception.__cause__, expected_jev_gateway_failure.cause_exception_type
    ):
        mismatches.append(
            f"Expected cause {expected_jev_gateway_failure.cause_exception_type.__name__}, "
            f"got {type(actual_system_one_gateway_exception.__cause__).__name__}"
        )
    actual_contextual_field_value_by_name: dict[str, object] = dict(actual_system_one_gateway_exception.diagnostics.additional_fields)
    mismatches.extend(
        f"Expected context {expected_contextual_field[0]}={expected_contextual_field[1]!r}, "
        f"got {actual_contextual_field_value_by_name.get(expected_contextual_field[0])!r}"
        for expected_contextual_field in expected_jev_gateway_failure.contextual_fields
        if actual_contextual_field_value_by_name.get(expected_contextual_field[0]) != expected_contextual_field[1]
    )
    redacted_actual_gateway_message: str = actual_system_one_gateway_exception.message
    forbidden_phrase: str
    for forbidden_phrase in expected_jev_gateway_failure.forbidden_message_phrases:
        redacted_actual_gateway_message = redacted_actual_gateway_message.replace(forbidden_phrase, "<redacted>")
    mismatches.extend(
        f"Expected message phrase {expected_phrase!r} in gateway message {redacted_actual_gateway_message!r}"
        for expected_phrase in expected_jev_gateway_failure.message_phrases
        if expected_phrase.casefold() not in actual_system_one_gateway_exception.message.casefold()
    )
    mismatches.extend(
        "Configured credential appeared in gateway message"
        for forbidden_phrase in expected_jev_gateway_failure.forbidden_message_phrases
        if forbidden_phrase in actual_system_one_gateway_exception.message
    )
    assert not mismatches, (
        f"Jev gateway failure for reason {expected_jev_gateway_failure.reason!r} and context "
        f"{dict(expected_jev_gateway_failure.contextual_fields)!r} has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)
    )
