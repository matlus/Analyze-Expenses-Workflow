import argparse
import asyncio
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from analyze_expenses_workflow import DomainFacade, ExpenseAnalysisResult
from analyze_expenses_workflow_app.composers.composer_expense_analysis_report import ComposerExpenseAnalysisReport

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
    return [line for line in source_lines if line.strip()]


def _paths_alias(input_path: Path, output_path: Path) -> bool:
    if input_path.resolve() == output_path.resolve():
        return True
    try:
        return input_path.samefile(output_path)
    except FileNotFoundError:
        return False


async def run(
    input_path: Path, output_path: Path | None = None, confirmed_duplicate_line_numbers: tuple[int, ...] = ()
) -> str:
    if output_path is not None and await asyncio.to_thread(_paths_alias, input_path, output_path):
        raise ValueError("Output path must differ from the input expense file")
    source_text: str = await asyncio.to_thread(input_path.read_text, encoding="utf-8-sig")
    expense_lines: list[str] = _expense_lines_from_text(source_text)
    async with DomainFacade() as facade:
        analysis: ExpenseAnalysisResult = await facade.analyze_expenses(
            expense_lines, confirmed_duplicate_line_numbers=confirmed_duplicate_line_numbers
        )
    report: str = ComposerExpenseAnalysisReport.compose(analysis)
    if output_path is not None:
        await asyncio.to_thread(output_path.write_text, report, encoding="utf-8")
    return report


def main(arguments: Sequence[str] | None = None) -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Analyze an expense log and render an inspectable report.")
    parser.add_argument("--input", type=Path, required=True, help="Path to the expense text file.")
    parser.add_argument("--output", type=Path, help="Write the Markdown report to this path as well as standard output.")
    parser.add_argument(
        "--confirmed-duplicate-line",
        type=int,
        action="append",
        default=[],
        help="Source line number confirmed as a duplicate of the preceding identical line; repeat for multiple lines.",
    )
    options: argparse.Namespace = parser.parse_args(arguments)
    report: str = asyncio.run(run(options.input, options.output, tuple(options.confirmed_duplicate_line)))
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(report)


if __name__ == "__main__":
    main()
