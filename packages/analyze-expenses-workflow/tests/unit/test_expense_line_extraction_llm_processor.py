import json
from datetime import date
from decimal import Decimal

import pytest

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException
from analyze_expenses_workflow.managers.llm_processors.bases.llm_processor_base import ProcessingEventKind
from analyze_expenses_workflow.managers.llm_processors.expense_line_extraction_llm_processor import ExpenseLineExtractionLlmProcessor
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine


class FakeLlmGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses: list[str] = responses
        self.prompts: list[str] = []

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self.prompts.append(prompt)
        assert model == "test-model"
        assert reasoning_effort is None
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


class FailingLlmGateway:
    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        raise LlmGatewayException("Subscription request failed")

    async def close(self) -> None:
        return None


async def test_gateway_failure_leaves_uncertain_line_unparsed_and_preserves_complete_line() -> None:
    complete_line: ParsedExpenseLine = ParsedExpenseLine(
        1, "2026-06-01 | Market | $10.00", date(2026, 6, 1), "Market", Decimal("10.00"), ()
    )
    uncertain_line: ParsedExpenseLine = ParsedExpenseLine(2, "unreadable entry", None, "unreadable entry", None, ("missing_date", "missing_amount"))
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        FailingLlmGateway(), LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((complete_line, uncertain_line), 2026)

    assert result[0] == complete_line
    assert result[1].source_text == "unreadable entry"
    assert result[1].issues == ("missing_date", "missing_amount", "llm_extraction_failed")


async def test_llm_cannot_use_text_only_amount_evidence_when_equal_balance_occurrence_exists() -> None:
    source_text: str = "2026-08-16 | Store | charge $100.00; available balance $100.00"
    line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 8, 16), "Store | charge ; available balance", None, ("multiple_amounts",))
    response: str = _response(
        line_number=1,
        source_text=source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="100.00",
        amount_evidence="$100.00",
    )
    gateway: FakeLlmGateway = FakeLlmGateway([response, response])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].amount is None
    assert "multiple_amounts" in result[0].issues


def _response(
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


async def test_when_all_lines_are_parsed_then_no_llm_call_is_made() -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(1, "2026-06-01 Market $10.00", date(2026, 6, 1), "Market", Decimal("10.00"), ())
    gateway: FakeLlmGateway = FakeLlmGateway([])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result == (line,)
    assert gateway.prompts == []


@pytest.mark.parametrize("fenced_json", [False, True])
async def test_when_llm_supplies_evidenced_fields_then_uncertain_line_is_resolved(fenced_json: bool) -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(7, "Market on 8/14: $12.50", None, "Market on", Decimal("12.50"), ("missing_date",))
    response: str = _response(
        line_number=7,
        source_text=line.source_text,
        occurred_on="2026-08-14",
        date_evidence="8/14",
        description=None,
        description_evidence=None,
        amount=None,
        amount_evidence=None,
    )
    gateway: FakeLlmGateway = FakeLlmGateway([f"```json\n{response}\n```" if fenced_json else response])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].occurred_on == date(2026, 8, 14)
    assert result[0].description == "Market on"
    assert result[0].issues == ()
    assert len(gateway.prompts) == 1


async def test_when_only_some_lines_are_uncertain_then_original_order_and_sources_are_preserved() -> None:
    first: ParsedExpenseLine = ParsedExpenseLine(1, "2026-08-14 Market $10.00", date(2026, 8, 14), "Market", Decimal("10.00"), ())
    uncertain: ParsedExpenseLine = ParsedExpenseLine(2, "Shop on 8/15 $12.50", None, "Shop", Decimal("12.50"), ("missing_date",))
    last: ParsedExpenseLine = ParsedExpenseLine(3, "2026-08-16 Cafe $5.00", date(2026, 8, 16), "Cafe", Decimal("5.00"), ())
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            _response(
                line_number=2,
                source_text=uncertain.source_text,
                occurred_on="2026-08-15",
                date_evidence="8/15",
                description=None,
                description_evidence=None,
                amount=None,
                amount_evidence=None,
            )
        ]
    )
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((first, uncertain, last), 2026)

    assert result[0] is first
    assert result[1].line_number == 2
    assert result[1].source_text == uncertain.source_text
    assert result[1].occurred_on == date(2026, 8, 15)
    assert result[2] is last
    assert json.loads(gateway.prompts[0].split("input_lines: ", maxsplit=1)[1])[0]["line_number"] == 2


async def test_when_response_schema_fails_once_then_retries_once_and_publishes_lifecycle() -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(1, "Aug 14 Market $12.50", None, "Market", Decimal("12.50"), ("year_not_inferable",))
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            "not JSON",
            _response(
                line_number=1,
                source_text=line.source_text,
                occurred_on="2026-08-14",
                date_evidence="Aug 14",
                description=None,
                description_evidence=None,
                amount=None,
                amount_evidence=None,
            ),
        ]
    )
    events: list[tuple[ProcessingEventKind, str]] = []

    async def record_event(kind: ProcessingEventKind, message: str) -> None:
        events.append((kind, message))

    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None), record_event
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].occurred_on == date(2026, 8, 14)
    assert len(gateway.prompts) == 2
    assert [event[0] for event in events] == ["progress", "complete"]


async def test_when_one_line_fails_evidence_validation_then_only_that_line_is_retried() -> None:
    first: ParsedExpenseLine = ParsedExpenseLine(1, "8/14 Market $10.00", None, "Market", Decimal("10.00"), ("year_not_inferable",))
    second: ParsedExpenseLine = ParsedExpenseLine(2, "8/15 Cafe $5.00", None, "Cafe", Decimal("5.00"), ("year_not_inferable",))

    def extracted_line(line: ParsedExpenseLine, evidence: str) -> dict[str, object]:
        return {
            "line_number": line.line_number,
            "source_text": line.source_text,
            "occurred_on": "2026-08-14" if line.line_number == 1 else "2026-08-15",
            "date_evidence": evidence,
            "description": None,
            "description_evidence": None,
            "amount": None,
            "amount_evidence": None,
        }

    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            json.dumps({"lines": [extracted_line(first, "8/14"), extracted_line(second, "8/14")]}),
            json.dumps({"lines": [extracted_line(second, "8/15")]}),
        ]
    )
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((first, second), 2026)

    assert [line.occurred_on for line in result] == [date(2026, 8, 14), date(2026, 8, 15)]
    assert [line.issues for line in result] == [(), ()]
    assert [item["line_number"] for item in json.loads(gateway.prompts[1].split("input_lines: ", maxsplit=1)[1].split("\n", maxsplit=1)[0])] == [2]


async def test_when_llm_invents_amount_then_preserves_original_issue_after_retry() -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(1, "Market 2026-08-14", date(2026, 8, 14), "Market", None, ("missing_amount",))
    invented_response: str = _response(
        line_number=1,
        source_text=line.source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="42.00",
        amount_evidence="$42.00",
    )
    gateway: FakeLlmGateway = FakeLlmGateway([invented_response, invented_response])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].amount is None
    assert result[0].issues == ("missing_amount", "llm_extraction_failed")
    assert len(gateway.prompts) == 2


async def test_when_llm_selects_a_balance_as_amount_then_line_remains_unresolved() -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(
        1, "2026-08-16 | available balance $102.00", date(2026, 8, 16), "available balance", None, ("nontransaction_amount",)
    )
    invalid_response: str = _response(
        line_number=1,
        source_text=line.source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="102.00",
        amount_evidence="$102.00",
    )
    gateway: FakeLlmGateway = FakeLlmGateway([invalid_response, invalid_response])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].amount is None
    assert result[0].issues == ("nontransaction_amount", "llm_extraction_failed")


async def test_when_llm_selects_a_suffix_of_malformed_amount_then_line_remains_unresolved() -> None:
    source_text: str = "2026-06-01 Market 1,23.45"
    line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 6, 1), "Market", None, ("missing_amount",))
    partial_response: str = _response(
        line_number=1,
        source_text=source_text,
        occurred_on=None,
        date_evidence=None,
        description=None,
        description_evidence=None,
        amount="23.45",
        amount_evidence="23.45",
    )
    gateway: FakeLlmGateway = FakeLlmGateway([partial_response, partial_response])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].amount is None
    assert result[0].issues == ("missing_amount", "llm_extraction_failed")


@pytest.mark.parametrize("description_evidence", ["2026-08-14", "2026-08-14 $10.00", " "])
async def test_when_llm_uses_only_date_or_amount_as_missing_description_then_line_remains_unresolved(
    description_evidence: str,
) -> None:
    source_text: str = "2026-08-14 $10.00"
    line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 8, 14), None, Decimal("10.00"), ("missing_description",))
    invalid_response: str = _response(
        line_number=1,
        source_text=source_text,
        occurred_on=None,
        date_evidence=None,
        description=description_evidence.strip(),
        description_evidence=description_evidence,
        amount=None,
        amount_evidence=None,
    )
    gateway: FakeLlmGateway = FakeLlmGateway([invalid_response, invalid_response])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].description is None
    assert result[0].issues == ("missing_description", "llm_extraction_failed")


async def test_when_llm_selects_unsigned_refund_from_ambiguous_amounts_then_credit_remains_negative() -> None:
    source_text: str = "Refund $68.00 and original $100.00 on 2026-07-18"
    line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, date(2026, 7, 18), "Refund", None, ("multiple_amounts",))
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            _response(
                line_number=1,
                source_text=source_text,
                occurred_on=None,
                date_evidence=None,
                description=None,
                description_evidence=None,
                amount="-68.00",
                amount_evidence="$68.00",
            )
        ]
    )
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].amount == Decimal("-68.00")
    assert result[0].issues == ()


async def test_when_llm_supplies_signed_amount_with_verbatim_evidence_then_keeps_refund_negative() -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(3, "Refund -$68.00 on 2026-07-18", date(2026, 7, 18), "Refund", None, ("missing_amount",))
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            _response(
                line_number=3,
                source_text=line.source_text,
                occurred_on=None,
                date_evidence=None,
                description=None,
                description_evidence=None,
                amount="-68.00",
                amount_evidence="-$68.00",
            )
        ]
    )
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].amount == Decimal("-68.00")
    assert result[0].issues == ()


@pytest.mark.parametrize("amount_evidence", ["$-68.00", "($68.00)", "($ 68.00)"])
async def test_when_llm_selects_a_signed_amount_from_ambiguous_source_then_accepts_its_verbatim_evidence(
    amount_evidence: str,
) -> None:
    source_text: str = f"Refund {amount_evidence} and original $100.00 on 2026-07-18"
    line: ParsedExpenseLine = ParsedExpenseLine(3, source_text, date(2026, 7, 18), "Refund", None, ("multiple_amounts",))
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            _response(
                line_number=3,
                source_text=source_text,
                occurred_on=None,
                date_evidence=None,
                description=None,
                description_evidence=None,
                amount="-68.00",
                amount_evidence=amount_evidence,
            )
        ]
    )
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), 2026)

    assert result[0].amount == Decimal("-68.00")
    assert result[0].issues == ()


async def test_when_year_is_unknown_then_llm_cannot_supply_a_date_without_year_evidence() -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(4, "Aug 14 Market $12.50", None, "Market", Decimal("12.50"), ("year_not_inferable",))
    invented_year_response: str = _response(
        line_number=4,
        source_text=line.source_text,
        occurred_on="2026-08-14",
        date_evidence="Aug 14",
        description=None,
        description_evidence=None,
        amount=None,
        amount_evidence=None,
    )
    gateway: FakeLlmGateway = FakeLlmGateway([invented_year_response, invented_year_response])
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), None)

    assert result[0].occurred_on is None
    assert result[0].issues == ("year_not_inferable", "llm_extraction_failed")


@pytest.mark.parametrize(
    ("source_text", "date_evidence", "expected_date", "inferred_year"),
    [
        ("Refund 8/14/69 or 8/15/69 $10.00", "8/14/69", date(2069, 8, 14), None),
        ("Refund Sept. 14 or Sept. 15 $10.00", "Sept. 14", date(2026, 9, 14), 2026),
        ("Refund sept 14 or sept 15 $10.00", "sept 14", date(2026, 9, 14), 2026),
    ],
)
async def test_when_llm_selects_supported_date_evidence_then_it_uses_the_parser_date_convention(
    source_text: str, date_evidence: str, expected_date: date, inferred_year: int | None
) -> None:
    line: ParsedExpenseLine = ParsedExpenseLine(1, source_text, None, "Refund", Decimal("-10.00"), ("multiple_dates",))
    gateway: FakeLlmGateway = FakeLlmGateway(
        [
            _response(
                line_number=1,
                source_text=source_text,
                occurred_on=expected_date.isoformat(),
                date_evidence=date_evidence,
                description=None,
                description_evidence=None,
                amount=None,
                amount_evidence=None,
            )
        ]
    )
    processor: ExpenseLineExtractionLlmProcessor = ExpenseLineExtractionLlmProcessor(
        gateway, LlmOperationSettings(model="test-model", reasoning_effort=None)
    )

    result: tuple[ParsedExpenseLine, ...] = await processor.extract_uncertain_lines((line,), inferred_year)

    assert result[0].occurred_on == expected_date
    assert result[0].issues == ()
