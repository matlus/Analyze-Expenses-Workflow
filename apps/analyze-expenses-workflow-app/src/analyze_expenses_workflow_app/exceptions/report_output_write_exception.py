from typing import final, override

from analyze_expenses_workflow.domain_facades import AnalyzeExpensesTechnicalBaseException, ExceptionAction, ExpenseLogEvent


@final
class ReportOutputWriteFailedException(AnalyzeExpensesTechnicalBaseException):
    def __init__(self, output_path: str, os_reason: str) -> None:
        super().__init__(
            (
                f"Report could not be written to the output file after the analysis completed; "
                f"the report was written to standard output: output={output_path}, reason={os_reason}"
            ),
            ExpenseLogEvent.REPORT_OUTPUT,
            {"OutputPath": output_path, "OsReason": os_reason},
        )

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.INFRA_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Report output file write failed after the analysis completed"
