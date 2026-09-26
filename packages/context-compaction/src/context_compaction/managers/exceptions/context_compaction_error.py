"""Semantic exception branches for context-compaction trials."""

from __future__ import annotations

from pathlib import Path
from typing import final


class ContextCompactionBusinessError(ValueError):
    """A trial input can be corrected by its caller."""


class ContextCompactionTechnicalError(RuntimeError):
    """A trial failure needs system-owner attention."""


@final
class ContextCompactionConfigurationError(ContextCompactionBusinessError):
    """Jev settings cannot be read or are invalid."""


@final
class ContextCompactionInputError(ContextCompactionBusinessError):
    """A caller supplied invalid trial options."""


@final
class CompactionDecisionError(ContextCompactionBusinessError):
    """The retention input cannot form a complete compaction decision."""


@final
class RolloutError(ContextCompactionBusinessError):
    """The selected Codex rollout cannot form a trial case."""


@final
class ContextCompactionConfigurationReadError(ContextCompactionTechnicalError):
    def __init__(self, env_file: Path) -> None:
        self.operation: str = "read_jev_settings"
        self.env_file: Path = env_file
        super().__init__(f"Could not read Jev settings from {env_file}.")
