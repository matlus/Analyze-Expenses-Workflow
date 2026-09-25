import json
import os
import secrets
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    CodingAssistantSubscription,
)
from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperation, LlmOperationSettings
from analyze_expenses_workflow.managers.exceptions.configuration_setting_exception import ConfigurationSettingException
from analyze_expenses_workflow.managers.gateways.codex_subscription_gateway import CodexSubscriptionGateway
from analyze_expenses_workflow.managers.gateways.copilot_subscription_gateway import CopilotSubscriptionGateway
from analyze_expenses_workflow.managers.gateways.llm_gateway_protocol import LlmGatewayProtocol
from analyze_expenses_workflow.managers.gateways.open_router_jev_gateway import OpenRouterJevGateway
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.llm_processors.expense_line_extraction_llm_processor import ExpenseLineExtractionLlmProcessor
from analyze_expenses_workflow.managers.processors.expense_line_jev_extraction_processor import ExpenseLineJevExtractionProcessor
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class _ExperimentJevGateway:
    def __init__(self, selected_choice: str | None = None) -> None:
        self.requested_lines: list[str] = []
        self._selected_choice: str | None = selected_choice

    async def choose(self, state: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        self.requested_lines.append(state)
        choice: str = self._selected_choice or ("none" if "available balance" in state else "candidate_0")
        return ChoiceDecision(
            choice=choice,
            confidence=1.0,
            probabilities={option: float(option == choice) for option in choice_question.criteria},
        )

    async def close(self) -> None:
        return None


class _ExperimentLlmGateway:
    _EXPECTED_REQUEST: tuple[str, str | None] = ("experiment-model", "low")

    def __init__(self, expense_lines: list[str]) -> None:
        self.prompts: list[str] = []
        self._expense_lines: list[str] = expense_lines

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        assert (model, reasoning_effort) == self._EXPECTED_REQUEST
        self.prompts.append(prompt)
        return json.dumps(
            {
                "lines": [
                    {
                        "line_number": 4,
                        "source_text": self._expense_lines[3],
                        "occurred_on": None,
                        "date_evidence": None,
                        "description": None,
                        "description_evidence": None,
                        "amount": "7.50",
                        "amount_evidence": "$7.50",
                    },
                    {
                        "line_number": 5,
                        "source_text": self._expense_lines[4],
                        "occurred_on": None,
                        "date_evidence": None,
                        "description": None,
                        "description_evidence": None,
                        "amount": None,
                        "amount_evidence": None,
                    },
                ]
            }
        )

    async def close(self) -> None:
        return None


class _RecordingLlmGateway:
    def __init__(self, llm_gateway_protocol: LlmGatewayProtocol) -> None:
        self._llm_gateway_protocol: LlmGatewayProtocol = llm_gateway_protocol
        self.responses: list[str] = []

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        response: str = await self._llm_gateway_protocol.complete(prompt, model, reasoning_effort)
        self.responses.append(response)
        return response

    async def close(self) -> None:
        await self._llm_gateway_protocol.close()


@pytest.mark.parametrize("minimum_choice_probability", [-0.1, 1.1, float("inf"), float("nan")])
def test_JevExtraction_WhenChoiceThresholdIsInvalid_ThenRejectsConfiguration(minimum_choice_probability: float) -> None:
    system_one_gateway_protocol: _ExperimentJevGateway = _ExperimentJevGateway()

    with pytest.raises(ConfigurationSettingException, match="between zero and one"):
        ExpenseLineJevExtractionProcessor(system_one_gateway_protocol, minimum_choice_probability)


@pytest.fixture
def expense_parsing_experiment_lines() -> list[str]:
    input_path: Path = Path(__file__).parents[1] / "fixtures" / "expense_parsing_experiment.txt"
    generated_merchant_name_by_fixture_name: dict[str, str] = {
        "Grocery Market": secrets.token_hex(8),
        "Cafe": secrets.token_hex(8),
        "Garage": secrets.token_hex(8),
        "Shop": secrets.token_hex(8),
    }
    expense_lines: list[str] = input_path.read_text(encoding="utf-8").splitlines()
    for merchant_name_pair in generated_merchant_name_by_fixture_name.items():
        fixture_merchant_name: str = merchant_name_pair[0]
        generated_merchant_name: str = merchant_name_pair[1]
        expense_lines = [line.replace(fixture_merchant_name, generated_merchant_name) for line in expense_lines]
    return expense_lines


async def test_ExpenseParsingCascade_WhenLinesNeedCodeJevAndLlm_ThenResolvesEachStage(expense_parsing_experiment_lines: list[str]) -> None:
    expense_lines: list[str] = expense_parsing_experiment_lines
    multiple_amounts_issue: str = "multiple_amounts"
    missing_amount_issue: str = "missing_amount"
    expected_code_issues: tuple[tuple[str, ...], ...] = (
        (),
        (multiple_amounts_issue,),
        ("multiple_dates",),
        (multiple_amounts_issue,),
        (missing_amount_issue,),
    )
    expected_code_description_cleanup: tuple[bool, bool, bool] = (True, True, True)
    expected_code_stage: tuple[tuple[tuple[str, ...], ...], tuple[bool, bool, bool]] = (
        expected_code_issues,
        expected_code_description_cleanup,
    )
    expected_jev_stage: tuple[Decimal, date, tuple[str, ...], tuple[str, ...], Decimal | None, int] = (
        Decimal("-8.00"),
        date(2026, 8, 14),
        (),
        (),
        None,
        3,
    )
    expected_llm_stage: tuple[bool, bool, Decimal, tuple[str, ...], Decimal | None, tuple[str, ...], int] = (
        True,
        True,
        Decimal("7.50"),
        (),
        None,
        (missing_amount_issue, "llm_extraction_failed"),
        2,
    )
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    code_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse(expense_lines)
    actual_code_issues: tuple[tuple[str, ...], ...] = tuple(line.issues for line in code_lines)
    actual_code_description_cleanup: tuple[bool, bool, bool] = (
        "$" not in (code_lines[1].description or ""),
        "2026" not in (code_lines[2].description or ""),
        "$" not in (code_lines[3].description or ""),
    )
    assert (actual_code_issues, actual_code_description_cleanup) == expected_code_stage

    jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway()
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(jev_gateway, 0.8)
    jev_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        code_lines, expense_line_parsing_processor.infer_year(expense_lines)
    )
    actual_jev_stage: tuple[Decimal | None, date | None, tuple[str, ...], tuple[str, ...], Decimal | None, int] = (
        jev_lines[1].amount,
        jev_lines[2].occurred_on,
        jev_lines[1].issues,
        jev_lines[2].issues,
        jev_lines[3].amount,
        len(jev_gateway.requested_lines),
    )
    assert actual_jev_stage == expected_jev_stage

    llm_gateway: _ExperimentLlmGateway = _ExperimentLlmGateway(expense_lines)
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        llm_gateway, LlmOperationSettings(model="experiment-model", reasoning_effort="low")
    )
    resolved_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        jev_lines, expense_line_parsing_processor.infer_year(expense_lines)
    )
    actual_llm_stage: tuple[bool, bool, Decimal | None, tuple[str, ...], Decimal | None, tuple[str, ...], int] = (
        resolved_lines[0] == code_lines[0],
        resolved_lines[1:3] == jev_lines[1:3],
        resolved_lines[3].amount,
        resolved_lines[3].issues,
        resolved_lines[4].amount,
        resolved_lines[4].issues,
        len(llm_gateway.prompts),
    )
    assert actual_llm_stage == expected_llm_stage


@pytest.mark.parametrize(
    ("selected_choice", "expected_amount"),
    [("candidate_0", Decimal("100.00")), ("candidate_1", None)],
)
async def test_JevExtraction_WhenEqualChargeAndBalanceOccur_ThenUsesSelectedOccurrence(selected_choice: str, expected_amount: Decimal | None) -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | charge $100.00; available balance $100.00"
    expected_initial_issues: tuple[str, ...] = ("multiple_amounts",)
    expected_final_issues: tuple[str, ...] = () if expected_amount is not None else expected_initial_issues
    expected_result: tuple[Decimal | None, tuple[str, ...]] = (expected_amount, expected_final_issues)
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    assert parsed_lines[0].issues == expected_initial_issues
    jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway(selected_choice)
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(jev_gateway, 0.8)

    extracted_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(parsed_lines, 2026)

    assert (extracted_lines[0].amount, extracted_lines[0].issues) == expected_result


@pytest.mark.parametrize(
    ("selected_choice", "expected_amount"),
    [("candidate_0", Decimal("-5.00")), ("candidate_1", Decimal("20.00"))],
)
async def test_JevExtraction_WhenRefundAndPurchaseAmountsCoexist_ThenKeepsSelectedAmountSign(selected_choice: str, expected_amount: Decimal) -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | Refund $5.00; new purchase $20.00"
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway(selected_choice)
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(jev_gateway, 0.8)
    expected_issues: tuple[str, ...] = ()
    expected_result: tuple[Decimal, tuple[str, ...]] = (expected_amount, expected_issues)

    resolved_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        parsed_expense_lines, 2026
    )

    assert (resolved_expense_lines[0].amount, resolved_expense_lines[0].issues) == expected_result


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_TESTS") != "1", reason="Real Jev calls are opt-in")
async def test_JevExtraction_WhenLiveGatewaySelectsCandidates_ThenResolvesDateAndAmount(
    expense_parsing_experiment_lines: list[str], record_property: Callable[[str, object], None]
) -> None:
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse(expense_parsing_experiment_lines)
    jev_settings: JevSettings = ConfigurationProvider().get_jev_settings()
    open_router_jev_gateway: OpenRouterJevGateway = OpenRouterJevGateway(jev_settings)
    expected_selected_fields: tuple[Decimal, date] = (Decimal("-8.00"), date(2026, 8, 14))
    try:
        expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(
            open_router_jev_gateway, jev_settings.parsing_minimum_choice_probability
        )
        selected_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
            parsed_lines, expense_line_parsing_processor.infer_year(expense_parsing_experiment_lines)
        )
    finally:
        await open_router_jev_gateway.close()

    record_property("selected_refund_amount", str(selected_lines[1].amount))
    record_property("selected_purchase_date", str(selected_lines[2].occurred_on))
    record_property("selected_balance_line_amount", str(selected_lines[3].amount))
    assert (selected_lines[1].amount, selected_lines[2].occurred_on) == expected_selected_fields


@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM_TESTS") != "1", reason="Real subscription LLM calls are opt-in")
async def test_CodexExtraction_WhenLiveJevAbstains_ThenResolvesAmount(expense_parsing_experiment_lines: list[str]) -> None:
    model: str = os.environ.get("EXPENSE_PARSING_TEST_MODEL", "gpt-6-sol")
    reasoning_effort: str = os.environ.get("EXPENSE_PARSING_TEST_REASONING_EFFORT", "medium")
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": model,
            "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT": reasoning_effort,
        }
    )
    if configuration_provider.get_coding_assistant_settings().coding_assistant_subscription != CodingAssistantSubscription.GPT_CODEX:
        pytest.skip("This smoke test uses the Codex subscription gateway")
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_line: ParsedExpenseLine = (await expense_line_parsing_processor.parse(expense_parsing_experiment_lines))[3]
    expected_initial_issues: tuple[str, ...] = ("multiple_amounts",)
    expected_resolved_fields: tuple[Decimal, tuple[str, ...]] = (Decimal("7.50"), ())
    assert parsed_expense_line.issues == expected_initial_issues
    recording_llm_gateway: _RecordingLlmGateway = _RecordingLlmGateway(CodexSubscriptionGateway())
    try:
        expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            recording_llm_gateway, configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
        )
        resolved: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines((parsed_expense_line,), 2026)
    finally:
        await recording_llm_gateway.close()
    assert (resolved[0].amount, resolved[0].issues) == expected_resolved_fields, recording_llm_gateway.responses


@pytest.mark.skipif(os.environ.get("RUN_LIVE_COPILOT_TESTS") != "1", reason="Real Copilot calls are opt-in")
async def test_CopilotExtraction_WhenLiveOpenAiModelRuns_ThenResolvesAmount(expense_parsing_experiment_lines: list[str]) -> None:
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "CODING_ASSISTANT_SUBSCRIPTION": "GITHUB_COPILOT",
            "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": "gpt-6-astra",
            "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT": "low",
        }
    )
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_line: ParsedExpenseLine = (await expense_line_parsing_processor.parse(expense_parsing_experiment_lines))[3]
    expected_initial_issues: tuple[str, ...] = ("multiple_amounts",)
    expected_resolved_fields: tuple[Decimal, tuple[str, ...]] = (Decimal("7.50"), ())
    assert parsed_expense_line.issues == expected_initial_issues
    async with CopilotSubscriptionGateway() as copilot_gateway:
        recording_llm_gateway: _RecordingLlmGateway = _RecordingLlmGateway(copilot_gateway)
        expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            recording_llm_gateway, configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
        )
        resolved: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines((parsed_expense_line,), 2026)
    assert (resolved[0].amount, resolved[0].issues) == expected_resolved_fields, recording_llm_gateway.responses
