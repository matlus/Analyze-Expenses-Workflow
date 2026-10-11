from analyze_expenses_workflow.models.expense_analysis_result import ExpenseCategorization


class ExpenseCategorizationAsserter:
    """Compares an arranged categorization with the processor's categorization and reports every mismatched property in one failure."""

    @staticmethod
    def assert_expected_and_actual_are_equal(
        expected_expense_categorization: ExpenseCategorization,
        actual_expense_categorization: ExpenseCategorization,
        business_rule_description: str,
    ) -> None:
        failure_report: str | None = ExpenseCategorizationAsserter.get_expense_categorization_failure(
            expected_expense_categorization, actual_expense_categorization
        )
        assert failure_report is None, f"{failure_report}\nBusiness rule: {business_rule_description}"

    @staticmethod
    def get_expense_categorization_failure(
        expected_expense_categorization: ExpenseCategorization, actual_expense_categorization: ExpenseCategorization
    ) -> str | None:
        mismatches: list[str] = []
        if actual_expense_categorization.source_line_number != expected_expense_categorization.source_line_number:
            mismatches.append(
                f"Source line number: expected {expected_expense_categorization.source_line_number}, "
                f"got {actual_expense_categorization.source_line_number}"
            )
        if actual_expense_categorization.source_text != expected_expense_categorization.source_text:
            mismatches.append(
                f"Source text: expected {expected_expense_categorization.source_text!r}, got {actual_expense_categorization.source_text!r}"
            )
        if actual_expense_categorization.category is not expected_expense_categorization.category:
            mismatches.append(f"Category: expected {expected_expense_categorization.category}, got {actual_expense_categorization.category}")
        if actual_expense_categorization.model_category is not expected_expense_categorization.model_category:
            mismatches.append(
                f"Model category: expected {expected_expense_categorization.model_category}, got {actual_expense_categorization.model_category}"
            )
        if actual_expense_categorization.policy_note != expected_expense_categorization.policy_note:
            mismatches.append(
                f"Policy note: expected {expected_expense_categorization.policy_note!r}, got {actual_expense_categorization.policy_note!r}"
            )
        if actual_expense_categorization.confidence != expected_expense_categorization.confidence:
            mismatches.append(f"Confidence: expected {expected_expense_categorization.confidence}, got {actual_expense_categorization.confidence}")
        if actual_expense_categorization.probabilities != expected_expense_categorization.probabilities:
            mismatches.append(
                f"Probabilities: expected {expected_expense_categorization.probabilities!r}, got {actual_expense_categorization.probabilities!r}"
            )
        if not mismatches:
            return None
        return (
            f"Categorization of line {expected_expense_categorization.source_line_number} "
            f"({expected_expense_categorization.source_text!r}) has {len(mismatches)} mismatch(es):\n" + "\n".join(mismatches)
        )
