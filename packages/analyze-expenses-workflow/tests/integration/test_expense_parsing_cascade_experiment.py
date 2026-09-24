import json
import os
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

    async def choose(self, state: str, question: ChoiceQuestion) -> ChoiceDecision:
        self.requested_lines.append(state)
        choice: str = self._selected_choice or ("none" if "available balance" in state else "candidate_0")
        return ChoiceDecision(
            choice=choice,
            confidence=1.0,
            probabilities={option: float(option == choice) for option in question.criteria},
        )

    async def close(self) -> None:
        return None


class _ExperimentLlmGateway:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        assert model == "experiment-model"
        assert reasoning_effort == "low"
        self.prompts.append(prompt)
        return json.dumps(
            {
                "lines": [
                    {
                        "line_number": 4,
                        "source_text": "2026-08-16 | Cafe | charge $7.50; available balance $102.00",
                        "occurred_on": None,
                        "date_evidence": None,
                        "description": None,
                        "description_evidence": None,
                        "amount": "7.50",
                        "amount_evidence": "$7.50",
                    },
                    {
                        "line_number": 5,
                        "source_text": "2026-08-17 | Shop | amount pending",
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
    def __init__(self, delegate: LlmGatewayProtocol) -> None:
        self._delegate: LlmGatewayProtocol = delegate
        self.responses: list[str] = []

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        response: str = await self._delegate.complete(prompt, model, reasoning_effort)
        self.responses.append(response)
        return response

    async def close(self) -> None:
        await self._delegate.close()


@pytest.fixture
def expense_parsing_experiment_lines() -> list[str]:
    input_path: Path = Path(__file__).parents[1] / "fixtures" / "expense_parsing_experiment.txt"
    return input_path.read_text(encoding="utf-8").splitlines()


async def test_expense_parsing_experiment_routes_code_jev_and_llm_cases(expense_parsing_experiment_lines: list[str]) -> None:
    expense_lines: list[str] = expense_parsing_experiment_lines
    parser: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    code_lines: tuple[ParsedExpenseLine, ...] = await parser.parse(expense_lines)
    assert tuple(line.issues for line in code_lines) == (
        (),
        ("multiple_amounts",),
        ("multiple_dates",),
        ("multiple_amounts",),
        ("missing_amount",),
    )
    assert "$" not in (code_lines[1].description or "")
    assert "2026" not in (code_lines[2].description or "")
    assert "$" not in (code_lines[3].description or "")

    jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway()
    jev_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(jev_gateway, 0.8)
    jev_lines: tuple[ParsedExpenseLine, ...] = await jev_processor.extract_uncertain_lines(code_lines, parser.infer_year(expense_lines))
    assert jev_lines[1].amount == Decimal("-8.00")
    assert jev_lines[2].occurred_on == date(2026, 8, 14)
    assert jev_lines[1].issues == jev_lines[2].issues == ()
    assert jev_lines[3].amount is None
    assert len(jev_gateway.requested_lines) == 3

    llm_gateway: _ExperimentLlmGateway = _ExperimentLlmGateway()
    llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        llm_gateway, LlmOperationSettings(model="experiment-model", reasoning_effort="low")
    )
    resolved_lines: tuple[ParsedExpenseLine, ...] = await llm_processor.extract_uncertain_lines(jev_lines, parser.infer_year(expense_lines))
    assert resolved_lines[0] == code_lines[0]
    assert resolved_lines[1:3] == jev_lines[1:3]
    assert resolved_lines[3].amount == Decimal("7.50")
    assert resolved_lines[3].issues == ()
    assert resolved_lines[4].amount is None
    assert resolved_lines[4].issues == ("missing_amount",)
    assert len(llm_gateway.prompts) == 1


@pytest.mark.parametrize(
    ("selected_choice", "expected_amount"),
    [("candidate_0", Decimal("100.00")), ("candidate_1", None)],
)
async def test_jev_distinguishes_equal_price_and_balance_occurrences(selected_choice: str, expected_amount: Decimal | None) -> None:
    source_text: str = "2026-08-16 | Store | charge $100.00; available balance $100.00"
    parser: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_lines: tuple[ParsedExpenseLine, ...] = await parser.parse([source_text])
    assert parsed_lines[0].issues == ("multiple_amounts",)
    jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway(selected_choice)
    jev_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(jev_gateway, 0.8)

    extracted_lines: tuple[ParsedExpenseLine, ...] = await jev_processor.extract_uncertain_lines(parsed_lines, 2026)

    assert extracted_lines[0].amount == expected_amount
    assert extracted_lines[0].issues == (() if expected_amount is not None else ("multiple_amounts",))


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_TESTS") != "1", reason="Real Jev calls are opt-in")
async def test_live_jev_selects_expense_date_and_amount_candidates(
    expense_parsing_experiment_lines: list[str], record_property: Callable[[str, object], None]
) -> None:
    parser: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_lines: tuple[ParsedExpenseLine, ...] = await parser.parse(expense_parsing_experiment_lines)
    settings: JevSettings = ConfigurationProvider().get_jev_settings()
    gateway: OpenRouterJevGateway = OpenRouterJevGateway(settings)
    try:
        processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(gateway, settings.parsing_minimum_choice_probability)
        selected_lines: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines(
            parsed_lines, parser.infer_year(expense_parsing_experiment_lines)
        )
    finally:
        await gateway.close()

    record_property("selected_refund_amount", str(selected_lines[1].amount))
    record_property("selected_purchase_date", str(selected_lines[2].occurred_on))
    record_property("selected_balance_line_amount", str(selected_lines[3].amount))
    assert selected_lines[1].amount == Decimal("-8.00")
    assert selected_lines[2].occurred_on == date(2026, 8, 14)


@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM_TESTS") != "1", reason="Real subscription LLM calls are opt-in")
async def test_live_codex_extracts_amount_after_jev_abstention(expense_parsing_experiment_lines: list[str]) -> None:
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
    parser: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_line: ParsedExpenseLine = (await parser.parse(expense_parsing_experiment_lines))[3]
    assert parsed_line.issues == ("multiple_amounts",)
    gateway: _RecordingLlmGateway = _RecordingLlmGateway(CodexSubscriptionGateway())
    try:
        processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            gateway, configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
        )
        resolved: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((parsed_line,), 2026)
    finally:
        await gateway.close()
    assert resolved[0].amount == Decimal("7.50"), gateway.responses
    assert resolved[0].issues == ()


@pytest.mark.skipif(os.environ.get("RUN_LIVE_COPILOT_TESTS") != "1", reason="Real Copilot calls are opt-in")
async def test_live_copilot_extracts_amount_with_openai_model(expense_parsing_experiment_lines: list[str]) -> None:
    configuration_provider: ConfigurationProvider = ConfigurationProvider(
        {
            "CODING_ASSISTANT_SUBSCRIPTION": "GITHUB_COPILOT",
            "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": "gpt-6-astra",
            "EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT": "low",
        }
    )
    parser: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_line: ParsedExpenseLine = (await parser.parse(expense_parsing_experiment_lines))[3]
    assert parsed_line.issues == ("multiple_amounts",)
    async with CopilotSubscriptionGateway() as copilot_gateway:
        gateway: _RecordingLlmGateway = _RecordingLlmGateway(copilot_gateway)
        processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            gateway, configuration_provider.get_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
        )
        resolved: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((parsed_line,), 2026)
    assert resolved[0].amount == Decimal("7.50"), gateway.responses
    assert resolved[0].issues == ()
