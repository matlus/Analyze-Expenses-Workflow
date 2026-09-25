from types import TracebackType
from typing import Self

from analyze_expenses_workflow.managers.manager_expense_analysis import ManagerExpenseAnalysis
from analyze_expenses_workflow.managers.service_locators.service_locator_production import ServiceLocatorProduction
from analyze_expenses_workflow.managers.service_locators.service_locator_protocol import ServiceLocatorProtocol
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseAnalysisResult


class DomainFacade:
    def __init__(self, service_locator: ServiceLocatorProtocol | None = None) -> None:
        if service_locator is None:
            service_locator = ServiceLocatorProduction()
        self._manager_expense_analysis: ManagerExpenseAnalysis = ManagerExpenseAnalysis(service_locator)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def analyze_expenses(
        self, expense_lines: list[str], *, confirmed_duplicate_line_numbers: tuple[int, ...] = ()
    ) -> ExpenseAnalysisResult:
        return await self._manager_expense_analysis.analyze_expenses(expense_lines, confirmed_duplicate_line_numbers)

    async def close(self) -> None:
        await self._manager_expense_analysis.close()
