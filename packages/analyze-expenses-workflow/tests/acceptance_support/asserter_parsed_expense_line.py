from collections.abc import Sequence
from dataclasses import fields

from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class ParsedExpenseLineAsserter:
    """Compares arranged parsed lines with the parser's lines, position by position, and reports every mismatched field in one failure."""

    @staticmethod
    def assert_expected_and_actual_are_equal(
        expected_parsed_expense_lines: Sequence[ParsedExpenseLine],
        actual_parsed_expense_lines: Sequence[ParsedExpenseLine],
        business_rule_description: str,
    ) -> None:
        mismatches: list[str] = []
        position: int
        for position in range(max(len(expected_parsed_expense_lines), len(actual_parsed_expense_lines))):
            if position >= len(actual_parsed_expense_lines):
                mismatches.append(f"  Line {expected_parsed_expense_lines[position].line_number}: missing from the result")
            elif position >= len(expected_parsed_expense_lines):
                mismatches.append(f"  Line {actual_parsed_expense_lines[position].line_number}: unexpected {actual_parsed_expense_lines[position]!r}")
            else:
                mismatches.extend(
                    ParsedExpenseLineAsserter._collect_field_mismatches(
                        expected_parsed_expense_lines[position], actual_parsed_expense_lines[position]
                    )
                )
        assert not mismatches, (
            f"\nPARSED EXPENSE LINE ASSERTION FAILED\nMismatches Found: {len(mismatches)}\n\n" + "\n".join(mismatches) + "\n\n"
            f"Business Rule Violated:\n  {business_rule_description}\n"
        )

    @staticmethod
    def _collect_field_mismatches(expected_parsed_expense_line: ParsedExpenseLine, actual_parsed_expense_line: ParsedExpenseLine) -> list[str]:
        return [
            f"  Line {expected_parsed_expense_line.line_number} {expected_parsed_expense_line.source_text!r}: {field.name} expected "
            f"{getattr(expected_parsed_expense_line, field.name)!r}, got {getattr(actual_parsed_expense_line, field.name)!r}"
            for field in fields(ParsedExpenseLine)
            if getattr(expected_parsed_expense_line, field.name) != getattr(actual_parsed_expense_line, field.name)
        ]
