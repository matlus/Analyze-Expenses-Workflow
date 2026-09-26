from __future__ import annotations

from typing import ClassVar, Literal, final

from pydantic import BaseModel, ConfigDict, Field


@final
class RolloutTextResource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="ignore")
    type: Literal["text", "input_text", "output_text"] | None = None
    text: str


@final
class RolloutMessageResource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="ignore")
    type: Literal["message"]
    role: Literal["user", "assistant"]
    content: tuple[RolloutTextResource, ...] = Field(strict=False)


@final
class RolloutToolCallResource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="ignore")
    type: Literal["custom_tool_call"]
    call_id: str = Field(min_length=1)
    name: str
    input: str


@final
class RolloutToolOutputResource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="ignore")
    type: Literal["custom_tool_call_output"]
    call_id: str = Field(min_length=1)
    output: object


type RolloutResponseResource = RolloutMessageResource | RolloutToolCallResource | RolloutToolOutputResource
