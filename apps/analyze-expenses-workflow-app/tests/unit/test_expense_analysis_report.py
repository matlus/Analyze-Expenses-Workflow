import asyncio
import errno
import os
import secrets
import stat
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

import analyze_expenses_workflow_app.main as app_main
from analyze_expenses_workflow.domain_facades import (
    BudgetComparison,
    CalculationReconciliation,
    ExpenseAnalysisResult,
    ExpenseCalculationResult,
    ExpenseCategorization,
    ExpenseCategory,
    ExpenseFinding,
    ExpenseTransaction,
    MonthlyCategoryTotal,
    MonthlyTotal,
    ParsedExpenseLine,
    TransactionTreatment,
)
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesBaseException,
    ExceptionAction,
    ExceptionCause,
    ExpenseLogEvent,
    Severity,
)
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow_app.composers.composer_expense_analysis_report import ComposerExpenseAnalysisReport
from analyze_expenses_workflow_app.exceptions.report_output_location_exception import (
    ReportOutputDirectoryNotFoundException,
    ReportOutputPathIsDirectoryException,
    ReportOutputPathNotWritableException,
)
from analyze_expenses_workflow_app.exceptions.report_output_path_exception import ReportOutputPathAliasesInputException
from analyze_expenses_workflow_app.exceptions.report_output_write_exception import ReportOutputWriteFailedException


def _analysis_result() -> ExpenseAnalysisResult:
    merchant_name: str = secrets.token_hex(8)
    finding_texts: tuple[str, str, str] = (
        f"{secrets.token_hex(8)} cites line 1.",
        f"{secrets.token_hex(8)} cites line 1.",
        f"{secrets.token_hex(8)} cites line 1.",
    )
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(
        line_number=1,
        source_text=f"6/1/26 | {merchant_name} | $82.18",
        occurred_on=date(2026, 6, 1),
        description=merchant_name,
        amount=Decimal("82.18"),
        issues=(),
    )
    expense_categorization: ExpenseCategorization = ExpenseCategorization(
        source_line_number=1,
        source_text=parsed_expense_line.source_text,
        category=ExpenseCategory.GROCERIES,
        model_category=ExpenseCategory.GROCERIES,
        policy_note=None,
        confidence=0.91,
        probabilities=(),
    )
    expense_transaction: ExpenseTransaction = ExpenseTransaction(
        parsed_line=parsed_expense_line,
        categorization=expense_categorization,
        treatment=TransactionTreatment.INCLUDED_SPENDING,
        duplicate_of_line_number=None,
    )
    expense_calculation_result: ExpenseCalculationResult = ExpenseCalculationResult(
        monthly_category_totals=(MonthlyCategoryTotal(date(2026, 6, 1), ExpenseCategory.GROCERIES, Decimal("82.18"), (1,)),),
        monthly_totals=(MonthlyTotal(date(2026, 6, 1), Decimal("82.18"), Decimal(0), Decimal("82.18"), (1,)),),
        budget_comparisons=(
            BudgetComparison(date(2026, 6, 1), ExpenseCategory.GROCERIES, Decimal(500), Decimal("82.18"), Decimal("-417.82"), (1,), None),
        ),
        month_category_deltas=(),
        reconciliation=CalculationReconciliation(1, (1,), (), (), (), Decimal("82.18"), Decimal(0), Decimal("82.18"), True),
    )
    return ExpenseAnalysisResult(
        categorizations=(expense_categorization,),
        parsed_lines=(parsed_expense_line,),
        transactions=(expense_transaction,),
        calculations=expense_calculation_result,
        findings=(
            ExpenseFinding(secrets.token_hex(8), finding_texts[0], (1,)),
            ExpenseFinding(secrets.token_hex(8), finding_texts[1], (1,)),
            ExpenseFinding(secrets.token_hex(8), finding_texts[2], (1,)),
        ),
        method=secrets.token_hex(16),
    )


class _DomainFacadeSpy:
    def __init__(self) -> None:
        self.analyze_expenses_call_count: int = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        return None

    async def analyze_expenses(self, expense_lines: list[str], *, confirmed_duplicate_line_numbers: tuple[int, ...] = ()) -> ExpenseAnalysisResult:
        self.analyze_expenses_call_count += 1
        return _analysis_result()


def test_ExpenseFixture_WhenOriginalFileIsRead_ThenSuppliesAll198TransactionLines() -> None:
    expected_expense_line_count: int = 198
    expected_first_prefix: str = "- June 1"
    expected_last_prefix: str = "8/31 | Riverside"
    fixture_path: Path = (
        Path(__file__).resolve().parents[4] / "packages" / "analyze-expenses-workflow" / "tests" / "fixtures" / "personal_expenses.txt"
    )
    expense_lines: list[str] = app_main._expense_lines_from_text(fixture_path.read_text(encoding="utf-8"))
    _assert_expense_line_boundaries(expense_lines, expected_expense_line_count, expected_first_prefix, expected_last_prefix)


@pytest.mark.asyncio
async def test_ConsoleApp_WhenExpenseFileIsRead_ThenForwardsLinesAndRendersReport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text_encoding: str = "utf-8"
    input_path: Path = tmp_path / "expenses.txt"
    output_path: Path = tmp_path / "report.md"
    source_line: str = f"6/1/26 | {secrets.token_hex(8)} | $82.18"
    expected_lines: list[str] = [source_line]
    expected_confirmed_duplicate_line_numbers: list[tuple[int, ...]] = [()]
    expense_analysis_result: ExpenseAnalysisResult = _analysis_result()
    expected_headings: tuple[str, ...] = (
        "## Transaction ledger",
        "## Monthly spending by expense category",
        "## Monthly totals",
        "## Budget comparisons",
        "## Month-to-month changes",
        "## Quality and reconciliation",
        "## Method and calculation formulas",
        "## Three evidence-based findings",
    )
    expected_fragments: tuple[str, ...] = (
        "| June 2026 | $82.18 | $0.00 | $82.18 | 1 |",
        "| June 2026 | Groceries | $500.00 | $82.18 | -$417.82 | 1 | — | — |",
        "Observed outflow = classified spending + unresolved outflow",
        f"{expense_analysis_result.findings[0].text} (Source lines: 1.)",
    )
    await asyncio.to_thread(input_path.write_text, source_line + "\n", encoding=text_encoding)
    actual_lines: list[str] = []
    actual_confirmed_duplicate_line_numbers: list[tuple[int, ...]] = []

    class FakeDomainFacade:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
            return None

        async def analyze_expenses(
            self, expense_lines: list[str], *, confirmed_duplicate_line_numbers: tuple[int, ...] = ()
        ) -> ExpenseAnalysisResult:
            actual_lines.extend(expense_lines)
            actual_confirmed_duplicate_line_numbers.append(confirmed_duplicate_line_numbers)
            return expense_analysis_result

    monkeypatch.setattr(app_main, "DomainFacade", FakeDomainFacade)
    report: str = await app_main.render_expense_analysis_report(input_path, output_path)

    _assert_forwarded_analysis_request(
        actual_lines, actual_confirmed_duplicate_line_numbers, expected_lines, expected_confirmed_duplicate_line_numbers
    )
    assert await asyncio.to_thread(output_path.read_text, encoding=text_encoding) == report
    _assert_report_contents(report, expected_fragments, expected_headings)
    _assert_finding_occurrences(report, expense_analysis_result.findings)


@pytest.mark.asyncio
async def test_ConsoleApp_WhenOutputAliasesInput_ThenRejectsOverwrite(tmp_path: Path) -> None:
    text_encoding: str = "utf-8"
    input_path: Path = tmp_path / "expenses.txt"
    source_text: str = f"6/1/26 | {secrets.token_hex(8)} | $82.18\n"
    expected_message_phrase: str = "Output path must differ"
    expected_reason: str = "Report output path refers to the input expense file"
    expected_action: ExceptionAction = ExceptionAction.USER_ACTION_REQUIRED
    expected_http_status_code: int = 400
    expected_severity: Severity = Severity.ERROR
    expected_log_event: ExpenseLogEvent = ExpenseLogEvent.INPUT_VALIDATION
    expected_contextual_field_value_by_name: dict[str, object] = {"OutputPath": str(input_path), "InputPath": str(input_path)}
    await asyncio.to_thread(input_path.write_text, source_text, encoding=text_encoding)

    with pytest.raises(ReportOutputPathAliasesInputException) as raised_report_output_path_aliases_input_exception:
        await app_main.render_expense_analysis_report(input_path, input_path)

    _assert_report_output_exception(
        raised_report_output_path_aliases_input_exception.value,
        expected_message_phrase=expected_message_phrase,
        expected_reason=expected_reason,
        expected_action=expected_action,
        expected_http_status_code=expected_http_status_code,
        expected_severity=expected_severity,
        expected_log_event=expected_log_event,
        expected_contextual_field_value_by_name=expected_contextual_field_value_by_name,
    )
    assert await asyncio.to_thread(input_path.read_text, encoding=text_encoding) == source_text


@pytest.mark.asyncio
async def test_ConsoleApp_WhenOutputIsHardLinkToInput_ThenRejectsOverwrite(tmp_path: Path) -> None:
    text_encoding: str = "utf-8"
    input_path: Path = tmp_path / "expenses.txt"
    output_path: Path = tmp_path / "linked-report.md"
    source_text: str = f"6/1/26 | {secrets.token_hex(8)} | $82.18\n"
    expected_message_phrase: str = "Output path must differ"
    expected_reason: str = "Report output path refers to the input expense file"
    expected_action: ExceptionAction = ExceptionAction.USER_ACTION_REQUIRED
    expected_http_status_code: int = 400
    expected_severity: Severity = Severity.ERROR
    expected_log_event: ExpenseLogEvent = ExpenseLogEvent.INPUT_VALIDATION
    expected_contextual_field_value_by_name: dict[str, object] = {"OutputPath": str(output_path), "InputPath": str(input_path)}
    await asyncio.to_thread(input_path.write_text, source_text, encoding=text_encoding)
    await asyncio.to_thread(os.link, input_path, output_path)

    with pytest.raises(ReportOutputPathAliasesInputException) as raised_report_output_path_aliases_input_exception:
        await app_main.render_expense_analysis_report(input_path, output_path)

    _assert_report_output_exception(
        raised_report_output_path_aliases_input_exception.value,
        expected_message_phrase=expected_message_phrase,
        expected_reason=expected_reason,
        expected_action=expected_action,
        expected_http_status_code=expected_http_status_code,
        expected_severity=expected_severity,
        expected_log_event=expected_log_event,
        expected_contextual_field_value_by_name=expected_contextual_field_value_by_name,
    )
    assert await asyncio.to_thread(input_path.read_text, encoding=text_encoding) == source_text


@pytest.mark.asyncio
async def test_ConsoleApp_WhenOutputDirectoryIsMissing_ThenRejectsBeforeAnalysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path: Path = tmp_path / "expenses.txt"
    output_directory: Path = tmp_path / secrets.token_hex(8)
    output_path: Path = output_directory / "report.md"
    expected_message_phrase: str = "Output directory must exist"
    expected_reason: str = "Report output directory does not exist"
    expected_action: ExceptionAction = ExceptionAction.USER_ACTION_REQUIRED
    expected_http_status_code: int = 400
    expected_severity: Severity = Severity.ERROR
    expected_log_event: ExpenseLogEvent = ExpenseLogEvent.INPUT_VALIDATION
    expected_contextual_field_value_by_name: dict[str, object] = {"OutputPath": str(output_path), "OutputDirectory": str(output_directory)}
    expected_analyze_expenses_call_count: int = 0
    await asyncio.to_thread(input_path.write_text, f"6/1/26 | {secrets.token_hex(8)} | $82.18\n", encoding="utf-8")
    domain_facade_spy: _DomainFacadeSpy = _DomainFacadeSpy()
    monkeypatch.setattr(app_main, "DomainFacade", lambda: domain_facade_spy)

    with pytest.raises(ReportOutputDirectoryNotFoundException) as raised_report_output_directory_not_found_exception:
        await app_main.render_expense_analysis_report(input_path, output_path)

    _assert_report_output_exception(
        raised_report_output_directory_not_found_exception.value,
        expected_message_phrase=expected_message_phrase,
        expected_reason=expected_reason,
        expected_action=expected_action,
        expected_http_status_code=expected_http_status_code,
        expected_severity=expected_severity,
        expected_log_event=expected_log_event,
        expected_contextual_field_value_by_name=expected_contextual_field_value_by_name,
    )
    assert domain_facade_spy.analyze_expenses_call_count == expected_analyze_expenses_call_count


@pytest.mark.asyncio
async def test_ConsoleApp_WhenOutputPathIsDirectory_ThenRejectsBeforeAnalysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path: Path = tmp_path / "expenses.txt"
    output_path: Path = tmp_path / secrets.token_hex(8)
    expected_message_phrase: str = "Output path must name a file, not a directory"
    expected_reason: str = "Report output path is a directory"
    expected_action: ExceptionAction = ExceptionAction.USER_ACTION_REQUIRED
    expected_http_status_code: int = 400
    expected_severity: Severity = Severity.ERROR
    expected_log_event: ExpenseLogEvent = ExpenseLogEvent.INPUT_VALIDATION
    expected_contextual_field_value_by_name: dict[str, object] = {"OutputPath": str(output_path)}
    expected_analyze_expenses_call_count: int = 0
    await asyncio.to_thread(input_path.write_text, f"6/1/26 | {secrets.token_hex(8)} | $82.18\n", encoding="utf-8")
    await asyncio.to_thread(output_path.mkdir)
    domain_facade_spy: _DomainFacadeSpy = _DomainFacadeSpy()
    monkeypatch.setattr(app_main, "DomainFacade", lambda: domain_facade_spy)

    with pytest.raises(ReportOutputPathIsDirectoryException) as raised_report_output_path_is_directory_exception:
        await app_main.render_expense_analysis_report(input_path, output_path)

    _assert_report_output_exception(
        raised_report_output_path_is_directory_exception.value,
        expected_message_phrase=expected_message_phrase,
        expected_reason=expected_reason,
        expected_action=expected_action,
        expected_http_status_code=expected_http_status_code,
        expected_severity=expected_severity,
        expected_log_event=expected_log_event,
        expected_contextual_field_value_by_name=expected_contextual_field_value_by_name,
    )
    assert domain_facade_spy.analyze_expenses_call_count == expected_analyze_expenses_call_count


@pytest.mark.asyncio
async def test_ConsoleApp_WhenOutputFileIsReadOnly_ThenRejectsBeforeAnalysisWithoutTruncating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text_encoding: str = "utf-8"
    input_path: Path = tmp_path / "expenses.txt"
    output_path: Path = tmp_path / "report.md"
    existing_report_text: str = secrets.token_hex(16)
    expected_message_phrase: str = "Output path must be writable"
    expected_reason: str = "Report output path is not writable"
    expected_action: ExceptionAction = ExceptionAction.USER_ACTION_REQUIRED
    expected_http_status_code: int = 400
    expected_severity: Severity = Severity.ERROR
    expected_log_event: ExpenseLogEvent = ExpenseLogEvent.INPUT_VALIDATION
    expected_contextual_field_value_by_name: dict[str, object] = {"OutputPath": str(output_path), "OsReason": os.strerror(errno.EACCES)}
    expected_cause_exception_type_name: str = PermissionError.__name__
    expected_run_state: tuple[int, str] = (0, existing_report_text)
    await asyncio.to_thread(input_path.write_text, f"6/1/26 | {secrets.token_hex(8)} | $82.18\n", encoding=text_encoding)
    await asyncio.to_thread(output_path.write_text, existing_report_text, encoding=text_encoding)
    await asyncio.to_thread(os.chmod, output_path, stat.S_IREAD)
    domain_facade_spy: _DomainFacadeSpy = _DomainFacadeSpy()
    monkeypatch.setattr(app_main, "DomainFacade", lambda: domain_facade_spy)

    with pytest.raises(ReportOutputPathNotWritableException) as raised_report_output_path_not_writable_exception:
        await app_main.render_expense_analysis_report(input_path, output_path)

    _assert_report_output_exception(
        raised_report_output_path_not_writable_exception.value,
        expected_message_phrase=expected_message_phrase,
        expected_reason=expected_reason,
        expected_action=expected_action,
        expected_http_status_code=expected_http_status_code,
        expected_severity=expected_severity,
        expected_log_event=expected_log_event,
        expected_contextual_field_value_by_name=expected_contextual_field_value_by_name,
        expected_cause_exception_type_name=expected_cause_exception_type_name,
    )
    actual_run_state: tuple[int, str] = (
        domain_facade_spy.analyze_expenses_call_count,
        await asyncio.to_thread(output_path.read_text, encoding=text_encoding),
    )
    assert actual_run_state == expected_run_state


@pytest.mark.asyncio
async def test_ConsoleApp_WhenOutputFileBecomesReadOnlyDuringAnalysis_ThenWritesReportToStandardOutputAndRaises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path: Path = tmp_path / "expenses.txt"
    output_path: Path = tmp_path / "report.md"
    expense_analysis_result: ExpenseAnalysisResult = _analysis_result()
    expected_report: str = ComposerExpenseAnalysisReport.compose(expense_analysis_result)
    expected_message_phrase: str = "the report was written to standard output"
    expected_reason: str = "Report output file write failed after the analysis completed"
    expected_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    expected_http_status_code: int = 500
    expected_severity: Severity = Severity.ERROR
    expected_log_event: ExpenseLogEvent = ExpenseLogEvent.REPORT_OUTPUT
    expected_contextual_field_value_by_name: dict[str, object] = {"OutputPath": str(output_path), "OsReason": os.strerror(errno.EACCES)}
    expected_cause_exception_type_name: str = PermissionError.__name__
    await asyncio.to_thread(input_path.write_text, f"6/1/26 | {secrets.token_hex(8)} | $82.18\n", encoding="utf-8")

    class OutputLockingDomainFacade:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
            return None

        async def analyze_expenses(
            self, expense_lines: list[str], *, confirmed_duplicate_line_numbers: tuple[int, ...] = ()
        ) -> ExpenseAnalysisResult:
            await asyncio.to_thread(output_path.write_text, "", encoding="utf-8")
            await asyncio.to_thread(os.chmod, output_path, stat.S_IREAD)
            return expense_analysis_result

    monkeypatch.setattr(app_main, "DomainFacade", OutputLockingDomainFacade)

    with pytest.raises(ReportOutputWriteFailedException) as raised_report_output_write_failed_exception:
        await app_main.render_expense_analysis_report(input_path, output_path)

    _assert_report_output_exception(
        raised_report_output_write_failed_exception.value,
        expected_message_phrase=expected_message_phrase,
        expected_reason=expected_reason,
        expected_action=expected_action,
        expected_http_status_code=expected_http_status_code,
        expected_severity=expected_severity,
        expected_log_event=expected_log_event,
        expected_contextual_field_value_by_name=expected_contextual_field_value_by_name,
        expected_cause_exception_type_name=expected_cause_exception_type_name,
    )
    assert capsys.readouterr().out == expected_report


def test_ReportOutputExceptionAsserter_WhenOnlySeverityMismatches_ThenFailureNamesExceptionTypeAndReason(tmp_path: Path) -> None:
    output_path: Path = tmp_path / "report.md"
    report_output_path_is_directory_exception: ReportOutputPathIsDirectoryException = ReportOutputPathIsDirectoryException(str(output_path))
    expected_reason: str = "Report output path is a directory"
    expected_failure_fragments: tuple[str, ...] = (
        f"{ReportOutputPathIsDirectoryException.__name__} for {expected_reason!r} has 1 report-output exception mismatches",
        "Severity: expected CRITICAL, actual ERROR",
    )

    with pytest.raises(AssertionError) as raised_assertion_error:
        _assert_report_output_exception(
            report_output_path_is_directory_exception,
            expected_message_phrase="Output path must name a file",
            expected_reason=expected_reason,
            expected_action=ExceptionAction.USER_ACTION_REQUIRED,
            expected_http_status_code=400,
            expected_severity=Severity.CRITICAL,
            expected_log_event=ExpenseLogEvent.INPUT_VALIDATION,
            expected_contextual_field_value_by_name={"OutputPath": str(output_path)},
        )

    _assert_report_contents(str(raised_assertion_error.value), expected_failure_fragments)


def test_ForwardedRequestAsserter_WhenLinesAndConfirmedDuplicatesBothMismatch_ThenFailureNamesBoth() -> None:
    expected_expense_lines: list[str] = [secrets.token_hex(8)]
    actual_expense_lines: list[str] = [secrets.token_hex(8)]
    expected_confirmed_duplicate_line_numbers: list[tuple[int, ...]] = [()]
    actual_confirmed_duplicate_line_numbers: list[tuple[int, ...]] = [(2,)]
    expected_failure_fragments: tuple[str, ...] = (
        "2 forwarded analysis-request mismatches",
        f"Forwarded lines: expected {expected_expense_lines!r}, actual {actual_expense_lines!r}",
        (
            f"Confirmed duplicate line numbers: expected {expected_confirmed_duplicate_line_numbers!r}, "
            f"actual {actual_confirmed_duplicate_line_numbers!r}"
        ),
    )

    with pytest.raises(AssertionError) as raised_assertion_error:
        _assert_forwarded_analysis_request(
            actual_expense_lines, actual_confirmed_duplicate_line_numbers, expected_expense_lines, expected_confirmed_duplicate_line_numbers
        )

    _assert_report_contents(str(raised_assertion_error.value), expected_failure_fragments)


def test_MarkdownTable_WhenCellContainsBackslashBeforePipe_ThenEscapesBoth() -> None:
    source_cell: str = r"merchant\|memo"
    expected_cell: str = r"merchant\\\|memo"

    actual_cell: str = ComposerExpenseAnalysisReport._cell(source_cell)

    assert actual_cell == expected_cell


def test_ExpenseReport_WhenRefundAndShoppingCoverageExist_ThenShowsAuditSources() -> None:
    expense_analysis_result: ExpenseAnalysisResult = _analysis_result()
    coverage_note: str = secrets.token_hex(8)
    shopping_finding_text: str = secrets.token_hex(8)
    budget_comparison: BudgetComparison = replace(
        expense_analysis_result.calculations.budget_comparisons[0],
        category=ExpenseCategory.SHOPPING,
        coverage_note=coverage_note,
        coverage_source_line_numbers=(2, 3),
    )
    calculation_reconciliation: CalculationReconciliation = replace(
        expense_analysis_result.calculations.reconciliation,
        refund_line_numbers=(4,),
        unresolved_credit_line_numbers=(5,),
    )
    expense_calculation_result: ExpenseCalculationResult = replace(
        expense_analysis_result.calculations,
        budget_comparisons=(budget_comparison,),
        reconciliation=calculation_reconciliation,
    )
    expense_findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = (
        expense_analysis_result.findings[0],
        expense_analysis_result.findings[1],
        ExpenseFinding(secrets.token_hex(8), shopping_finding_text, (1, 2, 3)),
    )
    expected_fragments: tuple[str, ...] = (
        "| Refund or negative adjustment lines | 4 |",
        "| Unresolved credit lines | 5 |",
        f"| June 2026 | Shopping | $500.00 | $82.18 | -$417.82 | 1 | {coverage_note} | 2, 3 |",
        f"{shopping_finding_text} (Source lines: 1, 2, 3.)",
    )

    report: str = ComposerExpenseAnalysisReport.compose(
        replace(expense_analysis_result, calculations=expense_calculation_result, findings=expense_findings)
    )

    _assert_report_contents(report, expected_fragments)


def test_ExpenseReport_WhenSourceLineIsUnparsed_ThenReportsLineAndContinuesAnalysis() -> None:
    expense_analysis_result: ExpenseAnalysisResult = _analysis_result()
    unparsed_source_text: str = secrets.token_hex(8)
    unparsed_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(2, unparsed_source_text, None, None, None, ("missing_date", "missing_amount"))
    expense_transaction: ExpenseTransaction = ExpenseTransaction(unparsed_parsed_expense_line, None, TransactionTreatment.UNPARSED, None)
    calculation_reconciliation: CalculationReconciliation = replace(
        expense_analysis_result.calculations.reconciliation, source_line_count=2, unparsed_line_numbers=(2,)
    )
    expense_calculation_result: ExpenseCalculationResult = replace(expense_analysis_result.calculations, reconciliation=calculation_reconciliation)
    expected_fragments: tuple[str, ...] = (
        "Unable to parse source lines 2.",
        f"| 2 | {unparsed_source_text} | — | — | — | — | unparsed | missing_date, missing_amount |",
        "| 2 | missing_date, missing_amount |",
        "| June 2026 | $82.18 | $0.00 | $82.18 | 1 |",
    )
    report: str = ComposerExpenseAnalysisReport.compose(
        replace(
            expense_analysis_result,
            parsed_lines=(*expense_analysis_result.parsed_lines, unparsed_parsed_expense_line),
            transactions=(*expense_analysis_result.transactions, expense_transaction),
            calculations=expense_calculation_result,
        )
    )

    _assert_report_contents(report, expected_fragments)


def test_ExpenseInput_WhenPreambleIsUnknown_ThenPreservesItForValidation() -> None:
    unknown_preamble: str = secrets.token_hex(8)
    expense_line: str = f"6/1/26 | {secrets.token_hex(8)} | $82.18"
    source_text: str = f"{unknown_preamble}\n\n{expense_line}\n"
    expected_lines: list[str] = [unknown_preamble, "", expense_line]

    actual_lines: list[str] = app_main._expense_lines_from_text(source_text)

    assert actual_lines == expected_lines


async def test_ExpenseInput_WhenBlankLineSeparatesExpenses_ThenPreservesSourceLinePositions() -> None:
    first_expense_line: str = f"6/1/26 | {secrets.token_hex(8)} | $82.18"
    second_expense_line: str = f"6/2/26 | {secrets.token_hex(8)} | $12.00"
    source_text: str = f"{first_expense_line}\n\n{second_expense_line}\n"
    expected_lines: list[str] = [first_expense_line, "", second_expense_line]
    expected_second_expense_line_number: int = 3
    expected_result: tuple[list[str], int, str] = (expected_lines, expected_second_expense_line_number, second_expense_line)

    actual_lines: list[str] = app_main._expense_lines_from_text(source_text)
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(actual_lines)

    assert (actual_lines, parsed_expense_lines[2].line_number, parsed_expense_lines[2].source_text) == expected_result


def _assert_expense_line_boundaries(
    actual_expense_lines: Sequence[str], expected_expense_line_count: int, expected_first_prefix: str, expected_last_prefix: str
) -> None:
    failures: list[str] = []
    if len(actual_expense_lines) != expected_expense_line_count:
        failures.append(f"Expected {expected_expense_line_count} lines, got {len(actual_expense_lines)}")
    if not actual_expense_lines or not actual_expense_lines[0].startswith(expected_first_prefix):
        actual_first_line: str | None = actual_expense_lines[0] if actual_expense_lines else None
        failures.append(f"First line: expected prefix {expected_first_prefix!r}, actual {actual_first_line!r}")
    if not actual_expense_lines or not actual_expense_lines[-1].startswith(expected_last_prefix):
        actual_last_line: str | None = actual_expense_lines[-1] if actual_expense_lines else None
        failures.append(f"Last line: expected prefix {expected_last_prefix!r}, actual {actual_last_line!r}")
    assert not failures, f"{len(failures)} expense-line boundary mismatches:\n" + "\n".join(failures)


def _assert_forwarded_analysis_request(
    actual_expense_lines: Sequence[str],
    actual_confirmed_duplicate_line_numbers: Sequence[tuple[int, ...]],
    expected_expense_lines: Sequence[str],
    expected_confirmed_duplicate_line_numbers: Sequence[tuple[int, ...]],
) -> None:
    failures: list[str] = []
    if list(actual_expense_lines) != list(expected_expense_lines):
        failures.append(f"Forwarded lines: expected {expected_expense_lines!r}, actual {actual_expense_lines!r}")
    if list(actual_confirmed_duplicate_line_numbers) != list(expected_confirmed_duplicate_line_numbers):
        failures.append(
            f"Confirmed duplicate line numbers: expected {expected_confirmed_duplicate_line_numbers!r}, "
            f"actual {actual_confirmed_duplicate_line_numbers!r}"
        )
    assert not failures, f"{len(failures)} forwarded analysis-request mismatches:\n" + "\n".join(failures)


def _assert_report_output_exception(
    actual_analyze_expenses_base_exception: AnalyzeExpensesBaseException,
    *,
    expected_message_phrase: str,
    expected_reason: str,
    expected_action: ExceptionAction,
    expected_http_status_code: int,
    expected_severity: Severity,
    expected_log_event: ExpenseLogEvent,
    expected_contextual_field_value_by_name: dict[str, object],
    expected_cause_exception_type_name: str | None = None,
) -> None:
    failures: list[str] = []
    if expected_message_phrase not in actual_analyze_expenses_base_exception.message:
        failures.append(f"Message: expected phrase {expected_message_phrase!r}, actual {actual_analyze_expenses_base_exception.message!r}")
    if actual_analyze_expenses_base_exception.reason != expected_reason:
        failures.append(f"Reason: expected {expected_reason!r}, actual {actual_analyze_expenses_base_exception.reason!r}")
    if actual_analyze_expenses_base_exception.action != expected_action:
        failures.append(f"Action: expected {expected_action.value}, actual {actual_analyze_expenses_base_exception.action.value}")
    if actual_analyze_expenses_base_exception.http_status_code != expected_http_status_code:
        failures.append(f"HTTP status: expected {expected_http_status_code}, actual {actual_analyze_expenses_base_exception.http_status_code}")
    if actual_analyze_expenses_base_exception.severity != expected_severity:
        failures.append(f"Severity: expected {expected_severity.name}, actual {actual_analyze_expenses_base_exception.severity.name}")
    if actual_analyze_expenses_base_exception.log_event != expected_log_event:
        failures.append(f"Log event: expected {expected_log_event.value}, actual {actual_analyze_expenses_base_exception.log_event.value}")
    actual_contextual_field_value_by_name: dict[str, object] = dict(actual_analyze_expenses_base_exception.diagnostics.additional_fields)
    if actual_contextual_field_value_by_name != expected_contextual_field_value_by_name:
        failures.append(f"Context: expected {expected_contextual_field_value_by_name!r}, actual {actual_contextual_field_value_by_name!r}")
    actual_exception_cause: ExceptionCause | None = actual_analyze_expenses_base_exception.cause
    actual_cause_exception_type_name: str | None = actual_exception_cause.exception_type if actual_exception_cause is not None else None
    if actual_cause_exception_type_name != expected_cause_exception_type_name:
        failures.append(f"Cause: expected {expected_cause_exception_type_name!r}, actual {actual_cause_exception_type_name!r}")
    assert not failures, (
        f"{type(actual_analyze_expenses_base_exception).__name__} for {expected_reason!r} has {len(failures)} report-output exception mismatches:\n"
        + "\n".join(failures)
    )


def _assert_report_contents(actual_report: str, expected_fragments: tuple[str, ...], expected_headings: tuple[str, ...] = ()) -> None:
    failures: list[str] = [f"Missing report text: {fragment!r}" for fragment in expected_fragments if fragment not in actual_report]
    heading_positions: list[int] = [actual_report.find(heading) for heading in expected_headings]
    if heading_positions != sorted(heading_positions) or any(position < 0 for position in heading_positions):
        failures.append(f"Report headings missing or out of order: expected {expected_headings}, actual positions {heading_positions}")
    assert not failures, f"{len(failures)} report-content mismatches:\n" + "\n".join(failures)


def _assert_finding_occurrences(actual_report: str, expected_expense_findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding]) -> None:
    failures: list[str] = [
        f"Finding {expense_finding.evidence_id!r}: expected 1 occurrence, actual {actual_report.count(expense_finding.text)}"
        for expense_finding in expected_expense_findings
        if actual_report.count(expense_finding.text) != 1
    ]
    assert not failures, f"{len(failures)} finding-occurrence mismatches:\n" + "\n".join(failures)
