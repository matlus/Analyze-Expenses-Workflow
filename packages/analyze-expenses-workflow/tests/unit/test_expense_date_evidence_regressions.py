import json
import secrets
from datetime import date
from decimal import Decimal

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.llm_processors.expense_line_extraction_llm_processor import ExpenseLineExtractionLlmProcessor
from analyze_expenses_workflow.managers.processors.expense_line_parsing_processor import ExpenseLineParsingProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class FakeLlmGateway:
    def __init__(self, response_texts: tuple[str, ...]) -> None:
        self._response_texts: list[str] = list(response_texts)
        self.call_count: int = 0

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self.call_count += 1
        return self._response_texts.pop(0)

    async def close(self) -> None:
        return None


async def test_ExpenseParsing_WhenDatesRunBackwardAcrossYearBoundary_ThenLeavesYearlessDateUnresolved() -> None:
    expense_lines: tuple[str, str] = (
        f"2026-01-02 | {secrets.token_hex(8)} | $1.00",
        f"Dec 31 | {secrets.token_hex(8)} | $2.00",
    )
    expected_date: date | None = None
    expected_issue: str = "year_not_inferable"
    expected_result: tuple[date | None, bool] = (expected_date, True)

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    assert (parsed_expense_lines[1].occurred_on, expected_issue in parsed_expense_lines[1].issues) == expected_result


async def test_ExpenseParsing_WhenInvalidMonthSeparatesValidDates_ThenIgnoresItForYearInference() -> None:
    expense_lines: tuple[str, str, str] = (
        f"2026-01-02 | {secrets.token_hex(8)} | $1.00",
        f"99/01 | {secrets.token_hex(8)} | $2.00",
        f"Feb 02 | {secrets.token_hex(8)} | $3.00",
    )
    expected_result: tuple[bool, date | None] = (True, date(2026, 2, 2))

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    assert ("invalid_date" in parsed_expense_lines[1].issues, parsed_expense_lines[2].occurred_on) == expected_result


async def test_ExpenseParsing_WhenExplicitDatesAdvanceWithinYear_ThenInfersFollowingYearlessDate() -> None:
    expense_lines: tuple[str, str, str] = (
        f"2026-01-02 | {secrets.token_hex(8)} | $1.00",
        f"2026-08-02 | {secrets.token_hex(8)} | $2.00",
        f"Sep 02 | {secrets.token_hex(8)} | $3.00",
    )
    expected_date: date = date(2026, 9, 2)

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await ExpenseLineParsingProcessor().parse(expense_lines)

    assert parsed_expense_lines[2].occurred_on == expected_date


async def test_ExpenseExtraction_WhenDateEvidenceIsPartOfMalformedToken_ThenLeavesDateUnresolved() -> None:
    merchant_name: str = secrets.token_hex(8)
    source_text: str = f"2026-08-144 | {merchant_name} | $10.00"
    expected_date: date | None = None
    missing_date_issue: str = "missing_date"
    expected_issues: tuple[str, str] = (missing_date_issue, "llm_extraction_failed")
    expected_result: tuple[date | None, tuple[str, str]] = (expected_date, expected_issues)
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, None, merchant_name, Decimal("10.00"), (missing_date_issue,))
    response_text: str = json.dumps(
        {
            "lines": [
                {
                    "line_number": 1,
                    "source_text": source_text,
                    "occurred_on": "2026-08-14",
                    "date_evidence": "2026-08-14",
                    "description": None,
                    "description_evidence": None,
                    "amount": None,
                    "amount_evidence": None,
                }
            ]
        }
    )
    model_name: str = secrets.token_hex(8)
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        FakeLlmGateway((response_text, response_text)), LlmOperationSettings(model=model_name, reasoning_effort=None)
    )

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), inferred_year=2026
    )

    assert (parsed_expense_lines[0].occurred_on, parsed_expense_lines[0].issues) == expected_result


async def test_ExpenseExtraction_WhenFirstAttemptResolvesOnlyDate_ThenRetriesRemainingAmount() -> None:
    merchant_name: str = secrets.token_hex(8)
    source_text: str = f"2026-06-01 | {merchant_name} | $10.00"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, None, merchant_name, None, ("missing_date", "missing_amount"))
    first_response: str = _extraction_response(source_text, "2026-06-01", "2026-06-01", None, None)
    second_response: str = _extraction_response(source_text, None, None, "10.00", "$10.00")
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway((first_response, second_response))
    model_name: str = secrets.token_hex(8)
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=model_name, reasoning_effort=None)
    )
    expected_result: tuple[date, Decimal, tuple[str, ...], int] = (date(2026, 6, 1), Decimal("10.00"), (), 2)

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), inferred_year=2026
    )

    assert (
        parsed_expense_lines[0].occurred_on,
        parsed_expense_lines[0].amount,
        parsed_expense_lines[0].issues,
        fake_llm_gateway.call_count,
    ) == expected_result


async def test_ExpenseExtraction_WhenBothAttemptsLeaveAmountUnknown_ThenPreservesDateAndMarksLineUnresolved() -> None:
    merchant_name: str = secrets.token_hex(8)
    source_text: str = f"2026-06-01 | {merchant_name} | $10.00"
    missing_amount_issue: str = "missing_amount"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, None, merchant_name, None, ("missing_date", missing_amount_issue))
    first_response: str = _extraction_response(source_text, "2026-06-01", "2026-06-01", None, None)
    second_response: str = _extraction_response(source_text, None, None, None, None)
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway((first_response, second_response))
    model_name: str = secrets.token_hex(8)
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=model_name, reasoning_effort=None)
    )
    expected_result: tuple[date, Decimal | None, tuple[str, str], int] = (
        date(2026, 6, 1),
        None,
        (missing_amount_issue, "llm_extraction_failed"),
        2,
    )

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), inferred_year=2026
    )

    assert (
        parsed_expense_lines[0].occurred_on,
        parsed_expense_lines[0].amount,
        parsed_expense_lines[0].issues,
        fake_llm_gateway.call_count,
    ) == expected_result


async def test_ExpenseExtraction_WhenEqualChargeAndBalanceAmountsExist_ThenUsesIndexedChargeEvidence() -> None:
    merchant_name: str = secrets.token_hex(8)
    source_text: str = f"2026-08-16 | {merchant_name} | charge $10.00; available balance $10.00"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 8, 16), merchant_name, None, ("multiple_amounts",))
    response_text: str = json.dumps(
        {
            "lines": [
                {
                    "line_number": 1,
                    "source_text": source_text,
                    "occurred_on": None,
                    "date_evidence": None,
                    "description": None,
                    "description_evidence": None,
                    "amount": "10.00",
                    "amount_evidence": "$10.00",
                    "amount_evidence_index": 0,
                }
            ]
        }
    )
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway((response_text,))
    model_name: str = secrets.token_hex(8)
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=model_name, reasoning_effort=None)
    )
    expected_result: tuple[Decimal, tuple[str, ...], int] = (Decimal("10.00"), (), 1)

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), inferred_year=2026
    )

    assert (parsed_expense_lines[0].amount, parsed_expense_lines[0].issues, fake_llm_gateway.call_count) == expected_result


async def test_ExpenseExtraction_WhenRefundAndPurchaseAmountsCoexist_ThenKeepsPurchasePositive() -> None:
    merchant_name: str = secrets.token_hex(8)
    source_text: str = f"2026-08-16 | {merchant_name} | Refund $5.00; new purchase $20.00"
    parsed_expense_line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 8, 16), merchant_name, None, ("multiple_amounts",))
    response_text: str = json.dumps(
        {
            "lines": [
                {
                    "line_number": 1,
                    "source_text": source_text,
                    "occurred_on": None,
                    "date_evidence": None,
                    "description": None,
                    "description_evidence": None,
                    "amount": "20.00",
                    "amount_evidence": "$20.00",
                    "amount_evidence_index": 1,
                }
            ]
        }
    )
    fake_llm_gateway: FakeLlmGateway = FakeLlmGateway((response_text,))
    model_name: str = secrets.token_hex(8)
    expense_line_extraction_llm_processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        fake_llm_gateway, LlmOperationSettings(model=model_name, reasoning_effort=None)
    )
    expected_result: tuple[Decimal, tuple[str, ...]] = (Decimal("20.00"), ())

    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = await expense_line_extraction_llm_processor.extract_uncertain_lines(
        (parsed_expense_line,), inferred_year=2026
    )

    assert (parsed_expense_lines[0].amount, parsed_expense_lines[0].issues) == expected_result


def _extraction_response(
    source_text: str, occurred_on: str | None, date_evidence: str | None, amount: str | None, amount_evidence: str | None
) -> str:
    return json.dumps(
        {
            "lines": [
                {
                    "line_number": 1,
                    "source_text": source_text,
                    "occurred_on": occurred_on,
                    "date_evidence": date_evidence,
                    "description": None,
                    "description_evidence": None,
                    "amount": amount,
                    "amount_evidence": amount_evidence,
                }
            ]
        }
    )
