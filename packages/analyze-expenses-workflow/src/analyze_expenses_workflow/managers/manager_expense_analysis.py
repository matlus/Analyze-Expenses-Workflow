import asyncio

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.budget_settings import BudgetSettings
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import CodingAssistantSubscription
from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperation, LlmOperationSettings
from analyze_expenses_workflow.managers.gateways.codex_subscription_gateway import CodexSubscriptionGateway
from analyze_expenses_workflow.managers.gateways.copilot_subscription_gateway import CopilotSubscriptionGateway
from analyze_expenses_workflow.managers.gateways.llm_gateway_protocol import LlmGatewayProtocol
from analyze_expenses_workflow.managers.gateways.open_router_jev_gateway import OpenRouterJevGateway
from analyze_expenses_workflow.managers.llm_processors.expense_findings_llm_processor import ExpenseFindingsLlmProcessor
from analyze_expenses_workflow.managers.llm_processors.expense_line_extraction_llm_processor import ExpenseLineExtractionLlmProcessor
from analyze_expenses_workflow.managers.processors.expense_calculation_processor import ExpenseCalculationProcessor
from analyze_expenses_workflow.managers.processors.expense_line_jev_extraction_processor import ExpenseLineJevExtractionProcessor
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.managers.processors.expense_reconciliation_processor import ExpenseReconciliationProcessor
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.managers.service_locators.service_locator_protocol import ServiceLocatorProtocol
from analyze_expenses_workflow.managers.validators.validator_expense_lines import ValidatorExpenseLines
from analyze_expenses_workflow.models.expense_analysis_result import ExpenseAnalysisResult, ExpenseCategorization
from analyze_expenses_workflow.models.expense_calculation_result import BudgetTarget, ExpenseCalculationResult
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.expense_finding import ExpenseFinding
from analyze_expenses_workflow.models.expense_transaction import ExpenseTransaction
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class ManagerExpenseAnalysis:
    def __init__(self, service_locator: ServiceLocatorProtocol) -> None:
        configuration_provider: ConfigurationProvider = service_locator.get_configuration_provider()
        subscription: CodingAssistantSubscription = configuration_provider.get_coding_assistant_settings().coding_assistant_subscription
        jev_settings: JevSettings = configuration_provider.get_jev_settings()
        self._budget_settings: BudgetSettings = configuration_provider.get_budget_settings()
        extraction_settings: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
        findings_settings: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_FINDINGS)
        self._jev_gateway: OpenRouterJevGateway = self._make_jev_gateway(jev_settings)
        self._llm_gateway: LlmGatewayProtocol = self._make_llm_gateway(subscription)
        self._parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
        self._jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(
            self._jev_gateway, jev_settings.parsing_minimum_choice_probability
        )
        self._extraction_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            self._llm_gateway, extraction_settings
        )
        self._findings_processor: ExpenseFindingsLlmProcessor = ExpenseFindingsLlmProcessor(
            self._llm_gateway, findings_settings
        )
        self._categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(self._jev_gateway)
        self._reconciliation_processor: ExpenseReconciliationProcessor = ExpenseReconciliationProcessor()
        self._calculation_processor: ExpenseCalculationProcessor = ExpenseCalculationProcessor()

    @staticmethod
    def _make_jev_gateway(settings: JevSettings) -> OpenRouterJevGateway:
        return OpenRouterJevGateway(settings)

    @staticmethod
    def _make_llm_gateway(subscription: CodingAssistantSubscription) -> LlmGatewayProtocol:
        if subscription == CodingAssistantSubscription.GPT_CODEX:
            return CodexSubscriptionGateway()
        if subscription == CodingAssistantSubscription.GITHUB_COPILOT:
            return CopilotSubscriptionGateway()
        raise ValueError(f"Unsupported coding assistant subscription: {subscription}")

    async def analyze_expenses(
        self, expense_lines: list[str], confirmed_duplicate_line_numbers: tuple[int, ...] = ()
    ) -> ExpenseAnalysisResult:
        ValidatorExpenseLines.validate(expense_lines)
        parsed_lines: tuple[ParsedExpenseLine, ...] = await self._parse_and_extract(expense_lines)
        categorization_slots: tuple[ExpenseCategorization | None, ...] = await self._categorize_lines(parsed_lines)
        transactions: tuple[ExpenseTransaction, ...] = await self._reconciliation_processor.reconcile(
            parsed_lines, categorization_slots, confirmed_duplicate_line_numbers
        )
        calculations: ExpenseCalculationResult = await self._calculate_and_validate(transactions)
        findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await self._findings_processor.findings(calculations)
        return self._build_result(parsed_lines, categorization_slots, transactions, calculations, findings)

    async def _parse_and_extract(self, expense_lines: list[str]) -> tuple[ParsedExpenseLine, ...]:
        parsed_lines: tuple[ParsedExpenseLine, ...] = await self._parsing_processor.parse(expense_lines)
        inferred_year: int | None = self._parsing_processor.infer_year(expense_lines)
        jev_lines: tuple[ParsedExpenseLine, ...] = await self._jev_extraction_processor.extract_uncertain_lines(parsed_lines, inferred_year)
        return await self._extraction_processor.extract_uncertain_lines(jev_lines, inferred_year)

    async def _calculate_and_validate(self, transactions: tuple[ExpenseTransaction, ...]) -> ExpenseCalculationResult:
        targets: tuple[BudgetTarget, ...] = (
            BudgetTarget(ExpenseCategory.DINING_COFFEE, self._budget_settings.dining_coffee_monthly_budget),
            BudgetTarget(ExpenseCategory.SHOPPING, self._budget_settings.shopping_monthly_budget),
        )
        calculations: ExpenseCalculationResult = await self._calculation_processor.calculate(transactions, targets)
        if not calculations.reconciliation.is_balanced:
            raise ValueError("Expense totals did not reconcile")
        return calculations

    @staticmethod
    def _build_result(
        parsed_lines: tuple[ParsedExpenseLine, ...],
        categorization_slots: tuple[ExpenseCategorization | None, ...],
        transactions: tuple[ExpenseTransaction, ...],
        calculations: ExpenseCalculationResult,
        findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding],
    ) -> ExpenseAnalysisResult:
        return ExpenseAnalysisResult(
            categorizations=tuple(slot for slot in categorization_slots if slot is not None),
            parsed_lines=parsed_lines,
            transactions=transactions,
            calculations=calculations,
            findings=findings,
            method=ManagerExpenseAnalysis._method_description(),
        )

    async def _categorize_lines(self, lines: tuple[ParsedExpenseLine, ...]) -> tuple[ExpenseCategorization | None, ...]:
        semaphore: asyncio.Semaphore = asyncio.Semaphore(8)

        async with asyncio.TaskGroup() as task_group:
            tasks: tuple[asyncio.Task[ExpenseCategorization | None], ...] = tuple(
                task_group.create_task(self._categorize_one(line, semaphore)) for line in lines
            )
        return tuple(task.result() for task in tasks)

    async def _categorize_one(self, line: ParsedExpenseLine, semaphore: asyncio.Semaphore) -> ExpenseCategorization | None:
        if line.occurred_on is None or line.amount is None or line.description is None:
            return None
        async with semaphore:
            return await self._categorization_processor.categorize(line.line_number, line.source_text, line.description)

    @staticmethod
    def _method_description() -> str:
        return (
            "Every supplied string is retained with its source line number. Code extracts dates and signed Decimal amounts; "
            "ambiguous date and amount candidates are offered to Jev, then unresolved lines are sent for structured, "
            "evidence-checked LLM extraction. Jev proposes a fixed expense category, "
            "then explicit merchant and cash-movement policy is applied. Caller-confirmed duplicate line numbers "
            "are excluded only when they match an earlier adjacent source line. Explicit refund labels are treated as credits "
            "when the source omits a minus sign. Refunds remain negative. Uncategorized outflows "
            "are reported separately. Python sums included category amounts by month, compares targets with actual minus "
            "target, and computes each adjacent month change as current minus previous. The source-line partitions and "
            "monthly totals are reconciled before findings are returned."
        )

    async def close(self) -> None:
        try:
            await self._jev_gateway.close()
        finally:
            await self._llm_gateway.close()
