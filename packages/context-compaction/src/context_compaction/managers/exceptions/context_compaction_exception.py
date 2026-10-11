"""Semantic exception branches for context-compaction trials."""

from __future__ import annotations

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
