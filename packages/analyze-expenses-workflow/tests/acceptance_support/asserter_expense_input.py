from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from acceptance_support.mediator_jev import CapturedJevRequest
from analyze_expenses_workflow.domain_facades import ExpenseInputInvalidException


@dataclass(frozen=True, slots=True)
class ExpectedExpenseInputInvalid:
    action: str
    reason: str
    log_event: str
    severity: str
    http_status_code: int
    message_phrases: tuple[str, ...]
    line_numbers: tuple[int, ...]
    contextual_fields: tuple[tuple[str, str | int], ...]
    problems: tuple[str, ...] | None = None

    @classmethod
    def create(
        cls,
        message_phrases: tuple[str, ...],
        problem_count: int,
        line_numbers: tuple[int, ...] = (),
        problems: tuple[str, ...] | None = None,
    ) -> Self:
        """Expense input is rejected before any work starts, so every rejection carries the same user-facing metadata."""
        problem_count_field: tuple[str, str | int] = ("ProblemCount", problem_count)
        line_numbers_fields: tuple[tuple[str, str | int], ...] = (
            (("LineNumbers", ", ".join(str(line_number) for line_number in line_numbers)),) if line_numbers else ()
        )
        return cls(
            action="UserActionRequired",
            reason="Expense input failed validation",
            log_event="ExpenseInputValidation",
            severity="ERROR",
            http_status_code=400,
            message_phrases=message_phrases,
            line_numbers=line_numbers,
            contextual_fields=(problem_count_field, *line_numbers_fields),
            problems=problems,
        )


def assert_expense_input_invalid(
    actual_expense_input_invalid_exception: ExpenseInputInvalidException,
    actual_captured_jev_requests: Sequence[CapturedJevRequest],
    expected_expense_input_invalid: ExpectedExpenseInputInvalid,
) -> None:
    mismatches: list[str] = []
    if actual_expense_input_invalid_exception.action.value != expected_expense_input_invalid.action:
        mismatches.append(f"Expected action {expected_expense_input_invalid.action}, got {actual_expense_input_invalid_exception.action}")
    if actual_expense_input_invalid_exception.reason != expected_expense_input_invalid.reason:
        mismatches.append(f"Expected reason {expected_expense_input_invalid.reason!r}, got {actual_expense_input_invalid_exception.reason!r}")
    if actual_expense_input_invalid_exception.log_event.value != expected_expense_input_invalid.log_event:
        mismatches.append(f"Expected log event {expected_expense_input_invalid.log_event}, got {actual_expense_input_invalid_exception.log_event}")
    if actual_expense_input_invalid_exception.severity.name != expected_expense_input_invalid.severity:
        mismatches.append(f"Expected severity {expected_expense_input_invalid.severity}, got {actual_expense_input_invalid_exception.severity}")
    if actual_expense_input_invalid_exception.http_status_code != expected_expense_input_invalid.http_status_code:
        mismatches.append(
            f"Expected HTTP status {expected_expense_input_invalid.http_status_code}, got {actual_expense_input_invalid_exception.http_status_code}"
        )
    mismatches.extend(
        f"Expected message phrase {expected_message_phrase!r} in {actual_expense_input_invalid_exception.message!r}"
        for expected_message_phrase in expected_expense_input_invalid.message_phrases
        if expected_message_phrase not in actual_expense_input_invalid_exception.message
    )
    if actual_expense_input_invalid_exception.line_numbers != expected_expense_input_invalid.line_numbers:
        mismatches.append(
            f"Expected line numbers {expected_expense_input_invalid.line_numbers}, got {actual_expense_input_invalid_exception.line_numbers}"
        )
    if (
        expected_expense_input_invalid.problems is not None
        and actual_expense_input_invalid_exception.problems != expected_expense_input_invalid.problems
    ):
        mismatches.append(f"Expected problems {expected_expense_input_invalid.problems}, got {actual_expense_input_invalid_exception.problems}")
    actual_contextual_field_value_by_name: dict[str, object] = dict(actual_expense_input_invalid_exception.diagnostics.additional_fields)
    expected_contextual_field_value_by_name: dict[str, object] = dict(expected_expense_input_invalid.contextual_fields)
    mismatches.extend(
        f"Expected context {expected_contextual_field[0]}={expected_contextual_field[1]!r}, "
        f"got {actual_contextual_field_value_by_name.get(expected_contextual_field[0])!r}"
        for expected_contextual_field in expected_contextual_field_value_by_name.items()
        if actual_contextual_field_value_by_name.get(expected_contextual_field[0]) != expected_contextual_field[1]
    )
    mismatches.extend(
        f"Unexpected context {actual_contextual_field[0]}={actual_contextual_field[1]!r}"
        for actual_contextual_field in actual_contextual_field_value_by_name.items()
        if actual_contextual_field[0] not in expected_contextual_field_value_by_name
    )
    if actual_captured_jev_requests:
        mismatches.append(f"Expected no Jev requests, got {len(actual_captured_jev_requests)}")
    assert not mismatches, (
        f"Expense input rejection with context {expected_contextual_field_value_by_name!r} has {len(mismatches)} mismatches:\n"
        + "\n".join(mismatches)
    )
