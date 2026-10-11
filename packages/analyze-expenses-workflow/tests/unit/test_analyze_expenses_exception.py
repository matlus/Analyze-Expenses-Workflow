import json
import secrets
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesBaseException,
    AnalyzeExpensesBusinessBaseException,
    AnalyzeExpensesTechnicalBaseException,
    ExceptionAction,
    ExpenseLogEvent,
    Severity,
)
from analyze_expenses_workflow.managers.exceptions.configuration_setting_exception import (
    CodingAssistantSettingsInvalidException,
    CodingAssistantSubscriptionUnknownException,
    ConfigurationSettingBlankException,
    ConfigurationSettingMissingException,
    JevMinimumChoiceProbabilitySettingInvalidException,
    LlmModelFamilyMismatchException,
    LlmModelSettingInvalidException,
    LlmModelSettingMissingException,
    LlmOperationNotConfiguredException,
    ModelFamilyOverrideRejectedException,
    MonthlyBudgetSettingInvalidException,
    OpenRouterBaseUrlSettingInvalidException,
    ReasoningEffortSettingUnknownException,
    ReasoningEffortUnsupportedException,
)
from analyze_expenses_workflow.managers.exceptions.expense_findings_exception import (
    ExpenseFindingsException,
)
from analyze_expenses_workflow.managers.exceptions.expense_input_exception import (
    ExpenseInputInvalidException,
)
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmClientCleanupFailedException,
    LlmCliExitedException,
    LlmCliNotFoundException,
    LlmCliStartFailedException,
    LlmGatewayBaseException,
    LlmGatewayClosedException,
    LlmRequestFailedException,
    LlmRequestRejectedException,
    LlmRequestTimedOutException,
    LlmResponseEmptyException,
    LlmResponseUnreadableException,
    LlmSessionDeleteFailedException,
)
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import (
    JevChoiceCategoriesMismatchException,
    JevChoiceCategoryUnrecognizedException,
    JevChoiceResponseMalformedException,
    JevClientCleanupFailedException,
    JevGatewayClosedException,
    JevRateLimitRetriesExhaustedException,
    JevRequestFailedException,
    JevRequestRejectedException,
    JevServerRetryRequestedRetriesExhaustedException,
    JevTransportCleanupFailedException,
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
        "LineNumbers": str(expected_line_number),
        "ProblemCount": 1,
        "ApplicationName": "AnalyzeExpensesWorkflow",
        "ExceptionType": ExpenseInputInvalidException.__name__,
        "HttpStatusCode": expected_status_code,
        "Action": ExceptionAction.USER_ACTION_REQUIRED.value,
        "LogEvent": ExpenseLogEvent.INPUT_VALIDATION.value,
        "Severity": Severity.ERROR.name,
        "Reason": "Expense input failed validation",
    }
    valid_expense_line: str = f"6/1/26 | {secrets.token_hex(8)} | $3.00"
    invalid_line_value: int = secrets.randbelow(10_000)
    with pytest.raises(ExpenseInputInvalidException, match=expected_message) as raised:
        ValidatorExpenseLines.validate([valid_expense_line, cast("str", invalid_line_value)])

    actual_expense_input_invalid_exception: ExpenseInputInvalidException = raised.value
    _assert_exception_details(
        actual_expense_input_invalid_exception,
        expected_type=AnalyzeExpensesBusinessBaseException,
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
            ConfigurationSettingMissingException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Required configuration setting is missing",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            ConfigurationSettingBlankException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Configuration setting is set but blank",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            CodingAssistantSubscriptionUnknownException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Coding assistant subscription is not a supported value",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            CodingAssistantSettingsInvalidException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Coding assistant subscription maps to an invalid model family",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            ModelFamilyOverrideRejectedException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Model family setting overrides the family derived from the subscription",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            MonthlyBudgetSettingInvalidException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Monthly budget setting is not a positive amount with at most two decimal places",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            LlmModelSettingMissingException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "LLM operation model setting is required for the model family",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            LlmModelFamilyMismatchException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "LLM operation model belongs to another model family",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            LlmModelSettingInvalidException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "LLM operation model produced invalid operation settings",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            ReasoningEffortSettingUnknownException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Reasoning effort setting is not a known effort",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            ReasoningEffortUnsupportedException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Reasoning effort is not offered by the coding assistant subscription",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            LlmOperationNotConfiguredException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "LLM operation has no loaded settings",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            OpenRouterBaseUrlSettingInvalidException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Open Router base URL setting is not a valid HTTPS URL",
            ExpenseLogEvent.CONFIGURATION,
        ),
        (
            JevMinimumChoiceProbabilitySettingInvalidException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Jev minimum choice probability setting is not between zero and one",
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
            LlmRequestTimedOutException,
            ExceptionAction.RETRY_ACTION_NEEDED,
            "Coding assistant request did not finish within its time limit",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmGatewayClosedException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Coding assistant gateway was used after closing",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmRequestRejectedException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Coding assistant provider rejected the request",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (LlmCliNotFoundException, ExceptionAction.INFRA_ACTION_REQUIRED, "Coding assistant CLI is not installed", ExpenseLogEvent.LLM_GATEWAY),
        (
            LlmCliStartFailedException,
            ExceptionAction.INFRA_ACTION_REQUIRED,
            "Coding assistant CLI is installed but could not be started",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmCliExitedException,
            ExceptionAction.RETRY_ACTION_NEEDED,
            "Coding assistant CLI exited with an error and no result",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmResponseEmptyException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Coding assistant returned no response text",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (
            LlmResponseUnreadableException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Coding assistant response could not be read",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (LlmClientCleanupFailedException, ExceptionAction.RETRY_ACTION_NEEDED, "Coding assistant client cleanup failed", ExpenseLogEvent.LLM_GATEWAY),
        (
            LlmSessionDeleteFailedException,
            ExceptionAction.RETRY_ACTION_NEEDED,
            "Coding assistant session could not be deleted from the user's history",
            ExpenseLogEvent.LLM_GATEWAY,
        ),
        (JevRequestFailedException, ExceptionAction.RETRY_ACTION_NEEDED, "Jev gateway request failed", ExpenseLogEvent.JEV_GATEWAY),
        (
            JevRequestRejectedException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Jev rejected the request and will reject it again unless the request changes",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (
            JevRateLimitRetriesExhaustedException,
            ExceptionAction.RETRY_ACTION_NEEDED,
            "Jev kept rate limiting the request through every retry attempt",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (
            JevServerRetryRequestedRetriesExhaustedException,
            ExceptionAction.RETRY_ACTION_NEEDED,
            "Jev kept failing with a server error that asked for a retry through every retry attempt",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (
            JevChoiceResponseMalformedException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Jev choice response does not have the expected shape",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (
            JevChoiceCategoriesMismatchException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Jev returned probabilities for categories other than those asked about",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (
            JevChoiceCategoryUnrecognizedException,
            ExceptionAction.DEVELOPER_ACTION_REQUIRED,
            "Jev selected a category that is not a known expense category",
            ExpenseLogEvent.JEV_GATEWAY,
        ),
        (JevGatewayClosedException, ExceptionAction.DEVELOPER_ACTION_REQUIRED, "Jev gateway was used after closing", ExpenseLogEvent.JEV_GATEWAY),
        (JevClientCleanupFailedException, ExceptionAction.RETRY_ACTION_NEEDED, "Jev client cleanup failed", ExpenseLogEvent.JEV_GATEWAY),
        (JevTransportCleanupFailedException, ExceptionAction.RETRY_ACTION_NEEDED, "Jev HTTP transport cleanup failed", ExpenseLogEvent.JEV_GATEWAY),
    ],
)
def test_ExpenseException_WhenTechnicalErrorIsConstructed_ThenSharesApplicationBase(
    error_type: type[AnalyzeExpensesTechnicalBaseException],
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
    analyze_expenses_technical_base_exception: AnalyzeExpensesTechnicalBaseException = error_type(expected_message)

    actual_metadata: tuple[bool, str, int, str, Severity, ExceptionAction, str, ExpenseLogEvent] = (
        isinstance(analyze_expenses_technical_base_exception, AnalyzeExpensesBaseException),
        analyze_expenses_technical_base_exception.diagnostics.application_name,
        analyze_expenses_technical_base_exception.http_status_code,
        analyze_expenses_technical_base_exception.diagnostics.exception_type,
        analyze_expenses_technical_base_exception.severity,
        analyze_expenses_technical_base_exception.action,
        analyze_expenses_technical_base_exception.reason,
        analyze_expenses_technical_base_exception.log_event,
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
    llm_gateway_base_exception: LlmGatewayBaseException = LlmRequestFailedException(expected_message, supplied_context_by_name)
    llm_gateway_base_exception.__cause__ = ValueError(expected_cause_message)

    supplied_context_by_name[operation_context_key] = secrets.token_hex(8)
    actual_diagnostic_values_by_name: dict[str, str | int | float | bool] = json.loads(llm_gateway_base_exception.to_json())
    assert actual_diagnostic_values_by_name == expected_diagnostic_values_by_name


def test_ExpenseException_WhenContextContainsNonfiniteFloat_ThenWritesStrictJson() -> None:
    score_context_key: str = "Score"
    nonfinite_score_text: str = "nan"
    expected_score_text: str = nonfinite_score_text
    llm_gateway_base_exception: LlmGatewayBaseException = LlmRequestFailedException(
        secrets.token_hex(8), {score_context_key: float(nonfinite_score_text)}
    )

    actual_diagnostic_values_by_name: dict[str, str | int | float | bool] = json.loads(
        llm_gateway_base_exception.to_json(), parse_constant=lambda value: pytest.fail(f"Nonstandard JSON constant: {value}")
    )

    assert actual_diagnostic_values_by_name[score_context_key] == expected_score_text


def test_ExpenseException_WhenTracebackProjectionIsSelected_ThenOnlyThatProjectionIncludesTraceback() -> None:
    expected_traceback_field: str = "Traceback"
    expected_traceback_presence: tuple[bool, bool, bool, bool] = (False, True, False, True)
    with pytest.raises(LlmRequestFailedException) as raised:
        raise LlmRequestFailedException(secrets.token_hex(8))
    llm_gateway_base_exception: LlmGatewayBaseException = raised.value
    actual_plain_field_names: tuple[str, ...] = tuple(name for name, _ in llm_gateway_base_exception.diagnostics.fields())
    actual_trace_field_names: tuple[str, ...] = tuple(name for name, _ in llm_gateway_base_exception.diagnostics.fields_with_traceback())
    actual_plain_text: str = llm_gateway_base_exception.to_string_without_traceback()
    actual_trace_text: str = llm_gateway_base_exception.to_string()

    assert (
        expected_traceback_field in actual_plain_field_names,
        expected_traceback_field in actual_trace_field_names,
        expected_traceback_field in actual_plain_text,
        expected_traceback_field in actual_trace_text,
    ) == expected_traceback_presence


def _assert_exception_details(
    actual_expense_input_invalid_exception: ExpenseInputInvalidException,
    *,
    expected_type: type[AnalyzeExpensesBaseException],
    expected_diagnostic_values_by_name: Mapping[str, str | int | float | bool],
    expected_line_number: int,
) -> None:
    diagnostic_values_by_name: dict[str, str | int | float | bool] = json.loads(actual_expense_input_invalid_exception.to_json())
    failures: list[str] = []
    for expected_value_item in expected_diagnostic_values_by_name.items():
        diagnostic_name: str = expected_value_item[0]
        expected_value: str | int | float | bool = expected_value_item[1]
        if diagnostic_values_by_name.get(diagnostic_name) != expected_value:
            failures.append(f"{diagnostic_name}: expected {expected_value!r}, got {diagnostic_values_by_name.get(diagnostic_name)!r}")
    if not isinstance(actual_expense_input_invalid_exception, expected_type):
        failures.append(f"Expected {expected_type.__name__}, got {type(actual_expense_input_invalid_exception).__name__}")
    if actual_expense_input_invalid_exception.line_numbers != (expected_line_number,):
        failures.append(f"Expected lines {(expected_line_number,)}, got {actual_expense_input_invalid_exception.line_numbers}")
    assert not failures, (
        "Expense input validation must report the offending expense line with its business diagnostics; "
        f"{len(failures)} exception-property mismatches for {type(actual_expense_input_invalid_exception).__name__} "
        f"on expected line numbers {(expected_line_number,)}:\n" + "\n".join(failures)
    )
