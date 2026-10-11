import analyze_expenses_workflow as compatibility_api
import analyze_expenses_workflow.domain_facades as public_api


def test_domain_facades_WhenThePublicNamespaceIsInspected_ThenExportsTheResultGraphWithoutImplementationTypes() -> None:
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
        "SystemOneGatewayOpenRouterJev",
    }
    expected_export_mismatches: list[str] = []

    actual_exported_names: set[str] = set(public_api.__all__)
    actual_export_mismatches: list[str] = [
        *(f"missing result type {missing_result_type}" for missing_result_type in sorted(expected_result_types - actual_exported_names)),
        *(f"leaked implementation type {leaked_type}" for leaked_type in sorted(excluded_implementation_types & actual_exported_names)),
        *(f"compatibility export difference {differing_name}" for differing_name in sorted(set(compatibility_api.__all__) ^ actual_exported_names)),
    ]

    assert actual_export_mismatches == expected_export_mismatches, (
        f"The public facade namespace must export the result graph and nothing internal; "
        f"{len(actual_export_mismatches)} mismatches: {actual_export_mismatches}"
    )


def test_domain_facades_WhenTheExceptionContractIsInspected_ThenExportsEveryTypeTheExceptionBasesNeed() -> None:
    expected_exception_contract_types: set[str] = {
        "AnalyzeExpensesBaseException",
        "AnalyzeExpensesBusinessBaseException",
        "AnalyzeExpensesTechnicalBaseException",
        "ExceptionAction",
        "ExceptionCause",
        "ExceptionContextInput",
        "ExceptionDiagnostics",
        "ExpenseLogEvent",
        "Severity",
    }
    expected_missing_exception_contract_types: list[str] = []

    actual_missing_exception_contract_types: list[str] = sorted(
        exception_contract_type
        for exception_contract_type in expected_exception_contract_types
        if exception_contract_type not in public_api.__all__ or not hasattr(public_api, exception_contract_type)
    )

    assert actual_missing_exception_contract_types == expected_missing_exception_contract_types, (
        f"Callers must be able to subclass and read the exported exception bases from the facade namespace alone; "
        f"missing {actual_missing_exception_contract_types}"
    )


def test_domain_facades_WhenTheCompatibilityNamespaceIsInspected_ThenResolvesEveryExportedNameToTheFacadeObject() -> None:
    expected_missing_names: list[str] = []

    actual_missing_names: list[str] = [
        exported_name
        for exported_name in public_api.__all__
        if getattr(compatibility_api, exported_name, None) is not getattr(public_api, exported_name)
    ]

    assert actual_missing_names == expected_missing_names, f"expected every exported name to resolve; missing {actual_missing_names}"
