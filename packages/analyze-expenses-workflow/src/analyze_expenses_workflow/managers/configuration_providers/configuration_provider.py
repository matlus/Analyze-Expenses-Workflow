from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from pydantic import ValidationError

from analyze_expenses_workflow.managers.configuration_providers.settings_models.budget_settings import BudgetSettings
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    MODEL_FAMILY_BY_SUBSCRIPTION,
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
from analyze_expenses_workflow.managers.configuration_providers.settings_providers.settings_provider_environment import SettingsProviderEnvironment
from analyze_expenses_workflow.managers.exceptions.configuration_setting_exception import ConfigurationSettingException

_DEFAULT_REASONING_EFFORT_BY_LLM_OPERATION: Final[Mapping[LlmOperation, ReasoningEffort]] = MappingProxyType(
    {
        LlmOperation.EXPENSE_LINE_EXTRACTION: ReasoningEffort.LOW,
        LlmOperation.EXPENSE_FINDINGS: ReasoningEffort.MEDIUM,
        LlmOperation.EXPENSE_CATEGORIZATION: ReasoningEffort.LOW,
    }
)
_COPILOT_SUPPORTED_REASONING_EFFORTS: Final[frozenset[ReasoningEffort]] = frozenset(
    {ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH, ReasoningEffort.XHIGH, ReasoningEffort.MAX}
)


class ConfigurationProvider:
    _CODING_ASSISTANT_SUBSCRIPTION_SETTING_NAME: Final[str] = "CODING_ASSISTANT_SUBSCRIPTION"
    _CODING_ASSISTANT_MODEL_FAMILY_SETTING_NAME: Final[str] = "CODING_ASSISTANT_MODEL_FAMILY"
    _DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME: Final[str] = "DINING_COFFEE_MONTHLY_BUDGET"
    _SHOPPING_MONTHLY_BUDGET_SETTING_NAME: Final[str] = "SHOPPING_MONTHLY_BUDGET"
    _OPEN_ROUTER_BASE_URL_SETTING_NAME: Final[str] = "OPEN_ROUTER_BASE_URL"

    def __init__(self, override_by_environment_variable_name: Mapping[str, str] | None = None) -> None:
        settings_provider_environment: SettingsProviderEnvironment = SettingsProviderEnvironment(override_by_environment_variable_name)
        self._coding_assistant_settings: CodingAssistantSettings = self._load_coding_assistant_settings(settings_provider_environment)
        self._budget_settings: BudgetSettings = self._load_budget_settings(settings_provider_environment)
        self._llm_operation_settings: dict[LlmOperation, LlmOperationSettings] = self._load_llm_operation_settings(
            settings_provider_environment, self._coding_assistant_settings
        )
        self._jev_settings: JevSettings = self._load_jev_settings(settings_provider_environment)

    @classmethod
    def _load_coding_assistant_settings(cls, settings_provider_environment: SettingsProviderEnvironment) -> CodingAssistantSettings:
        subscription_value: str = cls._require(settings_provider_environment, cls._CODING_ASSISTANT_SUBSCRIPTION_SETTING_NAME)
        subscription: CodingAssistantSubscription = cls._parse_subscription(subscription_value)
        cls._reject_model_family_override(settings_provider_environment, cls._CODING_ASSISTANT_MODEL_FAMILY_SETTING_NAME)
        family: ModelFamily = MODEL_FAMILY_BY_SUBSCRIPTION[subscription]
        if subscription == CodingAssistantSubscription.CLAUDE_CODE:
            raise ConfigurationSettingException(
                f"{cls._CODING_ASSISTANT_SUBSCRIPTION_SETTING_NAME}=CLAUDE_CODE maps to ANTHROPIC; SDK unavailable; use GPT_CODEX or GITHUB_COPILOT"
            )
        try:
            return CodingAssistantSettings(coding_assistant_subscription=subscription, model_family=family)
        except ValidationError as exc:
            raise ConfigurationSettingException(
                f"{cls._CODING_ASSISTANT_SUBSCRIPTION_SETTING_NAME}={subscription.value} produced invalid coding assistant settings"
            ) from exc

    @classmethod
    def _reject_model_family_override(cls, settings_provider_environment: SettingsProviderEnvironment, setting_name: str) -> None:
        if settings_provider_environment.read_setting_value(setting_name) is not None:
            raise ConfigurationSettingException(f"{setting_name} is derived from CODING_ASSISTANT_SUBSCRIPTION and must be removed")

    @classmethod
    def _parse_subscription(cls, subscription_value: str) -> CodingAssistantSubscription:
        try:
            return CodingAssistantSubscription(subscription_value)
        except ValueError as exc:
            valid_values: str = ", ".join(subscription.value for subscription in CodingAssistantSubscription)
            raise ConfigurationSettingException(
                f"{cls._CODING_ASSISTANT_SUBSCRIPTION_SETTING_NAME} must be one of: {valid_values}; received {subscription_value!r}"
            ) from exc

    @classmethod
    def _load_budget_settings(cls, settings_provider_environment: SettingsProviderEnvironment) -> BudgetSettings:
        dining_budget: str = cls._optional(settings_provider_environment, cls._DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME) or "260.00"
        shopping_budget: str = cls._optional(settings_provider_environment, cls._SHOPPING_MONTHLY_BUDGET_SETTING_NAME) or "1200.00"
        return cls._build_budget_settings(dining_budget, shopping_budget)

    @classmethod
    def _build_budget_settings(cls, dining_budget: str, shopping_budget: str) -> BudgetSettings:
        try:
            return BudgetSettings.model_validate(
                {
                    "dining_coffee_monthly_budget": dining_budget,
                    "shopping_monthly_budget": shopping_budget,
                }
            )
        except ValidationError as exc:
            budget_setting_names: str = f"{cls._DINING_COFFEE_MONTHLY_BUDGET_SETTING_NAME} and {cls._SHOPPING_MONTHLY_BUDGET_SETTING_NAME}"
            raise ConfigurationSettingException(
                f"{budget_setting_names} must be positive amounts with at most two decimal places; received {dining_budget!r} and {shopping_budget!r}"
            ) from exc

    @classmethod
    def _load_llm_operation_settings(
        cls, settings_provider_environment: SettingsProviderEnvironment, coding_assistant_settings: CodingAssistantSettings
    ) -> dict[LlmOperation, LlmOperationSettings]:
        return {
            operation: cls._load_one_llm_operation_setting(settings_provider_environment, operation, coding_assistant_settings)
            for operation in LlmOperation
        }

    @classmethod
    def _load_one_llm_operation_setting(
        cls,
        settings_provider_environment: SettingsProviderEnvironment,
        operation: LlmOperation,
        coding_assistant_settings: CodingAssistantSettings,
    ) -> LlmOperationSettings:
        cls._reject_model_family_override(settings_provider_environment, f"{operation.value}_MODEL_FAMILY")
        family: ModelFamily = coding_assistant_settings.model_family
        model_setting_name: str = f"{operation.value}_{family.value}_MODEL"
        effort_setting_name: str = f"{operation.value}_{family.value}_REASONING_EFFORT"
        model_name: str | None = cls._optional(settings_provider_environment, model_setting_name)
        reasoning_effort_value: str | None = cls._optional(settings_provider_environment, effort_setting_name)
        if family == ModelFamily.OPENAI:
            if model_name is None:
                legacy_model_setting_name: str = f"{operation.value}_MODEL"
                model_name = cls._optional(settings_provider_environment, legacy_model_setting_name)
                if model_name is not None:
                    model_setting_name = legacy_model_setting_name
            model_name = model_name or "gpt-6-luna"
            if reasoning_effort_value is None:
                effort_setting_name = f"{operation.value}_REASONING_EFFORT"
                reasoning_effort_value = cls._optional(settings_provider_environment, effort_setting_name)
            reasoning_effort_value = reasoning_effort_value or _DEFAULT_REASONING_EFFORT_BY_LLM_OPERATION[operation].value
        elif model_name is None:
            raise ConfigurationSettingException(f"{model_setting_name} is required for the selected model family")
        return cls._build_llm_operation_setting(
            family,
            model_name,
            model_setting_name,
            reasoning_effort_value,
            effort_setting_name,
            coding_assistant_settings.coding_assistant_subscription,
        )

    @staticmethod
    def _build_llm_operation_setting(
        family: ModelFamily,
        model_name: str,
        model_setting_name: str,
        reasoning_effort_value: str | None,
        effort_setting_name: str,
        subscription: CodingAssistantSubscription,
    ) -> LlmOperationSettings:
        normalized_model_name: str = model_name.casefold()
        if family == ModelFamily.OPENAI and normalized_model_name.startswith(("claude-", "anthropic/")):
            raise ConfigurationSettingException(f"{model_setting_name} must name an OPENAI model for {subscription.value}; received {model_name!r}")
        if family == ModelFamily.ANTHROPIC and normalized_model_name.startswith(("gpt-", "codex-", "openai/")):
            raise ConfigurationSettingException(
                f"{model_setting_name} must name an ANTHROPIC model for {subscription.value}; received {model_name!r}"
            )
        try:
            reasoning_effort: ReasoningEffort | None = ReasoningEffort(reasoning_effort_value) if reasoning_effort_value is not None else None
        except ValueError as exc:
            valid_reasoning_efforts: str = ", ".join(item.value for item in ReasoningEffort)
            raise ConfigurationSettingException(
                f"{effort_setting_name} must be one of: {valid_reasoning_efforts}; received {reasoning_effort_value!r}"
            ) from exc
        if (
            subscription == CodingAssistantSubscription.GITHUB_COPILOT
            and reasoning_effort is not None
            and reasoning_effort not in _COPILOT_SUPPORTED_REASONING_EFFORTS
        ):
            supported_efforts: str = ", ".join(effort.value for effort in ReasoningEffort if effort in _COPILOT_SUPPORTED_REASONING_EFFORTS)
            raise ConfigurationSettingException(
                f"{effort_setting_name} must be one of: {supported_efforts} for GITHUB_COPILOT; received {reasoning_effort_value!r}"
            )
        try:
            return LlmOperationSettings(model=model_name, reasoning_effort=reasoning_effort, model_family=family)
        except ValidationError as exc:
            raise ConfigurationSettingException(f"{model_setting_name} is invalid; received {model_name!r}") from exc

    @classmethod
    def _load_jev_settings(cls, settings_provider_environment: SettingsProviderEnvironment) -> JevSettings:
        open_router_base_url: str = cls._require(settings_provider_environment, cls._OPEN_ROUTER_BASE_URL_SETTING_NAME)
        jev_model: str = cls._require(settings_provider_environment, "JEV_MODEL")
        open_router_key: str = cls._require(settings_provider_environment, "OPEN_ROUTER_KEY")
        minimum_probability: str = cls._optional(settings_provider_environment, "JEV_PARSING_MINIMUM_CHOICE_PROBABILITY") or "0.8"
        return cls._build_jev_settings(open_router_base_url, jev_model, open_router_key, minimum_probability)

    @classmethod
    def _build_jev_settings(cls, open_router_base_url: str, jev_model: str, open_router_key: str, minimum_probability: str) -> JevSettings:
        try:
            return JevSettings.model_validate(
                {
                    "open_router_key": open_router_key,
                    "open_router_base_url": open_router_base_url,
                    "jev_model": jev_model,
                    "parsing_minimum_choice_probability": minimum_probability,
                }
            )
        except ValidationError as exc:
            if any("open_router_base_url" in error["loc"] for error in exc.errors()):
                raise ConfigurationSettingException(
                    f"{cls._OPEN_ROUTER_BASE_URL_SETTING_NAME} must be a valid HTTPS URL; received {open_router_base_url!r}"
                ) from exc
            raise ConfigurationSettingException(
                f"JEV_PARSING_MINIMUM_CHOICE_PROBABILITY must be between zero and one; received {minimum_probability!r}"
            ) from exc

    @staticmethod
    def _require(settings_provider_environment: SettingsProviderEnvironment, configuration_setting_name: str) -> str:
        configuration_setting_value: str | None = settings_provider_environment.read_setting_value(configuration_setting_name)
        if configuration_setting_value is None or not configuration_setting_value.strip():
            raise ConfigurationSettingException(f"{configuration_setting_name} is required and must not be blank")
        return configuration_setting_value.strip()

    @staticmethod
    def _optional(settings_provider_environment: SettingsProviderEnvironment, configuration_setting_name: str) -> str | None:
        configuration_setting_value: str | None = settings_provider_environment.read_setting_value(configuration_setting_name)
        if configuration_setting_value is None:
            return None
        cleaned_configuration_setting_value: str = configuration_setting_value.strip()
        if not cleaned_configuration_setting_value:
            raise ConfigurationSettingException(f"{configuration_setting_name} must not be blank when provided")
        return cleaned_configuration_setting_value

    def get_coding_assistant_settings(self) -> CodingAssistantSettings:
        return self._coding_assistant_settings

    def get_jev_settings(self) -> JevSettings:
        return self._jev_settings

    def get_budget_settings(self) -> BudgetSettings:
        return self._budget_settings

    def get_llm_operation_settings(self, operation: LlmOperation) -> LlmOperationSettings:
        try:
            return self._llm_operation_settings[operation]
        except KeyError as exc:
            available_operations: str = ", ".join(item.value for item in self._llm_operation_settings)
            raise ConfigurationSettingException(f"{operation.value} is not configured; available operations: {available_operations}") from exc
