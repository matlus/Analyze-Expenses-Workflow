from collections.abc import Mapping
from typing import Final, final

from context_compaction.managers.exceptions.context_compaction_exception import ContextCompactionTechnicalBaseException


class JevGatewayBaseException(ContextCompactionTechnicalBaseException):
    def __init__(self, message: str, *, operation: str, model: str, detail_value_by_name: Mapping[str, str] | None = None) -> None:
        self.operation: str = operation
        self.model: str = model
        self.detail_value_by_name: dict[str, str] = dict(detail_value_by_name or {})
        super().__init__(self._message_with_context(message))

    def _message_with_context(self, message: str) -> str:
        detail_text: str = ", ".join(f"{name}={self.detail_value_by_name[name]}" for name in self.detail_value_by_name)
        return f"{message} operation={self.operation}, model={self.model}" + (f", {detail_text}" if detail_text else "")


@final
class JevGatewayClosedException(JevGatewayBaseException):
    """The caller used a gateway after its client was released."""


@final
class JevRequestFailedException(JevGatewayBaseException):
    """The provider failed server-side or could not be reached; the gateway does not retry it, so the trial may be rerun once it recovers."""


@final
class JevRequestRejectedException(JevGatewayBaseException):
    """The provider refused the request with a 4xx other than 429; the request or configuration must change before it can succeed."""

    def __init__(self, target: str, http_status: int, provider_message: str, *, operation: str, model: str) -> None:
        self.target: str = target
        self.http_status: int = http_status
        self.provider_message: str = provider_message
        super().__init__(
            f"{target} rejected the context-retention request with HTTP {http_status}; the request must be corrected, not retried.",
            operation=operation,
            model=model,
            detail_value_by_name={"http_status": str(http_status), "provider_message": provider_message},
        )


@final
class JevRateLimitRetriesExhaustedException(JevGatewayBaseException):
    """Every attempt allowed for HTTP 429 rate limiting was spent; the trial may be rerun once the provider's rate limit recovers."""

    HTTP_STATUS: Final[int] = 429

    def __init__(self, target: str, attempt_count: int, last_retry_after_seconds: int | None, *, operation: str, model: str) -> None:
        self.target: str = target
        self.attempt_count: int = attempt_count
        self.http_status: int = JevRateLimitRetriesExhaustedException.HTTP_STATUS
        self.last_retry_after_seconds: int | None = last_retry_after_seconds
        last_retry_after_text: str = "none" if last_retry_after_seconds is None else f"{last_retry_after_seconds}s"
        super().__init__(
            f"Gave up retrying HTTP 429 rate limiting against {target} after {attempt_count} attempts; last Retry-After={last_retry_after_text}.",
            operation=operation,
            model=model,
            detail_value_by_name={
                "attempt_count": str(attempt_count),
                "http_status": str(self.http_status),
                "last_retry_after_seconds": last_retry_after_text,
            },
        )


@final
class JevServerRetryRequestedRetriesExhaustedException(JevGatewayBaseException):
    """Every attempt allowed for HTTP 500 responses that asked to be retried was spent; the trial may be rerun once the provider recovers."""

    HTTP_STATUS: Final[int] = 500

    def __init__(self, target: str, attempt_count: int, last_provider_message: str, *, operation: str, model: str) -> None:
        self.target: str = target
        self.attempt_count: int = attempt_count
        self.http_status: int = JevServerRetryRequestedRetriesExhaustedException.HTTP_STATUS
        self.last_provider_message: str = last_provider_message
        super().__init__(
            f"Gave up retrying HTTP 500 responses that asked to be retried against {target} after {attempt_count} attempts.",
            operation=operation,
            model=model,
            detail_value_by_name={
                "attempt_count": str(attempt_count),
                "http_status": str(self.http_status),
                "provider_message": last_provider_message,
            },
        )


@final
class JevResponseSchemaInvalidException(JevGatewayBaseException):
    """The provider answer did not match the SDK's structured response schema."""


@final
class JevAnswerSetMismatchException(JevGatewayBaseException):
    """The provider omitted a requested retention answer or returned an unrequested one."""


@final
class JevProbabilityOutOfRangeException(JevGatewayBaseException):
    """The provider returned a retention probability outside zero to one."""


@final
class JevClientCleanupException(JevGatewayBaseException):
    """The provider SDK client could not be closed."""


@final
class JevTransportCleanupException(JevGatewayBaseException):
    """The HTTP transport left open by a failed client close could not be closed."""
