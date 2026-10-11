"""Raised by the pre-flight check, before the slow and paid analysis begins."""

from typing import final, override

from analyze_expenses_workflow.domain_facades import AnalyzeExpensesBusinessBaseException, ExceptionAction, ExpenseLogEvent


@final
class ReportOutputDirectoryNotFoundException(AnalyzeExpensesBusinessBaseException):
    def __init__(self, output_path: str, output_directory: str) -> None:
        super().__init__(
            f"Output directory must exist before the analysis starts: output={output_path}, directory={output_directory}",
            ExpenseLogEvent.INPUT_VALIDATION,
            {"OutputPath": output_path, "OutputDirectory": output_directory},
        )

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.USER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Report output directory does not exist"


@final
class ReportOutputPathIsDirectoryException(AnalyzeExpensesBusinessBaseException):
    def __init__(self, output_path: str) -> None:
        super().__init__(
            f"Output path must name a file, not a directory, so the report can be written: output={output_path}",
            ExpenseLogEvent.INPUT_VALIDATION,
            {"OutputPath": output_path},
        )

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.USER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Report output path is a directory"


@final
class ReportOutputPathNotWritableException(AnalyzeExpensesBusinessBaseException):
    def __init__(self, output_path: str, os_reason: str) -> None:
        super().__init__(
            f"Output path must be writable before the analysis starts: output={output_path}, reason={os_reason}",
            ExpenseLogEvent.INPUT_VALIDATION,
            {"OutputPath": output_path, "OsReason": os_reason},
        )

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.USER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Report output path is not writable"
