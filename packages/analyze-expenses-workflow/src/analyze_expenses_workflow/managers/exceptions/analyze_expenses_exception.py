import json
import math
import traceback
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, IntEnum
from types import TracebackType
from typing import ClassVar, override

type ExceptionValue = str | int | float | bool
type ExceptionContext = dict[str, ExceptionValue]
type ExceptionContextInput = Mapping[str, ExceptionValue]
type ExceptionField = tuple[str, ExceptionValue]


class Severity(IntEnum):
    ERROR = 40
    CRITICAL = 50


class ExceptionAction(Enum):
    USER_ACTION_REQUIRED = "UserActionRequired"
    RETRY_ACTION_NEEDED = "RetryActionNeeded"
    INFRA_ACTION_REQUIRED = "InfraActionRequired"
    DEVELOPER_ACTION_REQUIRED = "DeveloperActionRequired"


class ExpenseLogEvent(Enum):
    INPUT_VALIDATION = "ExpenseInputValidation"
    CONFIGURATION = "ExpenseConfiguration"
    LLM_GATEWAY = "ExpenseLlmGateway"
    JEV_GATEWAY = "ExpenseJevGateway"
    LLM_RESPONSE_VALIDATION = "ExpenseLlmResponseValidation"
    FINDINGS_SELECTION = "ExpenseFindingsSelection"


@dataclass(frozen=True, slots=True)
class ExceptionCause:
    exception_type: str
    message: str
    traceback: TracebackType | None = None


@dataclass(frozen=True, slots=True)
class ExceptionDiagnostics:
    CAUSED_BY_FIELD_NAME: ClassVar[str] = "CausedBy"
    CAUSE_FIELD_NAME: ClassVar[str] = "Cause"
    TRACEBACK_FIELD_NAME: ClassVar[str] = "Traceback"

    application_name: str
    exception_type: str
    action: ExceptionAction
    reason: str
    log_event: ExpenseLogEvent
    severity: Severity
    http_status_code: int
    message: str
    additional_fields: tuple[ExceptionField, ...]
    cause: ExceptionCause | None
    traceback_text: str | None

    def fields(self) -> tuple[ExceptionField, ...]:
        standard_fields: tuple[ExceptionField, ...] = self._standard_fields()
        additional_fields: tuple[ExceptionField, ...] = self._unreserved_additional_fields(standard_fields)
        cause_fields: tuple[ExceptionField, ...] = self._cause_fields()
        return (*additional_fields, *standard_fields, *cause_fields)

    def _standard_fields(self) -> tuple[ExceptionField, ...]:
        standard_fields: tuple[ExceptionField, ...] = (
            ("ApplicationName", self.application_name),
            ("ExceptionType", self.exception_type),
            ("Action", self.action.value),
            ("Reason", self.reason),
            ("LogEvent", self.log_event.value),
            ("Severity", self.severity.name),
            ("HttpStatusCode", self.http_status_code),
            ("Message", self.message),
        )
        return standard_fields

    def _unreserved_additional_fields(self, standard_fields: tuple[ExceptionField, ...]) -> tuple[ExceptionField, ...]:
        reserved_names: frozenset[str] = frozenset(field[0] for field in standard_fields) | {
            self.CAUSED_BY_FIELD_NAME,
            self.CAUSE_FIELD_NAME,
            self.TRACEBACK_FIELD_NAME,
        }
        return tuple(field for field in self.additional_fields if field[0] not in reserved_names)

    def _cause_fields(self) -> tuple[ExceptionField, ...]:
        if self.cause is None:
            return ()
        return ((self.CAUSED_BY_FIELD_NAME, self.cause.exception_type), (self.CAUSE_FIELD_NAME, self.cause.message))

    def fields_with_traceback(self) -> tuple[ExceptionField, ...]:
        traceback_fields: tuple[ExceptionField, ...] = ((self.TRACEBACK_FIELD_NAME, self.traceback_text),) if self.traceback_text is not None else ()
        return (*self.fields(), *traceback_fields)


class AnalyzeExpensesBaseException(Exception, ABC):
    APPLICATION_NAME: ClassVar[str] = "AnalyzeExpensesWorkflow"

    def __init__(self, message: str, log_event: ExpenseLogEvent, contextual_data_by_name: ExceptionContextInput | None = None) -> None:
        self._raise_if_abstract()
        super().__init__(message)
        self._message: str = message
        self._log_event: ExpenseLogEvent = log_event
        self._additional_fields: tuple[ExceptionField, ...] = tuple((field[0], field[1]) for field in (contextual_data_by_name or {}).items())

    def _raise_if_abstract(self) -> None:
        # BaseException.__new__ skips the abstract-method check that object.__new__ performs, so it is enforced here.
        abstract_method_names: frozenset[str] = type(self).__abstractmethods__
        if abstract_method_names:
            missing_names: str = ", ".join(sorted(abstract_method_names))
            raise TypeError(f"Cannot instantiate abstract exception {type(self).__name__}; missing {missing_names}")

    @property
    def message(self) -> str:
        return self._message

    @property
    def log_event(self) -> ExpenseLogEvent:
        return self._log_event

    @property
    @abstractmethod
    def action(self) -> ExceptionAction: ...

    @property
    @abstractmethod
    def reason(self) -> str: ...

    @property
    @abstractmethod
    def http_status_code(self) -> int: ...

    @property
    def severity(self) -> Severity:
        return Severity.ERROR

    @property
    def cause(self) -> ExceptionCause | None:
        cause_exception: BaseException | None = self.__cause__
        if cause_exception is None:
            return None
        return ExceptionCause(type(cause_exception).__name__, str(cause_exception), cause_exception.__traceback__)

    @property
    def diagnostics(self) -> ExceptionDiagnostics:
        return ExceptionDiagnostics(
            application_name=self.APPLICATION_NAME,
            exception_type=type(self).__name__,
            action=self.action,
            reason=self.reason,
            log_event=self.log_event,
            severity=self.severity,
            http_status_code=self.http_status_code,
            message=self.message,
            additional_fields=self._additional_fields,
            cause=self.cause,
            traceback_text="".join(traceback.format_tb(self.__traceback__)) if self.__traceback__ is not None else None,
        )

    def to_string(self) -> str:
        return self._render_fields(self.diagnostics.fields_with_traceback())

    def to_string_without_traceback(self) -> str:
        return self._render_fields(self.diagnostics.fields())

    @staticmethod
    def _render_fields(fields: tuple[ExceptionField, ...]) -> str:
        traceback_field_name: str = ExceptionDiagnostics.TRACEBACK_FIELD_NAME
        lines: list[str] = [f"{field[0]}: {field[1]}" for field in fields if field[0] != traceback_field_name]
        traceback_text: str | None = next((str(field[1]) for field in fields if field[0] == traceback_field_name), None)
        if traceback_text is not None:
            lines.extend(("", traceback_text))
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self._json_value_by_field_name(), indent=2, allow_nan=False)

    def _json_value_by_field_name(self) -> dict[str, ExceptionValue]:
        # JSON has no NaN or infinity, so a non-finite number travels as its text.
        return {
            field[0]: str(field[1]) if isinstance(field[1], float) and not math.isfinite(field[1]) else field[1]
            for field in self.diagnostics.fields_with_traceback()
        }


class AnalyzeExpensesBusinessBaseException(AnalyzeExpensesBaseException, ABC):
    @property
    @override
    def http_status_code(self) -> int:
        return 400


class AnalyzeExpensesTechnicalBaseException(AnalyzeExpensesBaseException, ABC):
    @property
    @override
    def http_status_code(self) -> int:
        return 500
