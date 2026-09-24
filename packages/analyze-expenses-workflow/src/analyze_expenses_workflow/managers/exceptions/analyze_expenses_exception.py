import json
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, IntEnum
from types import TracebackType
from typing import ClassVar, override

type ExceptionContext = dict[str, str | int | float | bool]


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

    def to_dict(self) -> dict[str, str]:
        cause_data: dict[str, str] = {"exception_type": self.exception_type, "message": self.message}
        if self.traceback is not None:
            cause_data["traceback"] = "".join(traceback.format_tb(self.traceback))
        return cause_data


class AnalyzeExpensesException(Exception, ABC):
    APPLICATION_NAME: ClassVar[str] = "AnalyzeExpensesWorkflow"
    TRACEBACK_KEY: ClassVar[str] = "Traceback"

    def __init__(self, message: str, log_event: ExpenseLogEvent, contextual_data_by_name: ExceptionContext | None = None) -> None:
        abstract_method_names: frozenset[str] = type(self).__abstractmethods__
        if abstract_method_names:
            names: str = ", ".join(sorted(abstract_method_names))
            raise TypeError(f"Cannot instantiate abstract exception {type(self).__name__}; missing {names}")
        super().__init__(message)
        self._message: str = message
        self._log_event: ExpenseLogEvent = log_event
        self._additional_contextual_data_by_name: ExceptionContext = dict(contextual_data_by_name or {})

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
    def contextual_data_by_name(self) -> ExceptionContext:
        contextual_data: ExceptionContext = dict(self._additional_contextual_data_by_name)
        contextual_data.update({
            "ApplicationName": self.APPLICATION_NAME,
            "ExceptionType": type(self).__name__,
            "Action": self.action.value,
            "Reason": self.reason,
            "LogEvent": self.log_event.value,
            "Severity": self.severity.name,
            "HttpStatusCode": self.http_status_code,
            "Message": self.message,
        })
        return contextual_data

    def add_contextual_data(self, contextual_data_by_name: ExceptionContext) -> None:
        self._additional_contextual_data_by_name.update(contextual_data_by_name)

    def _complete_exception_data(self, *, include_traceback: bool) -> ExceptionContext:
        complete_exception_data: ExceptionContext = self.contextual_data_by_name
        exception_cause: ExceptionCause | None = self.cause
        if exception_cause is not None:
            complete_exception_data["CausedBy"] = exception_cause.exception_type
            complete_exception_data["Cause"] = exception_cause.message
        if include_traceback and self.__traceback__ is not None:
            complete_exception_data[self.TRACEBACK_KEY] = "".join(traceback.format_tb(self.__traceback__))
        return complete_exception_data

    def to_string(self, *, include_traceback: bool = True) -> str:
        complete_exception_data: ExceptionContext = self._complete_exception_data(include_traceback=include_traceback)
        lines: list[str] = [f"{key}: {value}" for key, value in complete_exception_data.items() if key != self.TRACEBACK_KEY]
        if include_traceback and self.TRACEBACK_KEY in complete_exception_data:
            lines.extend(("", str(complete_exception_data[self.TRACEBACK_KEY])))
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self._complete_exception_data(include_traceback=True), indent=2)


class AnalyzeExpensesBusinessException(AnalyzeExpensesException, ABC):
    @property
    @override
    def http_status_code(self) -> int:
        return 400


class AnalyzeExpensesTechnicalException(AnalyzeExpensesException, ABC):
    @property
    @override
    def http_status_code(self) -> int:
        return 500
