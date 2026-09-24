from dataclasses import dataclass
from enum import StrEnum

from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class TransactionTreatment(StrEnum):
    INCLUDED_SPENDING = "included_spending"
    EXCLUDED_DUPLICATE = "excluded_duplicate"
    UNCATEGORIZED_OUTFLOW = "uncategorized_outflow"
    UNCATEGORIZED_CREDIT = "uncategorized_credit"
    UNPARSED = "unparsed"


@dataclass(frozen=True, slots=True)
class ExpenseTransaction:
    parsed_line: ParsedExpenseLine
    categorization: ExpenseCategorization | None
    treatment: TransactionTreatment
    duplicate_of_line_number: int | None
