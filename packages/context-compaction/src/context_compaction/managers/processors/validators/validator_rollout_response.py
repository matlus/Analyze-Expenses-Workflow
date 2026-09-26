from __future__ import annotations

from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from context_compaction.managers.exceptions.context_compaction_error import RolloutError
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
        try:
            return ValidatorRolloutResponse._validate_supported_response(response_type, response_value_by_field_name)
        except ValidationError as error:
            raise ValidatorRolloutResponse._rollout_error(response_type) from error

    @staticmethod
    def _validate_supported_response(response_type: object, response_value_by_field_name: Mapping[str, object]) -> RolloutResponseResource | None:
        if response_type == "message":
            return ValidatorRolloutResponse._validate_message(response_value_by_field_name)
        if response_type == "custom_tool_call":
            return RolloutToolCallResource.model_validate(response_value_by_field_name)
        if response_type == "custom_tool_call_output":
            return RolloutToolOutputResource.model_validate(response_value_by_field_name)
        return None

    @staticmethod
    def _validate_message(response_value_by_field_name: Mapping[str, object]) -> RolloutMessageResource | None:
        role: object = response_value_by_field_name.get("role")
        if isinstance(role, str) and role not in {"user", "assistant"}:
            return None
        return RolloutMessageResource.model_validate(response_value_by_field_name)

    @staticmethod
    def _rollout_error(response_type: object) -> RolloutError:
        if response_type == "custom_tool_call":
            return RolloutError("Selected tool call needs string name and input and a nonempty call ID.")
        return RolloutError(f"Selected rollout contains an invalid {response_type} shape or unsupported nontext content.")

    @staticmethod
    def validate_text(rollout_message_content: object) -> list[RolloutTextResource]:
        try:
            return TypeAdapter(list[RolloutTextResource]).validate_python(rollout_message_content)
        except ValidationError as error:
            raise RolloutError("Selected conversation contains unsupported nontext content; supported=text, input_text, output_text.") from error
