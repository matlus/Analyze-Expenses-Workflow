import analyze_expenses_workflow as compatibility_api
import analyze_expenses_workflow.domain_facades as public_api


def test_facade_namespace_exports_the_result_graph_without_implementation_types() -> None:
    expected_result_types: set[str] = {
        "ExpenseAnalysisResult",
        "ExpenseCategorization",
        "CategoryProbability",
        "ExpenseCategory",
        "ParsedExpenseLine",
        "ExpenseTransaction",
        "TransactionTreatment",
        "ExpenseCalculationResult",
        "MonthlyCategoryTotal",
        "MonthlyTotal",
        "BudgetComparison",
        "MonthCategoryDelta",
        "CalculationReconciliation",
        "ExpenseFinding",
    }
    excluded_implementation_types: set[str] = {
        "ManagerExpenseAnalysis",
        "BudgetTarget",
        "ServiceLocatorProduction",
        "ExceptionAction",
        "ExpenseLogEvent",
        "Severity",
    }

    assert expected_result_types <= set(public_api.__all__)
    assert excluded_implementation_types.isdisjoint(public_api.__all__)
    assert set(compatibility_api.__all__) == set(public_api.__all__)
    assert compatibility_api.DomainFacade is public_api.DomainFacade
    assert compatibility_api.ExpenseInputException is public_api.ExpenseInputException


def test_compatibility_namespace_resolves_every_exported_name_to_the_facade_object() -> None:
    expected_missing_names: list[str] = []

    actual_missing_names: list[str] = [
        exported_name
        for exported_name in public_api.__all__
        if getattr(compatibility_api, exported_name, None) is not getattr(public_api, exported_name)
    ]

    assert actual_missing_names == expected_missing_names, f"expected every exported name to resolve; missing {actual_missing_names}"
