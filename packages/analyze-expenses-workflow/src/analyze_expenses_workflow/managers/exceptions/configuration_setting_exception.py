from typing import final, override

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import (
    AnalyzeExpensesTechnicalBaseException,
    ExceptionAction,
    ExceptionContextInput,
    ExpenseLogEvent,
)


class ConfigurationSettingBaseException(AnalyzeExpensesTechnicalBaseException):
    def __init__(self, message: str, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        super().__init__(message, ExpenseLogEvent.CONFIGURATION, contextual_data_by_name)

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.INFRA_ACTION_REQUIRED


@final
class ConfigurationSettingMissingException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Required configuration setting is missing"


@final
class ConfigurationSettingBlankException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Configuration setting is set but blank"


@final
class CodingAssistantSubscriptionUnknownException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Coding assistant subscription is not a supported value"


@final
class CodingAssistantSettingsInvalidException(ConfigurationSettingBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Coding assistant subscription maps to an invalid model family"


@final
class ModelFamilyOverrideRejectedException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Model family setting overrides the family derived from the subscription"


@final
class MonthlyBudgetSettingInvalidException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Monthly budget setting is not a positive amount with at most two decimal places"


@final
class LlmModelSettingMissingException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "LLM operation model setting is required for the model family"


@final
class LlmModelFamilyMismatchException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "LLM operation model belongs to another model family"


@final
class LlmModelSettingInvalidException(ConfigurationSettingBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "LLM operation model produced invalid operation settings"


@final
class ReasoningEffortSettingUnknownException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Reasoning effort setting is not a known effort"


@final
class ReasoningEffortUnsupportedException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Reasoning effort is not offered by the coding assistant subscription"


@final
class LlmOperationNotConfiguredException(ConfigurationSettingBaseException):
    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.DEVELOPER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "LLM operation has no loaded settings"


@final
class OpenRouterBaseUrlSettingInvalidException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Open Router base URL setting is not a valid HTTPS URL"


@final
class JevMinimumChoiceProbabilitySettingInvalidException(ConfigurationSettingBaseException):
    @property
    @override
    def reason(self) -> str:
        return "Jev minimum choice probability setting is not between zero and one"
