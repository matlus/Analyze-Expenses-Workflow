import json
from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesBusinessException,
    AnalyzeExpensesException,
    AnalyzeExpensesTechnicalException,
    ExceptionAction,
)
from analyze_expenses_workflow.managers.exceptions.configuration_setting_exception import ConfigurationSettingException
from analyze_expenses_workflow.managers.exceptions.expense_findings_exception import ExpenseFindingsException
from analyze_expenses_workflow.managers.exceptions.expense_input_exception import ExpenseInputException
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import SystemOneGatewayException
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.managers.processors.expense_reconciliation_processor import ExpenseReconciliationProcessor
from analyze_expenses_workflow.managers.validators.validator_expense_lines import ValidatorExpenseLines
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction, TransactionTreatment
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


def test_invalid_source_line_has_line_specific_business_exception_diagnostics() -> None:
    with pytest.raises(ExpenseInputException, match="Expense line 2 must contain text") as raised:
        ValidatorExpenseLines.validate(["6/1/26 | Coffee | $3.00", cast("str", 7)])

    error: ExpenseInputException = raised.value
    diagnostics: dict[str, str | int | float | bool] = json.loads(error.to_json())
    assert isinstance(error, AnalyzeExpensesBusinessException)
    assert error.line_number == 2
    assert error.action == ExceptionAction.USER_ACTION_REQUIRED
    assert diagnostics["LineNumber"] == 2
    assert diagnostics["HttpStatusCode"] == 400


async def test_blank_source_line_is_preserved_as_unparsed_beside_valid_expense() -> None:
    expense_lines: list[str] = ["2026-06-01 | Market | $10.00", " "]
    ValidatorExpenseLines.validate(expense_lines)
    parsed_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)
    categorization: ExpenseCategorization = ExpenseCategorization(
        1, expense_lines[0], ExpenseCategory.GROCERIES, ExpenseCategory.GROCERIES, None, 1.0, ()
    )

    transactions: tuple[ExpenseTransaction, ...] = await ExpenseReconciliationProcessor().reconcile(
        parsed_lines, (categorization, None)
    )

    assert parsed_lines[0].occurred_on == date(2026, 6, 1)
    assert parsed_lines[0].amount == Decimal("10.00")
    assert parsed_lines[1].issues == ("missing_date", "missing_amount", "missing_description")
    assert transactions[0].treatment == TransactionTreatment.INCLUDED_SPENDING
    assert transactions[1].treatment == TransactionTreatment.UNPARSED


@pytest.mark.parametrize(
    "error_type",
    [ConfigurationSettingException, ExpenseFindingsException, LlmGatewayException, SystemOneGatewayException],
)
def test_technical_exceptions_share_application_base(error_type: type[AnalyzeExpensesTechnicalException]) -> None:
    error: AnalyzeExpensesTechnicalException = error_type("failure")

    assert isinstance(error, AnalyzeExpensesException)
    assert error.http_status_code == 500
    assert error.contextual_data_by_name["ExceptionType"] == error_type.__name__


def test_exception_context_is_copied_and_foreign_cause_is_reported() -> None:
    context: dict[str, str | int | float | bool] = {"Operation": "categorization", "ExceptionType": "spoofed"}
    error: LlmGatewayException = LlmGatewayException("request failed", context)
    error.__cause__ = ValueError("vendor failure")

    context["Operation"] = "changed"
    diagnostics: dict[str, str | int | float | bool] = json.loads(error.to_json())
    assert diagnostics["Operation"] == "categorization"
    assert diagnostics["ExceptionType"] == "LlmGatewayException"
    assert diagnostics["CausedBy"] == "ValueError"
    assert diagnostics["Cause"] == "vendor failure"
