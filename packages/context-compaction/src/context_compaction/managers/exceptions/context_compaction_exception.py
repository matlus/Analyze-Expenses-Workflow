"""Semantic exception branches for context-compaction trials."""

from __future__ import annotations

from pathlib import Path
from typing import final


class ContextCompactionBusinessBaseException(ValueError):
    """A trial input can be corrected by its caller."""


class ContextCompactionTechnicalBaseException(RuntimeError):
    """A trial failure needs system-owner attention."""


@final
class ContextCompactionConfigurationException(ContextCompactionBusinessBaseException):
    """Jev settings cannot be read or are invalid."""


@final
class ContextCompactionInputException(ContextCompactionBusinessBaseException):
    """A caller supplied invalid trial options."""


@final
class CompactionDecisionException(ContextCompactionBusinessBaseException):
    """The retention input cannot form a complete compaction decision."""


@final
class RolloutException(ContextCompactionBusinessBaseException):
    """The selected Codex rollout cannot form a trial case."""


@final
class ContextCompactionConfigurationReadException(ContextCompactionTechnicalBaseException):
    def __init__(self, env_file: Path) -> None:
        self.operation: str = "read_jev_settings"
        self.env_file: Path = env_file
        super().__init__(f"Could not read Jev settings from {env_file}.")
