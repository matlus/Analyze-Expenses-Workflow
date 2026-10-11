"""Semantic exception branches for context-compaction trials."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set as AbstractSet
from pathlib import Path
from typing import final


class ContextCompactionBusinessBaseException(ValueError):
    """The caller supplied something wrong; there is nothing for us to fix, so the error goes back to the caller."""


class ContextCompactionTechnicalBaseException(RuntimeError):
    """We must fix something, such as configuration or the environment, before the trial can run."""


@final
class ContextCompactionConfigurationException(ContextCompactionTechnicalBaseException):
    """Jev settings are missing or invalid, so the system owner must correct the configuration."""


@final
class ContextCompactionConfigurationReadException(ContextCompactionTechnicalBaseException):
    def __init__(self, env_file: Path) -> None:
        self.operation: str = "read_jev_settings"
        self.env_file: Path = env_file
        super().__init__(f"Could not read Jev settings from {env_file}.")


@final
class JevDisabledException(ContextCompactionTechnicalBaseException):
    """Code asked a facade built without Jev for live evaluation, so the composing code must enable Jev."""

    def __init__(self) -> None:
        self.operation: str = "compact"
        super().__init__("Jev is disabled for this facade; construct it with DomainFacade.for_live_evaluation for live evaluation.")


@final
class RolloutLineRangeInvalidException(ContextCompactionBusinessBaseException):
    def __init__(self, start_line: int, cutoff_line: int) -> None:
        self.start_line: int = start_line
        self.cutoff_line: int = cutoff_line
        super().__init__(f"The start line must be positive and precede the held-out answer; start={start_line}, cutoff={cutoff_line}.")


@final
class RolloutFileNotFoundException(ContextCompactionBusinessBaseException):
    def __init__(self, source: Path) -> None:
        self.source: Path = source
        super().__init__(f"Codex rollout does not exist: {source}")


@final
class RolloutRecordInvalidJsonException(ContextCompactionBusinessBaseException):
    def __init__(self, source: Path, line_number: int) -> None:
        self.source: Path = source
        self.line_number: int = line_number
        super().__init__(f"Invalid JSONL record at line {line_number} in {source}.")


@final
class RolloutResponsePayloadNotObjectException(ContextCompactionBusinessBaseException):
    def __init__(self, source: Path, line_number: int) -> None:
        self.source: Path = source
        self.line_number: int = line_number
        super().__init__(f"Selected response at line {line_number} in {source} needs an object payload.")


@final
class RolloutToolCallIdDuplicateException(ContextCompactionBusinessBaseException):
    def __init__(self, line_number: int, call_fingerprint: str) -> None:
        self.line_number: int = line_number
        self.call_fingerprint: str = call_fingerprint
        super().__init__(f"Duplicate tool call ID at line {line_number}; fingerprint={call_fingerprint}.")


@final
class RolloutMessageInvalidException(ContextCompactionBusinessBaseException):
    def __init__(self) -> None:
        super().__init__(
            "Selected rollout contains an invalid message shape or unsupported nontext content; supported=text, input_text, output_text."
        )


@final
class RolloutToolCallInvalidException(ContextCompactionBusinessBaseException):
    def __init__(self) -> None:
        super().__init__("Selected tool call needs string name and input and a nonempty call ID.")


@final
class RolloutToolOutputInvalidException(ContextCompactionBusinessBaseException):
    def __init__(self) -> None:
        super().__init__("Selected tool call output needs a nonempty string call ID.")


@final
class RolloutToolOutputNonTextContentException(ContextCompactionBusinessBaseException):
    def __init__(self) -> None:
        super().__init__("Selected tool call output contains unsupported nontext content; supported=text, input_text, output_text.")


@final
class RolloutWindowIncompleteException(ContextCompactionBusinessBaseException):
    """The window lacks one or more parts every trial case needs; each missing part is listed."""

    def __init__(self, window: str, problems: Sequence[str]) -> None:
        self.window: str = window
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(f"The selected window {window} cannot form a trial case: {'; '.join(self.problems)}.")


@final
class ContextPreparationInvalidException(ContextCompactionBusinessBaseException):
    """The evidence options or the context cannot form a Jev request; each problem is listed."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__("; ".join(self.problems))


@final
class JevStateTooLargeException(ContextCompactionBusinessBaseException):
    def __init__(self, state_characters: int, mode: str, maximum_state_characters: int) -> None:
        self.state_characters: int = state_characters
        self.mode: str = mode
        self.maximum_state_characters: int = maximum_state_characters
        super().__init__(f"Jev state has {state_characters} characters; {mode} trial limit is {maximum_state_characters}.")


@final
class CompactionThresholdOutOfRangeException(ContextCompactionBusinessBaseException):
    def __init__(self, threshold: float) -> None:
        self.threshold: float = threshold
        super().__init__(f"Threshold must be between zero and one; received {threshold}.")


@final
class CompactionContextEmptyException(ContextCompactionBusinessBaseException):
    def __init__(self) -> None:
        super().__init__("Context must contain nonempty segment text.")


@final
class RetentionProbabilitiesInvalidException(ContextCompactionBusinessBaseException):
    def __init__(
        self, expected_tool_ids: AbstractSet[str], received_tool_ids: AbstractSet[str], invalid_probability_by_tool_id: Mapping[str, float]
    ) -> None:
        self.expected_tool_ids: tuple[str, ...] = tuple(sorted(expected_tool_ids))
        self.received_tool_ids: tuple[str, ...] = tuple(sorted(received_tool_ids))
        self.invalid_probability_by_tool_id: dict[str, float] = dict(invalid_probability_by_tool_id)
        super().__init__(
            "".join(
                (
                    "Jev probabilities do not match the complete tool-event set; ",
                    f"expected={list(self.expected_tool_ids)}, received={list(self.received_tool_ids)}, ",
                    f"invalid={self.invalid_probability_by_tool_id}.",
                )
            )
        )


@final
class PreparedQuestionsMismatchException(ContextCompactionBusinessBaseException):
    def __init__(self, expected_tool_ids: AbstractSet[str], received_tool_ids: AbstractSet[str], mismatched_question_ids: AbstractSet[str]) -> None:
        self.expected_tool_ids: tuple[str, ...] = tuple(sorted(expected_tool_ids))
        self.received_tool_ids: tuple[str, ...] = tuple(sorted(received_tool_ids))
        self.mismatched_question_ids: tuple[str, ...] = tuple(sorted(mismatched_question_ids))
        super().__init__(
            "".join(
                (
                    "Prepared questions must match the complete tool-event set and their rebuilt instructions; ",
                    f"expected={list(self.expected_tool_ids)}, received={list(self.received_tool_ids)}, ",
                    f"mismatched={list(self.mismatched_question_ids)}.",
                )
            )
        )


@final
class PreparedStateMismatchException(ContextCompactionBusinessBaseException):
    def __init__(self, source: Path, mode: str, variant: str) -> None:
        self.source: Path = source
        self.mode: str = mode
        self.variant: str = variant
        super().__init__(f"Prepared state must match the redacted source context and current goal; source={source}, mode={mode}, variant={variant}.")


@final
class TrialOptionsInvalidException(ContextCompactionBusinessBaseException):
    """The run count, threshold, or required tool IDs are unusable; each problem is listed."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(f"Trial options are invalid: {'; '.join(self.problems)}")


@final
class TrialOutputOverwritesRolloutException(ContextCompactionBusinessBaseException):
    def __init__(self, source: Path, output_artifact_path: Path) -> None:
        self.source: Path = source
        self.output_artifact_path: Path = output_artifact_path
        super().__init__(f"Trial output would overwrite or delete the original rollout; source={source}, output={output_artifact_path}.")
