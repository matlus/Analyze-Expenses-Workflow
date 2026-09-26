import json
import secrets
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesBusinessException,
    AnalyzeExpensesException,
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
    ExpenseLogEvent,
    Severity,
)
from analyze_expenses_workflow.managers.exceptions.configuration_setting_exception import ConfigurationSettingException
from analyze_expenses_workflow.managers.exceptions.expense_findings_exception import ExpenseFindingsException
from analyze_expenses_workflow.managers.exceptions.expense_input_exception import ExpenseInputException
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmGatewayClosedException,
    LlmGatewayException,
    LlmRequestFailedException,
    LlmResponseInvalidException,
    LlmUnsupportedReasoningEffortException,
)
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import (
    JevGatewayClosedException,
    JevRequestFailedException,
    JevResourceCleanupFailedException,
    JevResponseInvalidException,
)
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.managers.processors.expense_reconciliation_processor import ExpenseReconciliationProcessor
from analyze_expenses_workflow.managers.validators.validator_expense_lines import ValidatorExpenseLines
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction, TransactionTreatment
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


def test_ExpenseInput_WhenSourceLineHasWrongType_ThenReportsLineAndBusinessDiagnostics() -> None:
    expected_line_number: int = 2
    expected_status_code: int = 400
    expected_message: str = "Expense line 2 must contain text"
    expected_diagnostic_values_by_name: dict[str, str | int | float | bool] = {
        "LineNumber": expected_line_number,
        "ApplicationName": "AnalyzeExpensesWorkflow",
        "ExceptionType": ExpenseInputException.__name__,
        "HttpStatusCode": expected_status_code,
        "Action": ExceptionAction.USER_ACTION_REQUIRED.value,
        "LogEvent": ExpenseLogEvent.INPUT_VALIDATION.value,
        "Severity": Severity.ERROR.name,
        "Reason": "Expense input failed validation",
    }
    valid_expense_line: str = f"6/1/26 | {secrets.token_hex(8)} | $3.00"
    invalid_line_value: int = secrets.randbelow(10_000)
    with pytest.raises(ExpenseInputException, match=expected_message) as raised:
        ValidatorExpenseLines.validate([valid_expense_line, cast("str", invalid_line_value)])

    expense_input_exception: ExpenseInputException = raised.value
    _assert_exception_details(
        expense_input_exception,
        expected_type=AnalyzeExpensesBusinessException,
        expected_diagnostic_values_by_name=expected_diagnostic_values_by_name,
        expected_line_number=expected_line_number,
    )


async def test_ExpenseParsing_WhenBlankLineFollowsExpense_ThenPreservesUnparsedLine() -> None:
    expense_lines: list[str] = [f"2026-06-01 | {secrets.token_hex(8)} | $10.00", " "]
    expected_parsed_properties: tuple[date | None, Decimal | None, tuple[str, ...]] = (
        date(2026, 6, 1),
        Decimal("10.00"),
        ("missing_date", "missing_amount", "missing_description"),
    )
    expected_treatments: tuple[TransactionTreatment, TransactionTreatment] = (
        TransactionTreatment.INCLUDED_SPENDING,
        TransactionTreatment.UNPARSED,
    )
    expected_result: tuple[
        tuple[date | None, Decimal | None, tuple[str, ...]],
        tuple[TransactionTreatment, TransactionTreatment],
    ] = (expected_parsed_properties, expected_treatments)
    ValidatorExpenseLines.validate(expense_lines)
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)
    expense_categorization: ExpenseCategorization = ExpenseCategorization(
        1, expense_lines[0], ExpenseCategory.GROCERIES, ExpenseCategory.GROCERIES, None, 1.0, ()
    )

    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(parsed_lines, (expense_categorization, None))

    actual_parsed_properties: tuple[date | None, Decimal | None, tuple[str, ...]] = (
        parsed_lines[0].occurred_on,
        parsed_lines[0].amount,
        parsed_lines[1].issues,
    )
    actual_treatments: tuple[TransactionTreatment, TransactionTreatment] = (transactions[0].treatment, transactions[1].treatment)
    assert (actual_parsed_properties, actual_treatments) == expected_result


@pytest.mark.parametrize(
    ("error_type", "expected_action", "expected_reason", "expected_log_event"),
    [
        (
            ConfigurationSettingException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Configuration is invalid or unavailable",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            ExpenseFindingsException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Expense findings could not be grounded",
            ExpenseLogEvent.FINDINGS_SELECTION,
        ),
        (LlmRequestFailedException, ExceptionAction.RETRY_ACTION_NEEDED, "Coding assistant request failed", ExpenseLogEvent.LLM_GATEWAY),
        (
            LlmGatewayClosedException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Coding assistant gateway was used after closing",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmUnsupportedReasoningEffortException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Coding assistant reasoning effort is unsupported",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmResponseInvalidException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Coding assistant response violates its contract",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmClientCleanupFailedException,
            ExceptionAction.RETRY_ACTION_NEEDED,
            "Coding assistant client cleanup failed",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (JevRequestFailedException, ExceptionAction.RETRY_ACTION_NEEDED, "Jev gateway request failed", ExpenseLogEvent.JEV_GATEWAY),
        (
            JevResponseInvalidException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Jev choice response violates its contract",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (
            JevGatewayClosedException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Jev gateway was used after closing",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (
            JevResourceCleanupFailedException,
            ExceptionAction.RETRY_ACTION_NEEDED,
            "Jev gateway resource cleanup failed",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
    ],
)
def test_ExpenseException_WhenTechnicalErrorIsConstructed_ThenSharesApplicationBase(
    error_type: type[AnalyzeExpensesTechnicalException],
    expected_action: ExceptionAction,
    expected_reason: str,
    expected_log_event: ExpenseLogEvent,
) -> None:
    expected_status_code: int = 500
    expected_severity: Severity = Severity.ERROR
    expected_message: str = secrets.token_hex(8)
    expected_metadata: tuple[bool, str, int, str, Severity, ExceptionAction, str, ExpenseLogEvent] = (
        True,
        "AnalyzeExpensesWorkflow",
        expected_status_code,
        error_type.__name__,
        expected_severity,
        expected_action,
        expected_reason,
        expected_log_event,
    )
    analyze_expenses_technical_exception: AnalyzeExpensesTechnicalException = error_type(expected_message)

    actual_metadata: tuple[bool, str, int, str, Severity, ExceptionAction, str, ExpenseLogEvent] = (
        isinstance(analyze_expenses_technical_exception, AnalyzeExpensesException),
        analyze_expenses_technical_exception.diagnostics.application_name,
        analyze_expenses_technical_exception.http_status_code,
        analyze_expenses_technical_exception.diagnostics.exception_type,
        analyze_expenses_technical_exception.severity,
        analyze_expenses_technical_exception.action,
        analyze_expenses_technical_exception.reason,
        analyze_expenses_technical_exception.log_event,
    )
    assert actual_metadata == expected_metadata


def test_ExpenseException_WhenContextChangesAfterConstruction_ThenPreservesDiagnosticsAndCause() -> None:
    operation_context_key: str = "Operation"
    exception_type_context_key: str = "ExceptionType"
    expected_operation: str = secrets.token_hex(8)
    expected_cause_message: str = secrets.token_hex(8)
    expected_message: str = secrets.token_hex(8)
    supplied_context_by_name: dict[str, str | int | float | bool] = {
        operation_context_key: expected_operation,
        exception_type_context_key: secrets.token_hex(8),
    }
    expected_diagnostic_values_by_name: dict[str, str | int | float | bool] = {
        operation_context_key: expected_operation,
        "ApplicationName": "AnalyzeExpensesWorkflow",
        exception_type_context_key: LlmRequestFailedException.__name__,
        "Action": ExceptionAction.RETRY_ACTION_NEEDED.value,
        "Reason": "Coding assistant request failed",
        "LogEvent": ExpenseLogEvent.LLM_GATEWAY.value,
        "Severity": Severity.ERROR.name,
        "HttpStatusCode": 500,
        "Message": expected_message,
        "CausedBy": "ValueError",
        "Cause": expected_cause_message,
    }
    llm_gateway_exception: LlmGatewayException = LlmRequestFailedException(expected_message, supplied_context_by_name)
    llm_gateway_exception.__cause__ = ValueError(expected_cause_message)

    supplied_context_by_name[operation_context_key] = secrets.token_hex(8)
    actual_diagnostic_values_by_name: dict[str, str | int | float | bool] = json.loads(llm_gateway_exception.to_json())
    assert actual_diagnostic_values_by_name == expected_diagnostic_values_by_name


def test_ExpenseException_WhenContextContainsNonfiniteFloat_ThenWritesStrictJson() -> None:
    score_context_key: str = "Score"
    nonfinite_score_text: str = "nan"
    expected_score_text: str = nonfinite_score_text
    llm_gateway_exception: LlmGatewayException = LlmRequestFailedException(
        secrets.token_hex(8), {score_context_key: float(nonfinite_score_text)}
    )

    actual_diagnostic_values_by_name: dict[str, str | int | float | bool] = json.loads(
        llm_gateway_exception.to_json(), parse_constant=lambda value: pytest.fail(f"Nonstandard JSON constant: {value}")
    )

    assert actual_diagnostic_values_by_name[score_context_key] == expected_score_text


def test_ExpenseException_WhenTracebackProjectionIsSelected_ThenOnlyThatProjectionIncludesTraceback() -> None:
    expected_traceback_field: str = "Traceback"
    expected_traceback_presence: tuple[bool, bool, bool, bool] = (False, True, False, True)
    with pytest.raises(LlmRequestFailedException) as raised:
        raise LlmRequestFailedException(secrets.token_hex(8))
    llm_gateway_exception: LlmGatewayException = raised.value
    actual_plain_field_names: tuple[str, ...] = tuple(name for name, _ in llm_gateway_exception.diagnostics.fields())
    actual_trace_field_names: tuple[str, ...] = tuple(name for name, _ in llm_gateway_exception.diagnostics.fields_with_traceback())
    actual_plain_text: str = llm_gateway_exception.to_string_without_traceback()
    actual_trace_text: str = llm_gateway_exception.to_string()

    assert (
        expected_traceback_field in actual_plain_field_names,
        expected_traceback_field in actual_trace_field_names,
        expected_traceback_field in actual_plain_text,
        expected_traceback_field in actual_trace_text,
    ) == expected_traceback_presence


def _assert_exception_details(
    expense_input_exception: ExpenseInputException,
    *,
    expected_type: type[AnalyzeExpensesException],
    expected_diagnostic_values_by_name: Mapping[str, str | int | float | bool],
    expected_line_number: int,
) -> None:
    diagnostic_values_by_name: dict[str, str | int | float | bool] = json.loads(expense_input_exception.to_json())
    failures: list[str] = []
    for expected_value_item in expected_diagnostic_values_by_name.items():
        diagnostic_name: str = expected_value_item[0]
        expected_value: str | int | float | bool = expected_value_item[1]
        if diagnostic_values_by_name.get(diagnostic_name) != expected_value:
            failures.append(f"{diagnostic_name}: expected {expected_value!r}, got {diagnostic_values_by_name.get(diagnostic_name)!r}")
    if not isinstance(expense_input_exception, expected_type):
        failures.append(f"Expected {expected_type.__name__}, got {type(expense_input_exception).__name__}")
    if expense_input_exception.line_number != expected_line_number:
        failures.append(f"Expected line {expected_line_number}, got {expense_input_exception.line_number}")
    assert not failures, f"{len(failures)} exception-property mismatches:\n" + "\n".join(failures)
