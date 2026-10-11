import argparse
import asyncio
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from analyze_expenses_workflow.domain_facades import DomainFacade, ExpenseAnalysisResult
from analyze_expenses_workflow_app.composers.composer_expense_analysis_report import ComposerExpenseAnalysisReport
from analyze_expenses_workflow_app.exceptions.report_output_location_exception import (
    ReportOutputDirectoryNotFoundException,
    ReportOutputPathIsDirectoryException,
    ReportOutputPathNotWritableException,
)
from analyze_expenses_workflow_app.exceptions.report_output_path_exception import ReportOutputPathAliasesInputException
from analyze_expenses_workflow_app.exceptions.report_output_write_exception import ReportOutputWriteFailedException

_FIXTURE_PREAMBLE: tuple[str, str] = (
    "misc expenses - running list",
    (
        "(started keeping track of this after i realized i had no idea where money was going. "
        "not super consistent about it, some entries are from memory a day or two late. june through august.)"
    ),
)


def _expense_lines_from_text(source_text: str) -> list[str]:
    source_lines: list[str] = source_text.splitlines()
    if tuple(source_lines[:2]) == _FIXTURE_PREAMBLE:
        source_lines = source_lines[2:]
        if source_lines and not source_lines[0].strip():
            source_lines = source_lines[1:]
    return source_lines


def _paths_alias(input_path: Path, output_path: Path) -> bool:
    if input_path.resolve() == output_path.resolve():
        return True
    try:
        return input_path.samefile(output_path)
    except FileNotFoundError:
        return False


def _validate_report_output_path(input_path: Path, output_path: Path) -> None:
    # The analysis is slow and calls paid models, so an unwritable output location is rejected before it starts.
    if _paths_alias(input_path, output_path):
        raise ReportOutputPathAliasesInputException(str(output_path), str(input_path))
    if output_path.is_dir():
        raise ReportOutputPathIsDirectoryException(str(output_path))
    if not output_path.parent.is_dir():
        raise ReportOutputDirectoryNotFoundException(str(output_path), str(output_path.parent))
    _probe_report_output_writable(output_path)


def _probe_report_output_writable(output_path: Path) -> None:
    # Append mode opens an existing report without truncating it; a missing report is created exclusively and removed again.
    output_file_existed: bool = output_path.exists()
    try:
        with output_path.open("a" if output_file_existed else "x", encoding="utf-8"):
            pass
        if not output_file_existed:
            output_path.unlink()
    except OSError as os_error:
        raise ReportOutputPathNotWritableException(str(output_path), _os_reason(os_error)) from os_error


def _os_reason(os_error: OSError) -> str:
    return os_error.strerror or str(os_error)


def _write_standard_output(report: str) -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(report)


def _write_report_output(output_path: Path, report: str) -> None:
    try:
        output_path.write_text(report, encoding="utf-8")
    except OSError as os_error:
        raise ReportOutputWriteFailedException(str(output_path), _os_reason(os_error)) from os_error


async def render_expense_analysis_report(
    input_path: Path, output_path: Path | None = None, confirmed_duplicate_line_numbers: tuple[int, ...] = ()
) -> str:
    if output_path is not None:
        await asyncio.to_thread(_validate_report_output_path, input_path, output_path)
    source_text: str = await asyncio.to_thread(input_path.read_text, encoding="utf-8-sig")
    expense_lines: list[str] = _expense_lines_from_text(source_text)
    async with DomainFacade() as domain_facade:
        expense_analysis_result: ExpenseAnalysisResult = await domain_facade.analyze_expenses(
            expense_lines, confirmed_duplicate_line_numbers=confirmed_duplicate_line_numbers
        )
    report: str = ComposerExpenseAnalysisReport.compose(expense_analysis_result)
    # Standard output receives the paid-for report first, so a late output-file failure cannot discard it.
    _write_standard_output(report)
    if output_path is not None:
        await asyncio.to_thread(_write_report_output, output_path, report)
    return report


def main(arguments: Sequence[str] | None = None) -> None:
    argument_parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Analyze an expense log and render an inspectable report.")
    argument_parser.add_argument("--input", type=Path, required=True, help="Path to the expense text file.")
    argument_parser.add_argument("--output", type=Path, help="Write the Markdown report to this path as well as standard output.")
    argument_parser.add_argument(
        "--confirmed-duplicate-line",
        type=int,
        action="append",
        default=[],
        help="Expense input line number, after any recognized heading, confirmed as a duplicate of the preceding identical line.",
    )
    command_line_namespace: argparse.Namespace = argument_parser.parse_args(arguments)
    asyncio.run(
        render_expense_analysis_report(
            command_line_namespace.input, command_line_namespace.output, tuple(command_line_namespace.confirmed_duplicate_line)
        )
    )


if __name__ == "__main__":
    main()
