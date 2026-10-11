from typing import final, override

from analyze_expenses_workflow.domain_facades import AnalyzeExpensesBusinessBaseException, ExceptionAction, ExpenseLogEvent


@final
class ReportOutputPathAliasesInputException(AnalyzeExpensesBusinessBaseException):
    def __init__(self, output_path: str, input_path: str) -> None:
        super().__init__(
            f"Output path must differ from the input expense file so the report cannot overwrite it: output={output_path}, input={input_path}",
            ExpenseLogEvent.INPUT_VALIDATION,
            {"OutputPath": output_path, "InputPath": input_path},
        )

    @property
    @override
    def action(self) -> ExceptionAction:
        return ExceptionAction.USER_ACTION_REQUIRED

    @property
    @override
    def reason(self) -> str:
        return "Report output path refers to the input expense file"
