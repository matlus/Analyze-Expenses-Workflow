from dataclasses import dataclass, fields
from typing import Final

from analyze_expenses_workflow.managers.llm_processors.bases.llm_processor_base import ProcessingEventKind
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


@dataclass(frozen=True, slots=True)
class ExpectedExpenseLineExtraction:
    parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    passed_through_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    prompted_line_numbers: tuple[tuple[int, ...], ...]
    processing_event_kinds: tuple[ProcessingEventKind, ...]


@dataclass(frozen=True, slots=True)
class ActualExpenseLineExtraction:
    parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    prompted_line_numbers: tuple[tuple[int, ...], ...]
    processing_event_kinds: tuple[ProcessingEventKind, ...]


class ExpenseLineExtractionAsserter:
    EXPENSE_LINE_EXTRACTION_ASSERTION_FAILED_HEADER: Final[str] = "\nEXPENSE LINE EXTRACTION ASSERTION FAILED\n"

    @staticmethod
    def assert_expected_and_actual_are_equal(
        expected_expense_line_extraction: ExpectedExpenseLineExtraction,
        actual_expense_line_extraction: ActualExpenseLineExtraction,
        business_rule_description: str,
    ) -> None:
        mismatches: list[str] = [
            *ExpenseLineExtractionAsserter._collect_parsed_expense_line_mismatches(
                expected_expense_line_extraction.parsed_expense_lines, actual_expense_line_extraction.parsed_expense_lines
            ),
            *ExpenseLineExtractionAsserter._collect_passed_through_mismatches(
                expected_expense_line_extraction.passed_through_parsed_expense_lines, actual_expense_line_extraction.parsed_expense_lines
            ),
            *ExpenseLineExtractionAsserter._collect_prompted_line_number_mismatches(
                expected_expense_line_extraction.prompted_line_numbers, actual_expense_line_extraction.prompted_line_numbers
            ),
            *ExpenseLineExtractionAsserter._collect_processing_event_kind_mismatches(
                expected_expense_line_extraction.processing_event_kinds, actual_expense_line_extraction.processing_event_kinds
            ),
        ]
        source_texts: str = "\n".join(
            f"  * Line {parsed_expense_line.line_number}: {parsed_expense_line.source_text!r}"
            for parsed_expense_line in expected_expense_line_extraction.parsed_expense_lines
        )
        assert not mismatches, (
            f"{ExpenseLineExtractionAsserter.EXPENSE_LINE_EXTRACTION_ASSERTION_FAILED_HEADER}"
            f"Mismatches Found: {len(mismatches)}\n\n" + "\n".join(mismatches) + "\n\n"
            f"Business Rule Violated:\n  {business_rule_description}\n\n"
            f"Test Context (source lines):\n{source_texts or '  (none)'}\n"
        )

    @staticmethod
    def _collect_parsed_expense_line_mismatches(
        expected_parsed_expense_lines: tuple[ParsedExpenseLine, ...], actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    ) -> list[str]:
        mismatches: list[str] = []
        position: int
        for position in range(max(len(expected_parsed_expense_lines), len(actual_parsed_expense_lines))):
            if position >= len(actual_parsed_expense_lines):
                mismatches.append(
                    f"  Position {position + 1}: expected line {expected_parsed_expense_lines[position].line_number} is missing from the result"
                )
                continue
            if position >= len(expected_parsed_expense_lines):
                mismatches.append(f"  Position {position + 1}: unexpected line {actual_parsed_expense_lines[position]!r}")
                continue
            mismatches.extend(
                ExpenseLineExtractionAsserter._collect_parsed_expense_line_field_mismatches(
                    position, expected_parsed_expense_lines[position], actual_parsed_expense_lines[position]
                )
            )
        return mismatches

    @staticmethod
    def _collect_parsed_expense_line_field_mismatches(
        position: int, expected_parsed_expense_line: ParsedExpenseLine, actual_parsed_expense_line: ParsedExpenseLine
    ) -> list[str]:
        return [
            f"  Position {position + 1} (line {expected_parsed_expense_line.line_number}) {field.name}:\n"
            f"    Expected: {getattr(expected_parsed_expense_line, field.name)!r}\n"
            f"    Actual:   {getattr(actual_parsed_expense_line, field.name)!r}"
            for field in fields(ParsedExpenseLine)
            if getattr(actual_parsed_expense_line, field.name) != getattr(expected_parsed_expense_line, field.name)
        ]

    @staticmethod
    def _collect_passed_through_mismatches(
        expected_passed_through_parsed_expense_lines: tuple[ParsedExpenseLine, ...], actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...]
    ) -> list[str]:
        return [
            f"  Line {expected_parsed_expense_line.line_number} needed no extraction but was not passed through as the supplied line"
            for expected_parsed_expense_line in expected_passed_through_parsed_expense_lines
            if not any(actual_parsed_expense_line is expected_parsed_expense_line for actual_parsed_expense_line in actual_parsed_expense_lines)
        ]

    @staticmethod
    def _collect_prompted_line_number_mismatches(
        expected_prompted_line_numbers: tuple[tuple[int, ...], ...], actual_prompted_line_numbers: tuple[tuple[int, ...], ...]
    ) -> list[str]:
        if actual_prompted_line_numbers == expected_prompted_line_numbers:
            return []
        prompted_line_number_mismatch: str = (
            f"  Line numbers sent in each LLM request ({len(expected_prompted_line_numbers)} expected, "
            f"{len(actual_prompted_line_numbers)} made):\n"
            f"    Expected: {expected_prompted_line_numbers!r}\n"
            f"    Actual:   {actual_prompted_line_numbers!r}"
        )
        return [prompted_line_number_mismatch]

    @staticmethod
    def _collect_processing_event_kind_mismatches(
        expected_processing_event_kinds: tuple[ProcessingEventKind, ...], actual_processing_event_kinds: tuple[ProcessingEventKind, ...]
    ) -> list[str]:
        if actual_processing_event_kinds == expected_processing_event_kinds:
            return []
        return [f"  Published processing events:\n    Expected: {expected_processing_event_kinds!r}\n    Actual:   {actual_processing_event_kinds!r}"]
