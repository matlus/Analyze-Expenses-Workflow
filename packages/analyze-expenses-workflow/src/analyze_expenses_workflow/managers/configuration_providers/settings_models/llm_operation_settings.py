from enum import StrEnum
from typing import final

from pydantic import BaseModel, ConfigDict, Field

from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import ModelFamily


class LlmOperation(StrEnum):
    EXPENSE_LINE_EXTRACTION = "EXPENSE_LINE_EXTRACTION"
    EXPENSE_FINDINGS = "EXPENSE_FINDINGS"
    EXPENSE_CATEGORIZATION = "EXPENSE_CATEGORIZATION"


class ReasoningEffort(StrEnum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"
    ULTRA = "ultra"


@final
class LlmOperationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort | None
    model_family: ModelFamily = ModelFamily.OPENAI
