from __future__ import annotations

from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from context_compaction.managers.exceptions.context_compaction_exception import (
    RolloutMessageInvalidException,
    RolloutToolCallInvalidException,
    RolloutToolOutputInvalidException,
    RolloutToolOutputNonTextContentException,
)
from context_compaction.managers.processors.models.rollout_resources import (
    RolloutMessageResource,
    RolloutResponseResource,
    RolloutTextResource,
    RolloutToolCallResource,
    RolloutToolOutputResource,
)


class ValidatorRolloutResponse:
    @staticmethod
    def validate(response_value_by_field_name: Mapping[str, object]) -> RolloutResponseResource | None:
        response_type: object = response_value_by_field_name.get("type")
        if response_type == "message":
            return ValidatorRolloutResponse._validate_message(response_value_by_field_name)
        if response_type == "custom_tool_call":
            return ValidatorRolloutResponse._validate_tool_call(response_value_by_field_name)
        if response_type == "custom_tool_call_output":
            return ValidatorRolloutResponse._validate_tool_output(response_value_by_field_name)
        return None

    @staticmethod
    def _validate_message(response_value_by_field_name: Mapping[str, object]) -> RolloutMessageResource | None:
        role: object = response_value_by_field_name.get("role")
        if isinstance(role, str) and role not in {"user", "assistant"}:
            return None
        try:
            return RolloutMessageResource.model_validate(response_value_by_field_name)
        except ValidationError as error:
            raise RolloutMessageInvalidException from error

    @staticmethod
    def _validate_tool_call(response_value_by_field_name: Mapping[str, object]) -> RolloutToolCallResource:
        try:
            return RolloutToolCallResource.model_validate(response_value_by_field_name)
        except ValidationError as error:
            raise RolloutToolCallInvalidException from error

    @staticmethod
    def _validate_tool_output(response_value_by_field_name: Mapping[str, object]) -> RolloutToolOutputResource:
        try:
            return RolloutToolOutputResource.model_validate(response_value_by_field_name)
        except ValidationError as error:
            raise RolloutToolOutputInvalidException from error

    @staticmethod
    def validate_tool_output_text(rollout_tool_output_content: object) -> list[RolloutTextResource]:
        try:
            return TypeAdapter(list[RolloutTextResource]).validate_python(rollout_tool_output_content)
        except ValidationError as error:
            raise RolloutToolOutputNonTextContentException from error
