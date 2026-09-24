from typing import final

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator


@final
class JevSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    open_router_key: SecretStr
    open_router_base_url: HttpUrl
    jev_model: str = Field(min_length=1)
    parsing_minimum_choice_probability: float = Field(default=0.8, ge=0, le=1)

    @field_validator("open_router_base_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("OPEN_ROUTER_BASE_URL must use HTTPS")
        return value
