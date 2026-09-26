from collections.abc import Mapping, Set as AbstractSet
from pathlib import Path
from types import TracebackType
from typing import Self

from context_compaction.managers.manager_context_compaction import ManagerContextCompaction
from context_compaction.managers.models.context_models import (
    Compaction,
    CompactionResult,
    EvidenceMode,
    PreparedCompaction,
    Segment,
    TrialCase,
    Variant,
)
from context_compaction.managers.models.records import TrialManifest
from context_compaction.managers.service_locators.service_locator_production import ServiceLocatorProduction
from context_compaction.managers.service_locators.service_locator_protocol import ServiceLocatorProtocol

DEFAULT_ENV_FILE = Path(".env")


class DomainFacade:
    def __init__(self, service_locator: ServiceLocatorProtocol | None = None, *, enable_jev: bool = False, env_file: Path = DEFAULT_ENV_FILE) -> None:
        self._manager_context_compaction: ManagerContextCompaction = ManagerContextCompaction(
            service_locator if service_locator is not None else ServiceLocatorProduction(env_file), enable_jev=enable_jev
        )

    def load_context(self, source: Path, start_line: int, cutoff_line: int) -> TrialCase:
        return self._manager_context_compaction.load_context(source, start_line, cutoff_line)

    def prepare(self, trial_case: TrialCase, mode: EvidenceMode = "full", variant: Variant = "careful") -> PreparedCompaction:
        return self._manager_context_compaction.prepare(trial_case, mode, variant)

    def describe_context(self, trial_case: TrialCase, mode: EvidenceMode = "full") -> TrialManifest:
        return self._manager_context_compaction.describe_context(trial_case, mode)

    def validate_trial_options(self, runs: int, threshold: float, required_tool_ids: AbstractSet[str], available_tool_ids: AbstractSet[str]) -> None:
        self._manager_context_compaction.validate_trial_options(runs, threshold, required_tool_ids, available_tool_ids)

    def apply_saved_decisions(self, segments: tuple[Segment, ...], probabilities: Mapping[str, float], threshold: float = 0.5) -> Compaction:
        return self._manager_context_compaction.apply_saved_decisions(segments, probabilities, threshold)

    async def compact(self, prepared_compaction: PreparedCompaction, threshold: float = 0.5) -> CompactionResult:
        return await self._manager_context_compaction.compact(prepared_compaction, threshold)

    async def close(self) -> None:
        await self._manager_context_compaction.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        await self.close()
