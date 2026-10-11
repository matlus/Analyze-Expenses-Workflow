from dataclasses import dataclass

from openai_codex import ApprovalMode, Sandbox

from acceptance_support.mediator_codex import TestMediatorCodex
from acceptance_support.mediator_jev import TestMediatorJev
from analyze_expenses_workflow.domain_facades import AnalyzeExpensesTechnicalBaseException


@dataclass(frozen=True, slots=True)
class ExpectedLlmGatewayFailure:
    exception_type: type[AnalyzeExpensesTechnicalBaseException]
    action: str
    reason: str
    message_phrases: tuple[str, ...]
    contextual_fields: tuple[tuple[str, str | int], ...]
    cause_type: str | None = None
    forbidden_message_phrases: tuple[str, ...] = ()
    cause_message: str | None = None
    cause_note_phrases: tuple[str, ...] = ()
    forbidden_cause_note_phrases: tuple[str, ...] = ()


def assert_codex_requests(
    actual_test_mediator_codex: TestMediatorCodex,
    actual_test_mediator_jev: TestMediatorJev,
    expected_model: str,
    expected_expense_line: str,
    expected_request_count: int,
    expected_close_count: int,
) -> None:
    mismatches: list[str] = []
    if len(actual_test_mediator_codex.captured_prompts) != expected_request_count:
        mismatches.append(f"Expected {expected_request_count} Codex prompts, got {len(actual_test_mediator_codex.captured_prompts)}")
    if actual_test_mediator_codex.captured_models != [expected_model] * expected_request_count:
        mismatches.append(f"Expected Codex model {expected_model!r} for each request, got {actual_test_mediator_codex.captured_models!r}")
    if actual_test_mediator_codex.captured_sandboxes != [Sandbox.read_only] * expected_request_count:
        mismatches.append(f"Expected read-only Codex sandbox for each request, got {actual_test_mediator_codex.captured_sandboxes!r}")
    if actual_test_mediator_codex.captured_approval_modes != [ApprovalMode.deny_all] * expected_request_count:
        mismatches.append(f"Expected denied Codex approvals for each request, got {actual_test_mediator_codex.captured_approval_modes!r}")
    if actual_test_mediator_codex.close_count != expected_close_count:
        mismatches.append(f"Expected {expected_close_count} Codex closes, got {actual_test_mediator_codex.close_count}")
    if actual_test_mediator_jev.captured_jev_requests:
        mismatches.append(f"Expected no Jev requests, got {len(actual_test_mediator_jev.captured_jev_requests)}")
    if not actual_test_mediator_codex.captured_prompts or expected_expense_line not in actual_test_mediator_codex.captured_prompts[0]:
        mismatches.append(f"Expected the Codex prompt to contain expense line {expected_expense_line!r}")
    assert not mismatches, f"Codex request for expense line {expected_expense_line!r} has {len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def assert_llm_gateway_failure(
    actual_llm_gateway_exception: AnalyzeExpensesTechnicalBaseException,
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure,
) -> None:
    mismatches: list[str] = []
    if type(actual_llm_gateway_exception) is not expected_llm_gateway_failure.exception_type:
        mismatches.append(
            f"Expected exception {expected_llm_gateway_failure.exception_type.__name__}, got {type(actual_llm_gateway_exception).__name__}"
        )
    if actual_llm_gateway_exception.action.value != expected_llm_gateway_failure.action:
        mismatches.append(f"Expected action {expected_llm_gateway_failure.action}, got {actual_llm_gateway_exception.action}")
    if actual_llm_gateway_exception.reason != expected_llm_gateway_failure.reason:
        mismatches.append(f"Expected reason {expected_llm_gateway_failure.reason!r}, got {actual_llm_gateway_exception.reason!r}")
    if actual_llm_gateway_exception.log_event.value != "ExpenseLlmGateway":
        mismatches.append(f"Expected LLM gateway log event, got {actual_llm_gateway_exception.log_event}")
    if actual_llm_gateway_exception.severity.name != "ERROR":
        mismatches.append(f"Expected error severity, got {actual_llm_gateway_exception.severity}")
    if actual_llm_gateway_exception.http_status_code != 500:
        mismatches.append(f"Expected HTTP status 500, got {actual_llm_gateway_exception.http_status_code}")
    actual_contextual_field_value_by_name: dict[str, object] = dict(actual_llm_gateway_exception.diagnostics.additional_fields)
    expected_contextual_field: tuple[str, str | int]
    for expected_contextual_field in expected_llm_gateway_failure.contextual_fields:
        actual_contextual_field_value: object = actual_contextual_field_value_by_name.get(expected_contextual_field[0])
        if actual_contextual_field_value != expected_contextual_field[1]:
            mismatches.append(
                f"Expected context {expected_contextual_field[0]}={expected_contextual_field[1]!r}, got {actual_contextual_field_value!r}"
            )
    actual_cause_type: str | None = actual_llm_gateway_exception.cause.exception_type if actual_llm_gateway_exception.cause is not None else None
    if actual_cause_type != expected_llm_gateway_failure.cause_type:
        mismatches.append(f"Expected cause type {expected_llm_gateway_failure.cause_type}, got {actual_cause_type}")
    actual_cause_message: str | None = actual_llm_gateway_exception.cause.message if actual_llm_gateway_exception.cause is not None else None
    if expected_llm_gateway_failure.cause_message is not None and actual_cause_message != expected_llm_gateway_failure.cause_message:
        mismatches.append(f"Expected cause message {expected_llm_gateway_failure.cause_message!r}, got {actual_cause_message!r}")
    mismatches.extend(
        _note_mismatches(
            actual_llm_gateway_exception.__cause__,
            expected_llm_gateway_failure.cause_note_phrases,
            expected_llm_gateway_failure.forbidden_cause_note_phrases,
        )
    )
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


def assert_exception_notes(
    actual_exception: BaseException,
    expected_note_phrases: tuple[str, ...],
    forbidden_note_phrases: tuple[str, ...],
    scenario_description: str,
) -> None:
    """For a failure that leaves the gateway untranslated, such as a cancellation, whose notes record what cleanup could not do."""
    mismatches: list[str] = _note_mismatches(actual_exception, expected_note_phrases, forbidden_note_phrases)
    assert not mismatches, f"{scenario_description} has {len(mismatches)} note mismatches:\n" + "\n".join(mismatches)


def _note_mismatches(
    actual_exception: BaseException | None,
    expected_note_phrases: tuple[str, ...],
    forbidden_note_phrases: tuple[str, ...],
) -> list[str]:
    actual_notes: list[str] = getattr(actual_exception, "__notes__", [])
    # A forbidden phrase is expense text, so it is redacted wherever the notes are repeated in the failure output.
    redacted_actual_notes: list[str] = list(actual_notes)
    forbidden_note_phrase: str
    for forbidden_note_phrase in forbidden_note_phrases:
        redacted_actual_notes = [redacted_actual_note.replace(forbidden_note_phrase, "<redacted>") for redacted_actual_note in redacted_actual_notes]
    mismatches: list[str] = [
        f"Expected a note containing {expected_note_phrase!r}, got notes {redacted_actual_notes!r}"
        for expected_note_phrase in expected_note_phrases
        if not any(expected_note_phrase in actual_note for actual_note in actual_notes)
    ]
    mismatches.extend(
        "Expense text appeared in an exception note"
        for forbidden_note_phrase in forbidden_note_phrases
        if any(forbidden_note_phrase in actual_note for actual_note in actual_notes)
    )
    return mismatches
