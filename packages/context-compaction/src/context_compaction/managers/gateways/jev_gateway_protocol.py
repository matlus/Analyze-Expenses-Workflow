from collections.abc import Mapping
from typing import Protocol

from context_compaction.managers.models.context_models import JevDecisionBatch, RetentionQuestion
from context_compaction.managers.models.records import JevState


class JevGatewayProtocol(Protocol):
    async def decide(self, jev_state: JevState, questions: Mapping[str, RetentionQuestion]) -> JevDecisionBatch: ...
    async def close(self) -> None: ...
