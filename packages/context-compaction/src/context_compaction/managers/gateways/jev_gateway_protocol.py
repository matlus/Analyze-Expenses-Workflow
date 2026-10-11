from collections.abc import Mapping
from typing import Protocol

from context_compaction.managers.models.context_models import JevDecisionBatch, RetentionQuestion
from context_compaction.managers.models.records import JevState


class JevGatewayProtocol(Protocol):
    async def decide(self, jev_state: JevState, retention_questions_by_tool_id: Mapping[str, RetentionQuestion]) -> JevDecisionBatch: ...
    async def close(self) -> None: ...
