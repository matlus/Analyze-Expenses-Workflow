import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from secrets import randbelow, token_hex

import pytest
from pydantic import ValidationError

from analyze_expenses_workflow.managers.configuration_providers import configuration_provider as configuration_provider_module
from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.budget_settings import BudgetSettings
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    CodingAssistantSettings,
    CodingAssistantSubscription,
    ModelFamily,
)
from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import (
    LlmOperation,
    LlmOperationSettings,
    ReasoningEffort,
)
from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    ExceptionAction,
    ExceptionDiagnostics,
    ExpenseLogEvent,
    Severity,
)
from analyze_expenses_workflow.managers.exceptions.configuration_setting_exception import (
    CodingAssistantSettingsInvalidException,
    CodingAssistantSubscriptionUnknownException,
    ConfigurationSettingBaseException,
    ConfigurationSettingBlankException,
    ConfigurationSettingMissingException,
    JevMinimumChoiceProbabilitySettingInvalidException,
    LlmModelFamilyMismatchException,
    LlmModelSettingInvalidException,
    LlmModelSettingMissingException,
    LlmOperationNotConfiguredException,
    ModelFamilyOverrideRejectedException,
    MonthlyBudgetSettingInvalidException,
    OpenRouterBaseUrlSettingInvalidException,
    ReasoningEffortSettingUnknownException,
    ReasoningEffortUnsupportedException,
)

SUBSCRIPTION_SETTING_NAME: str = "CODING_ASSISTANT_SUBSCRIPTION"
OPEN_ROUTER_KEY_SETTING_NAME: str = "OPEN_ROUTER_KEY"
OPEN_ROUTER_BASE_URL_SETTING_NAME: str = "OPEN_ROUTER_BASE_URL"
JEV_MODEL_SETTING_NAME: str = "JEV_MODEL"
OPEN_ROUTER_BASE_URL: str = "https://openrouter.ai/api"
DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME: str = "DINING_COFFEE_MONTHLY_BUDGET"
SHOPPING_MONTHLY_BUDGET_SETTING_NAME: str = "SHOPPING_MONTHLY_BUDGET"
DEFAULT_DINING_COFFEE_MONTHLY_BUDGET: str = "260.00"
DEFAULT_SHOPPING_MONTHLY_BUDGET: str = "1200.00"
SETTING_NAME_CONTEXT_FIELD_NAME: str = "SettingName"
RECEIVED_VALUE_CONTEXT_FIELD_NAME: str = "ReceivedValue"
MODEL_FAMILY_CONTEXT_FIELD_NAME: str = "ModelFamily"
SUBSCRIPTION_CONTEXT_FIELD_NAME: str = "Subscription"
CONFIGURATION_SETTING_PREFIXES: tuple[str, ...] = ("CODING_ASSISTANT_", "EXPENSE_", "OPEN_ROUTER_", "JEV_", "DINING_COFFEE_", "SHOPPING_")
CONFIGURATION_EXCEPTION_SEVERITY: Severity = Severity.ERROR
CONFIGURATION_EXCEPTION_HTTP_STATUS_CODE: int = 500
CONFIGURATION_EXCEPTION_LOG_EVENT: ExpenseLogEvent = ExpenseLogEvent.CONFIGURATION


@pytest.fixture(autouse=True)
def isolated_configuration_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test supplies its own settings: no developer .env and no configuration variable from the environment leaks in."""
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for environment_variable_name in tuple(os.environ):
        if environment_variable_name.startswith(CONFIGURATION_SETTING_PREFIXES):
            monkeypatch.delenv(environment_variable_name)


def create_required_settings(subscription: CodingAssistantSubscription) -> dict[str, str]:
    return {
        SUBSCRIPTION_SETTING_NAME: subscription.value,
        OPEN_ROUTER_KEY_SETTING_NAME: token_hex(16),
        OPEN_ROUTER_BASE_URL_SETTING_NAME: OPEN_ROUTER_BASE_URL,
        JEV_MODEL_SETTING_NAME: f"jev-{token_hex(4)}",
    }


def create_model_name(model_family: ModelFamily) -> str:
    return f"{'claude' if model_family is ModelFamily.ANTHROPIC else 'gpt'}-{token_hex(4)}"


def _assert_jev_settings_match(actual_jev_settings: JevSettings, expected_open_router_base_url: str, expected_jev_model: str) -> None:
    setting_mismatches: list[str] = []
    if str(actual_jev_settings.open_router_base_url) != expected_open_router_base_url:
        setting_mismatches.append(
            f"Open Router base URL: expected {expected_open_router_base_url!r}, got {str(actual_jev_settings.open_router_base_url)!r}"
        )
    if actual_jev_settings.jev_model != expected_jev_model:
        setting_mismatches.append(f"JEV model: expected {expected_jev_model!r}, got {actual_jev_settings.jev_model!r}")
    assert not setting_mismatches, (
        f"JEV settings should match base URL {expected_open_router_base_url!r} and model {expected_jev_model!r}; "
        f"{len(setting_mismatches)} property mismatch(es):\n" + "\n".join(setting_mismatches)
    )


def _assert_budget_settings_match(
    actual_budget_settings: BudgetSettings, expected_dining_coffee_monthly_budget: Decimal, expected_shopping_monthly_budget: Decimal
) -> None:
    budget_mismatches: list[str] = []
    if actual_budget_settings.dining_coffee_monthly_budget != expected_dining_coffee_monthly_budget:
        budget_mismatches.append(
            f"Dining budget: expected {expected_dining_coffee_monthly_budget!r}, got {actual_budget_settings.dining_coffee_monthly_budget!r}"
        )
    if actual_budget_settings.shopping_monthly_budget != expected_shopping_monthly_budget:
        budget_mismatches.append(
            f"Shopping budget: expected {expected_shopping_monthly_budget!r}, got {actual_budget_settings.shopping_monthly_budget!r}"
        )
    assert not budget_mismatches, (
        f"Monthly budgets should match dining {expected_dining_coffee_monthly_budget!r} and shopping {expected_shopping_monthly_budget!r}; "
        f"{len(budget_mismatches)} property mismatch(es):\n" + "\n".join(budget_mismatches)
    )


def _assert_llm_operation_settings_match(
    actual_llm_operation_settings: LlmOperationSettings,
    expected_model_name: str,
    expected_reasoning_effort: ReasoningEffort,
    expected_model_family: ModelFamily,
) -> None:
    operation_setting_mismatches: list[str] = []
    if actual_llm_operation_settings.model != expected_model_name:
        operation_setting_mismatches.append(f"Model: expected {expected_model_name!r}, got {actual_llm_operation_settings.model!r}")
    if actual_llm_operation_settings.reasoning_effort is not expected_reasoning_effort:
        operation_setting_mismatches.append(
            f"Reasoning effort: expected {expected_reasoning_effort!r}, got {actual_llm_operation_settings.reasoning_effort!r}"
        )
    if actual_llm_operation_settings.model_family is not expected_model_family:
        operation_setting_mismatches.append(f"Model family: expected {expected_model_family!r}, got {actual_llm_operation_settings.model_family!r}")
    assert not operation_setting_mismatches, (
        f"LLM operation settings should match model {expected_model_name!r}, reasoning effort {expected_reasoning_effort!r} "
        f"and family {expected_model_family!r}; {len(operation_setting_mismatches)} property mismatch(es):\n"
        + "\n".join(operation_setting_mismatches)
    )


def _assert_configuration_setting_exception(
    actual_configuration_setting_exception: ConfigurationSettingBaseException,
    *,
    expected_message_phrases: tuple[str, ...],
    expected_context_value_by_name: Mapping[str, str],
    expected_exception_action: ExceptionAction,
    forbidden_message_phrases: tuple[str, ...] = (),
) -> None:
    actual_message: str = str(actual_configuration_setting_exception)
    actual_exception_diagnostics: ExceptionDiagnostics = actual_configuration_setting_exception.diagnostics
    actual_context_value_by_name: dict[str, str | int | float | bool] = dict(actual_exception_diagnostics.additional_fields)
    exception_mismatches: list[str] = []
    if actual_exception_diagnostics.action is not expected_exception_action:
        exception_mismatches.append(f"Action: expected {expected_exception_action.value!r}, got {actual_exception_diagnostics.action.value!r}")
    if actual_exception_diagnostics.severity is not CONFIGURATION_EXCEPTION_SEVERITY:
        exception_mismatches.append(
            f"Severity: expected {CONFIGURATION_EXCEPTION_SEVERITY.name!r}, got {actual_exception_diagnostics.severity.name!r}"
        )
    if actual_exception_diagnostics.http_status_code != CONFIGURATION_EXCEPTION_HTTP_STATUS_CODE:
        exception_mismatches.append(
            f"HTTP status code: expected {CONFIGURATION_EXCEPTION_HTTP_STATUS_CODE}, got {actual_exception_diagnostics.http_status_code}"
        )
    if actual_exception_diagnostics.log_event is not CONFIGURATION_EXCEPTION_LOG_EVENT:
        exception_mismatches.append(
            f"Log event: expected {CONFIGURATION_EXCEPTION_LOG_EVENT.value!r}, got {actual_exception_diagnostics.log_event.value!r}"
        )
    exception_mismatches.extend(
        f"Missing expected diagnostic phrase {expected_message_phrase!r} in {actual_message!r}"
        for expected_message_phrase in expected_message_phrases
        if expected_message_phrase not in actual_message
    )
    exception_mismatches.extend(
        f"Protected phrase {forbidden_message_phrase!r} appeared in the diagnostic message"
        for forbidden_message_phrase in forbidden_message_phrases
        if forbidden_message_phrase in actual_message
    )
    exception_mismatches.extend(
        f"Context {expected_context_field[0]}: expected {expected_context_field[1]!r}, "
        f"got {actual_context_value_by_name.get(expected_context_field[0])!r}"
        for expected_context_field in expected_context_value_by_name.items()
        if actual_context_value_by_name.get(expected_context_field[0]) != expected_context_field[1]
    )
    exception_mismatches.extend(
        f"Unexpected context {actual_context_field[0]}={actual_context_field[1]!r}"
        for actual_context_field in actual_context_value_by_name.items()
        if actual_context_field[0] not in expected_context_value_by_name
    )
    exception_mismatches.extend(
        f"Protected phrase {forbidden_message_phrase!r} appeared in context {actual_context_field[0]}"
        for forbidden_message_phrase in forbidden_message_phrases
        for actual_context_field in actual_context_value_by_name.items()
        if forbidden_message_phrase in str(actual_context_field[1])
    )
    assert not exception_mismatches, (
        f"Configuration setting exception should be a {CONFIGURATION_EXCEPTION_SEVERITY.name} {expected_exception_action.value} "
        f"technical failure (HTTP {CONFIGURATION_EXCEPTION_HTTP_STATUS_CODE}), include {expected_message_phrases!r}, "
        f"carry context {dict(expected_context_value_by_name)!r} and omit protected phrases; "
        f"{len(exception_mismatches)} mismatch(es):\n" + "\n".join(exception_mismatches)
    )


def test_ConfigurationProvider_WhenSettingsAreValid_ThenReturnsTypedSubscriptionJevAndDefaultBudgetSettings() -> None:
    expected_coding_assistant_subscription: CodingAssistantSubscription = CodingAssistantSubscription.GITHUB_COPILOT
    required_settings: dict[str, str] = create_required_settings(expected_coding_assistant_subscription)
    expected_jev_model: str = required_settings[JEV_MODEL_SETTING_NAME]
    expected_dining_coffee_monthly_budget: Decimal = Decimal("260.00")
    expected_shopping_monthly_budget: Decimal = Decimal("1200.00")

    configuration_provider: ConfigurationProvider = ConfigurationProvider(required_settings)

    actual_jev_settings: JevSettings = configuration_provider.get_jev_settings()
    actual_budget_settings: BudgetSettings = configuration_provider.get_budget_settings()
    assert configuration_provider.get_coding_assistant_settings().coding_assistant_subscription is expected_coding_assistant_subscription
    _assert_jev_settings_match(actual_jev_settings, OPEN_ROUTER_BASE_URL, expected_jev_model)
    _assert_budget_settings_match(actual_budget_settings, expected_dining_coffee_monthly_budget, expected_shopping_monthly_budget)


def test_ConfigurationProvider_WhenBudgetsAreSet_ThenReturnsThem() -> None:
    expected_dining_coffee_monthly_budget: Decimal = Decimal("275.50")
    expected_shopping_monthly_budget: Decimal = Decimal("900.25")
    settings: dict[str, str] = {
        **create_required_settings(CodingAssistantSubscription.GPT_CODEX),
        DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME: str(expected_dining_coffee_monthly_budget),
        SHOPPING_MONTHLY_BUDGET_SETTING_NAME: str(expected_shopping_monthly_budget),
    }

    configuration_provider: ConfigurationProvider = ConfigurationProvider(settings)

    actual_budget_settings: BudgetSettings = configuration_provider.get_budget_settings()
    _assert_budget_settings_match(actual_budget_settings, expected_dining_coffee_monthly_budget, expected_shopping_monthly_budget)


@pytest.mark.parametrize("subscription", [CodingAssistantSubscription.GPT_CODEX, CodingAssistantSubscription.GITHUB_COPILOT])
def test_ConfigurationProvider_WhenOpenAiSubscriptionHasOperationSettings_ThenReturnsTheirModelAndEffortForEveryOperation(
    subscription: CodingAssistantSubscription,
) -> None:
    expected_model_family: ModelFamily = ModelFamily.OPENAI
    expected_extraction_model_name: str = create_model_name(expected_model_family)
    expected_findings_model_name: str = create_model_name(expected_model_family)
    expected_categorization_model_name: str = create_model_name(expected_model_family)
    expected_extraction_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    expected_findings_reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH
    expected_categorization_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    settings: dict[str, str] = {
        **create_required_settings(subscription),
        "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": expected_extraction_model_name,
        "EXPENSE_LINE_EXTRACTION_ANTHROPIC_MODEL": create_model_name(ModelFamily.ANTHROPIC),
        "EXPENSE_FINDINGS_OPENAI_MODEL": expected_findings_model_name,
        "EXPENSE_FINDINGS_OPENAI_REASONING_EFFORT": expected_findings_reasoning_effort.value,
        "EXPENSE_CATEGORIZATION_OPENAI_MODEL": expected_categorization_model_name,
    }

    configuration_provider: ConfigurationProvider = ConfigurationProvider(settings)

    actual_extraction_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_LINE_EXTRACTION
    )
    actual_findings_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_FINDINGS
    )
    actual_categorization_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_CATEGORIZATION
    )
    _assert_llm_operation_settings_match(
        actual_extraction_llm_operation_settings, expected_extraction_model_name, expected_extraction_reasoning_effort, expected_model_family
    )
    _assert_llm_operation_settings_match(
        actual_findings_llm_operation_settings, expected_findings_model_name, expected_findings_reasoning_effort, expected_model_family
    )
    _assert_llm_operation_settings_match(
        actual_categorization_llm_operation_settings,
        expected_categorization_model_name,
        expected_categorization_reasoning_effort,
        expected_model_family,
    )


def test_ConfigurationProvider_WhenOnlyUnqualifiedOperationKeysAreSet_ThenIgnoresThemAndUsesDefaults() -> None:
    expected_model_name: str = "gpt-6-luna"
    expected_extraction_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    expected_model_family: ModelFamily = ModelFamily.OPENAI
    settings: dict[str, str] = {
        **create_required_settings(CodingAssistantSubscription.GPT_CODEX),
        "EXPENSE_LINE_EXTRACTION_MODEL": create_model_name(expected_model_family),
        "EXPENSE_LINE_EXTRACTION_REASONING_EFFORT": ReasoningEffort.XHIGH.value,
    }

    configuration_provider: ConfigurationProvider = ConfigurationProvider(settings)

    actual_extraction_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_LINE_EXTRACTION
    )
    _assert_llm_operation_settings_match(
        actual_extraction_llm_operation_settings, expected_model_name, expected_extraction_reasoning_effort, expected_model_family
    )


@pytest.mark.parametrize(
    "family_setting_name",
    ["CODING_ASSISTANT_MODEL_FAMILY", "EXPENSE_LINE_EXTRACTION_MODEL_FAMILY", "EXPENSE_FINDINGS_MODEL_FAMILY"],
)
@pytest.mark.parametrize("subscription", [CodingAssistantSubscription.GPT_CODEX, CodingAssistantSubscription.GITHUB_COPILOT])
def test_ConfigurationProvider_WhenFamilyOverrideIsSupplied_ThenRejectsIt(
    family_setting_name: str, subscription: CodingAssistantSubscription
) -> None:
    rejected_model_family: str = ModelFamily.ANTHROPIC.value
    expected_message_phrases: tuple[str, ...] = (f"{family_setting_name} is derived from {SUBSCRIPTION_SETTING_NAME}", rejected_model_family)
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: family_setting_name,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: rejected_model_family,
    }
    settings: dict[str, str] = {SUBSCRIPTION_SETTING_NAME: subscription.value, family_setting_name: rejected_model_family}
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED

    with pytest.raises(ModelFamilyOverrideRejectedException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
    )


def test_ConfigurationProvider_WhenClaudeCodeIsSelected_ThenSelectsAnthropicModelAndEffortForEveryOperation() -> None:
    expected_model_family: ModelFamily = ModelFamily.ANTHROPIC
    expected_extraction_model_name: str = create_model_name(expected_model_family)
    expected_findings_model_name: str = create_model_name(expected_model_family)
    expected_findings_reasoning_effort: ReasoningEffort = ReasoningEffort.XHIGH
    expected_default_extraction_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    settings: dict[str, str] = {
        **create_required_settings(CodingAssistantSubscription.CLAUDE_CODE),
        "EXPENSE_LINE_EXTRACTION_ANTHROPIC_MODEL": expected_extraction_model_name,
        "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": create_model_name(ModelFamily.OPENAI),
        "EXPENSE_FINDINGS_ANTHROPIC_MODEL": expected_findings_model_name,
        "EXPENSE_FINDINGS_ANTHROPIC_REASONING_EFFORT": expected_findings_reasoning_effort.value,
        "EXPENSE_CATEGORIZATION_ANTHROPIC_MODEL": create_model_name(expected_model_family),
    }

    configuration_provider: ConfigurationProvider = ConfigurationProvider(settings)

    actual_extraction_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_LINE_EXTRACTION
    )
    actual_findings_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_FINDINGS
    )
    assert configuration_provider.get_coding_assistant_settings().model_family is expected_model_family
    _assert_llm_operation_settings_match(
        actual_extraction_llm_operation_settings, expected_extraction_model_name, expected_default_extraction_reasoning_effort, expected_model_family
    )
    _assert_llm_operation_settings_match(
        actual_findings_llm_operation_settings, expected_findings_model_name, expected_findings_reasoning_effort, expected_model_family
    )


def test_ConfigurationProvider_WhenClaudeCodeModelIsMissing_ThenRejectsTheOperationSetting() -> None:
    missing_model_setting_name: str = "EXPENSE_LINE_EXTRACTION_ANTHROPIC_MODEL"
    expected_message_phrases: tuple[str, ...] = (missing_model_setting_name, "required for the selected model family")
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: missing_model_setting_name,
        MODEL_FAMILY_CONTEXT_FIELD_NAME: ModelFamily.ANTHROPIC.value,
    }
    settings: dict[str, str] = create_required_settings(CodingAssistantSubscription.CLAUDE_CODE)
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(LlmModelSettingMissingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize("unsupported_effort", ["none", "minimal", "ultra"])
@pytest.mark.parametrize(
    ("subscription", "effort_setting_name", "model_setting_name"),
    [
        (CodingAssistantSubscription.CLAUDE_CODE, "EXPENSE_LINE_EXTRACTION_ANTHROPIC_REASONING_EFFORT", "EXPENSE_LINE_EXTRACTION_ANTHROPIC_MODEL"),
        (CodingAssistantSubscription.GITHUB_COPILOT, "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT", "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL"),
    ],
)
def test_ConfigurationProvider_WhenSubscriptionDoesNotOfferTheEffort_ThenRejectsItDuringConfiguration(
    unsupported_effort: str, subscription: CodingAssistantSubscription, effort_setting_name: str, model_setting_name: str
) -> None:
    supported_efforts: str = "low, medium, high, xhigh, max"
    expected_message_phrases: tuple[str, ...] = (effort_setting_name, subscription.value, unsupported_effort, supported_efforts)
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: effort_setting_name,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: unsupported_effort,
        SUBSCRIPTION_CONTEXT_FIELD_NAME: subscription.value,
    }
    model_family: ModelFamily = ModelFamily.ANTHROPIC if subscription is CodingAssistantSubscription.CLAUDE_CODE else ModelFamily.OPENAI
    settings: dict[str, str] = {
        **create_required_settings(subscription),
        model_setting_name: create_model_name(model_family),
        effort_setting_name: unsupported_effort,
    }
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(ReasoningEffortUnsupportedException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize("subscription", [CodingAssistantSubscription.GPT_CODEX, CodingAssistantSubscription.GITHUB_COPILOT])
@pytest.mark.parametrize("anthropic_model_name", ["claude-haiku-5-5", "anthropic/claude-haiku-5-5"])
def test_ConfigurationProvider_WhenOpenAiSubscriptionReceivesAnthropicModel_ThenRejectsItDuringConfiguration(
    subscription: CodingAssistantSubscription, anthropic_model_name: str
) -> None:
    model_setting_name: str = "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL"
    expected_message_phrases: tuple[str, ...] = (model_setting_name, ModelFamily.OPENAI.value, subscription.value, anthropic_model_name)
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: model_setting_name,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: anthropic_model_name,
        MODEL_FAMILY_CONTEXT_FIELD_NAME: ModelFamily.OPENAI.value,
        SUBSCRIPTION_CONTEXT_FIELD_NAME: subscription.value,
    }
    settings: dict[str, str] = {**create_required_settings(subscription), model_setting_name: anthropic_model_name}
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(LlmModelFamilyMismatchException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize(
    ("subscription", "family"),
    [
        (CodingAssistantSubscription.GPT_CODEX, ModelFamily.OPENAI),
        (CodingAssistantSubscription.CLAUDE_CODE, ModelFamily.ANTHROPIC),
        (CodingAssistantSubscription.GITHUB_COPILOT, ModelFamily.OPENAI),
    ],
)
def test_CodingAssistantSettings_WhenSdkAndFamilyMatch_ThenIsValid(subscription: CodingAssistantSubscription, family: ModelFamily) -> None:
    expected_model_family: ModelFamily = family

    coding_assistant_settings: CodingAssistantSettings = CodingAssistantSettings(coding_assistant_subscription=subscription, model_family=family)

    assert coding_assistant_settings.model_family is expected_model_family


@pytest.mark.parametrize(
    ("subscription", "family"),
    [
        (CodingAssistantSubscription.GPT_CODEX, ModelFamily.ANTHROPIC),
        (CodingAssistantSubscription.GITHUB_COPILOT, ModelFamily.ANTHROPIC),
        (CodingAssistantSubscription.CLAUDE_CODE, ModelFamily.OPENAI),
    ],
)
def test_CodingAssistantSettings_WhenSdkAndFamilyDisagree_ThenRejectsThePair(subscription: CodingAssistantSubscription, family: ModelFamily) -> None:
    expected_error_phrase: str = "Model family must match the selected coding assistant SDK"

    with pytest.raises(ValidationError, match=expected_error_phrase):
        CodingAssistantSettings(coding_assistant_subscription=subscription, model_family=family)


def test_ConfigurationProvider_WhenReasoningEffortIsUnset_ThenUsesOperationDefaults() -> None:
    expected_model_family: ModelFamily = ModelFamily.OPENAI
    expected_extraction_model_name: str = create_model_name(expected_model_family)
    expected_findings_model_name: str = create_model_name(expected_model_family)
    expected_extraction_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    expected_findings_reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    settings: dict[str, str] = {
        **create_required_settings(CodingAssistantSubscription.GPT_CODEX),
        "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": expected_extraction_model_name,
        "EXPENSE_FINDINGS_OPENAI_MODEL": expected_findings_model_name,
    }

    configuration_provider: ConfigurationProvider = ConfigurationProvider(settings)

    actual_extraction_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_LINE_EXTRACTION
    )
    actual_findings_llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(
        LlmOperation.EXPENSE_FINDINGS
    )
    _assert_llm_operation_settings_match(
        actual_extraction_llm_operation_settings, expected_extraction_model_name, expected_extraction_reasoning_effort, expected_model_family
    )
    _assert_llm_operation_settings_match(
        actual_findings_llm_operation_settings, expected_findings_model_name, expected_findings_reasoning_effort, expected_model_family
    )


def test_ConfigurationProvider_WhenReasoningEffortIsUnknown_ThenListsTheKnownEfforts() -> None:
    effort_setting_name: str = "EXPENSE_FINDINGS_OPENAI_REASONING_EFFORT"
    rejected_effort: str = f"extreme-{token_hex(4)}"
    settings: dict[str, str] = {**create_required_settings(CodingAssistantSubscription.GPT_CODEX), effort_setting_name: rejected_effort}
    expected_message_phrases: tuple[str, ...] = (
        f"{effort_setting_name} must be one of",
        rejected_effort,
        *(reasoning_effort.value for reasoning_effort in ReasoningEffort),
    )
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: effort_setting_name,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: rejected_effort,
    }
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(ReasoningEffortSettingUnknownException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize(
    "optional_setting_name",
    [
        DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME,
        SHOPPING_MONTHLY_BUDGET_SETTING_NAME,
        "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL",
        "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT",
        "EXPENSE_FINDINGS_OPENAI_MODEL",
        "EXPENSE_FINDINGS_OPENAI_REASONING_EFFORT",
        "JEV_PARSING_MINIMUM_CHOICE_PROBABILITY",
    ],
)
def test_ConfigurationProvider_WhenOptionalSettingIsBlank_ThenRejectsIt(optional_setting_name: str) -> None:
    settings: dict[str, str] = {**create_required_settings(CodingAssistantSubscription.GPT_CODEX), optional_setting_name: "  "}
    expected_message_phrases: tuple[str, ...] = (f"{optional_setting_name} must not be blank",)
    expected_context_value_by_name: dict[str, str] = {SETTING_NAME_CONTEXT_FIELD_NAME: optional_setting_name}
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(ConfigurationSettingBlankException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize("budget_setting_name", [DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME, SHOPPING_MONTHLY_BUDGET_SETTING_NAME])
@pytest.mark.parametrize("amount", ["0.001", "275.505", "-1.00", "0"])
def test_ConfigurationProvider_WhenBudgetIsNonpositiveOrSubcent_ThenRejectsIt(budget_setting_name: str, amount: str) -> None:
    settings: dict[str, str] = {**create_required_settings(CodingAssistantSubscription.GPT_CODEX), budget_setting_name: amount}
    expected_message_phrases: tuple[str, ...] = (budget_setting_name, "positive amounts", "at most two decimal places", repr(amount))
    expected_context_value_by_name: dict[str, str] = {
        DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME: DEFAULT_DINING_COFFEE_MONTHLY_BUDGET,
        SHOPPING_MONTHLY_BUDGET_SETTING_NAME: DEFAULT_SHOPPING_MONTHLY_BUDGET,
        budget_setting_name: amount,
    }
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(MonthlyBudgetSettingInvalidException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


def test_ConfigurationProvider_WhenSubscriptionIsUnknown_ThenRejectsIt() -> None:
    rejected_subscription: str = f"ANOTHER_SUBSCRIPTION_{token_hex(4)}"
    expected_message_phrases: tuple[str, ...] = (f"{SUBSCRIPTION_SETTING_NAME} must be one of", rejected_subscription)
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: SUBSCRIPTION_SETTING_NAME,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: rejected_subscription,
    }
    settings: dict[str, str] = {SUBSCRIPTION_SETTING_NAME: rejected_subscription}
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED

    with pytest.raises(CodingAssistantSubscriptionUnknownException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
    )


def test_ConfigurationProvider_WhenJevEndpointIsInsecure_ThenRejectsIt() -> None:
    rejected_url: str = f"http://{token_hex(4)}.example/api"
    settings: dict[str, str] = {**create_required_settings(CodingAssistantSubscription.GPT_CODEX), OPEN_ROUTER_BASE_URL_SETTING_NAME: rejected_url}
    expected_message_phrases: tuple[str, ...] = (f"{OPEN_ROUTER_BASE_URL_SETTING_NAME} must be a valid HTTPS URL", rejected_url)
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: OPEN_ROUTER_BASE_URL_SETTING_NAME,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: rejected_url,
    }
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(OpenRouterBaseUrlSettingInvalidException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize(
    "required_setting_name", [SUBSCRIPTION_SETTING_NAME, OPEN_ROUTER_KEY_SETTING_NAME, OPEN_ROUTER_BASE_URL_SETTING_NAME, JEV_MODEL_SETTING_NAME]
)
def test_ConfigurationProvider_WhenRequiredSettingIsAbsent_ThenRejectsItAsMissing(required_setting_name: str) -> None:
    settings: dict[str, str] = create_required_settings(CodingAssistantSubscription.GPT_CODEX)
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)
    del settings[required_setting_name]
    expected_message_phrases: tuple[str, ...] = (f"{required_setting_name} is required",)
    expected_context_value_by_name: dict[str, str] = {SETTING_NAME_CONTEXT_FIELD_NAME: required_setting_name}
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED

    with pytest.raises(ConfigurationSettingMissingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize("required_setting_name", [OPEN_ROUTER_KEY_SETTING_NAME, JEV_MODEL_SETTING_NAME])
def test_ConfigurationProvider_WhenRequiredSettingIsBlank_ThenRejectsItAsBlank(required_setting_name: str) -> None:
    settings: dict[str, str] = create_required_settings(CodingAssistantSubscription.GPT_CODEX)
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)
    settings[required_setting_name] = " " * (randbelow(3) + 1)
    expected_message_phrases: tuple[str, ...] = (f"{required_setting_name} must not be blank",)
    expected_context_value_by_name: dict[str, str] = {SETTING_NAME_CONTEXT_FIELD_NAME: required_setting_name}
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED

    with pytest.raises(ConfigurationSettingBlankException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize("openai_model_name", ["gpt-5-5", "codex-mini", "openai/gpt-5-5"])
def test_ConfigurationProvider_WhenClaudeCodeReceivesOpenAiModel_ThenRejectsItDuringConfiguration(openai_model_name: str) -> None:
    coding_assistant_subscription: CodingAssistantSubscription = CodingAssistantSubscription.CLAUDE_CODE
    model_setting_name: str = "EXPENSE_LINE_EXTRACTION_ANTHROPIC_MODEL"
    expected_message_phrases: tuple[str, ...] = (
        model_setting_name,
        ModelFamily.ANTHROPIC.value,
        coding_assistant_subscription.value,
        openai_model_name,
    )
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: model_setting_name,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: openai_model_name,
        MODEL_FAMILY_CONTEXT_FIELD_NAME: ModelFamily.ANTHROPIC.value,
        SUBSCRIPTION_CONTEXT_FIELD_NAME: coding_assistant_subscription.value,
    }
    settings: dict[str, str] = {**create_required_settings(coding_assistant_subscription), model_setting_name: openai_model_name}
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(LlmModelFamilyMismatchException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


@pytest.mark.parametrize("minimum_probability", ["1.5", "-0.1", "nan"])
def test_ConfigurationProvider_WhenJevMinimumChoiceProbabilityIsOutOfRange_ThenRejectsIt(minimum_probability: str) -> None:
    probability_setting_name: str = "JEV_PARSING_MINIMUM_CHOICE_PROBABILITY"
    settings: dict[str, str] = {**create_required_settings(CodingAssistantSubscription.GPT_CODEX), probability_setting_name: minimum_probability}
    expected_message_phrases: tuple[str, ...] = (f"{probability_setting_name} must be between zero and one", repr(minimum_probability))
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: probability_setting_name,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: minimum_probability,
    }
    expected_exception_action: ExceptionAction = ExceptionAction.INFRA_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)

    with pytest.raises(JevMinimumChoiceProbabilitySettingInvalidException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


def test_ConfigurationProvider_WhenSubscriptionMapsToAnotherFamily_ThenRejectsTheCodingAssistantSettings(monkeypatch: pytest.MonkeyPatch) -> None:
    coding_assistant_subscription: CodingAssistantSubscription = CodingAssistantSubscription.CLAUDE_CODE
    expected_message_phrases: tuple[str, ...] = (f"{SUBSCRIPTION_SETTING_NAME}={coding_assistant_subscription.value}", ModelFamily.OPENAI.value)
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: SUBSCRIPTION_SETTING_NAME,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: coding_assistant_subscription.value,
        MODEL_FAMILY_CONTEXT_FIELD_NAME: ModelFamily.OPENAI.value,
    }
    settings: dict[str, str] = create_required_settings(coding_assistant_subscription)
    expected_exception_action: ExceptionAction = ExceptionAction.DEVELOPER_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)
    monkeypatch.setattr(configuration_provider_module, "MODEL_FAMILY_BY_SUBSCRIPTION", {coding_assistant_subscription: ModelFamily.OPENAI})

    with pytest.raises(CodingAssistantSettingsInvalidException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


def test_ConfigurationProvider_WhenResolvedModelIsRejectedBySettings_ThenRejectsTheOperationModel(monkeypatch: pytest.MonkeyPatch) -> None:
    rejected_model_setting_name: str = "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL"
    rejected_model_name: str = ""
    expected_message_phrases: tuple[str, ...] = (rejected_model_setting_name, repr(rejected_model_name))
    expected_context_value_by_name: dict[str, str] = {
        SETTING_NAME_CONTEXT_FIELD_NAME: rejected_model_setting_name,
        RECEIVED_VALUE_CONTEXT_FIELD_NAME: rejected_model_name,
    }
    settings: dict[str, str] = create_required_settings(CodingAssistantSubscription.GPT_CODEX)
    expected_exception_action: ExceptionAction = ExceptionAction.DEVELOPER_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)
    monkeypatch.setattr(ConfigurationProvider, "_DEFAULT_OPENAI_MODEL", rejected_model_name)

    with pytest.raises(LlmModelSettingInvalidException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )


def test_ConfigurationProvider_WhenOperationHasNoLoadedSettings_ThenRejectsTheRetrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    unloaded_llm_operation: LlmOperation = LlmOperation.EXPENSE_FINDINGS
    expected_message_phrases: tuple[str, ...] = (
        f"{unloaded_llm_operation.value} has no loaded settings",
        LlmOperation.EXPENSE_LINE_EXTRACTION.value,
    )
    expected_context_value_by_name: dict[str, str] = {"LlmOperation": unloaded_llm_operation.value}
    settings: dict[str, str] = create_required_settings(CodingAssistantSubscription.GPT_CODEX)
    expected_exception_action: ExceptionAction = ExceptionAction.DEVELOPER_ACTION_REQUIRED
    forbidden_message_phrases: tuple[str, ...] = (settings[OPEN_ROUTER_KEY_SETTING_NAME],)
    configuration_provider: ConfigurationProvider = ConfigurationProvider(settings)
    monkeypatch.delitem(configuration_provider._llm_operation_settings, unloaded_llm_operation)

    with pytest.raises(LlmOperationNotConfiguredException) as raised_configuration_setting_exception:
        configuration_provider.retrieve_llm_operation_settings(unloaded_llm_operation)

    _assert_configuration_setting_exception(
        raised_configuration_setting_exception.value,
        expected_message_phrases=expected_message_phrases,
        expected_context_value_by_name=expected_context_value_by_name,
        expected_exception_action=expected_exception_action,
        forbidden_message_phrases=forbidden_message_phrases,
    )
