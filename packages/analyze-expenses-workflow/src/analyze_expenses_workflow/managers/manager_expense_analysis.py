import asyncio
from collections.abc import Sequence

import httpx2

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
    def __init__(self, service_locator_protocol: ServiceLocatorProtocol) -> None:
        configuration_provider: ConfigurationProvider = service_locator_protocol.get_configuration_provider()
        coding_assistant_subscription: CodingAssistantSubscription = (
            configuration_provider.get_coding_assistant_settings().coding_assistant_subscription
        )
        jev_settings: JevSettings = configuration_provider.get_jev_settings()
        self._budget_settings: BudgetSettings = configuration_provider.get_budget_settings()
        extraction_llm_operation_settings: LlmOperationSettings = configuration_provider.get_llm_operation_settings(
            LlmOperation.EXPENSE_LINE_EXTRACTION
        )
        findings_llm_operation_settings: LlmOperationSettings = configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_FINDINGS)
        self._open_router_jev_gateway: OpenRouterJevGateway = self._create_jev_gateway(
            jev_settings, service_locator_protocol.create_jev_http_transport()
        )
        self._llm_gateway_protocol: LlmGatewayProtocol = self._create_llm_gateway(coding_assistant_subscription)
        self._expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
        self._expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(
            self._open_router_jev_gateway, jev_settings.parsing_minimum_choice_probability
        )
        self._expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            self._llm_gateway_protocol, extraction_llm_operation_settings
        )
        self._expense_findings_llm_processor: ExpenseFindingsLlmProcessor = ExpenseFindingsLlmProcessor(
            self._llm_gateway_protocol, findings_llm_operation_settings
        )
        self._transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
            self._open_router_jev_gateway
        )
        self._expense_reconciliation_processor: ExpenseReconciliationProcessor = ExpenseReconciliationProcessor()
        self._expense_calculation_processor: ExpenseCalculationProcessor = ExpenseCalculationProcessor()

    @staticmethod
    def _create_jev_gateway(jev_settings: JevSettings, async_base_transport: httpx2.AsyncBaseTransport | None) -> OpenRouterJevGateway:
        return OpenRouterJevGateway(jev_settings, async_base_transport=async_base_transport)

    @staticmethod
    def _create_llm_gateway(coding_assistant_subscription: CodingAssistantSubscription) -> LlmGatewayProtocol:
        if coding_assistant_subscription == CodingAssistantSubscription.GPT_CODEX:
            return CodexSubscriptionGateway()
        if coding_assistant_subscription == CodingAssistantSubscription.GITHUB_COPILOT:
            return CopilotSubscriptionGateway()
        raise ValueError(f"Unsupported coding assistant subscription: {coding_assistant_subscription}")

    async def analyze_expenses(self, expense_lines: Sequence[str], confirmed_duplicate_line_numbers: tuple[int, ...] = ()) -> ExpenseAnalysisResult:
        ValidatorExpenseLines.validate(expense_lines)
        parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await self._parse_and_extract(expense_lines)
        expense_categorization_slots: tuple[ExpenseCategorization | None, ...] = await self._categorize_lines(parsed_expense_lines)
        expense_transactions: tuple[ExpenseTransaction, ...] = await self._expense_reconciliation_processor.reconcile(
            parsed_expense_lines, expense_categorization_slots, confirmed_duplicate_line_numbers
        )
        expense_calculation_result: ExpenseCalculationResult = await self._calculate_and_validate(expense_transactions)
        expense_findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding] = await self._expense_findings_llm_processor.findings(
            expense_calculation_result
        )
        return self._build_result(
            parsed_expense_lines, expense_categorization_slots, expense_transactions, expense_calculation_result, expense_findings
        )

    async def _parse_and_extract(self, expense_lines: Sequence[str]) -> tuple[ParsedExpenseLine, ...]:
        parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await self._expense_line_parsing_processor.parse(expense_lines)
        inferred_year: int | None = self._expense_line_parsing_processor.infer_year(expense_lines)
        jev_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await self._expense_line_jev_extraction_processor.extract_uncertain_lines(
            parsed_expense_lines, inferred_year
        )
        return await self._expense_line_extraction_llm_processor.extract_uncertain_lines(jev_parsed_expense_lines, inferred_year)

    async def _calculate_and_validate(self, expense_transactions: tuple[ExpenseTransaction, ...]) -> ExpenseCalculationResult:
        budget_targets: tuple[BudgetTarget, ...] = self._budget_targets()
        expense_calculation_result: ExpenseCalculationResult = await self._expense_calculation_processor.calculate(
            expense_transactions, budget_targets
        )
        self._validate_reconciliation(expense_calculation_result)
        return expense_calculation_result

    def _budget_targets(self) -> tuple[BudgetTarget, ...]:
        return (
            BudgetTarget(ExpenseCategory.DINING_COFFEE, self._budget_settings.dining_coffee_monthly_budget),
            BudgetTarget(ExpenseCategory.SHOPPING, self._budget_settings.shopping_monthly_budget),
        )

    @staticmethod
    def _validate_reconciliation(expense_calculation_result: ExpenseCalculationResult) -> None:
        if not expense_calculation_result.reconciliation.is_balanced:
            raise ValueError("Expense totals did not reconcile")

    @staticmethod
    def _build_result(
        parsed_expense_lines: tuple[ParsedExpenseLine, ...],
        expense_categorization_slots: tuple[ExpenseCategorization | None, ...],
        expense_transactions: tuple[ExpenseTransaction, ...],
        expense_calculation_result: ExpenseCalculationResult,
        expense_findings: tuple[ExpenseFinding, ExpenseFinding, ExpenseFinding],
    ) -> ExpenseAnalysisResult:
        return ExpenseAnalysisResult(
            categorizations=tuple(
                expense_categorization
                for expense_categorization in expense_categorization_slots
                if expense_categorization is not None
            ),
            parsed_lines=parsed_expense_lines,
            transactions=expense_transactions,
            calculations=expense_calculation_result,
            findings=expense_findings,
            method=ManagerExpenseAnalysis._method_description(),
        )

    async def _categorize_lines(self, parsed_expense_lines: tuple[ParsedExpenseLine, ...]) -> tuple[ExpenseCategorization | None, ...]:
        semaphore: asyncio.Semaphore = asyncio.Semaphore(8)

        async with asyncio.TaskGroup() as task_group:
            tasks: tuple[asyncio.Task[ExpenseCategorization | None], ...] = tuple(
                task_group.create_task(self._categorize_one(parsed_expense_line, semaphore))
                for parsed_expense_line in parsed_expense_lines
            )
        return tuple(task.result() for task in tasks)

    async def _categorize_one(self, parsed_expense_line: ParsedExpenseLine, semaphore: asyncio.Semaphore) -> ExpenseCategorization | None:
        if parsed_expense_line.occurred_on is None or parsed_expense_line.amount is None or parsed_expense_line.description is None:
            return None
        async with semaphore:
            return await self._transaction_categorization_processor.categorize(
                parsed_expense_line.line_number, parsed_expense_line.source_text, parsed_expense_line.description
            )

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
            await self._open_router_jev_gateway.close()
        finally:
            await self._llm_gateway_protocol.close()
