from collections.abc import Mapping
from typing import final

from context_compaction.managers.exceptions.context_compaction_exception import ContextCompactionTechnicalBaseException


class JevGatewayBaseException(ContextCompactionTechnicalBaseException):
    """Base class for distinct Jev gateway failures."""

    def __init__(self, message: str, *, operation: str, model: str, detail_value_by_name: Mapping[str, str] | None = None) -> None:
        self.operation: str = operation
        self.model: str = model
        self.detail_value_by_name: dict[str, str] = dict(detail_value_by_name or {})
        context: str = ", ".join(f"{name}={self.detail_value_by_name[name]}" for name in self.detail_value_by_name)
        super().__init__(f"{message} operation={operation}, model={model}" + (f", {context}" if context else ""))


@final
class JevGatewayClosedException(JevGatewayBaseException):
    """The caller used a gateway after its client was released."""


@final
class JevRequestException(JevGatewayBaseException):
    """The provider request failed."""


@final
class JevResponseException(JevGatewayBaseException):
    """The provider returned an unusable answer."""


@final
class JevCleanupException(JevGatewayBaseException):
    """The provider client could not be closed."""
