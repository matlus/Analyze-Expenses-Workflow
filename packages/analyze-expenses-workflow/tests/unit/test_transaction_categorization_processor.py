import asyncio
import secrets
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

import pytest
from acceptance_support.asserter_jev import ExpectedJevGatewayFailure, assert_jev_gateway_failure, assert_task_group_context

from analyze_expenses_workflow.managers.exceptions.system_one_gateway_exception import (
    JevChoiceCategoryUnrecognizedException,
    JevRequestFailedException,
)
from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision, ChoiceQuestion
from analyze_expenses_workflow.managers.processors.transaction_categorization_processor import TransactionCategorizationProcessor
from analyze_expenses_workflow.models.expense_category_catalog import ExpenseCategory
from analyze_expenses_workflow.models.parsed_expense_line import ParsedExpenseLine

KNOWN_EXPENSE_CATEGORIES_TEXT: str = ", ".join(expense_category.value for expense_category in ExpenseCategory)
JEV_REQUEST_FAILED_MESSAGE: str = "Jev via OpenRouter categorization request failed"


class StubSystemOneGateway:
    def __init__(self, choice_decision: ChoiceDecision) -> None:
        self._choice_decision: ChoiceDecision = choice_decision

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        return self._choice_decision

    async def close(self) -> None:
        return None


class FailingSystemOneGateway:
    """Fails the lines it names and categorizes the rest; the named lines fail together once all of them reach Jev."""

    def __init__(self, choice_decision: ChoiceDecision, jev_request_failed_exception_by_source_text: Mapping[str, JevRequestFailedException]) -> None:
        self._choice_decision: ChoiceDecision = choice_decision
        self._jev_request_failed_exception_by_source_text: Mapping[str, JevRequestFailedException] = jev_request_failed_exception_by_source_text
        self._failing_requests_barrier: asyncio.Barrier = asyncio.Barrier(len(jev_request_failed_exception_by_source_text))

    async def choose(self, source_text: str, choice_question: ChoiceQuestion) -> ChoiceDecision:
        jev_request_failed_exception: JevRequestFailedException | None = self._jev_request_failed_exception_by_source_text.get(source_text)
        if jev_request_failed_exception is None:
            return self._choice_decision
        await self._failing_requests_barrier.wait()
        raise jev_request_failed_exception

    async def close(self) -> None:
        return None


def _create_parsed_expense_line(line_number: int) -> ParsedExpenseLine:
    description: str = f"Merchant {secrets.token_hex(4)}"
    return ParsedExpenseLine(
        line_number=line_number,
        source_text=f"June {line_number} {description} $25.00",
        occurred_on=date(2026, 6, line_number),
        description=description,
        amount=Decimal("25.00"),
        issues=(),
    )


def _create_jev_request_failed_exception(provider_message: str) -> JevRequestFailedException:
    jev_request_message: str = f"{JEV_REQUEST_FAILED_MESSAGE} {secrets.token_hex(4)}"
    jev_request_failed_exception: JevRequestFailedException = JevRequestFailedException(jev_request_message, {"Operation": "categorization_request"})
    jev_request_failed_exception.__cause__ = RuntimeError(provider_message)
    return jev_request_failed_exception


def _expected_jev_request_failure(message_phrase: str) -> ExpectedJevGatewayFailure:
    return ExpectedJevGatewayFailure(
        exception_type=JevRequestFailedException,
        action="RetryActionNeeded",
        reason="Jev gateway request failed",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=(message_phrase,),
        contextual_fields=(("Operation", "categorization_request"),),
        cause_exception_type=RuntimeError,
    )


def _expected_jev_choice_category_unrecognized(selected_category: str, unrecognized_category: str) -> ExpectedJevGatewayFailure:
    return ExpectedJevGatewayFailure(
        exception_type=JevChoiceCategoryUnrecognizedException,
        action="DeveloperActionRequired",
        reason="Jev selected a category that is not a known expense category",
        log_event="ExpenseJevGateway",
        severity="ERROR",
        http_status_code=500,
        message_phrases=("Jev", unrecognized_category, "not known expense categories", KNOWN_EXPENSE_CATEGORIES_TEXT),
        contextual_fields=(
            ("Operation", "choice_category_translation"),
            ("SelectedCategory", selected_category),
            ("UnrecognizedCategories", unrecognized_category),
            ("AllowedCategories", KNOWN_EXPENSE_CATEGORIES_TEXT),
        ),
        cause_exception_type=ValueError,
    )


async def test_categorize_WhenJevSelectsAnUnknownCategory_ThenReportsTheUnrecognizedCategoryAndTheKnownCategories() -> None:
    unknown_category: str = f"unknown-{secrets.token_hex(4)}"
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _expected_jev_choice_category_unrecognized(unknown_category, unknown_category)
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(ChoiceDecision(choice=unknown_category, confidence=1.0, probabilities={unknown_category: 1.0}))
    )

    with pytest.raises(JevChoiceCategoryUnrecognizedException) as raised_exception:
        await transaction_categorization_processor.categorize(1, f"June 1 {secrets.token_hex(8)} $25.00")

    actual_jev_choice_category_unrecognized_exception: JevChoiceCategoryUnrecognizedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_category_unrecognized_exception, expected_jev_gateway_failure)


async def test_categorize_WhenJevReturnsAProbabilityForAnUnknownCategory_ThenReportsTheUnknownProbabilityCategoryAndTheKnownCategories() -> None:
    selected_category: str = ExpenseCategory.GROCERIES.value
    unknown_probability_category: str = f"unknown-{secrets.token_hex(4)}"
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _expected_jev_choice_category_unrecognized(
        selected_category, unknown_probability_category
    )
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        StubSystemOneGateway(
            ChoiceDecision(
                choice=selected_category,
                confidence=0.9,
                probabilities={selected_category: 0.9, unknown_probability_category: 0.1},
            )
        )
    )

    with pytest.raises(JevChoiceCategoryUnrecognizedException) as raised_exception:
        await transaction_categorization_processor.categorize(1, f"June 1 {secrets.token_hex(8)} $25.00")

    actual_jev_choice_category_unrecognized_exception: JevChoiceCategoryUnrecognizedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_choice_category_unrecognized_exception, expected_jev_gateway_failure)


async def test_categorize_lines_WhenJevFailsForOneLine_ThenRaisesThatLinesJevExceptionWithTheTaskGroupAsContext() -> None:
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = tuple(_create_parsed_expense_line(line_number) for line_number in (1, 2, 3))
    expected_jev_request_failed_exception: JevRequestFailedException = _create_jev_request_failed_exception(
        f"provider unavailable {secrets.token_hex(4)}"
    )
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _expected_jev_request_failure(expected_jev_request_failed_exception.message)
    expected_collected_exceptions: tuple[JevRequestFailedException, ...] = (expected_jev_request_failed_exception,)
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        FailingSystemOneGateway(
            ChoiceDecision(choice=ExpenseCategory.GROCERIES.value, confidence=0.9, probabilities={ExpenseCategory.GROCERIES.value: 0.9}),
            {parsed_expense_lines[1].source_text: expected_jev_request_failed_exception},
        )
    )

    with pytest.raises(JevRequestFailedException) as raised_exception:
        await transaction_categorization_processor.categorize_lines(parsed_expense_lines)

    actual_jev_request_failed_exception: JevRequestFailedException = raised_exception.value
    assert actual_jev_request_failed_exception is expected_jev_request_failed_exception, (
        f"Expected the failed line's own exception, got {actual_jev_request_failed_exception!r}"
    )
    assert_jev_gateway_failure(actual_jev_request_failed_exception, expected_jev_gateway_failure)
    assert_task_group_context(actual_jev_request_failed_exception, expected_collected_exceptions)


async def test_categorize_lines_WhenJevFailsForSeveralLinesTogether_ThenRaisesTheFirstFailureWithEveryFailureInTheContext() -> None:
    parsed_expense_lines: tuple[ParsedExpenseLine, ...] = tuple(_create_parsed_expense_line(line_number) for line_number in (1, 2, 3))
    jev_request_failed_exception_by_source_text: dict[str, JevRequestFailedException] = {
        parsed_expense_lines[0].source_text: _create_jev_request_failed_exception(f"provider unavailable {secrets.token_hex(4)}"),
        parsed_expense_lines[2].source_text: _create_jev_request_failed_exception(f"provider overloaded {secrets.token_hex(4)}"),
    }
    expected_jev_gateway_failure: ExpectedJevGatewayFailure = _expected_jev_request_failure(JEV_REQUEST_FAILED_MESSAGE)
    expected_collected_exceptions: tuple[JevRequestFailedException, ...] = tuple(jev_request_failed_exception_by_source_text.values())
    transaction_categorization_processor: TransactionCategorizationProcessor = TransactionCategorizationProcessor(
        FailingSystemOneGateway(
            ChoiceDecision(choice=ExpenseCategory.GROCERIES.value, confidence=0.9, probabilities={ExpenseCategory.GROCERIES.value: 0.9}),
            jev_request_failed_exception_by_source_text,
        )
    )

    with pytest.raises(JevRequestFailedException) as raised_exception:
        await transaction_categorization_processor.categorize_lines(parsed_expense_lines)

    actual_jev_request_failed_exception: JevRequestFailedException = raised_exception.value
    assert_jev_gateway_failure(actual_jev_request_failed_exception, expected_jev_gateway_failure)
    assert_task_group_context(actual_jev_request_failed_exception, expected_collected_exceptions)
