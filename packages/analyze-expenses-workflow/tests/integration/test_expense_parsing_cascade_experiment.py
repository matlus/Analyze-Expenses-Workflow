import json
import os
import secrets
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from acceptance_support.mediator_jev import TestMediatorJev
from acceptance_support.service_locator_testing import ServiceLocatorTesting

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    CodingAssistantSubscription,
)
from analyze_expenses_workflow.managers.configuration_providers.settings_models.jev_settings import JevSettings
from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperation, LlmOperationSettings
from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import (
    JevRateLimitRetriesExhaustedException,
    JevRequestFailedException,
    JevRequestRejectedException,
    JevServerRetryRequestedRetriesExhaustedException,
    SystemOneGatewayBaseException,
)
from analyze_expenses_workflow.managers.gateways.llm_gateway_codex_subscription import LlmGatewayCodexSubscription
from analyze_expenses_workflow.managers.gateways.llm_gateway_copilot_subscription import LlmGatewayCopilotSubscription
from analyze_expenses_workflow.managers.gateways.llm_gateway_protocol import LlmGatewayProtocol
from analyze_expenses_workflow.managers.gateways.system_one_gateway_open_router_jev import SystemOneGatewayOpenRouterJev
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.llm_processors.expense_line_extraction_llm_processor import ExpenseLineExtractionLlmProcessor
from analyze_expenses_workflow.managers.processors.expense_line_jev_extraction_processor import ExpenseLineJevExtractionProcessor
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class _ExperimentJevGateway:
    def __init__(self, selected_choice: str | None = None) -> None:
        self.requested_lines: list[str] = []
        self._selected_choice: str | None = selected_choice

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        self.requested_lines.append(source_text)
        choice: str = self._selected_choice or ("none" if "available balance" in source_text else "candidate_0")
        return ChoiceDecision(
            choice=choice,
            confidence=1.0,
            probabilities={option: float(option == choice) for option in choice_question.criterion_by_choice_key},
        )

    async def close(self) -> None:
        return None


class _FailingJevGateway:
    def __init__(self, system_one_gateway_base_exception: SystemOneGatewayBaseException) -> None:
        self.requested_lines: list[str] = []
        self._system_one_gateway_base_exception: SystemOneGatewayBaseException = system_one_gateway_base_exception

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        self.requested_lines.append(source_text)
        raise self._system_one_gateway_base_exception

    async def close(self) -> None:
        return None


class _ExperimentLlmGateway:
    def __init__(self, expense_lines: list[str], expected_model: str) -> None:
        self.prompts: list[str] = []
        self._expense_lines: list[str] = expense_lines
        self._expected_request: tuple[str, str | None] = (expected_model, "low")

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        assert (model, reasoning_effort) == self._expected_request
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
    expected_model: str = f"test-model-{secrets.token_hex(8)}"
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    code_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse(expense_lines)
    actual_code_issues: tuple[tuple[str, ...], ...] = tuple(line.issues for line in code_parsed_expense_lines)
    actual_code_description_cleanup: tuple[bool, bool, bool] = (
        "$" not in (code_parsed_expense_lines[1].description or ""),
        "2026" not in (code_parsed_expense_lines[2].description or ""),
        "$" not in (code_parsed_expense_lines[3].description or ""),
    )
    assert (actual_code_issues, actual_code_description_cleanup) == expected_code_stage

    experiment_jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway()
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(experiment_jev_gateway, 0.8)
    jev_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        code_parsed_expense_lines, expense_line_parsing_processor.infer_year(expense_lines)
    )
    actual_jev_stage: tuple[Decimal | None, date | None, tuple[str, ...], tuple[str, ...], Decimal | None, int] = (
        jev_parsed_expense_lines[1].amount,
        jev_parsed_expense_lines[2].occurred_on,
        jev_parsed_expense_lines[1].issues,
        jev_parsed_expense_lines[2].issues,
        jev_parsed_expense_lines[3].amount,
        len(experiment_jev_gateway.requested_lines),
    )
    assert actual_jev_stage == expected_jev_stage

    experiment_llm_gateway: _ExperimentLlmGateway = _ExperimentLlmGateway(expense_lines, expected_model)
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        experiment_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort="low")
    )
    resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        jev_parsed_expense_lines, expense_line_parsing_processor.infer_year(expense_lines)
    )
    actual_llm_stage: tuple[bool, bool, Decimal | None, tuple[str, ...], Decimal | None, tuple[str, ...], int] = (
        resolved_parsed_expense_lines[0] == code_parsed_expense_lines[0],
        resolved_parsed_expense_lines[1:3] == jev_parsed_expense_lines[1:3],
        resolved_parsed_expense_lines[3].amount,
        resolved_parsed_expense_lines[3].issues,
        resolved_parsed_expense_lines[4].amount,
        resolved_parsed_expense_lines[4].issues,
        len(experiment_llm_gateway.prompts),
    )
    assert actual_llm_stage == expected_llm_stage


async def test_JevExtraction_WhenEqualChargeAndBalanceOccur_ThenUsesTheSelectedChargeOccurrence() -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | charge $100.00; available balance $100.00"
    expected_initial_issues: tuple[str, ...] = ("multiple_amounts",)
    expected_result: tuple[Decimal | None, tuple[str, ...]] = (Decimal("100.00"), ())
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    assert parsed_expense_lines[0].issues == expected_initial_issues
    experiment_jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway("candidate_0")
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(experiment_jev_gateway, 0.8)

    extracted_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        parsed_expense_lines, 2026
    )

    assert (extracted_parsed_expense_lines[0].amount, extracted_parsed_expense_lines[0].issues) == expected_result


async def test_JevExtraction_WhenJevSelectsTheBalanceOccurrence_ThenKeepsTheLineUnresolved() -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | charge $100.00; available balance $100.00"
    expected_initial_issues: tuple[str, ...] = ("multiple_amounts",)
    expected_result: tuple[Decimal | None, tuple[str, ...]] = (None, expected_initial_issues)
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    assert parsed_expense_lines[0].issues == expected_initial_issues
    experiment_jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway("candidate_1")
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(experiment_jev_gateway, 0.8)

    extracted_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        parsed_expense_lines, 2026
    )

    assert (extracted_parsed_expense_lines[0].amount, extracted_parsed_expense_lines[0].issues) == expected_result


@pytest.mark.parametrize(
    "transient_jev_exception_type",
    [JevRequestFailedException, JevRateLimitRetriesExhaustedException, JevServerRetryRequestedRetriesExhaustedException],
)
async def test_JevExtraction_WhenJevIsTemporarilyUnavailable_ThenKeepsTheDeterministicParse(
    transient_jev_exception_type: type[SystemOneGatewayBaseException],
) -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | charge $100.00; available balance $100.00"
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    expected_result: tuple[tuple[ParsedExpenseLine, ...], int] = (parsed_expense_lines, 1)
    failing_jev_gateway: _FailingJevGateway = _FailingJevGateway(transient_jev_exception_type(secrets.token_hex(8)))
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(failing_jev_gateway, 0.8)

    extracted_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        parsed_expense_lines, 2026
    )

    assert (extracted_parsed_expense_lines, len(failing_jev_gateway.requested_lines)) == expected_result


async def test_JevExtraction_WhenJevRejectsTheRequest_ThenPropagatesTheRejection() -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | charge $100.00; available balance $100.00"
    expected_exception_message: str = secrets.token_hex(8)
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    failing_jev_gateway: _FailingJevGateway = _FailingJevGateway(JevRequestRejectedException(expected_exception_message))
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(failing_jev_gateway, 0.8)

    with pytest.raises(JevRequestRejectedException, match=expected_exception_message):
        await expense_line_jev_extraction_processor.extract_uncertain_lines(parsed_expense_lines, 2026)


@pytest.mark.parametrize(
    ("selected_choice", "expected_amount"),
    [("candidate_0", Decimal("-5.00")), ("candidate_1", Decimal("20.00"))],
)
async def test_JevExtraction_WhenRefundAndPurchaseAmountsCoexist_ThenKeepsSelectedAmountSign(selected_choice: str, expected_amount: Decimal) -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | Refund $5.00; new purchase $20.00"
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    experiment_jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway(selected_choice)
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(experiment_jev_gateway, 0.8)
    expected_issues: tuple[str, ...] = ()
    expected_result: tuple[Decimal, tuple[str, ...]] = (expected_amount, expected_issues)

    resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        parsed_expense_lines, 2026
    )

    assert (resolved_parsed_expense_lines[0].amount, resolved_parsed_expense_lines[0].issues) == expected_result


async def test_JevExtraction_WhenALineHasMultipleDatesAndAmounts_ThenAppliesBothSelections() -> None:
    source_text: str = f"2026-08-16 | {secrets.token_hex(8)} | posted 2026-08-18 | Refund $5.00; new purchase $20.00"
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse([source_text])
    expected_initial_issues: tuple[str, ...] = ("multiple_dates", "multiple_amounts")
    assert parsed_expense_lines[0].issues == expected_initial_issues
    experiment_jev_gateway: _ExperimentJevGateway = _ExperimentJevGateway("candidate_1")
    expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(experiment_jev_gateway, 0.8)
    expected_jev_extraction: tuple[date | None, Decimal | None, tuple[str, ...], int] = (date(2026, 8, 18), Decimal("20.00"), (), 2)

    resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
        parsed_expense_lines, 2026
    )

    actual_jev_extraction: tuple[date | None, Decimal | None, tuple[str, ...], int] = (
        resolved_parsed_expense_lines[0].occurred_on,
        resolved_parsed_expense_lines[0].amount,
        resolved_parsed_expense_lines[0].issues,
        len(experiment_jev_gateway.requested_lines),
    )
    assert actual_jev_extraction == expected_jev_extraction, (
        f"Jev must select both the date and the amount of {source_text!r}; "
        f"expected (date, amount, remaining issues, Jev requests) {expected_jev_extraction}; actual {actual_jev_extraction}"
    )


@pytest.mark.live_jev
@pytest.mark.skipif(os.environ.get("RUN_LIVE_JEV_TESTS") != "1", reason="Real Jev calls are opt-in")
async def test_JevExtraction_WhenLiveGatewaySelectsCandidates_ThenResolvesDateAndAmount(
    expense_parsing_experiment_lines: list[str], record_property: Callable[[str, object], None]
) -> None:
    expense_line_parsing_processor: ExpenseLineParsingProcessor = ExpenseLineParsingProcessor()
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_parsing_processor.parse(expense_parsing_experiment_lines)
    jev_settings: JevSettings = ConfigurationProvider().get_jev_settings()
    system_one_gateway_open_router_jev: SystemOneGatewayOpenRouterJev = SystemOneGatewayOpenRouterJev(jev_settings)
    expected_selected_fields: tuple[Decimal, date] = (Decimal("-8.00"), date(2026, 8, 14))
    try:
        expense_line_jev_extraction_processor: ExpenseLineJevExtractionProcessor = ExpenseLineJevExtractionProcessor(
            system_one_gateway_open_router_jev, jev_settings.parsing_minimum_choice_probability
        )
        selected_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_jev_extraction_processor.extract_uncertain_lines(
            parsed_expense_lines, expense_line_parsing_processor.infer_year(expense_parsing_experiment_lines)
        )
    finally:
        await system_one_gateway_open_router_jev.close()

    record_property("selected_refund_amount", str(selected_parsed_expense_lines[1].amount))
    record_property("selected_purchase_date", str(selected_parsed_expense_lines[2].occurred_on))
    record_property("selected_balance_line_amount", str(selected_parsed_expense_lines[3].amount))
    assert (selected_parsed_expense_lines[1].amount, selected_parsed_expense_lines[2].occurred_on) == expected_selected_fields


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
    service_locator_testing: ServiceLocatorTesting = ServiceLocatorTesting(TestMediatorJev())
    recording_llm_gateway: _RecordingLlmGateway = _RecordingLlmGateway(LlmGatewayCodexSubscription(service_locator_testing.create_codex_client()))
    try:
        expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            recording_llm_gateway, configuration_provider.retrieve_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
        )
        resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
            (parsed_expense_line,), 2026
        )
    finally:
        await recording_llm_gateway.close()
    assert (
        resolved_parsed_expense_lines[0].amount,
        resolved_parsed_expense_lines[0].issues,
    ) == expected_resolved_fields, recording_llm_gateway.responses


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
    service_locator_testing: ServiceLocatorTesting = ServiceLocatorTesting(TestMediatorJev())
    async with LlmGatewayCopilotSubscription(service_locator_testing.create_copilot_client()) as copilot_gateway:
        recording_llm_gateway: _RecordingLlmGateway = _RecordingLlmGateway(copilot_gateway)
        expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
            recording_llm_gateway, configuration_provider.retrieve_llm_operation_settings(LlmOperation.EXPENSE_LINE_EXTRACTION)
        )
        resolved_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
            (parsed_expense_line,), 2026
        )
    assert (
        resolved_parsed_expense_lines[0].amount,
        resolved_parsed_expense_lines[0].issues,
    ) == expected_resolved_fields, recording_llm_gateway.responses
