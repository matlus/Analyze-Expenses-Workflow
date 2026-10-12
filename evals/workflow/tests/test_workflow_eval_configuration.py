import json
import os
import secrets
from decimal import Decimal
from pathlib import Path

import pytest

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import CodingAssistantSubscription
from expense_analysis_workflow_eval import (
    EVAL_SUITE_DIRECTORY,
    AmountCheck,
    CompletedJudgment,
    EvalCase,
    EvalSuite,
    GradingReference,
    JevRequestCountingTransport,
    JudgeSessionFailedException,
    LedgerBucket,
    LedgerEntry,
    ModelStep,
    ModelStepSelection,
    NotApplicableCheck,
    ReferenceLedger,
    ServiceLocatorEval,
    SettingOverrideException,
    SettingOverrideRequest,
    build_setting_overrides,
    compose_run_label,
    describe_model_steps,
    find_check_reference_problems,
    find_ledger_source_problems,
    load_eval_suite,
    parse_setting_assignments,
    read_judge_verdict,
    select_model_steps,
)

PART_THREE_CHECK_NAMES: tuple[str, ...] = (
    "dining-june-total",
    "dining-july-total",
    "dining-august-total",
    "dining-august-over-budget",
    "refund-netted",
    "shopping-july-total",
    "duplicate-flagged",
    "both-delta-charges-kept",
    "travel-august-total",
    "uncategorized-entry-count",
    "largest-increase-is-travel",
    "code-executed",
    "ambiguous-entries-not-forced",
    "tables-before-interpretation",
    "three-supported-findings",
    "no-invented-budgets",
)
JEV_PROVIDER: str = "OpenRouter (Jev)"
JEV_REASONING_EFFORT: str = "not supported"
CONFIGURATION_SETTING_PREFIXES: tuple[str, ...] = ("CODING_ASSISTANT_", "EXPENSE_", "OPEN_ROUTER_", "JEV_", "DINING_COFFEE_", "SHOPPING_")


@pytest.fixture(autouse=True)
def isolated_configuration_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test supplies its own settings: no developer .env and no configuration variable from the environment leaks in."""
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for environment_variable_name in tuple(os.environ):
        if environment_variable_name.startswith(CONFIGURATION_SETTING_PREFIXES):
            monkeypatch.delenv(environment_variable_name)


def create_jev_settings() -> dict[str, str]:
    return {
        "OPEN_ROUTER_KEY": secrets.token_hex(16),
        "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
        "JEV_MODEL": f"jev-{secrets.token_hex(4)}",
    }


def create_setting_override_request(
    coding_assistant_subscription: CodingAssistantSubscription,
    model: str | None,
    reasoning_effort: str | None,
    extra_setting_value_by_name: dict[str, str],
) -> SettingOverrideRequest:
    return SettingOverrideRequest({}, ModelStepSelection(coding_assistant_subscription, model, reasoning_effort), extra_setting_value_by_name)


def test_SettingOverrides_WhenSubscriptionModelAndEffortAreGiven_ThenEveryLlmStepResolvesToThem() -> None:
    expected_model: str = f"claude-{secrets.token_hex(4)}"
    expected_reasoning_effort: str = "high"
    expected_provider: str = "CLAUDE_CODE (ANTHROPIC)"
    jev_setting_value_by_name: dict[str, str] = create_jev_settings()
    expected_jev_model: str = jev_setting_value_by_name["JEV_MODEL"]
    expected_model_steps: tuple[ModelStep, ...] = (
        ModelStep("expense_line_jev_extraction", JEV_PROVIDER, expected_jev_model, JEV_REASONING_EFFORT),
        ModelStep("expense_line_extraction", expected_provider, expected_model, expected_reasoning_effort),
        ModelStep("transaction_categorization", JEV_PROVIDER, expected_jev_model, JEV_REASONING_EFFORT),
        ModelStep("expense_findings", expected_provider, expected_model, expected_reasoning_effort),
    )
    setting_override_request: SettingOverrideRequest = create_setting_override_request(
        CodingAssistantSubscription.CLAUDE_CODE, expected_model, expected_reasoning_effort, jev_setting_value_by_name
    )

    setting_value_by_name: dict[str, str] = build_setting_overrides(setting_override_request)

    actual_model_steps: tuple[ModelStep, ...] = describe_model_steps(ConfigurationProvider(setting_value_by_name))
    assert actual_model_steps == expected_model_steps


def test_SettingOverrides_WhenAStepSettingIsGivenExplicitly_ThenItOverridesTheModelForThatStepOnly() -> None:
    shared_model: str = f"gpt-{secrets.token_hex(4)}"
    expected_findings_model: str = f"gpt-{secrets.token_hex(4)}"
    expected_model_by_step_name: dict[str, str] = {"expense_line_extraction": shared_model, "expense_findings": expected_findings_model}
    setting_override_request: SettingOverrideRequest = create_setting_override_request(
        CodingAssistantSubscription.GPT_CODEX,
        shared_model,
        "medium",
        {**create_jev_settings(), "EXPENSE_FINDINGS_OPENAI_MODEL": expected_findings_model},
    )

    setting_value_by_name: dict[str, str] = build_setting_overrides(setting_override_request)

    actual_model_by_step_name: dict[str, str] = {
        model_step.step_name: model_step.model
        for model_step in describe_model_steps(ConfigurationProvider(setting_value_by_name))
        if model_step.provider != JEV_PROVIDER
    }
    assert actual_model_by_step_name == expected_model_by_step_name


def test_SettingOverrides_WhenCaseSettingsAreGiven_ThenTheServiceLocatorConfiguresTheCaseBudgetsAndCountsJevRequests() -> None:
    expected_dining_budget: Decimal = Decimal("260.00")
    expected_shopping_budget: Decimal = Decimal("1200.00")
    setting_override_request: SettingOverrideRequest = SettingOverrideRequest(
        case_setting_value_by_name={
            "DINING_COFFEE_MONTHLY_BUDGET": str(expected_dining_budget),
            "SHOPPING_MONTHLY_BUDGET": str(expected_shopping_budget),
        },
        model_step_selection=ModelStepSelection(CodingAssistantSubscription.GPT_CODEX, model=None, reasoning_effort=None),
        extra_setting_value_by_name=create_jev_settings(),
    )
    service_locator_eval: ServiceLocatorEval = ServiceLocatorEval(build_setting_overrides(setting_override_request))

    actual_configuration_provider: ConfigurationProvider = service_locator_eval.get_configuration_provider()

    assert (
        actual_configuration_provider.get_budget_settings().dining_coffee_monthly_budget,
        actual_configuration_provider.get_budget_settings().shopping_monthly_budget,
        isinstance(service_locator_eval.create_jev_http_transport(), JevRequestCountingTransport),
    ) == (expected_dining_budget, expected_shopping_budget, True)


def test_ModelStepSelection_WhenAModelIsGivenWithoutASubscription_ThenRejectsIt() -> None:
    expected_error_phrase: str = "needs the subscription that selects their family"
    model: str = f"gpt-{secrets.token_hex(4)}"

    with pytest.raises(SettingOverrideException, match=expected_error_phrase):
        select_model_steps(None, model, None)


def test_SettingAssignments_WhenAValueContainsAnEqualsSign_ThenKeepsTheWholeValueAndTrimsSpaces() -> None:
    jev_model_value: str = f"a={secrets.token_hex(4)}"
    budget_value: str = "900.00"
    setting_assignments: list[str] = [f"JEV_MODEL={jev_model_value}", f"SHOPPING_MONTHLY_BUDGET = {budget_value} "]
    expected_setting_value_by_name: dict[str, str] = {"JEV_MODEL": jev_model_value, "SHOPPING_MONTHLY_BUDGET": budget_value}

    actual_setting_value_by_name: dict[str, str] = parse_setting_assignments(setting_assignments)

    assert actual_setting_value_by_name == expected_setting_value_by_name


@pytest.mark.parametrize("malformed_assignment", ["JEV_MODEL", "=jev-1.13"])
def test_SettingAssignments_WhenAnAssignmentLacksANameOrEqualsSign_ThenRejectsIt(malformed_assignment: str) -> None:
    expected_error_phrase: str = "must read NAME=VALUE"

    with pytest.raises(SettingOverrideException, match=expected_error_phrase):
        parse_setting_assignments([malformed_assignment])


def test_RunLabel_WhenStepsShareOneModel_ThenNamesTheProviderModelAndEffortOnce() -> None:
    model: str = f"claude-{secrets.token_hex(4)}"
    provider: str = "CLAUDE_CODE (ANTHROPIC)"
    reasoning_effort: str = "low"
    model_steps: tuple[ModelStep, ...] = (
        ModelStep("expense_line_jev_extraction", JEV_PROVIDER, f"jev-{secrets.token_hex(4)}", JEV_REASONING_EFFORT),
        ModelStep("expense_line_extraction", provider, model, reasoning_effort),
        ModelStep("expense_findings", provider, model, reasoning_effort),
    )
    expected_run_label: str = f"claude_code-{model}-{reasoning_effort}"

    actual_run_label: str = compose_run_label(model_steps)

    assert actual_run_label == expected_run_label


def test_EvalSuite_WhenThePartThreeCaseLoads_ThenEveryPartThreeCheckIsMappedAndTwoDoNotApply() -> None:
    expected_not_applicable_check_names: list[str] = ["code-executed", "tables-before-interpretation"]
    expected_expense_line_count: int = 198
    duplicate_line_number_in_file: int = 112
    expected_duplicate_line_number_in_workflow: int = 109

    eval_suite: EvalSuite = load_eval_suite(EVAL_SUITE_DIRECTORY)

    actual_not_applicable_check_names: list[str] = [check.check_name for check in eval_suite.checks if isinstance(check, NotApplicableCheck)]
    assert (
        tuple(check.check_name for check in eval_suite.checks),
        actual_not_applicable_check_names,
        len(eval_suite.eval_case.expense_lines),
        eval_suite.eval_case.to_workflow_line_number(duplicate_line_number_in_file),
    ) == (PART_THREE_CHECK_NAMES, expected_not_applicable_check_names, expected_expense_line_count, expected_duplicate_line_number_in_workflow)


def test_EvalSuite_WhenALedgerLineDiffersFromTheInput_ThenReportsTheBrokenReference() -> None:
    input_line: str = f"June 1 Merchant {secrets.token_hex(4)} $10.00"
    ledger_line: str = f"June 1 Merchant {secrets.token_hex(4)} $10.00"
    first_expense_line_number: int = 4
    eval_case: EvalCase = EvalCase("case", (input_line,), first_expense_line_number, (), {})
    ledger_entry: LedgerEntry = LedgerEntry(
        first_expense_line_number, ledger_line, "2026-06", Decimal("10.00"), "Shopping", None, is_counted=True, note=None
    )
    expected_problems: list[str] = [f"reference ledger line {first_expense_line_number} reads {ledger_line!r}; the input has {input_line!r}"]

    actual_problems: list[str] = find_ledger_source_problems(ReferenceLedger((ledger_entry,), {}), eval_case)

    assert actual_problems == expected_problems


def test_EvalSuite_WhenAnExpectedAmountIsNotAPlainNumber_ThenReportsItBeforeAnyRun() -> None:
    fact_name: str = f"fact_{secrets.token_hex(4)}"
    formatted_amount: str = "1,655.92"
    eval_suite: EvalSuite = load_eval_suite(EVAL_SUITE_DIRECTORY)
    broken_eval_suite: EvalSuite = EvalSuite(
        eval_suite.eval_suite_name,
        eval_suite.eval_case,
        GradingReference(
            {fact_name: (formatted_amount,)},
            ReferenceLedger(eval_suite.grading_reference.reference_ledger.ledger_entries, {fact_name: LedgerBucket("Travel", "2026-08")}),
            eval_suite.grading_reference.uncategorized_category,
        ),
        eval_suite.reference_category_by_workflow_category,
        eval_suite.checks,
    )
    amount_check: AmountCheck = AmountCheck("amount-check", "A total.", 0.8, None, fact_name=fact_name)
    expected_problems: list[str] = [f"check 'amount-check': the expected amount '{formatted_amount}' of '{fact_name}' is not a finite plain number"]

    actual_problems: list[str] = find_check_reference_problems(amount_check, broken_eval_suite)

    assert actual_problems == expected_problems


def test_JudgeVerdict_WhenTheResultCarriesStructuredOutput_ThenReadsPassAndReason() -> None:
    expected_completed_judgment: CompletedJudgment = CompletedJudgment(passed=True, reason=f"All three findings agree {secrets.token_hex(4)}")
    judge_result: dict[str, object] = {
        "is_error": False,
        "subtype": "success",
        "structured_output": {"passed": expected_completed_judgment.passed, "reason": expected_completed_judgment.reason},
    }

    actual_completed_judgment: CompletedJudgment = read_judge_verdict(0, json.dumps(judge_result).encode(), b"")

    assert actual_completed_judgment == expected_completed_judgment


def test_JudgeVerdict_WhenTheSessionEndsInAnErrorResult_ThenRaisesSessionFailureWithStandardError() -> None:
    standard_error: str = f"rate limited {secrets.token_hex(4)}"
    expected_error_phrase: str = rf"the judge session failed \(exit 1\): {standard_error}"
    judge_result: dict[str, object] = {"is_error": True, "subtype": "error_during_execution", "result": "API error"}

    with pytest.raises(JudgeSessionFailedException, match=expected_error_phrase):
        read_judge_verdict(1, json.dumps(judge_result).encode(), standard_error.encode())


def test_JudgeVerdict_WhenTheCliFailsBeforeWritingAResult_ThenRaisesSessionFailureWithStandardError() -> None:
    standard_error: str = f"model not found {secrets.token_hex(4)}"
    expected_error_phrase: str = rf"the judge session failed \(exit 2\) without a result: {standard_error}"

    with pytest.raises(JudgeSessionFailedException, match=expected_error_phrase):
        read_judge_verdict(2, b"", standard_error.encode())


def test_JudgeVerdict_WhenStructuredOutputIsMissing_ThenRaisesUnreadableVerdict() -> None:
    expected_error_phrase: str = "unreadable verdict"
    judge_result: dict[str, object] = {"is_error": False, "subtype": "success", "result": "PASS"}

    with pytest.raises(JudgeSessionFailedException, match=expected_error_phrase):
        read_judge_verdict(0, json.dumps(judge_result).encode(), b"")
