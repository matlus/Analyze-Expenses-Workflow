import os
from decimal import Decimal
from pathlib import Path
from secrets import token_hex

import pytest
from pydantic import ValidationError

from analyze_expenses_workflow.domain_facades import ConfigurationSettingException
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

SUBSCRIPTION_SETTING_NAME: str = "CODING_ASSISTANT_SUBSCRIPTION"
OPEN_ROUTER_BASE_URL: str = "https://openrouter.ai/api"
CONFIGURATION_SETTING_PREFIXES: tuple[str, ...] = ("CODING_ASSISTANT_", "EXPENSE_", "OPEN_ROUTER_", "JEV_", "DINING_COFFEE_", "SHOPPING_")


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
        "OPEN_ROUTER_KEY": token_hex(16),
        "OPEN_ROUTER_BASE_URL": OPEN_ROUTER_BASE_URL,
        "JEV_MODEL": f"jev-{token_hex(4)}",
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


def _assert_configuration_setting_exception_message(
    actual_configuration_setting_exception: ConfigurationSettingException,
    expected_message_phrases: tuple[str, ...],
    forbidden_message_phrases: tuple[str, ...] = (),
) -> None:
    actual_message: str = str(actual_configuration_setting_exception)
    message_mismatches: list[str] = [
        f"Missing expected diagnostic phrase {expected_message_phrase!r} in {actual_message!r}"
        for expected_message_phrase in expected_message_phrases
        if expected_message_phrase not in actual_message
    ]
    message_mismatches.extend(
        f"Protected phrase {forbidden_message_phrase!r} appeared in the diagnostic {actual_message!r}"
        for forbidden_message_phrase in forbidden_message_phrases
        if forbidden_message_phrase in actual_message
    )
    assert not message_mismatches, (
        f"Configuration setting diagnostic should include {expected_message_phrases!r} and omit protected phrases; "
        f"{len(message_mismatches)} phrase mismatch(es):\n" + "\n".join(message_mismatches)
    )


def test_ConfigurationProvider_WhenSettingsAreValid_ThenReturnsTypedSubscriptionJevAndDefaultBudgetSettings() -> None:
    expected_subscription: CodingAssistantSubscription = CodingAssistantSubscription.GITHUB_COPILOT
    required_settings: dict[str, str] = create_required_settings(expected_subscription)
    expected_jev_model: str = required_settings["JEV_MODEL"]
    expected_dining_coffee_monthly_budget: Decimal = Decimal("260.00")
    expected_shopping_monthly_budget: Decimal = Decimal("1200.00")

    configuration_provider: ConfigurationProvider = ConfigurationProvider(required_settings)

    actual_jev_settings: JevSettings = configuration_provider.get_jev_settings()
    actual_budget_settings: BudgetSettings = configuration_provider.get_budget_settings()
    assert configuration_provider.get_coding_assistant_settings().coding_assistant_subscription is expected_subscription
    _assert_jev_settings_match(actual_jev_settings, OPEN_ROUTER_BASE_URL, expected_jev_model)
    _assert_budget_settings_match(actual_budget_settings, expected_dining_coffee_monthly_budget, expected_shopping_monthly_budget)


def test_ConfigurationProvider_WhenBudgetsAreSet_ThenReturnsThem() -> None:
    expected_dining_coffee_monthly_budget: Decimal = Decimal("275.50")
    expected_shopping_monthly_budget: Decimal = Decimal("900.25")
    settings: dict[str, str] = {
        **create_required_settings(CodingAssistantSubscription.GPT_CODEX),
        "DINING_COFFEE_MONTHLY_BUDGET": str(expected_dining_coffee_monthly_budget),
        "SHOPPING_MONTHLY_BUDGET": str(expected_shopping_monthly_budget),
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
    expected_error_phrase: str = f"{family_setting_name} is derived from {SUBSCRIPTION_SETTING_NAME}"
    settings: dict[str, str] = {SUBSCRIPTION_SETTING_NAME: subscription.value, family_setting_name: ModelFamily.ANTHROPIC.value}

    with pytest.raises(ConfigurationSettingException, match=expected_error_phrase):
        ConfigurationProvider(settings)


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
    settings: dict[str, str] = create_required_settings(CodingAssistantSubscription.CLAUDE_CODE)

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases)


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
    model_family: ModelFamily = ModelFamily.ANTHROPIC if subscription is CodingAssistantSubscription.CLAUDE_CODE else ModelFamily.OPENAI
    settings: dict[str, str] = {
        **create_required_settings(subscription),
        model_setting_name: create_model_name(model_family),
        effort_setting_name: unsupported_effort,
    }

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases)


@pytest.mark.parametrize("subscription", [CodingAssistantSubscription.GPT_CODEX, CodingAssistantSubscription.GITHUB_COPILOT])
@pytest.mark.parametrize("anthropic_model_name", ["claude-haiku-5-5", "anthropic/claude-haiku-5-5"])
def test_ConfigurationProvider_WhenOpenAiSubscriptionReceivesAnthropicModel_ThenRejectsItDuringConfiguration(
    subscription: CodingAssistantSubscription, anthropic_model_name: str
) -> None:
    model_setting_name: str = "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL"
    expected_message_phrases: tuple[str, ...] = (model_setting_name, ModelFamily.OPENAI.value, subscription.value, anthropic_model_name)
    settings: dict[str, str] = {**create_required_settings(subscription), model_setting_name: anthropic_model_name}

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases)


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
    forbidden_message_phrases: tuple[str, ...] = (settings["OPEN_ROUTER_KEY"],)

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases, forbidden_message_phrases)


@pytest.mark.parametrize(
    "optional_setting_name",
    [
        "DINING_COFFEE_MONTHLY_BUDGET",
        "SHOPPING_MONTHLY_BUDGET",
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
    forbidden_message_phrases: tuple[str, ...] = (settings["OPEN_ROUTER_KEY"],)

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases, forbidden_message_phrases)


@pytest.mark.parametrize("budget_setting_name", ["DINING_COFFEE_MONTHLY_BUDGET", "SHOPPING_MONTHLY_BUDGET"])
@pytest.mark.parametrize("amount", ["0.001", "275.505", "-1.00", "0"])
def test_ConfigurationProvider_WhenBudgetIsNonpositiveOrSubcent_ThenRejectsIt(budget_setting_name: str, amount: str) -> None:
    settings: dict[str, str] = {**create_required_settings(CodingAssistantSubscription.GPT_CODEX), budget_setting_name: amount}
    expected_message_phrases: tuple[str, ...] = (budget_setting_name, "positive amounts", "at most two decimal places", repr(amount))
    forbidden_message_phrases: tuple[str, ...] = (settings["OPEN_ROUTER_KEY"],)

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases, forbidden_message_phrases)


def test_ConfigurationProvider_WhenSubscriptionIsUnknown_ThenRejectsIt() -> None:
    rejected_subscription: str = f"ANOTHER_SUBSCRIPTION_{token_hex(4)}"
    expected_message_phrases: tuple[str, ...] = (f"{SUBSCRIPTION_SETTING_NAME} must be one of", rejected_subscription)
    settings: dict[str, str] = {SUBSCRIPTION_SETTING_NAME: rejected_subscription}

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases)


def test_ConfigurationProvider_WhenJevEndpointIsInsecure_ThenRejectsIt() -> None:
    rejected_url: str = f"http://{token_hex(4)}.example/api"
    settings: dict[str, str] = {**create_required_settings(CodingAssistantSubscription.GPT_CODEX), "OPEN_ROUTER_BASE_URL": rejected_url}
    expected_message_phrases: tuple[str, ...] = ("OPEN_ROUTER_BASE_URL must be a valid HTTPS URL", rejected_url)
    forbidden_message_phrases: tuple[str, ...] = (settings["OPEN_ROUTER_KEY"],)

    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(settings)

    _assert_configuration_setting_exception_message(raised_configuration_setting_exception.value, expected_message_phrases, forbidden_message_phrases)
