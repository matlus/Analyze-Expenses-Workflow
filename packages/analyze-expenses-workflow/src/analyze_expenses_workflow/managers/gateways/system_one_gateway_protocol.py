from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChoiceQuestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    instructions: str
    criteria: dict[str, str]


class ChoiceDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    choice: str
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    probabilities: dict[str, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]]

    @model_validator(mode="after")
    def require_supported_choice(self) -> Self:
        # Jev can make an explicit second-stage choice after scoring higher-cardinality options.
        if self.choice not in self.probabilities:
            raise ValueError("Jev choice must be one of the scored options")
        return self


class SystemOneGatewayProtocol(Protocol):
    async def choose(self, state: str, question: ChoiceQuestion) -> ChoiceDecision: ...

    async def close(self) -> None: ...
