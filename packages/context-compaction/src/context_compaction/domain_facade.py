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
    """Offline preparation needs no credentials; live Jev evaluation is composed through for_live_evaluation."""

    def __init__(self, manager_context_compaction: ManagerContextCompaction | None = None) -> None:
        self._manager_context_compaction: ManagerContextCompaction = (
            manager_context_compaction if manager_context_compaction is not None else ManagerContextCompaction.for_preparation()
        )

    @classmethod
    def for_live_evaluation(cls, service_locator: ServiceLocatorProtocol | None = None, *, env_file: Path = DEFAULT_ENV_FILE) -> Self:
        return cls(
            ManagerContextCompaction.for_live_evaluation(service_locator if service_locator is not None else ServiceLocatorProduction(env_file))
        )

    def load_context(self, source: Path, start_line: int, cutoff_line: int) -> TrialCase:
        return self._manager_context_compaction.load_context(source, start_line, cutoff_line)

    def prepare(self, trial_case: TrialCase, mode: EvidenceMode = "full", variant: Variant = "careful") -> PreparedCompaction:
        return self._manager_context_compaction.prepare(trial_case, mode, variant)

    def describe_context(self, trial_case: TrialCase, mode: EvidenceMode = "full") -> TrialManifest:
        return self._manager_context_compaction.describe_context(trial_case, mode)

    def validate_trial_options(self, runs: int, threshold: float, required_tool_ids: AbstractSet[str], available_tool_ids: AbstractSet[str]) -> None:
        self._manager_context_compaction.validate_trial_options(runs, threshold, required_tool_ids, available_tool_ids)

    def apply_saved_decisions(self, segments: tuple[Segment, ...], probability_by_tool_id: Mapping[str, float], threshold: float = 0.5) -> Compaction:
        return self._manager_context_compaction.apply_saved_decisions(segments, probability_by_tool_id, threshold)

    async def compact(self, prepared_compaction: PreparedCompaction, threshold: float = 0.5) -> CompactionResult:
        return await self._manager_context_compaction.compact(prepared_compaction, threshold)

    async def close(self) -> None:
        await self._manager_context_compaction.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        await self.close()
