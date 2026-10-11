import json
import secrets
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Final

import pytest
from acceptance_support.asserter_expense_line_extraction import (
    ActualExpenseLineExtraction,
    ExpectedExpenseLineExtraction,
    ExpenseLineExtractionAsserter,
)

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmCliExitedException,
    LlmGatewayBaseException,
    LlmRequestFailedException,
    LlmRequestTimedOutException,
)
from analyze_expenses_workflow.managers.llm_processors.bases.llm_processor_base import ProcessingEventKind
from analyze_expenses_workflow.managers.llm_processors.expense_line_extraction_llm_processor import ExpenseLineExtractionLlmProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine

LLM_EXTRACTION_FAILED_ISSUE: Final[str] = "llm_extraction_failed"
EXTRACTION_LIFECYCLE_EVENT_KINDS: Final[tuple[ProcessingEventKind, ...]] = ("progress", "complete")


def _create_test_model() -> str:
    return f"test-model-{secrets.token_hex(8)}"


@dataclass(frozen=True, slots=True)
class ExtractedLineFixture:
    line_number: int
    source_text: str
    occurred_on: str | None
    date_evidence: str | None
    description: str | None
    description_evidence: str | None
    amount: str | None
    amount_evidence: str | None


class FakeLlmGateway:
    def __init__(self, expected_model: str, responses: list[str]) -> None:
        self.responses: list[str] = responses
        self.prompts: list[str] = []
        self._expected_model: str = expected_model

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self.prompts.append(prompt)
        assert model == self._expected_model, f"The processor must forward the configured model {self._expected_model!r}; received {model!r}"
        assert reasoning_effort is None, f"The processor must forward no reasoning effort when none is configured; received {reasoning_effort!r}"
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


class FailingLlmGateway:
    def __init__(self, failure_type: type[LlmGatewayBaseException]) -> None:
        self._failure_type: type[LlmGatewayBaseException] = failure_type
        self.prompts: list[str] = []

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self.prompts.append(prompt)
        raise self._failure_type("Subscription request failed")

    async def close(self) -> None:
        return None


class ProcessingEventRecorder:
    def __init__(self) -> None:
        self.captured_processing_event_kinds: list[ProcessingEventKind] = []

    async def record(self, processing_event_kind: ProcessingEventKind, message: str) -> None:
        self.captured_processing_event_kinds.append(processing_event_kind)


@pytest.mark.parametrize("failure_type", [LlmRequestFailedException, LlmRequestTimedOutException, LlmCliExitedException])
async def test_extract_uncertain_lines_WhenTheGatewayFails_ThenLeavesTheUncertainLineUnparsedAndPreservesTheCompleteLine(
    failure_type: type[LlmGatewayBaseException],
) -> None:
    # Arrange
    uncertain_source_text: str = "unreadable entry"
    original_issues: tuple[str, ...] = ("missing_date", "missing_amount")
    complete_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(
        1, "2026-06-01 | Market | $10.00", date(2026, 6, 1), "Market", Decimal("10.00"), ()
    )
    uncertain_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(2, uncertain_source_text, None, uncertain_source_text, None, original_issues)
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(
            complete_parsed_expense_line,
            replace(uncertain_parsed_expense_line, issues=(*original_issues, LLM_EXTRACTION_FAILED_ISSUE)),
        ),
        passed_through_parsed_expense_lines=(complete_parsed_expense_line,),
        prompted_line_numbers=((uncertain_parsed_expense_line.line_number,),),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    model: str = _create_test_model()
    failing_llm_gateway: FailingLlmGateway = FailingLlmGateway(failure_type)
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        failing_llm_gateway, LlmOperationSettings(model=model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (complete_parsed_expense_line, uncertain_parsed_expense_line), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(failing_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "A gateway failure is not retried: the uncertain line keeps its issues plus llm_extraction_failed and the complete line passes through.",
    )


async def test_extract_uncertain_lines_WhenAmountEvidenceMatchesAnEqualBalanceOccurrence_ThenLeavesTheAmountUnresolved() -> None:
    # Arrange
    source_text: str = "2026-08-16 | Store | charge $100.00; available balance $100.00"
    original_issue: str = "multiple_amounts"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(
        1, source_text, date(2026, 8, 16), "Store | charge ; available balance", None, (original_issue,)
    )
    extraction_response_json: str = _create_extraction_response_json(
        line_number=1,
        source_text=source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="100.00",
        amount_evidence="$100.00",
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, issues=(original_issue, LLM_EXTRACTION_FAILED_ISSUE)),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((1,), (1,)),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(expected_model, [extraction_response_json, extraction_response_json])
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "Amount evidence whose text also matches a balance occurrence is ambiguous, so the amount stays unresolved after the retry.",
    )


async def test_extract_uncertain_lines_WhenAllLinesAreParsed_ThenMakesNoLlmCall() -> None:
    # Arrange
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, "2026-06-01 Market $10.00", date(2026, 6, 1), "Market", Decimal("10.00"), ())
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(parsed_expense_line,),
        passed_through_parsed_expense_lines=(parsed_expense_line,),
        prompted_line_numbers=(),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(expected_model, [])
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "When every line is already parsed, no LLM request is made and the lines are returned unchanged.",
    )


@pytest.mark.parametrize("fenced_json", [False, True])
async def test_extract_uncertain_lines_WhenTheLlmSuppliesEvidencedFields_ThenResolvesTheUncertainLine(fenced_json: bool) -> None:
    # Arrange
    expected_occurred_on: date = date(2026, 8, 14)
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(7, "Market on 8/14: $12.50", None, "Market on", Decimal("12.50"), ("missing_date",))
    extraction_response_json: str = _create_extraction_response_json(
        line_number=parsed_expense_line.line_number,
        source_text=parsed_expense_line.source_text,
        occurred_on=expected_occurred_on.isoformat(),
        date_evidence="8/14",
        description=None,
        description_evidence=None,
        amount=None,
        amount_evidence=None,
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, occurred_on=expected_occurred_on, issues=()),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,),),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model, [f"```json\n{extraction_response_json}\n```" if fenced_json else extraction_response_json]
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "An evidenced date from one LLM response resolves the line, keeps its known description and clears the resolved issue.",
    )


async def test_extract_uncertain_lines_WhenOnlySomeLinesAreUncertain_ThenPreservesTheOriginalOrderAndSources() -> None:
    # Arrange
    expected_occurred_on: date = date(2026, 8, 15)
    first_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, "2026-08-14 Market $10.00", date(2026, 8, 14), "Market", Decimal("10.00"), ())
    uncertain_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(2, "Shop on 8/15 $12.50", None, "Shop", Decimal("12.50"), ("missing_date",))
    last_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(3, "2026-08-16 Cafe $5.00", date(2026, 8, 16), "Cafe", Decimal("5.00"), ())
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(
            first_parsed_expense_line,
            replace(uncertain_parsed_expense_line, occurred_on=expected_occurred_on, issues=()),
            last_parsed_expense_line,
        ),
        passed_through_parsed_expense_lines=(first_parsed_expense_line, last_parsed_expense_line),
        prompted_line_numbers=((uncertain_parsed_expense_line.line_number,),),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model,
        [
            _create_extraction_response_json(
                line_number=uncertain_parsed_expense_line.line_number,
                source_text=uncertain_parsed_expense_line.source_text,
                occurred_on=expected_occurred_on.isoformat(),
                date_evidence="8/15",
                description=None,
                description_evidence=None,
                amount=None,
                amount_evidence=None,
            )
        ],
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (first_parsed_expense_line, uncertain_parsed_expense_line, last_parsed_expense_line), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "Only uncertain lines are sent to the LLM; resolved lines return in their original position and certain lines pass through untouched.",
    )


async def test_extract_uncertain_lines_WhenTheResponseSchemaFailsOnce_ThenRetriesOnceAndPublishesTheLifecycle() -> None:
    # Arrange
    expected_occurred_on: date = date(2026, 8, 14)
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, "Aug 14 Market $12.50", None, "Market", Decimal("12.50"), ("year_not_inferable",))
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, occurred_on=expected_occurred_on, issues=()),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,), (parsed_expense_line.line_number,)),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model,
        [
            "not JSON",
            _create_extraction_response_json(
                line_number=parsed_expense_line.line_number,
                source_text=parsed_expense_line.source_text,
                occurred_on=expected_occurred_on.isoformat(),
                date_evidence="Aug 14",
                description=None,
                description_evidence=None,
                amount=None,
                amount_evidence=None,
            ),
        ],
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "A response that fails the schema is retried once, and the extraction publishes progress then complete.",
    )


async def test_extract_uncertain_lines_WhenOneLineFailsEvidenceValidation_ThenRetriesOnlyThatLine() -> None:
    # Arrange
    expected_first_occurred_on: date = date(2026, 8, 14)
    expected_second_occurred_on: date = date(2026, 8, 15)
    first_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(
        1, "8/14 Market $10.00", None, "Market", Decimal("10.00"), ("year_not_inferable",)
    )
    second_parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(2, "8/15 Cafe $5.00", None, "Cafe", Decimal("5.00"), ("year_not_inferable",))

    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(
            replace(first_parsed_expense_line, occurred_on=expected_first_occurred_on, issues=()),
            replace(second_parsed_expense_line, occurred_on=expected_second_occurred_on, issues=()),
        ),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=(
            (first_parsed_expense_line.line_number, second_parsed_expense_line.line_number),
            (second_parsed_expense_line.line_number,),
        ),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model,
        [
            json.dumps(
                {
                    "lines": [
                        asdict(_create_extracted_line_fixture(first_parsed_expense_line, expected_first_occurred_on, "8/14")),
                        asdict(_create_extracted_line_fixture(second_parsed_expense_line, expected_second_occurred_on, "8/14")),
                    ]
                }
            ),
            json.dumps({"lines": [asdict(_create_extracted_line_fixture(second_parsed_expense_line, expected_second_occurred_on, "8/15"))]}),
        ],
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (first_parsed_expense_line, second_parsed_expense_line), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "A line whose date evidence does not match is retried alone; the line that validated keeps its first-attempt result.",
    )


async def test_extract_uncertain_lines_WhenTheLlmInventsAnAmount_ThenPreservesTheOriginalIssueAfterTheRetry() -> None:
    # Arrange
    original_issue: str = "missing_amount"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, "Market 2026-08-14", date(2026, 8, 14), "Market", None, (original_issue,))
    invented_amount_response_json: str = _create_extraction_response_json(
        line_number=parsed_expense_line.line_number,
        source_text=parsed_expense_line.source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="42.00",
        amount_evidence="$42.00",
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, issues=(original_issue, LLM_EXTRACTION_FAILED_ISSUE)),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,), (parsed_expense_line.line_number,)),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(expected_model, [invented_amount_response_json, invented_amount_response_json])
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "An amount absent from the source text is rejected on both attempts, so the line keeps missing_amount plus llm_extraction_failed.",
    )


async def test_extract_uncertain_lines_WhenTheLlmSelectsABalanceAsTheAmount_ThenLeavesTheLineUnresolved() -> None:
    # Arrange
    original_issue: str = "nontransaction_amount"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(
        1, "2026-08-16 | available balance $102.00", date(2026, 8, 16), "available balance", None, (original_issue,)
    )
    balance_amount_response_json: str = _create_extraction_response_json(
        line_number=parsed_expense_line.line_number,
        source_text=parsed_expense_line.source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="102.00",
        amount_evidence="$102.00",
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, issues=(original_issue, LLM_EXTRACTION_FAILED_ISSUE)),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,), (parsed_expense_line.line_number,)),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(expected_model, [balance_amount_response_json, balance_amount_response_json])
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "A balance is never accepted as the transaction amount, so the line stays unresolved.",
    )


async def test_extract_uncertain_lines_WhenTheLlmSelectsASuffixOfAMalformedAmount_ThenLeavesTheLineUnresolved() -> None:
    # Arrange
    source_text: str = "2026-06-01 Market 1,23.45"
    original_issue: str = "missing_amount"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 6, 1), "Market", None, (original_issue,))
    partial_amount_response_json: str = _create_extraction_response_json(
        line_number=parsed_expense_line.line_number,
        source_text=source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="23.45",
        amount_evidence="23.45",
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, issues=(original_issue, LLM_EXTRACTION_FAILED_ISSUE)),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,), (parsed_expense_line.line_number,)),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(expected_model, [partial_amount_response_json, partial_amount_response_json])
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "Amount evidence must be a whole amount occurrence; a suffix of a malformed amount leaves the line unresolved.",
    )


@pytest.mark.parametrize("description_evidence", ["2026-08-14", "2026-08-14 $10.00", " "])
async def test_extract_uncertain_lines_WhenTheLlmUsesOnlyADateOrAmountAsTheMissingDescription_ThenLeavesTheLineUnresolved(
    description_evidence: str,
) -> None:
    # Arrange
    source_text: str = "2026-08-14 $10.00"
    original_issue: str = "missing_description"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 8, 14), None, Decimal("10.00"), (original_issue,))
    non_descriptive_response_json: str = _create_extraction_response_json(
        line_number=parsed_expense_line.line_number,
        source_text=source_text,
        occurred_on=None,
        date_evidence=None,
        description=description_evidence.strip(),
        description_evidence=description_evidence,
        amount=None,
        amount_evidence=None,
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, issues=(original_issue, LLM_EXTRACTION_FAILED_ISSUE)),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,), (parsed_expense_line.line_number,)),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(expected_model, [non_descriptive_response_json, non_descriptive_response_json])
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "A description must contain descriptive source text; a date, an amount or blank text leaves the description unresolved.",
    )


async def test_extract_uncertain_lines_WhenTheLlmSelectsAnUnsignedRefundFromAmbiguousAmounts_ThenKeepsTheCreditNegative() -> None:
    # Arrange
    source_text: str = "Refund $68.00 and original $100.00 on 2026-07-18"
    refund_amount_text: str = "-68.00"
    expected_refund_amount: Decimal = Decimal(refund_amount_text)
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 7, 18), "Refund", None, ("multiple_amounts",))
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, amount=expected_refund_amount, issues=()),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,),),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model,
        [
            _create_extraction_response_json(
                line_number=parsed_expense_line.line_number,
                source_text=source_text,
                occurred_on=None,
                date_evidence=None,
                description=None,
                description_evidence=None,
                amount=refund_amount_text,
                amount_evidence="$68.00",
            )
        ],
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "A refund selected from unsigned evidence among several amounts is kept as a negative credit.",
    )


async def test_extract_uncertain_lines_WhenTheLlmSuppliesASignedAmountWithVerbatimEvidence_ThenKeepsTheRefundNegative() -> None:
    # Arrange
    refund_amount_text: str = "-68.00"
    expected_refund_amount: Decimal = Decimal(refund_amount_text)
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(
        3, "Refund -$68.00 on 2026-07-18", date(2026, 7, 18), "Refund", None, ("missing_amount",)
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, amount=expected_refund_amount, issues=()),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,),),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model,
        [
            _create_extraction_response_json(
                line_number=parsed_expense_line.line_number,
                source_text=parsed_expense_line.source_text,
                occurred_on=None,
                date_evidence=None,
                description=None,
                description_evidence=None,
                amount=refund_amount_text,
                amount_evidence="-$68.00",
            )
        ],
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "A signed refund amount with verbatim signed evidence resolves the line as a negative amount.",
    )


@pytest.mark.parametrize("amount_evidence", ["$-68.00", "($68.00)", "($ 68.00)"])
async def test_extract_uncertain_lines_WhenTheLlmSelectsASignedAmountFromAnAmbiguousSource_ThenAcceptsItsVerbatimEvidence(
    amount_evidence: str,
) -> None:
    # Arrange
    source_text: str = f"Refund {amount_evidence} and original $100.00 on 2026-07-18"
    refund_amount_text: str = "-68.00"
    expected_refund_amount: Decimal = Decimal(refund_amount_text)
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(3, source_text, date(2026, 7, 18), "Refund", None, ("multiple_amounts",))
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, amount=expected_refund_amount, issues=()),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,),),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model,
        [
            _create_extraction_response_json(
                line_number=parsed_expense_line.line_number,
                source_text=source_text,
                occurred_on=None,
                date_evidence=None,
                description=None,
                description_evidence=None,
                amount=refund_amount_text,
                amount_evidence=amount_evidence,
            )
        ],
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), 2026
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "Signed or parenthesized refund evidence copied verbatim from an ambiguous source resolves the line as a negative amount.",
    )


async def test_extract_uncertain_lines_WhenTheYearIsUnknownAndEvidenceOmitsIt_ThenLeavesTheDateUnresolved() -> None:
    # Arrange
    original_issue: str = "year_not_inferable"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(4, "Aug 14 Market $12.50", None, "Market", Decimal("12.50"), (original_issue,))
    invented_year_response_json: str = _create_extraction_response_json(
        line_number=parsed_expense_line.line_number,
        source_text=parsed_expense_line.source_text,
        occurred_on="2026-08-14",
        date_evidence="Aug 14",
        description=None,
        description_evidence=None,
        amount=None,
        amount_evidence=None,
    )
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, issues=(original_issue, LLM_EXTRACTION_FAILED_ISSUE)),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,), (parsed_expense_line.line_number,)),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(expected_model, [invented_year_response_json, invented_year_response_json])
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), None
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "Without an inferable year, the LLM cannot supply a year the date evidence does not contain, so the date stays unresolved.",
    )


@pytest.mark.parametrize(
    ("source_text", "date_evidence", "expected_date", "inferred_year"),
    [
        ("Refund 8/14/69 or 8/15/69 $10.00", "8/14/69", date(2069, 8, 14), None),
        ("Refund Sept. 14 or Sept. 15 $10.00", "Sept. 14", date(2026, 9, 14), 2026),
        ("Refund sept 14 or sept 15 $10.00", "sept 14", date(2026, 9, 14), 2026),
    ],
)
async def test_extract_uncertain_lines_WhenTheLlmSelectsSupportedDateEvidence_ThenUsesTheParserDateConvention(
    source_text: str, date_evidence: str, expected_date: date, inferred_year: int | None
) -> None:
    # Arrange
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, None, "Refund", Decimal("-10.00"), ("multiple_dates",))
    expected_expense_line_extraction: ExpectedExpenseLineExtraction = ExpectedExpenseLineExtraction(
        parsed_expense_lines=(replace(parsed_expense_line, occurred_on=expected_date, issues=()),),
        passed_through_parsed_expense_lines=(),
        prompted_line_numbers=((parsed_expense_line.line_number,),),
        processing_event_kinds=EXTRACTION_LIFECYCLE_EVENT_KINDS,
    )
    expected_model: str = _create_test_model()
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway(
        expected_model,
        [
            _create_extraction_response_json(
                line_number=parsed_expense_line.line_number,
                source_text=source_text,
                occurred_on=expected_date.isoformat(),
                date_evidence=date_evidence,
                description=None,
                description_evidence=None,
                amount=None,
                amount_evidence=None,
            )
        ],
    )
    processing_event_recorder: ProcessingEventRecorder = ProcessingEventRecorder()
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=expected_model, reasoning_effort=None), processing_event_recorder.record
    )

    # Act
    actual_parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), inferred_year
    )

    # Assert
    actual_expense_line_extraction: ActualExpenseLineExtraction = ActualExpenseLineExtraction(
        actual_parsed_expense_lines,
        _gather_prompted_line_numbers(fake_llm_gateway.prompts),
        tuple(processing_event_recorder.captured_processing_event_kinds),
    )
    ExpenseLineExtractionAsserter.assert_expected_and_actual_are_equal(
        expected_expense_line_extraction,
        actual_expense_line_extraction,
        "Date evidence the LLM selects is read with the same century and month-name conventions as the deterministic parser.",
    )


def _create_extraction_response_json(
    *,
    line_number: int,
    source_text: str,
    occurred_on: str | None,
    date_evidence: str | None,
    description: str | None,
    description_evidence: str | None,
    amount: str | None,
    amount_evidence: str | None,
) -> str:
    return json.dumps(
        {
            "lines": [
                {
                    "line_number": line_number,
                    "source_text": source_text,
                    "occurred_on": occurred_on,
                    "date_evidence": date_evidence,
                    "description": description,
                    "description_evidence": description_evidence,
                    "amount": amount,
                    "amount_evidence": amount_evidence,
                }
            ]
        }
    )


def _create_extracted_line_fixture(parsed_expense_line: ParsedExpenseLine, occurred_on: date, date_evidence: str) -> ExtractedLineFixture:
    return ExtractedLineFixture(
        line_number=parsed_expense_line.line_number,
        source_text=parsed_expense_line.source_text,
        occurred_on=occurred_on.isoformat(),
        date_evidence=date_evidence,
        description=None,
        description_evidence=None,
        amount=None,
        amount_evidence=None,
    )


def _gather_prompted_line_numbers(prompts: Iterable[str]) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(int(input_line["line_number"]) for input_line in json.loads(prompt.split("input_lines: ", maxsplit=1)[1].split("\n", maxsplit=1)[0]))
        for prompt in prompts
    )
