from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Self, final

from pydantic import BaseModel, ConfigDict, model_validator


class CodingAssistantSubscription(StrEnum):
    GITHUB_COPILOT = "GITHUB_COPILOT"
    GPT_CODEX = "GPT_CODEX"
    CLAUDE_CODE = "CLAUDE_CODE"


class ModelFamily(StrEnum):
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"


MODEL_FAMILY_BY_SUBSCRIPTION: Final[Mapping[CodingAssistantSubscription, ModelFamily]] = MappingProxyType(
    {
        CodingAssistantSubscription.GPT_CODEX: ModelFamily.OPENAI,
        CodingAssistantSubscription.CLAUDE_CODE: ModelFamily.ANTHROPIC,
        CodingAssistantSubscription.GITHUB_COPILOT: ModelFamily.OPENAI,
    }
)


@final
class CodingAssistantSettings(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    coding_assistant_subscription: CodingAssistantSubscription
    model_family: ModelFamily

    @model_validator(mode="after")
    def require_sdk_model_family(self) -> Self:
        if self.model_family != MODEL_FAMILY_BY_SUBSCRIPTION[self.coding_assistant_subscription]:
            raise ValueError("Model family must match the selected coding assistant SDK")
        return self
