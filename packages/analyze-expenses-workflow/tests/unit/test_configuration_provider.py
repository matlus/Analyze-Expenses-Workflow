from decimal import Decimal
from pathlib import Path
from secrets import token_hex

import pytest
from pydantic import ValidationError

from analyze_expenses_workflow import ConfigurationSettingException
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
    actual_llm_operation_settings: LlmOperationSettings, expected_model_name: str, expected_reasoning_effort: ReasoningEffort
) -> None:
    operation_setting_mismatches: list[str] = []
    if actual_llm_operation_settings.model != expected_model_name:
        operation_setting_mismatches.append(f"Model: expected {expected_model_name!r}, got {actual_llm_operation_settings.model!r}")
    if actual_llm_operation_settings.reasoning_effort is not expected_reasoning_effort:
        operation_setting_mismatches.append(
            f"Reasoning effort: expected {expected_reasoning_effort!r}, got {actual_llm_operation_settings.reasoning_effort!r}"
        )
    assert not operation_setting_mismatches, (
        f"LLM operation settings should match model {expected_model_name!r} and reasoning effort {expected_reasoning_effort!r}; "
        f"{len(operation_setting_mismatches)} property mismatch(es):\n" + "\n".join(operation_setting_mismatches)
    )


def _assert_configuration_setting_exception_message(
    actual_configuration_setting_exception: ConfigurationSettingException,
    expected_message_phrases: tuple[str, ...],
    forbidden_message_phrases: tuple[str, ...] = (),
) -> None:
    actual_message: str = str(actual_configuration_setting_exception)
    message_mismatches: list[str] = [
        f"Missing expected diagnostic phrase {expected_message_phrase!r}"
        for expected_message_phrase in expected_message_phrases
        if expected_message_phrase not in actual_message
    ]
    message_mismatches.extend(
        "Protected phrase appeared in the diagnostic"
        for forbidden_message_phrase in forbidden_message_phrases
        if forbidden_message_phrase in actual_message
    )
    assert not message_mismatches, (
        f"Configuration setting diagnostic should include {expected_message_phrases!r} and omit protected phrases; "
        f"{len(message_mismatches)} phrase mismatch(es):\n" + "\n".join(message_mismatches)
    )


def test_ConfigurationProvider_WhenSettingsAreValid_ThenReturnsTypedSubscriptionAndJevSettings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected_open_router_base_url: str = "https://openrouter.ai/api"
    expected_jev_model: str = f"jev-{token_hex(8)}"
    open_router_key: str = token_hex(16)
    expected_dining_coffee_monthly_budget: Decimal = Decimal("260.00")
    expected_shopping_monthly_budget: Decimal = Decimal("1200.00")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DINING_COFFEE_MONTHLY_BUDGET", raising=False)
    monkeypatch.delenv("SHOPPING_MONTHLY_BUDGET", raising=False)
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "CODING_ASSISTANT_SUBSCRIPTION": "GITHUB_COPILOT",
            "OPEN_ROUTER_KEY": open_router_key,
            "OPEN_ROUTER_BASE_URL": expected_open_router_base_url,
            "JEV_MODEL": expected_jev_model,
        }
    )

    assert configuration_provider.get_coding_assistant_settings().coding_assistant_subscription is CodingAssistantSubscription.GITHUB_COPILOT
    actual_jev_settings: JevSettings = configuration_provider.get_jev_settings()
    _assert_jev_settings_match(actual_jev_settings, expected_open_router_base_url, expected_jev_model)
    actual_budget_settings: BudgetSettings = configuration_provider.get_budget_settings()
    _assert_budget_settings_match(actual_budget_settings, expected_dining_coffee_monthly_budget, expected_shopping_monthly_budget)


def test_ConfigurationProvider_WhenOperationOverridesAreSet_ThenReturnsTheirModelEffortAndBudget() -> None:
    expected_extraction_model_name: str = f"gpt-{token_hex(8)}"
    expected_findings_model_name: str = f"gpt-{token_hex(8)}"
    expected_extraction_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    expected_findings_reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH
    expected_dining_coffee_monthly_budget: Decimal = Decimal("275.50")
    open_router_key: str = token_hex(16)
    jev_model: str = f"jev-{token_hex(8)}"
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
            "OPEN_ROUTER_KEY": open_router_key,
            "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
            "JEV_MODEL": jev_model,
            "EXPENSE_LINE_EXTRACTION_MODEL": expected_extraction_model_name,
            "EXPENSE_LINE_EXTRACTION_REASONING_EFFORT": expected_extraction_reasoning_effort.value,
            "EXPENSE_FINDINGS_MODEL": expected_findings_model_name,
            "EXPENSE_FINDINGS_REASONING_EFFORT": expected_findings_reasoning_effort.value,
            "DINING_COFFEE_MONTHLY_BUDGET": str(expected_dining_coffee_monthly_budget),
        }
    )

    actual_extraction_settings: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
    _assert_llm_operation_settings_match(actual_extraction_settings, expected_extraction_model_name, expected_extraction_reasoning_effort)
    actual_findings_settings: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_FINDINGS)
    _assert_llm_operation_settings_match(actual_findings_settings, expected_findings_model_name, expected_findings_reasoning_effort)
    assert configuration_provider.get_budget_settings().dining_coffee_monthly_budget == expected_dining_coffee_monthly_budget


@pytest.mark.parametrize("subscription", [CodingAssistantSubscription.GPT_CODEX, CodingAssistantSubscription.GITHUB_COPILOT])
def test_ConfigurationProvider_WhenSdkIsAvailable_ThenSelectsItsOpenAiSettingsForEveryOperation(
    subscription: CodingAssistantSubscription,
) -> None:
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "CODING_ASSISTANT_SUBSCRIPTION": subscription.value,
            "OPEN_ROUTER_KEY": token_hex(16),
            "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
            "JEV_MODEL": "jev-1.13",
            "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": "gpt-extraction-example",
            "EXPENSE_LINE_EXTRACTION_ANTHROPIC_MODEL": "claude-ignored-example",
            "EXPENSE_FINDINGS_OPENAI_MODEL": "gpt-findings-example",
            "EXPENSE_CATEGORIZATION_OPENAI_MODEL": "gpt-category-example",
        }
    )
    assert configuration_provider.get_coding_assistant_settings().model_family is ModelFamily.OPENAI
    extraction: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
    findings: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_FINDINGS)
    categorization: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_CATEGORIZATION)
    assert (extraction.model_family, extraction.model) == (ModelFamily.OPENAI, "gpt-extraction-example")
    assert (findings.model_family, findings.model) == (ModelFamily.OPENAI, "gpt-findings-example")
    assert (categorization.model_family, categorization.model) == (ModelFamily.OPENAI, "gpt-category-example")


@pytest.mark.parametrize(
    "family_setting_name",
    ["CODING_ASSISTANT_MODEL_FAMILY", "EXPENSE_LINE_EXTRACTION_MODEL_FAMILY", "EXPENSE_FINDINGS_MODEL_FAMILY"],
)
@pytest.mark.parametrize("subscription", ["GPT_CODEX", "GITHUB_COPILOT"])
def test_ConfigurationProvider_WhenFamilyOverrideIsSupplied_ThenRejectsIt(family_setting_name: str, subscription: str) -> None:
    with pytest.raises(ConfigurationSettingException, match=f"{family_setting_name} is derived from CODING_ASSISTANT_SUBSCRIPTION"):
        ConfigurationProvider({"CODING_ASSISTANT_SUBSCRIPTION": subscription, family_setting_name: "ANTHROPIC"})


def test_ConfigurationProvider_WhenClaudeCodeIsSelected_ThenRejectsTheUnavailableGateway() -> None:
    with pytest.raises(ConfigurationSettingException, match="CODING_ASSISTANT_SUBSCRIPTION=CLAUDE_CODE maps to ANTHROPIC"):
        ConfigurationProvider({"CODING_ASSISTANT_SUBSCRIPTION": "CLAUDE_CODE"})


@pytest.mark.parametrize("subscription", [CodingAssistantSubscription.GPT_CODEX, CodingAssistantSubscription.GITHUB_COPILOT])
@pytest.mark.parametrize("model_setting_name", ["EXPENSE_LINE_EXTRACTION_MODEL", "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL"])
@pytest.mark.parametrize("anthropic_model_name", ["claude-haiku-4.5", "anthropic/claude-haiku-4.5"])
def test_ConfigurationProvider_WhenOpenAiSdkReceivesAnthropicModel_ThenRejectsItDuringConfiguration(
    subscription: CodingAssistantSubscription, model_setting_name: str, anthropic_model_name: str
) -> None:
    with pytest.raises(ConfigurationSettingException) as raised_configuration_setting_exception:
        ConfigurationProvider(
            {
                "CODING_ASSISTANT_SUBSCRIPTION": subscription.value,
                model_setting_name: anthropic_model_name,
            }
        )

    _assert_configuration_setting_exception_message(
        raised_configuration_setting_exception.value,
        (model_setting_name, "OPENAI", subscription.value, anthropic_model_name),
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
    settings: CodingAssistantSettings = CodingAssistantSettings(coding_assistant_subscription=subscription, model_family=family)

    assert settings.model_family is family


@pytest.mark.parametrize(
    ("subscription", "family"),
    [
        (CodingAssistantSubscription.GPT_CODEX, ModelFamily.ANTHROPIC),
        (CodingAssistantSubscription.GITHUB_COPILOT, ModelFamily.ANTHROPIC),
        (CodingAssistantSubscription.CLAUDE_CODE, ModelFamily.OPENAI),
    ],
)
def test_CodingAssistantSettings_WhenSdkAndFamilyDisagree_ThenRejectsThePair(subscription: CodingAssistantSubscription, family: ModelFamily) -> None:
    with pytest.raises(ValidationError, match="Model family must match the selected coding assistant SDK"):
        CodingAssistantSettings(coding_assistant_subscription=subscription, model_family=family)


@pytest.mark.parametrize("unsupported_effort", ["none", "minimal", "ultra"])
def test_copilot_rejects_unsupported_reasoning_effort_during_configuration(unsupported_effort: str) -> None:
    with pytest.raises(ConfigurationSettingException, match=r"EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT.*GITHUB_COPILOT"):
        ConfigurationProvider(
            {
                "CODING_ASSISTANT_SUBSCRIPTION": "GITHUB_COPILOT",
                "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT": unsupported_effort,
            }
        )


def test_family_specific_openai_settings_precede_legacy_operation_settings() -> None:
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
            "OPEN_ROUTER_KEY": token_hex(16),
            "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
            "JEV_MODEL": "jev-1.13",
            "EXPENSE_LINE_EXTRACTION_MODEL": "legacy-model",
            "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": "selected-model",
            "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT": "xhigh",
        }
    )
    extraction: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
    assert (extraction.model, extraction.reasoning_effort, extraction.model_family) == (
        "selected-model",
        ReasoningEffort.XHIGH,
        ModelFamily.OPENAI,
    )


def test_ConfigurationProvider_WhenReasoningEffortIsUnset_ThenUsesOperationDefaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    expected_extraction_reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    expected_findings_reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    open_router_key: str = token_hex(16)
    jev_model: str = f"jev-{token_hex(8)}"
    extraction_model_name: str = f"gpt-{token_hex(8)}"
    findings_model_name: str = f"gpt-{token_hex(8)}"
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXPENSE_LINE_EXTRACTION_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("EXPENSE_FINDINGS_REASONING_EFFORT", raising=False)
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
            "OPEN_ROUTER_KEY": open_router_key,
            "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
            "JEV_MODEL": jev_model,
            "EXPENSE_LINE_EXTRACTION_MODEL": extraction_model_name,
            "EXPENSE_FINDINGS_MODEL": findings_model_name,
            "DINING_COFFEE_MONTHLY_BUDGET": "260.00",
            "SHOPPING_MONTHLY_BUDGET": "1200.00",
        }
    )

    assert (
        configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION).reasoning_effort
        is expected_extraction_reasoning_effort
    )
    assert configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_FINDINGS).reasoning_effort is expected_findings_reasoning_effort


def test_ConfigurationProvider_WhenEffortOrBudgetIsInvalid_ThenExplainsTheConstraint() -> None:
    open_router_key: str = token_hex(16)
    valid_configuration_value_by_environment_variable_name: dict[str, str] = {
        "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
        "OPEN_ROUTER_KEY": open_router_key,
        "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
        "JEV_MODEL": "jev-1.13",
    }
    expected_rejected_effort: str = "extreme"
    expected_effort_error_phrase: str = "EXPENSE_FINDINGS_REASONING_EFFORT must be one of"
    expected_budget_error_phrase: str = "SHOPPING_MONTHLY_BUDGET must be positive amounts with at most two decimal places"
    expected_effort_message_phrases: tuple[str, ...] = (
        expected_rejected_effort,
        *(reasoning_effort.value for reasoning_effort in ReasoningEffort),
    )
    expected_budget_message_phrases: tuple[str, ...] = (
        "SHOPPING_MONTHLY_BUDGET",
        "positive amounts",
        "at most two decimal places",
    )
    forbidden_message_phrases: tuple[str, ...] = (open_router_key,)
    with pytest.raises(ConfigurationSettingException, match=expected_effort_error_phrase) as exception_info:
        ConfigurationProvider(
            {**valid_configuration_value_by_environment_variable_name, "EXPENSE_FINDINGS_REASONING_EFFORT": expected_rejected_effort}
        )
    _assert_configuration_setting_exception_message(exception_info.value, expected_effort_message_phrases, forbidden_message_phrases)
    with pytest.raises(ConfigurationSettingException, match=expected_budget_error_phrase) as budget_exception_info:
        ConfigurationProvider({**valid_configuration_value_by_environment_variable_name, "SHOPPING_MONTHLY_BUDGET": "-20"})
    _assert_configuration_setting_exception_message(budget_exception_info.value, expected_budget_message_phrases, forbidden_message_phrases)


@pytest.mark.parametrize(
    "optional_setting_environment_variable_name",
    [
        "DINING_COFFEE_MONTHLY_BUDGET",
        "SHOPPING_MONTHLY_BUDGET",
        "EXPENSE_LINE_EXTRACTION_MODEL",
        "EXPENSE_LINE_EXTRACTION_REASONING_EFFORT",
        "EXPENSE_FINDINGS_MODEL",
        "EXPENSE_FINDINGS_REASONING_EFFORT",
    ],
)
def test_ConfigurationProvider_WhenOptionalSettingIsBlank_ThenRejectsIt(optional_setting_environment_variable_name: str) -> None:
    open_router_key: str = token_hex(16)
    valid_configuration_value_by_environment_variable_name: dict[str, str] = {
        "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
        "OPEN_ROUTER_KEY": open_router_key,
        "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
        "JEV_MODEL": "jev-1.13",
    }

    expected_error_phrase: str = f"{optional_setting_environment_variable_name} must not be blank"
    expected_message_phrases: tuple[str, ...] = (expected_error_phrase,)
    forbidden_message_phrases: tuple[str, ...] = (open_router_key,)
    with pytest.raises(ConfigurationSettingException, match=expected_error_phrase) as exception_info:
        ConfigurationProvider({**valid_configuration_value_by_environment_variable_name, optional_setting_environment_variable_name: "  "})
    _assert_configuration_setting_exception_message(exception_info.value, expected_message_phrases, forbidden_message_phrases)


@pytest.mark.parametrize("amount", ["0.001", "275.505", "-1.00"])
def test_ConfigurationProvider_WhenBudgetIsNonpositiveOrSubcent_ThenRejectsIt(amount: str) -> None:
    open_router_key: str = token_hex(16)
    valid_configuration_value_by_environment_variable_name: dict[str, str] = {
        "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
        "OPEN_ROUTER_KEY": open_router_key,
        "OPEN_ROUTER_BASE_URL": "https://openrouter.ai/api",
        "JEV_MODEL": "jev-1.13",
    }

    expected_error_phrase: str = "DINING_COFFEE_MONTHLY_BUDGET.*positive amounts with at most two decimal places"
    expected_message_phrases: tuple[str, ...] = (
        "DINING_COFFEE_MONTHLY_BUDGET",
        "positive amounts",
        "at most two decimal places",
    )
    forbidden_message_phrases: tuple[str, ...] = (open_router_key,)
    with pytest.raises(ConfigurationSettingException, match=expected_error_phrase) as exception_info:
        ConfigurationProvider({**valid_configuration_value_by_environment_variable_name, "DINING_COFFEE_MONTHLY_BUDGET": amount})
    _assert_configuration_setting_exception_message(exception_info.value, expected_message_phrases, forbidden_message_phrases)


def test_ConfigurationProvider_WhenSubscriptionIsUnknown_ThenRejectsIt() -> None:
    expected_error_phrase: str = "CODING_ASSISTANT_SUBSCRIPTION must be one of"
    with pytest.raises(ConfigurationSettingException, match=expected_error_phrase):
        ConfigurationProvider({"CODING_ASSISTANT_SUBSCRIPTION": "ANOTHER_SUBSCRIPTION"})


def test_ConfigurationProvider_WhenJevEndpointIsInsecure_ThenRejectsIt() -> None:
    expected_rejected_url: str = "http://openrouter.ai/api"
    expected_error_phrase: str = "OPEN_ROUTER_BASE_URL must be a valid HTTPS URL"
    open_router_key: str = token_hex(16)
    expected_message_phrases: tuple[str, ...] = (expected_rejected_url,)
    forbidden_message_phrases: tuple[str, ...] = (open_router_key,)
    with pytest.raises(ConfigurationSettingException, match=expected_error_phrase) as exception_info:
        ConfigurationProvider(
            {
                "CODING_ASSISTANT_SUBSCRIPTION": "GPT_CODEX",
                "OPEN_ROUTER_KEY": open_router_key,
                "OPEN_ROUTER_BASE_URL": expected_rejected_url,
                "JEV_MODEL": "jev-1.13",
            }
        )
    _assert_configuration_setting_exception_message(
        exception_info.value,
        expected_message_phrases=expected_message_phrases,
        forbidden_message_phrases=forbidden_message_phrases,
    )
