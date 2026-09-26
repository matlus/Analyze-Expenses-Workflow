from collections.abc import Mapping, Set as AbstractSet
from pathlib import Path
from types import TracebackType
from typing import Self

from context_compaction.managers.configuration_providers.configuration_provider import ConfigurationProvider
from context_compaction.managers.exceptions.context_compaction_error import ContextCompactionConfigurationError
from context_compaction.managers.gateways.jev_gateway_protocol import JevGatewayProtocol
from context_compaction.managers.models.context_models import (
    Compaction,
    CompactionResult,
    EvidenceMode,
    JevDecisionBatch,
    JevSettings,
    PreparedCompaction,
    Segment,
    TrialCase,
    Variant,
)
from context_compaction.managers.models.records import TrialManifest
from context_compaction.managers.processors.context_preparation_processor import ContextPreparationProcessor
from context_compaction.managers.processors.context_selection_processor import ContextSelectionProcessor
from context_compaction.managers.processors.rollout_processor import ContextRolloutProcessor
from context_compaction.managers.service_locators.service_locator_protocol import ServiceLocatorProtocol
from context_compaction.managers.validators.validator_context_compaction import (
    ValidatorContextPreparation,
    ValidatorContextSelection,
    ValidatorPreparedCompaction,
    ValidatorRolloutWindow,
    ValidatorTrialOptions,
)


class ManagerContextCompaction:
    def __init__(self, service_locator: ServiceLocatorProtocol, *, enable_jev: bool) -> None:
        self._jev_gateway: JevGatewayProtocol | None = self._create_gateway(service_locator) if enable_jev else None
        self._closed: bool = False
        self._context_preparation_processor: ContextPreparationProcessor = ContextPreparationProcessor()
        self._context_selection_processor: ContextSelectionProcessor = ContextSelectionProcessor()
        self._context_rollout_processor: ContextRolloutProcessor = ContextRolloutProcessor()

    @staticmethod
    def _create_gateway(service_locator: ServiceLocatorProtocol) -> JevGatewayProtocol:
        configuration_provider: ConfigurationProvider = service_locator.get_configuration_provider()
        jev_settings: JevSettings = configuration_provider.get_jev_settings()
        return service_locator.create_jev_gateway(jev_settings)

    def load_context(self, source: Path, start_line: int, cutoff_line: int) -> TrialCase:
        ValidatorRolloutWindow.validate(source, start_line, cutoff_line)
        return self._context_rollout_processor.load_trial_case(source, start_line, cutoff_line)

    def prepare(self, trial_case: TrialCase, mode: EvidenceMode, variant: Variant) -> PreparedCompaction:
        ValidatorContextPreparation.validate(trial_case, mode, variant)
        return self._context_preparation_processor.prepare(trial_case, mode, variant)

    def describe_context(self, trial_case: TrialCase, mode: EvidenceMode) -> TrialManifest:
        ValidatorContextPreparation.validate(trial_case, mode, "careful")
        return self._context_preparation_processor.describe_context(trial_case, mode)

    def validate_trial_options(self, runs: int, threshold: float, required_tool_ids: AbstractSet[str], available_tool_ids: AbstractSet[str]) -> None:
        ValidatorTrialOptions.validate(runs, threshold, required_tool_ids, available_tool_ids)

    def apply_saved_decisions(self, segments: tuple[Segment, ...], probabilities: Mapping[str, float], threshold: float) -> Compaction:
        ValidatorContextSelection.validate(segments, probabilities, threshold)
        return self._context_selection_processor.select(segments, probabilities, threshold)

    async def compact(self, prepared_compaction: PreparedCompaction, threshold: float) -> CompactionResult:
        ValidatorPreparedCompaction.validate(prepared_compaction)
        ValidatorContextPreparation.validate(prepared_compaction.trial_case, prepared_compaction.mode, prepared_compaction.variant)
        ValidatorContextSelection.validate_threshold(threshold)
        if self._jev_gateway is None:
            raise ContextCompactionConfigurationError("Jev is disabled for this facade; construct it with enable_jev=True for live evaluation.")
        jev_decision_batch: JevDecisionBatch = await self._jev_gateway.decide(prepared_compaction.jev_state, prepared_compaction.questions)
        return CompactionResult(
            self._context_selection_processor.select(prepared_compaction.trial_case.segments, jev_decision_batch.probabilities, threshold),
            jev_decision_batch,
        )

    async def close(self) -> None:
        if not self._closed:
            if self._jev_gateway is not None:
                await self._jev_gateway.close()
            self._closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        await self.close()
