"""The expense-analysis eval for the code-controlled workflow, in one module. Read it top to bottom.

    Arrange  read and validate the eval suite folder (the Part Three case files, unchanged) into typed records
    Act      run the real workflow end to end, once per isolated run, through DomainFacade with real models
    Assert   read every fact from the workflow's structured result, judge only what needs judgment,
             turn the grades into pass rates, and require each check to meet its minimum

The prompt eval had a model write prose and another model extract the facts from it. The workflow returns a typed
result, so this eval reads each fact directly: a monthly total is the workflow's own MonthlyCategoryTotal, a refund's
treatment is the treatment of its source line. Only "three supported findings" still needs a judge.

Every check grade is passed, failed, or errored. Errored means the workflow or the judge failed, so the check could not
be graded; it is not a pass, and the scorecard lists it apart from the failures. A check that cannot apply to a
code-controlled graph is listed as not applicable, with the reason, and is left out of the pass rates.

A failed check states its cause. Because the workflow reports every source line's category and treatment, a wrong total
is traced exactly, line by line, against the reference ledger: "line 106 'Amazon (2026-07-18) - $141.82' (-141.82):
the workflow put it in Other; the reference puts it in Shopping".

Run it with:  uv run pytest evals/workflow -s --eval-repetitions 1
"""

import contextlib
import json
import logging
import math
import re
import secrets
import shutil
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Awaitable, Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from itertools import combinations
from pathlib import Path
from typing import Final, cast, override

import httpx2

from analyze_expenses_workflow.domain_facades import (
    ExpenseAnalysisResult,
    ExpenseCategory,
    ExpenseTransaction,
    TransactionTreatment,
)
from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import (
    MODEL_FAMILY_BY_SUBSCRIPTION,
    CodingAssistantSettings,
    CodingAssistantSubscription,
    ModelFamily,
)
from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperation, LlmOperationSettings
from analyze_expenses_workflow.managers.service_locators.service_locator_production import ServiceLocatorProduction
from analyze_expenses_workflow_app.composers.composer_expense_analysis_report import ComposerExpenseAnalysisReport

EVAL_SUITE_DIRECTORY: Final[Path] = Path(__file__).resolve().parents[1] / "expense_analysis"
# Skills and Evals writes results/pwi and results/minimal; this workflow's runs go to results/workflow.
RESULTS_DIRECTORY: Final[Path] = Path(__file__).resolve().parents[2] / "results" / "workflow" / "expense_analysis"
EVAL_SUITE_DEFINITION_FILE_NAME: Final[str] = "eval_suite.json"
TEXT_ENCODING: Final[str] = "utf-8"
AMOUNT_TOLERANCE: Final[Decimal] = Decimal("0.01")
RUN_IDENTITY_RANDOM_BYTES: Final[int] = 3
JUDGE_TIMEOUT_SECONDS: Final[int] = 600
JUDGE_EXECUTABLE_NAME: Final[str] = "claude"
STANDARD_ERROR_TAIL_LENGTH: Final[int] = 500
# The two model steps the workflow invokes. EXPENSE_CATEGORIZATION is configured but reserved, so it is not reported.
INVOKED_LLM_OPERATIONS: Final[tuple[LlmOperation, ...]] = (LlmOperation.EXPENSE_LINE_EXTRACTION, LlmOperation.EXPENSE_FINDINGS)
LLM_STEP_LOGGER_NAME: Final[str] = "analyze_expenses_workflow.managers.llm_processors.clients.llm_request_client"
CODING_ASSISTANT_SUBSCRIPTION_SETTING_NAME: Final[str] = "CODING_ASSISTANT_SUBSCRIPTION"
JEV_PROVIDER: Final[str] = "OpenRouter (Jev)"
JEV_REASONING_EFFORT: Final[str] = "not supported"
SCORECARD_JSON_FILE_NAME: Final[str] = "scorecard.json"
COMPARISON_FILE_NAME: Final[str] = "scorecard_comparison.md"
SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_HOUR: Final[int] = 3600
# Below this many seconds a time is shown to a tenth of a second; above it, a tenth is noise.
TENTHS_SHOWN_BELOW_SECONDS: Final[float] = 10.0
NOT_RECORDED_TEXT: Final[str] = "not recorded"
JUDGE_VERDICT_SCHEMA: Final[str] = json.dumps(
    {
        "type": "object",
        "required": ["passed", "reason"],
        "properties": {"passed": {"type": "boolean"}, "reason": {"type": "string"}},
    }
)


# ----------------------------------------------------------------------------------------------------------
# Foreign JSON: eval suite files are checked field by field before any value reaches a typed record,
# so a bad document fails once, naming the offending field.
# ----------------------------------------------------------------------------------------------------------


type JsonObject = Mapping[str, object]


class EvalBaseException(Exception):
    """Every failure the eval reports for itself."""


class EvalBusinessBaseException(EvalBaseException):
    """An input the person running the eval supplies is wrong: an eval suite file or a command-line setting."""


class EvalTechnicalBaseException(EvalBaseException):
    """The eval, a tool it runs, or a file it wrote failed; correcting the eval's inputs would not help."""


class InvalidEvalSuiteException(EvalBusinessBaseException):
    """An eval suite file is not valid JSON, lacks a field the eval needs, or holds a value the eval cannot use."""


def parse_json_object(json_text: str, location: str) -> JsonObject:
    try:
        parsed_json: object = json.loads(json_text)
    except json.JSONDecodeError as decode_failure:
        raise InvalidEvalSuiteException(f"{location}: not valid JSON ({decode_failure})") from decode_failure
    return as_json_object(parsed_json, location)


def as_json_object(json_value: object, location: str) -> JsonObject:
    if not isinstance(json_value, dict):
        raise InvalidEvalSuiteException(f"{location}: expected a JSON object, found {type(json_value).__name__}")
    # json.loads only builds dictionaries with string keys, so the cast states a fact rather than a hope.
    return cast("JsonObject", json_value)


def read_field(json_object: JsonObject, field_name: str, location: str) -> object:
    if field_name not in json_object:
        raise InvalidEvalSuiteException(f"{location}: the '{field_name}' field is missing")
    return json_object[field_name]


def read_string(json_object: JsonObject, field_name: str, location: str) -> str:
    field_value: object = read_field(json_object, field_name, location)
    if not isinstance(field_value, str):
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be a string")
    return field_value


def read_optional_string(json_object: JsonObject, field_name: str, location: str) -> str | None:
    return None if json_object.get(field_name) is None else read_string(json_object, field_name, location)


def read_bool(json_object: JsonObject, field_name: str, location: str) -> bool:
    field_value: object = read_field(json_object, field_name, location)
    if not isinstance(field_value, bool):
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be true or false")
    return field_value


def read_whole_number(json_object: JsonObject, field_name: str, location: str) -> int:
    field_value: object = read_field(json_object, field_name, location)
    # bool is a subclass of int in Python, so true would otherwise pass as 1.
    if isinstance(field_value, bool) or not isinstance(field_value, int):
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be a whole number")
    return field_value


def read_whole_numbers(json_object: JsonObject, field_name: str, location: str) -> tuple[int, ...]:
    field_value: object = read_field(json_object, field_name, location)
    if not isinstance(field_value, list):
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be a list of whole numbers")
    field_values: list[object] = cast("list[object]", field_value)
    if not all(isinstance(listed_value, int) and not isinstance(listed_value, bool) for listed_value in field_values):
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be a list of whole numbers, not {field_values!r}")
    return tuple(cast("list[int]", field_values))


def read_object(json_object: JsonObject, field_name: str, location: str) -> JsonObject:
    return as_json_object(read_field(json_object, field_name, location), f"{location} '{field_name}'")


def read_object_list(json_object: JsonObject, field_name: str, location: str) -> tuple[JsonObject, ...]:
    field_value: object = read_field(json_object, field_name, location)
    if not isinstance(field_value, list):
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be a list")
    return tuple(as_json_object(list_item, f"{location} '{field_name}'") for list_item in cast("list[object]", field_value))


def read_string_list(json_object: JsonObject, field_name: str, location: str) -> tuple[str, ...]:
    field_value: object = read_field(json_object, field_name, location)
    if not isinstance(field_value, list) or not all(isinstance(list_item, str) for list_item in cast("list[object]", field_value)):
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be a list of strings")
    return tuple(cast("list[str]", field_value))


def read_string_mapping(json_object: JsonObject, field_name: str, location: str) -> dict[str, str]:
    mapping_object: JsonObject = read_object(json_object, field_name, location)
    return {key: read_string(mapping_object, key, f"{location} '{field_name}'") for key in mapping_object}


def read_amount(amount_text: str, location: str) -> Decimal:
    try:
        amount: Decimal = Decimal(amount_text)
    except InvalidOperation as invalid_amount:
        raise InvalidEvalSuiteException(f"{location}: '{amount_text}' is not a number") from invalid_amount
    if not amount.is_finite():
        raise InvalidEvalSuiteException(f"{location}: '{amount_text}' is not a finite number")
    return amount


def is_finite_amount_text(amount_text: str) -> bool:
    try:
        return Decimal(amount_text).is_finite()
    except InvalidOperation:
        return False


def read_pass_rate(json_object: JsonObject, field_name: str, location: str) -> float:
    field_value: object = read_field(json_object, field_name, location)
    # NaN fails the range test.
    if isinstance(field_value, bool) or not isinstance(field_value, int | float) or not 0 <= field_value <= 1:
        raise InvalidEvalSuiteException(f"{location}: '{field_name}' must be a number from 0 to 1, not {field_value!r}")
    return float(field_value)


# ----------------------------------------------------------------------------------------------------------
# Arrange: the eval suite folder as typed records
# ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetStatusReader:
    """Reads "over", "under" or "not_stated" from the budget comparison for one category and month."""

    category: str
    period: str


@dataclass(frozen=True)
class RefundTreatmentReader:
    """Reads "netted_against_spending", "counted_as_purchase", "excluded" or "not_mentioned" for one source line."""

    line_number: int


@dataclass(frozen=True)
class DuplicateExcludedReader:
    """Reads "true" or "false"; "false" also when the line is missing from the result."""

    line_number: int


@dataclass(frozen=True)
class LinesCountedReader:
    """Reads whether every one of several source lines was counted as spending."""

    line_numbers: tuple[int, ...]


@dataclass(frozen=True)
class UncategorizedCountReader:
    """Counts both uncategorized treatments, outflows and credits."""


@dataclass(frozen=True)
class LargestIncreaseReader:
    """Reads the category with the largest increase from one month to the next."""

    from_period: str
    to_period: str


type FactReader = (
    BudgetStatusReader | RefundTreatmentReader | DuplicateExcludedReader | LinesCountedReader | UncategorizedCountReader | LargestIncreaseReader
)


@dataclass(frozen=True)
class CheckDefinition:
    check_name: str
    description: str
    minimum_pass_rate: float
    # How this check was carried over from the prompt eval when the workflow cannot be graded the same way.
    adaptation: str | None


@dataclass(frozen=True)
class AmountCheck(CheckDefinition):
    fact_name: str


@dataclass(frozen=True)
class FactValueCheck(CheckDefinition):
    fact_name: str
    fact_reader: FactReader


@dataclass(frozen=True)
class LedgerCategoryCheck(CheckDefinition):
    reference_category: str


@dataclass(frozen=True)
class LedgerLinesCheck(CheckDefinition):
    """Every input line against the reference ledger: its amount, month, category and whether it is counted."""


@dataclass(frozen=True)
class BudgetTargetsCheck(CheckDefinition):
    target_by_category: Mapping[str, Decimal]


@dataclass(frozen=True)
class JudgeCheck(CheckDefinition):
    rubric_text: str


@dataclass(frozen=True)
class NotApplicableCheck(CheckDefinition):
    not_applicable_reason: str


type GradedCheck = AmountCheck | FactValueCheck | LedgerCategoryCheck | LedgerLinesCheck | BudgetTargetsCheck | JudgeCheck
type EvalSuiteCheck = GradedCheck | NotApplicableCheck


@dataclass(frozen=True)
class AcceptedPolicy:
    """An accepted alternative of the reference key, which may move its entry to one of `alternative_categories`."""

    policy_name: str
    alternative_categories: tuple[str, ...]


@dataclass(frozen=True)
class LedgerEntry:
    """One input line as the reference key reads it; an entry that is not counted (a likely duplicate) is in no reference total."""

    line_number: int
    source_text: str
    period: str
    amount: Decimal
    category: str
    accepted_policy: AcceptedPolicy | None
    is_counted: bool
    note: str | None


@dataclass(frozen=True)
class LedgerBucket:
    """One category in one month."""

    category: str
    period: str


@dataclass(frozen=True)
class ReferenceLedger:
    ledger_entries: tuple[LedgerEntry, ...]
    ledger_bucket_by_fact_name: Mapping[str, LedgerBucket]


@dataclass(frozen=True)
class GradingReference:
    """What the graders compare a run with: the accepted values per fact, the reference ledger, and the reference's
    name for the category the workflow leaves out of its totals (uncategorized lines)."""

    expected_fact_values_by_fact_name: Mapping[str, tuple[str, ...]]
    reference_ledger: ReferenceLedger
    uncategorized_category: str


@dataclass(frozen=True)
class EvalCase:
    """The workflow's input: the expense lines after the log's heading, the caller-confirmed duplicates, and the
    settings the case fixes (the budget targets from budget_notes.txt). Line numbers are the input file's."""

    case_name: str
    expense_lines: tuple[str, ...]
    first_expense_line_number: int
    confirmed_duplicate_line_numbers: tuple[int, ...]
    setting_value_by_name: Mapping[str, str]

    def to_workflow_line_number(self, line_number: int) -> int:
        return line_number - self.first_expense_line_number + 1


@dataclass(frozen=True)
class EvalSuite:
    eval_suite_name: str
    eval_case: EvalCase
    grading_reference: GradingReference
    reference_category_by_workflow_category: Mapping[ExpenseCategory, str]
    checks: tuple[EvalSuiteCheck, ...]


def load_eval_suite(eval_suite_directory: Path) -> EvalSuite:
    """Read and validate the suite folder. Every broken reference fails here, before any paid run starts."""
    eval_suite_object: JsonObject = parse_json_object(
        (eval_suite_directory / EVAL_SUITE_DEFINITION_FILE_NAME).read_text(encoding=TEXT_ENCODING), EVAL_SUITE_DEFINITION_FILE_NAME
    )
    case_objects: tuple[JsonObject, ...] = read_object_list(eval_suite_object, "cases", EVAL_SUITE_DEFINITION_FILE_NAME)
    if len(case_objects) != 1:
        raise InvalidEvalSuiteException(f"{EVAL_SUITE_DEFINITION_FILE_NAME}: 'cases' must list exactly one case, not {len(case_objects)}")
    case_object: JsonObject = case_objects[0]
    case_location: str = f"{EVAL_SUITE_DEFINITION_FILE_NAME} case"
    expected_facts_file: str = read_string(case_object, "expected_facts_file", case_location)
    reference_ledger_file: str = read_string(case_object, "reference_ledger_file", case_location)
    reference_category_by_workflow_category: dict[ExpenseCategory, str] = validate_category_names(
        read_string_mapping(eval_suite_object, "reference_category_by_workflow_category", EVAL_SUITE_DEFINITION_FILE_NAME)
    )
    eval_suite: EvalSuite = EvalSuite(
        eval_suite_name=read_string(eval_suite_object, "eval_suite_name", EVAL_SUITE_DEFINITION_FILE_NAME),
        eval_case=validate_eval_case(case_object, eval_suite_directory),
        grading_reference=GradingReference(
            expected_fact_values_by_fact_name=validate_expected_facts(
                (eval_suite_directory / expected_facts_file).read_text(encoding=TEXT_ENCODING), expected_facts_file
            ),
            reference_ledger=validate_reference_ledger(
                (eval_suite_directory / reference_ledger_file).read_text(encoding=TEXT_ENCODING), reference_ledger_file
            ),
            uncategorized_category=reference_category_by_workflow_category[ExpenseCategory.UNCATEGORIZED],
        ),
        reference_category_by_workflow_category=reference_category_by_workflow_category,
        checks=tuple(
            validate_check(check_object, eval_suite_directory)
            for check_object in read_object_list(eval_suite_object, "checks", EVAL_SUITE_DEFINITION_FILE_NAME)
        ),
    )
    reference_problems: list[str] = find_reference_problems(eval_suite)
    if reference_problems:
        problem_lines: str = "\n".join(f"  - {reference_problem}" for reference_problem in reference_problems)
        raise InvalidEvalSuiteException(f"the eval suite has {len(reference_problems)} broken references:\n{problem_lines}")
    return eval_suite


def validate_eval_case(case_object: JsonObject, eval_suite_directory: Path) -> EvalCase:
    case_location: str = f"{EVAL_SUITE_DEFINITION_FILE_NAME} case"
    first_expense_line_number: int = read_whole_number(case_object, "first_expense_line_number", case_location)
    if first_expense_line_number < 1:
        raise InvalidEvalSuiteException(f"{case_location}: 'first_expense_line_number' must be at least 1, not {first_expense_line_number}")
    expense_log_text: str = (eval_suite_directory / read_string(case_object, "expense_log_file", case_location)).read_text(encoding=TEXT_ENCODING)
    # Split on "\n" only, as the input file is LF; str.splitlines() would also split inside a line on U+2028 and similar.
    expense_log_lines: list[str] = expense_log_text.removesuffix("\n").split("\n")
    return EvalCase(
        case_name=read_string(case_object, "case_name", case_location),
        expense_lines=tuple(expense_log_lines[first_expense_line_number - 1 :]),
        first_expense_line_number=first_expense_line_number,
        confirmed_duplicate_line_numbers=read_whole_numbers(case_object, "confirmed_duplicate_line_numbers", case_location),
        setting_value_by_name=read_string_mapping(case_object, "settings", case_location),
    )


def validate_expected_facts(expected_facts_text: str, expected_facts_file: str) -> dict[str, tuple[str, ...]]:
    expected_facts_object: JsonObject = read_object(
        parse_json_object(expected_facts_text, expected_facts_file), "expected_facts", expected_facts_file
    )
    return {fact_name: read_string_list(expected_facts_object, fact_name, expected_facts_file) for fact_name in expected_facts_object}


def validate_reference_ledger(reference_ledger_text: str, reference_ledger_file: str) -> ReferenceLedger:
    reference_ledger_object: JsonObject = parse_json_object(reference_ledger_text, reference_ledger_file)
    amount_facts_object: JsonObject = read_object(reference_ledger_object, "amount_facts", reference_ledger_file)
    ledger_bucket_by_fact_name: dict[str, LedgerBucket] = {}
    for fact_name in amount_facts_object:
        bucket_object: JsonObject = read_object(amount_facts_object, fact_name, reference_ledger_file)
        ledger_bucket_by_fact_name[fact_name] = LedgerBucket(
            category=read_string(bucket_object, "category", fact_name), period=read_string(bucket_object, "period", fact_name)
        )
    return ReferenceLedger(
        ledger_entries=tuple(
            validate_ledger_entry(entry_object, f"{reference_ledger_file} entry {entry_number}")
            for entry_number, entry_object in enumerate(read_object_list(reference_ledger_object, "entries", reference_ledger_file), start=1)
        ),
        ledger_bucket_by_fact_name=ledger_bucket_by_fact_name,
    )


def validate_ledger_entry(entry_object: JsonObject, entry_location: str) -> LedgerEntry:
    return LedgerEntry(
        line_number=read_whole_number(entry_object, "line_number", entry_location),
        source_text=read_string(entry_object, "source_text", entry_location),
        period=read_string(entry_object, "period", entry_location),
        amount=read_amount(read_string(entry_object, "amount", entry_location), entry_location),
        category=read_string(entry_object, "category", entry_location),
        accepted_policy=validate_accepted_policy(entry_object, entry_location),
        is_counted=read_bool(entry_object, "is_counted", entry_location),
        note=read_optional_string(entry_object, "note", entry_location),
    )


def validate_accepted_policy(entry_object: JsonObject, entry_location: str) -> AcceptedPolicy | None:
    alternative_categories: tuple[str, ...] = read_string_list(entry_object, "alternative_categories", entry_location)
    policy_name: str | None = read_optional_string(entry_object, "policy_name", entry_location)
    if policy_name is None and not alternative_categories:
        return None
    if policy_name is None or not alternative_categories:
        raise InvalidEvalSuiteException(f"{entry_location}: needs a 'policy_name' exactly when it lists 'alternative_categories'")
    return AcceptedPolicy(policy_name, alternative_categories)


def validate_category_names(reference_category_by_category_value: Mapping[str, str]) -> dict[ExpenseCategory, str]:
    location: str = f"{EVAL_SUITE_DEFINITION_FILE_NAME} 'reference_category_by_workflow_category'"
    unknown_category_values: list[str] = [
        category_value for category_value in reference_category_by_category_value if category_value not in set(ExpenseCategory)
    ]
    missing_categories: list[str] = [category.value for category in ExpenseCategory if category.value not in reference_category_by_category_value]
    repeated_names: list[str] = [name for name, use_count in Counter(reference_category_by_category_value.values()).items() if use_count > 1]
    if unknown_category_values or missing_categories or repeated_names:
        category_names_message: str = (
            f"{location}: must name every workflow category exactly once, each with its own reference name; "
            f"unknown {unknown_category_values}, missing {missing_categories}, repeated names {repeated_names}"
        )
        raise InvalidEvalSuiteException(category_names_message)
    return {ExpenseCategory(category_value): name for category_value, name in reference_category_by_category_value.items()}


def validate_check(check_object: JsonObject, eval_suite_directory: Path) -> EvalSuiteCheck:
    check_name: str = read_string(check_object, "check_name", f"{EVAL_SUITE_DEFINITION_FILE_NAME} check")
    check_location: str = f"{EVAL_SUITE_DEFINITION_FILE_NAME} check '{check_name}'"
    description: str = read_string(check_object, "description", check_location)
    minimum_pass_rate: float = read_pass_rate(check_object, "minimum_pass_rate", check_location)
    adaptation: str | None = read_optional_string(check_object, "adaptation", check_location)
    match read_string(check_object, "grader", check_location):
        case "amount":
            return AmountCheck(
                check_name, description, minimum_pass_rate, adaptation, fact_name=read_string(check_object, "fact_name", check_location)
            )
        case "fact_value":
            return FactValueCheck(
                check_name,
                description,
                minimum_pass_rate,
                adaptation,
                fact_name=read_string(check_object, "fact_name", check_location),
                fact_reader=validate_fact_reader(read_object(check_object, "fact_reader", check_location), check_location),
            )
        case "ledger_category":
            return LedgerCategoryCheck(
                check_name,
                description,
                minimum_pass_rate,
                adaptation,
                reference_category=read_string(check_object, "reference_category", check_location),
            )
        case "ledger_lines":
            return LedgerLinesCheck(check_name, description, minimum_pass_rate, adaptation)
        case "budget_targets":
            return BudgetTargetsCheck(
                check_name,
                description,
                minimum_pass_rate,
                adaptation,
                target_by_category={
                    category: read_amount(target_text, check_location)
                    for category, target_text in read_string_mapping(check_object, "budget_targets", check_location).items()
                },
            )
        case "judge":
            rubric_file: str = read_string(check_object, "rubric_file", check_location)
            rubric_text: str = (eval_suite_directory / rubric_file).read_text(encoding=TEXT_ENCODING)
            return JudgeCheck(check_name, description, minimum_pass_rate, adaptation, rubric_text=rubric_text)
        case "not_applicable":
            return NotApplicableCheck(
                check_name,
                description,
                minimum_pass_rate,
                adaptation,
                not_applicable_reason=read_string(check_object, "not_applicable_reason", check_location),
            )
        case unknown_grader:
            unknown_grader_message: str = (
                f"{check_location}: 'grader' names an unknown grader: {unknown_grader}; known graders are "
                "['amount', 'budget_targets', 'fact_value', 'judge', 'ledger_category', 'ledger_lines', 'not_applicable']"
            )
            raise InvalidEvalSuiteException(unknown_grader_message)


def validate_fact_reader(fact_reader_object: JsonObject, check_location: str) -> FactReader:
    reader_location: str = f"{check_location} 'fact_reader'"
    match read_string(fact_reader_object, "reader", reader_location):
        case "budget_status":
            return BudgetStatusReader(
                category=read_string(fact_reader_object, "category", reader_location),
                period=read_string(fact_reader_object, "period", reader_location),
            )
        case "refund_treatment":
            return RefundTreatmentReader(line_number=read_whole_number(fact_reader_object, "line_number", reader_location))
        case "duplicate_excluded":
            return DuplicateExcludedReader(line_number=read_whole_number(fact_reader_object, "line_number", reader_location))
        case "lines_counted":
            return LinesCountedReader(line_numbers=read_whole_numbers(fact_reader_object, "line_numbers", reader_location))
        case "uncategorized_count":
            return UncategorizedCountReader()
        case "largest_increase":
            return LargestIncreaseReader(
                from_period=read_string(fact_reader_object, "from_period", reader_location),
                to_period=read_string(fact_reader_object, "to_period", reader_location),
            )
        case unknown_reader:
            unknown_reader_message: str = (
                f"{reader_location}: 'reader' names an unknown fact reader: {unknown_reader}; known fact readers are "
                "['budget_status', 'duplicate_excluded', 'largest_increase', 'lines_counted', 'refund_treatment', 'uncategorized_count']"
            )
            raise InvalidEvalSuiteException(unknown_reader_message)


def find_reference_problems(eval_suite: EvalSuite) -> list[str]:
    """Each of these defects would otherwise fail a check on every run, and so blame the workflow for an eval suite error."""
    eval_case: EvalCase = eval_suite.eval_case
    last_line_number: int = eval_case.first_expense_line_number + len(eval_case.expense_lines) - 1
    reference_problems: list[str] = [
        f"'check_name' '{check_name}' is used {use_count} times"
        for check_name, use_count in Counter(check.check_name for check in eval_suite.checks).items()
        if use_count > 1
    ]
    reference_problems.extend(
        f"confirmed duplicate line {line_number} is outside the expense lines {eval_case.first_expense_line_number}-{last_line_number}"
        for line_number in eval_case.confirmed_duplicate_line_numbers
        if not eval_case.first_expense_line_number <= line_number <= last_line_number
    )
    reference_problems.extend(find_ledger_source_problems(eval_suite.grading_reference.reference_ledger, eval_case))
    for check in eval_suite.checks:
        reference_problems.extend(find_check_reference_problems(check, eval_suite))
    return reference_problems


def find_ledger_source_problems(reference_ledger: ReferenceLedger, eval_case: EvalCase) -> list[str]:
    # The ledger and the workflow must number the same lines, or every traced cause would name the wrong entry.
    ledger_source_problems: list[str] = []
    for ledger_entry in reference_ledger.ledger_entries:
        line_index: int = eval_case.to_workflow_line_number(ledger_entry.line_number) - 1
        source_text: str | None = eval_case.expense_lines[line_index] if 0 <= line_index < len(eval_case.expense_lines) else None
        if source_text != ledger_entry.source_text:
            ledger_source_problems.append(
                f"reference ledger line {ledger_entry.line_number} reads {ledger_entry.source_text!r}; the input has {source_text!r}"
            )
    return ledger_source_problems


def find_check_reference_problems(check: EvalSuiteCheck, eval_suite: EvalSuite) -> list[str]:
    check_location: str = f"check '{check.check_name}'"
    known_categories: set[str] = set(eval_suite.reference_category_by_workflow_category.values())
    match check:
        case AmountCheck():
            expected_amounts: tuple[str, ...] = eval_suite.grading_reference.expected_fact_values_by_fact_name.get(check.fact_name, ())
            return [
                *find_fact_name_problems(check_location, check.fact_name, eval_suite.grading_reference.expected_fact_values_by_fact_name),
                *(
                    [f"{check_location}: '{check.fact_name}' has no category and month in the reference ledger's 'amount_facts'"]
                    if check.fact_name not in eval_suite.grading_reference.reference_ledger.ledger_bucket_by_fact_name
                    else []
                ),
                *(
                    f"{check_location}: the expected amount '{expected_amount}' of '{check.fact_name}' is not a finite plain number"
                    for expected_amount in expected_amounts
                    if not is_finite_amount_text(expected_amount)
                ),
            ]
        case FactValueCheck():
            return find_fact_name_problems(check_location, check.fact_name, eval_suite.grading_reference.expected_fact_values_by_fact_name)
        case LedgerCategoryCheck():
            return [] if check.reference_category in known_categories else [f"{check_location}: '{check.reference_category}' is not a category name"]
        case BudgetTargetsCheck():
            return [
                f"{check_location}: '{category}' is not a category name" for category in check.target_by_category if category not in known_categories
            ]
        case LedgerLinesCheck() | JudgeCheck() | NotApplicableCheck():
            return []


def find_fact_name_problems(check_location: str, fact_name: str, expected_fact_values_by_fact_name: Mapping[str, Sequence[str]]) -> list[str]:
    if fact_name not in expected_fact_values_by_fact_name:
        return [f"{check_location}: '{fact_name}' has no expected values in the expected facts file"]
    return []


def create_run_directory(results_directory: Path, run_label: str) -> Path:
    # The random suffix keeps two runs started in the same second with the same configuration out of one folder.
    run_identity: str = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{run_label}-{secrets.token_hex(RUN_IDENTITY_RANDOM_BYTES)}"
    run_directory: Path = results_directory / re.sub(r"[^A-Za-z0-9._-]", "-", run_identity)
    run_directory.mkdir(parents=True)
    return run_directory


# ----------------------------------------------------------------------------------------------------------
# Arrange: the workflow configuration each run uses. Production reads the same settings from the environment;
# the eval swaps them per run through the Domain Facade's service locator seam.
# ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelStep:
    """One step of the workflow that calls a model, as the run's configuration resolved it."""

    step_name: str
    provider: str
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class ModelStepSelection:
    """The subscription for every LLM step, with one model and effort for all of them; a model or effort left out keeps
    its configured value."""

    coding_assistant_subscription: CodingAssistantSubscription
    model: str | None
    reasoning_effort: str | None


@dataclass(frozen=True)
class SettingOverrideRequest:
    """The layers of one run's configuration, in the order they apply: the case's settings, then the model step
    selection, then explicit settings. Anything left out keeps its .env or environment value."""

    case_setting_value_by_name: Mapping[str, str]
    model_step_selection: ModelStepSelection | None
    extra_setting_value_by_name: Mapping[str, str]


class SettingOverrideException(EvalBusinessBaseException):
    """A model or reasoning effort was given without the subscription that selects their family, or a command-line
    setting does not read NAME=VALUE."""


def select_model_steps(coding_assistant_subscription_value: str | None, model: str | None, reasoning_effort: str | None) -> ModelStepSelection | None:
    if coding_assistant_subscription_value is None:
        if model is not None or reasoning_effort is not None:
            known_subscriptions: str = ", ".join(coding_assistant_subscription.value for coding_assistant_subscription in CodingAssistantSubscription)
            received_values: str = f"model {model!r} and reasoning effort {reasoning_effort!r} with no subscription (one of {known_subscriptions})"
            raise SettingOverrideException(
                f"A model or reasoning effort for every LLM step needs the subscription that selects their family; received {received_values}"
            )
        return None
    return ModelStepSelection(CodingAssistantSubscription(coding_assistant_subscription_value), model, reasoning_effort)


def build_setting_overrides(setting_override_request: SettingOverrideRequest) -> dict[str, str]:
    setting_value_by_name: dict[str, str] = dict(setting_override_request.case_setting_value_by_name)
    model_step_selection: ModelStepSelection | None = setting_override_request.model_step_selection
    if model_step_selection is not None:
        setting_value_by_name.update(build_model_step_settings(model_step_selection))
    setting_value_by_name.update(setting_override_request.extra_setting_value_by_name)
    return setting_value_by_name


def build_model_step_settings(model_step_selection: ModelStepSelection) -> dict[str, str]:
    coding_assistant_subscription: CodingAssistantSubscription = model_step_selection.coding_assistant_subscription
    model_step_setting_value_by_name: dict[str, str] = {CODING_ASSISTANT_SUBSCRIPTION_SETTING_NAME: coding_assistant_subscription.value}
    model_family: ModelFamily = MODEL_FAMILY_BY_SUBSCRIPTION[coding_assistant_subscription]
    for llm_operation in LlmOperation:
        if model_step_selection.model is not None:
            model_step_setting_value_by_name[f"{llm_operation.value}_{model_family.value}_MODEL"] = model_step_selection.model
        if model_step_selection.reasoning_effort is not None:
            model_step_setting_value_by_name[f"{llm_operation.value}_{model_family.value}_REASONING_EFFORT"] = model_step_selection.reasoning_effort
    return model_step_setting_value_by_name


def describe_model_steps(configuration_provider: ConfigurationProvider) -> tuple[ModelStep, ...]:
    coding_assistant_settings: CodingAssistantSettings = configuration_provider.get_coding_assistant_settings()
    jev_model: str = configuration_provider.get_jev_settings().jev_model
    llm_model_steps: list[ModelStep] = []
    for llm_operation in INVOKED_LLM_OPERATIONS:
        llm_operation_settings: LlmOperationSettings = configuration_provider.retrieve_llm_operation_settings(llm_operation)
        llm_model_steps.append(
            ModelStep(
                step_name=llm_operation.value.lower(),
                provider=f"{coding_assistant_settings.coding_assistant_subscription.value} ({coding_assistant_settings.model_family.value})",
                model=llm_operation_settings.model,
                reasoning_effort=llm_operation_settings.reasoning_effort.value if llm_operation_settings.reasoning_effort is not None else "default",
            )
        )
    # Jev's SDK takes a model name only; it has no reasoning-effort control.
    return (
        ModelStep("expense_line_jev_extraction", JEV_PROVIDER, jev_model, JEV_REASONING_EFFORT),
        *llm_model_steps[:1],
        ModelStep("transaction_categorization", JEV_PROVIDER, jev_model, JEV_REASONING_EFFORT),
        *llm_model_steps[1:],
    )


def parse_setting_assignments(setting_assignments: Iterable[str]) -> dict[str, str]:
    """NAME=VALUE pairs from the command line; a pair without a name or an equals sign is a mistake, not an empty setting."""
    setting_value_by_name: dict[str, str] = {}
    for setting_assignment in setting_assignments:
        setting_name: str
        separator: str
        setting_value: str
        setting_name, separator, setting_value = setting_assignment.partition("=")
        if not separator or not setting_name.strip():
            raise SettingOverrideException(f"A workflow setting must read NAME=VALUE; received {setting_assignment!r}")
        setting_value_by_name[setting_name.strip()] = setting_value.strip()
    return setting_value_by_name


def compose_run_label(model_steps: Sequence[ModelStep]) -> str:
    """The distinct LLM models and efforts of the run, for its folder name; Jev steps are the same in every run."""
    llm_model_steps: list[ModelStep] = [model_step for model_step in model_steps if model_step.reasoning_effort != JEV_REASONING_EFFORT]
    provider_name: str = llm_model_steps[0].provider.split(" ", maxsplit=1)[0].lower() if llm_model_steps else "no-llm"
    model_labels: list[str] = list(dict.fromkeys(f"{model_step.model}-{model_step.reasoning_effort}" for model_step in llm_model_steps))
    return "-".join((provider_name, *model_labels))


class JevRequestCountingTransport(httpx2.AsyncBaseTransport):
    """The default HTTP transport the Jev SDK would create for itself, counting the requests it sends. Nothing is recorded
    from a request, so the OpenRouter key in its headers never reaches an artifact."""

    def __init__(self, async_base_transport: httpx2.AsyncBaseTransport) -> None:
        self._async_base_transport: httpx2.AsyncBaseTransport = async_base_transport
        self.request_count: int = 0

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.request_count += 1
        return await self._async_base_transport.handle_async_request(request)

    @override
    async def aclose(self) -> None:
        await self._async_base_transport.aclose()


class ServiceLocatorEval(ServiceLocatorProduction):
    """Production clients and gateways, with the run's settings layered over .env and the environment."""

    def __init__(self, setting_value_by_name: Mapping[str, str]) -> None:
        self._setting_value_by_name: Mapping[str, str] = setting_value_by_name
        self.jev_request_counting_transport: JevRequestCountingTransport = JevRequestCountingTransport(httpx2.AsyncHTTPTransport())

    @override
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider(self._setting_value_by_name)

    @override
    def create_jev_http_transport(self) -> httpx2.AsyncBaseTransport:
        return self.jev_request_counting_transport


# ----------------------------------------------------------------------------------------------------------
# Act: one isolated run of the real workflow
# ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TrialActivity:
    """What one run did: how long it took, how many Jev requests it sent, the LLM steps' own progress messages, which
    say whether any line needed LLM extraction, and whether the workflow returned a result or failed."""

    duration_seconds: float
    jev_request_count: int
    llm_step_events: tuple[str, ...]
    workflow_completed: bool


@dataclass(frozen=True)
class CompletedTrial:
    expense_analysis_result: ExpenseAnalysisResult
    trial_activity: TrialActivity


@dataclass(frozen=True)
class ErroredTrial:
    failure_description: str
    trial_activity: TrialActivity


class LlmStepEventHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.llm_step_events: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.llm_step_events.append(record.getMessage())


@contextlib.contextmanager
def capture_llm_step_events() -> Generator[LlmStepEventHandler]:
    """The LLM processors log their progress at INFO when no event callback is supplied, as in production."""
    llm_step_logger: logging.Logger = logging.getLogger(LLM_STEP_LOGGER_NAME)
    llm_step_event_handler: LlmStepEventHandler = LlmStepEventHandler()
    original_level: int = llm_step_logger.level
    llm_step_logger.addHandler(llm_step_event_handler)
    llm_step_logger.setLevel(logging.INFO)
    try:
        yield llm_step_event_handler
    finally:
        llm_step_logger.removeHandler(llm_step_event_handler)
        llm_step_logger.setLevel(original_level)


type TrialAttempt = CompletedTrial | ErroredTrial


@dataclass(frozen=True)
class TrialMonitor:
    """Watches one run from outside: its start time, its Jev transport and the LLM steps' log."""

    started_at: float
    jev_request_counting_transport: JevRequestCountingTransport
    llm_step_event_handler: LlmStepEventHandler

    def record_trial_activity(self, *, workflow_completed: bool) -> TrialActivity:
        return TrialActivity(
            duration_seconds=time.perf_counter() - self.started_at,
            jev_request_count=self.jev_request_counting_transport.request_count,
            llm_step_events=tuple(self.llm_step_event_handler.llm_step_events),
            workflow_completed=workflow_completed,
        )


async def attempt_trial(expense_analysis: Awaitable[ExpenseAnalysisResult], trial_monitor: TrialMonitor) -> TrialAttempt:
    """Any workflow failure errors this run's checks; it never stops the eval."""
    try:
        expense_analysis_result: ExpenseAnalysisResult = await expense_analysis
    except Exception as workflow_failure:  # noqa: BLE001 - recorded as an errored run, never swallowed silently
        return ErroredTrial(describe_workflow_failure(workflow_failure), trial_monitor.record_trial_activity(workflow_completed=False))
    return CompletedTrial(expense_analysis_result, trial_monitor.record_trial_activity(workflow_completed=True))


def describe_workflow_failure(workflow_failure: BaseException) -> str:
    """The failure and every exception behind it: a gateway's own message hides the provider's reason, such as a model the
    subscription does not offer, and an exception group's message hides every failure it collected."""
    failure_descriptions: list[str] = []
    _describe_failure_chain(workflow_failure, "", failure_descriptions, set())
    return "; ".join(failure_descriptions)


def _describe_failure_chain(failure: BaseException, link_text: str, failure_descriptions: list[str], described_failure_ids: set[int]) -> None:
    current_failure: BaseException | None = failure
    current_link_text: str = link_text
    while current_failure is not None and id(current_failure) not in described_failure_ids:
        described_failure_ids.add(id(current_failure))
        failure_descriptions.append(f"{current_link_text}{type(current_failure).__name__}: {current_failure}")
        if isinstance(current_failure, BaseExceptionGroup):
            collected_failures: tuple[BaseException, ...] = cast("BaseExceptionGroup[BaseException]", current_failure).exceptions
            collected_failure: BaseException
            for collected_failure in collected_failures:
                _describe_failure_chain(collected_failure, "including ", failure_descriptions, described_failure_ids)
        current_failure = current_failure.__cause__ or (None if current_failure.__suppress_context__ else current_failure.__context__)
        current_link_text = "caused by "


# ----------------------------------------------------------------------------------------------------------
# Assert, part 1a: the workflow's result in the reference ledger's terms (input-file line numbers, reference categories)
# ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineCategorization:
    """The line's category, and why it has it: Jev's own choice, and the policy that overrode it, if any."""

    category: str
    model_category: str
    policy_note: str | None


@dataclass(frozen=True)
class LineOutcome:
    line_number: int
    source_text: str
    amount: Decimal | None
    period: str | None
    treatment: str
    line_categorization: LineCategorization | None

    @property
    def category(self) -> str | None:
        return self.line_categorization.category if self.line_categorization is not None else None


@dataclass(frozen=True)
class BucketTotal:
    category: str
    period: str
    amount: Decimal
    line_numbers: tuple[int, ...]


@dataclass(frozen=True)
class BudgetOutcome:
    category: str
    period: str
    target: Decimal
    actual: Decimal


@dataclass(frozen=True)
class ChangeOutcome:
    category: str
    from_period: str
    to_period: str
    change: Decimal


@dataclass(frozen=True)
class WorkflowOutcome:
    line_outcomes: tuple[LineOutcome, ...]
    bucket_totals: tuple[BucketTotal, ...]
    budget_outcomes: tuple[BudgetOutcome, ...]
    change_outcomes: tuple[ChangeOutcome, ...]
    finding_texts: tuple[str, ...]

    def find_line(self, line_number: int) -> LineOutcome | None:
        return next((line_outcome for line_outcome in self.line_outcomes if line_outcome.line_number == line_number), None)

    def find_bucket(self, category: str, period: str) -> BucketTotal | None:
        return next((bucket_total for bucket_total in self.bucket_totals if (bucket_total.category, bucket_total.period) == (category, period)), None)


def to_period(month: date) -> str:
    return f"{month:%Y-%m}"


def project_workflow_outcome(
    expense_analysis_result: ExpenseAnalysisResult, line_offset: int, reference_category_by_workflow_category: Mapping[ExpenseCategory, str]
) -> WorkflowOutcome:
    """`line_offset` turns the workflow's line numbers into the input file's: the file's lines before the first expense line."""
    return WorkflowOutcome(
        line_outcomes=tuple(
            project_line_outcome(expense_transaction, line_offset, reference_category_by_workflow_category)
            for expense_transaction in expense_analysis_result.transactions
        ),
        bucket_totals=tuple(
            BucketTotal(
                category=reference_category_by_workflow_category[monthly_category_total.category],
                period=to_period(monthly_category_total.month),
                amount=monthly_category_total.amount,
                line_numbers=tuple(line_number + line_offset for line_number in monthly_category_total.source_line_numbers),
            )
            for monthly_category_total in expense_analysis_result.calculations.monthly_category_totals
        ),
        budget_outcomes=tuple(
            BudgetOutcome(
                category=reference_category_by_workflow_category[budget_comparison.category],
                period=to_period(budget_comparison.month),
                target=budget_comparison.target,
                actual=budget_comparison.actual,
            )
            for budget_comparison in expense_analysis_result.calculations.budget_comparisons
        ),
        change_outcomes=tuple(
            ChangeOutcome(
                category=reference_category_by_workflow_category[month_category_delta.category],
                from_period=to_period(month_category_delta.from_month),
                to_period=to_period(month_category_delta.to_month),
                change=month_category_delta.change,
            )
            for month_category_delta in expense_analysis_result.calculations.month_category_deltas
        ),
        finding_texts=tuple(expense_finding.text for expense_finding in expense_analysis_result.findings),
    )


def project_line_outcome(
    expense_transaction: ExpenseTransaction, line_offset: int, reference_category_by_workflow_category: Mapping[ExpenseCategory, str]
) -> LineOutcome:
    occurred_on: date | None = expense_transaction.parsed_line.occurred_on
    return LineOutcome(
        line_number=expense_transaction.parsed_line.line_number + line_offset,
        source_text=expense_transaction.parsed_line.source_text,
        amount=expense_transaction.parsed_line.amount,
        period=to_period(occurred_on) if occurred_on is not None else None,
        treatment=expense_transaction.treatment.value,
        line_categorization=LineCategorization(
            category=reference_category_by_workflow_category[expense_transaction.categorization.category],
            model_category=reference_category_by_workflow_category[expense_transaction.categorization.model_category],
            policy_note=expense_transaction.categorization.policy_note,
        )
        if expense_transaction.categorization is not None
        else None,
    )


# ----------------------------------------------------------------------------------------------------------
# Assert, part 1b: trace a wrong total to the exact source lines that make the difference
# ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TracedCause:
    """`summary` reads the same in every run with the same mistake; `description` adds the amounts."""

    summary: str
    description: str


@dataclass(frozen=True)
class LineDifference:
    line_number: int
    summary: str
    amount_effect: Decimal


@dataclass(frozen=True)
class PolicyComposition:
    """The reference entries of one category and month under one set of applied accepted policies."""

    applied_policy_names: tuple[str, ...]
    member_entries: list[LedgerEntry]


@dataclass(frozen=True)
class BucketComparison:
    """The workflow's lines for one category and month against one policy composition of the reference's."""

    policy_composition: PolicyComposition
    line_differences: list[LineDifference]


def compose_bucket(reference_ledger: ReferenceLedger, ledger_bucket: LedgerBucket) -> list[PolicyComposition]:
    """The bucket's reference entries under the key's own policy first, then under every combination of the accepted
    policies that touch it."""
    entries_in_period: list[LedgerEntry] = [
        ledger_entry for ledger_entry in reference_ledger.ledger_entries if ledger_entry.is_counted and ledger_entry.period == ledger_bucket.period
    ]
    policy_names: list[str] = list(
        dict.fromkeys(
            ledger_entry.accepted_policy.policy_name
            for ledger_entry in entries_in_period
            if ledger_entry.accepted_policy is not None
            and ledger_bucket.category in (ledger_entry.category, *ledger_entry.accepted_policy.alternative_categories)
        )
    )
    return [
        PolicyComposition(
            applied_policy_names,
            [
                ledger_entry
                for ledger_entry in entries_in_period
                if decide_reference_category(ledger_entry.category, ledger_entry.accepted_policy, applied_policy_names, ledger_bucket.category)
                == ledger_bucket.category
            ],
        )
        for policy_count in range(len(policy_names) + 1)
        for applied_policy_names in combinations(policy_names, policy_count)
    ]


def decide_reference_category(
    category: str, accepted_policy: AcceptedPolicy | None, applied_policy_names: Sequence[str], bucket_category: str
) -> str:
    if accepted_policy is None or accepted_policy.policy_name not in applied_policy_names:
        return category
    return bucket_category if bucket_category in accepted_policy.alternative_categories else accepted_policy.alternative_categories[0]


def quote_source(source_text: str) -> str:
    return '"' + " ".join(source_text.split()) + '"'


def describe_where_line_went(line_outcome: LineOutcome | None) -> str:
    if line_outcome is None:
        return "the workflow did not report it"
    match line_outcome.treatment:
        case TransactionTreatment.UNPARSED.value:
            return "the workflow left it unparsed"
        case TransactionTreatment.EXCLUDED_DUPLICATE.value:
            return "the workflow excluded it as a duplicate"
        case TransactionTreatment.UNCATEGORIZED_OUTFLOW.value | TransactionTreatment.UNCATEGORIZED_CREDIT.value:
            return "the workflow left it uncategorized"
        case _:
            line_categorization: LineCategorization | None = line_outcome.line_categorization
            if line_categorization is None:
                return "the workflow left it uncategorized"
            category_decision: str = describe_category_decision(line_categorization.model_category, line_categorization.policy_note)
            return f"the workflow put it in {line_categorization.category} {line_outcome.period} {category_decision}"


def describe_category_decision(model_category: str, policy_note: str | None) -> str:
    if policy_note is not None:
        return f"(Jev chose {model_category}; the workflow's policy decided: {policy_note})"
    return "(Jev's choice, no policy applied)"


def find_line_differences(
    member_entries: Sequence[LedgerEntry], ledger_bucket: LedgerBucket, workflow_outcome: WorkflowOutcome, reference_ledger: ReferenceLedger
) -> list[LineDifference]:
    bucket_total: BucketTotal | None = workflow_outcome.find_bucket(ledger_bucket.category, ledger_bucket.period)
    workflow_line_numbers: set[int] = set(bucket_total.line_numbers) if bucket_total is not None else set()
    ledger_entry_by_line_number: dict[int, LedgerEntry] = {ledger_entry.line_number: ledger_entry for ledger_entry in reference_ledger.ledger_entries}
    reference_line_numbers: set[int] = {member_entry.line_number for member_entry in member_entries}
    line_differences: list[LineDifference] = []
    for member_entry in member_entries:
        line_outcome: LineOutcome | None = workflow_outcome.find_line(member_entry.line_number)
        if member_entry.line_number not in workflow_line_numbers:
            line_differences.append(
                LineDifference(
                    member_entry.line_number,
                    (
                        f"line {member_entry.line_number} {quote_source(member_entry.source_text)}: {describe_where_line_went(line_outcome)}; "
                        f"the reference puts it in {ledger_bucket.category}"
                    ),
                    -member_entry.amount,
                )
            )
        elif line_outcome is not None and line_outcome.amount != member_entry.amount:
            line_differences.append(
                LineDifference(
                    member_entry.line_number,
                    (
                        f"line {member_entry.line_number} {quote_source(member_entry.source_text)}: the workflow read the amount as "
                        f"{line_outcome.amount}; the reference reads {member_entry.amount}"
                    ),
                    (line_outcome.amount or Decimal(0)) - member_entry.amount,
                )
            )
    for line_number in sorted(workflow_line_numbers - reference_line_numbers):
        extra_line_outcome: LineOutcome | None = workflow_outcome.find_line(line_number)
        extra_amount: Decimal = extra_line_outcome.amount if extra_line_outcome is not None and extra_line_outcome.amount is not None else Decimal(0)
        ledger_entry: LedgerEntry | None = ledger_entry_by_line_number.get(line_number)
        line_label: str = (
            f"line {line_number} {quote_source(extra_line_outcome.source_text)}" if extra_line_outcome is not None else f"line {line_number}"
        )
        reference_placement: str = (
            describe_reference_placement(
                is_counted=ledger_entry.is_counted,
                note=ledger_entry.note,
                period=ledger_entry.period,
                category=ledger_entry.category,
                bucket_period=ledger_bucket.period,
            )
            if ledger_entry is not None
            else "the reference has no expense on that line"
        )
        line_differences.append(
            LineDifference(line_number, f"{line_label}: the workflow counted it in {ledger_bucket.category}; {reference_placement}", extra_amount)
        )
    return sorted(line_differences, key=lambda line_difference: line_difference.line_number)


def describe_reference_placement(*, is_counted: bool, note: str | None, period: str, category: str, bucket_period: str) -> str:
    if not is_counted:
        return f"the reference does not count it ({note})"
    if period != bucket_period:
        return f"the reference puts it in {period}"
    return f"the reference puts it in {category}"


def trace_bucket_difference(reference_ledger: ReferenceLedger, ledger_bucket: LedgerBucket, workflow_outcome: WorkflowOutcome) -> TracedCause | None:
    """Compare the workflow's lines for one category and month with the reference's, under the accepted policy that
    leaves the fewest differences. None when the lines agree under some accepted policy."""
    bucket_total: BucketTotal | None = workflow_outcome.find_bucket(ledger_bucket.category, ledger_bucket.period)
    reported_amount: Decimal = bucket_total.amount if bucket_total is not None else Decimal(0)
    bucket_comparisons: list[BucketComparison] = [
        BucketComparison(
            policy_composition,
            find_line_differences(policy_composition.member_entries, ledger_bucket, workflow_outcome, reference_ledger),
        )
        for policy_composition in compose_bucket(reference_ledger, ledger_bucket)
    ]
    # compose_bucket always yields the key's own policy first, so there is at least one comparison; the fewest differences wins.
    bucket_comparison: BucketComparison = min(
        bucket_comparisons, key=lambda compared_bucket_comparison: len(compared_bucket_comparison.line_differences)
    )
    if not bucket_comparison.line_differences:
        return None
    applied_policy_names: tuple[str, ...] = bucket_comparison.policy_composition.applied_policy_names
    line_differences: list[LineDifference] = bucket_comparison.line_differences
    reference_amount: Decimal = sum((member_entry.amount for member_entry in bucket_comparison.policy_composition.member_entries), Decimal(0))
    bucket_label: str = f"{ledger_bucket.category} {ledger_bucket.period}"
    policy_clause: str = f" (under the accepted policy {', '.join(applied_policy_names)})" if applied_policy_names else ""
    difference: Decimal = reported_amount - reference_amount
    unexplained_amount: Decimal = difference - sum((line_difference.amount_effect for line_difference in line_differences), Decimal(0))
    unexplained_clause: str = f" A difference of {unexplained_amount:+} remains unexplained." if abs(unexplained_amount) > AMOUNT_TOLERANCE else ""
    return TracedCause(
        summary=f"{bucket_label}{policy_clause}: " + "; ".join(line_difference.summary for line_difference in line_differences),
        description=(
            f"{bucket_label}: reported {reported_amount}, reference {reference_amount}{policy_clause}, a difference of {difference:+}. Explained by: "
            + "; ".join(f"{line_difference.summary} ({line_difference.amount_effect:+})" for line_difference in line_differences)
            + f".{unexplained_clause}"
        ),
    )


def trace_category_differences(reference_ledger: ReferenceLedger, reference_category: str, line_outcomes: Sequence[LineOutcome]) -> list[TracedCause]:
    """Every counted reference entry in the category that the workflow placed elsewhere or excluded, and every line the
    workflow counted in the category that the reference puts elsewhere. A category the key accepts as an alternative matches."""
    line_outcome_by_line_number: dict[int, LineOutcome] = {line_outcome.line_number: line_outcome for line_outcome in line_outcomes}
    traced_causes: list[TracedCause] = []
    reference_line_numbers: set[int] = set()
    for ledger_entry in reference_ledger.ledger_entries:
        if not ledger_entry.is_counted or ledger_entry.category != reference_category:
            continue
        reference_line_numbers.add(ledger_entry.line_number)
        line_outcome: LineOutcome | None = line_outcome_by_line_number.get(ledger_entry.line_number)
        if line_outcome is None or not is_counted_line(line_outcome) or line_outcome.category not in accepted_categories(ledger_entry):
            cause_text: str = (
                f"line {ledger_entry.line_number} {quote_source(ledger_entry.source_text)}: {describe_where_line_went(line_outcome)}; "
                f"the reference puts it in {reference_category}"
            )
            traced_causes.append(TracedCause(cause_text, f"{cause_text} ({ledger_entry.amount})"))
    ledger_entry_by_line_number: dict[int, LedgerEntry] = {ledger_entry.line_number: ledger_entry for ledger_entry in reference_ledger.ledger_entries}
    for line_outcome in line_outcomes:
        if line_outcome.category != reference_category or not is_counted_line(line_outcome) or line_outcome.line_number in reference_line_numbers:
            continue
        reference_ledger_entry: LedgerEntry | None = ledger_entry_by_line_number.get(line_outcome.line_number)
        if (
            reference_ledger_entry is None
            or not reference_ledger_entry.is_counted
            or reference_category not in accepted_categories(reference_ledger_entry)
        ):
            cause_text = (
                f"line {line_outcome.line_number} {quote_source(line_outcome.source_text)}: the workflow put it in {reference_category}; "
                + (
                    f"the reference puts it in {reference_ledger_entry.category}"
                    if reference_ledger_entry is not None
                    else "the reference has no expense on that line"
                )
            )
            traced_causes.append(TracedCause(cause_text, f"{cause_text} ({line_outcome.amount})"))
    return traced_causes


def is_counted_line(line_outcome: LineOutcome) -> bool:
    return line_outcome.treatment != TransactionTreatment.EXCLUDED_DUPLICATE.value


def accepted_categories(ledger_entry: LedgerEntry) -> tuple[str, ...]:
    return (ledger_entry.category, *(ledger_entry.accepted_policy.alternative_categories if ledger_entry.accepted_policy is not None else ()))


# ----------------------------------------------------------------------------------------------------------
# Assert, part 1c: read each fact from the outcome and grade every check (plain comparisons, no sessions)
# ----------------------------------------------------------------------------------------------------------


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"  # the workflow or the judge failed, so the check could not be graded; not a pass


@dataclass(frozen=True)
class CheckGrade:
    check_status: CheckStatus
    reason: str
    causes: tuple[TracedCause, ...] = ()


@dataclass(frozen=True)
class ReadFact:
    """A fact as the workflow's result states it, with the evidence it was read from. None when the result does not state it."""

    fact_value: str | None
    evidence: str


@dataclass(frozen=True)
class CompletedJudgment:
    passed: bool
    reason: str


@dataclass(frozen=True)
class ErroredJudgment:
    failure_description: str


type JudgmentAttempt = CompletedJudgment | ErroredJudgment


def to_check_status(*, passed: bool) -> CheckStatus:
    return CheckStatus.PASSED if passed else CheckStatus.FAILED


def describe_line(line_outcome: LineOutcome) -> str:
    return f"line {line_outcome.line_number} {quote_source(line_outcome.source_text)}: amount {line_outcome.amount}, {line_outcome.treatment}" + (
        f" in {line_outcome.category}" if line_outcome.category is not None else ""
    )


def read_fact(fact_reader: FactReader, workflow_outcome: WorkflowOutcome) -> ReadFact:
    match fact_reader:
        case BudgetStatusReader():
            return read_budget_status(fact_reader, workflow_outcome.budget_outcomes)
        case RefundTreatmentReader():
            return read_refund_treatment(fact_reader, workflow_outcome)
        case DuplicateExcludedReader():
            line_outcome: LineOutcome | None = workflow_outcome.find_line(fact_reader.line_number)
            if line_outcome is None:
                return ReadFact("false", f"line {fact_reader.line_number} is not in the workflow's result")
            return ReadFact(str(line_outcome.treatment == TransactionTreatment.EXCLUDED_DUPLICATE.value).lower(), describe_line(line_outcome))
        case LinesCountedReader():
            return read_lines_counted(fact_reader, workflow_outcome)
        case UncategorizedCountReader():
            uncategorized_line_numbers: list[int] = [
                line_outcome.line_number
                for line_outcome in workflow_outcome.line_outcomes
                if line_outcome.treatment in (TransactionTreatment.UNCATEGORIZED_OUTFLOW.value, TransactionTreatment.UNCATEGORIZED_CREDIT.value)
            ]
            return ReadFact(str(len(uncategorized_line_numbers)), f"uncategorized lines {uncategorized_line_numbers}")
        case LargestIncreaseReader():
            return read_largest_increase(fact_reader, workflow_outcome.change_outcomes)


def read_budget_status(budget_status_reader: BudgetStatusReader, budget_outcomes: Sequence[BudgetOutcome]) -> ReadFact:
    budget_outcome: BudgetOutcome | None = next(
        (
            budget_outcome
            for budget_outcome in budget_outcomes
            if (budget_outcome.category, budget_outcome.period) == (budget_status_reader.category, budget_status_reader.period)
        ),
        None,
    )
    if budget_outcome is None:
        return ReadFact("not_stated", f"no budget comparison for {budget_status_reader.category} {budget_status_reader.period}")
    return ReadFact(
        "over" if budget_outcome.actual > budget_outcome.target else "under",
        f"{budget_outcome.category} {budget_outcome.period}: actual {budget_outcome.actual} against target {budget_outcome.target}",
    )


def read_refund_treatment(refund_treatment_reader: RefundTreatmentReader, workflow_outcome: WorkflowOutcome) -> ReadFact:
    line_outcome: LineOutcome | None = workflow_outcome.find_line(refund_treatment_reader.line_number)
    if line_outcome is None:
        return ReadFact("not_mentioned", f"line {refund_treatment_reader.line_number} is not in the workflow's result")
    if line_outcome.treatment != TransactionTreatment.INCLUDED_SPENDING.value or line_outcome.amount is None:
        return ReadFact("excluded", describe_line(line_outcome))
    return ReadFact("netted_against_spending" if line_outcome.amount < 0 else "counted_as_purchase", describe_line(line_outcome))


def read_lines_counted(lines_counted_reader: LinesCountedReader, workflow_outcome: WorkflowOutcome) -> ReadFact:
    line_outcomes: list[LineOutcome | None] = [workflow_outcome.find_line(line_number) for line_number in lines_counted_reader.line_numbers]
    are_all_counted: bool = all(
        line_outcome is not None and line_outcome.treatment == TransactionTreatment.INCLUDED_SPENDING.value for line_outcome in line_outcomes
    )
    evidence: str = "; ".join(
        describe_line(line_outcome) if line_outcome is not None else f"line {line_number} is not in the workflow's result"
        for line_number, line_outcome in zip(lines_counted_reader.line_numbers, line_outcomes, strict=True)
    )
    return ReadFact(str(are_all_counted).lower(), evidence)


def read_largest_increase(largest_increase_reader: LargestIncreaseReader, change_outcomes: Sequence[ChangeOutcome]) -> ReadFact:
    ranked_change_outcomes: list[ChangeOutcome] = sorted(
        (
            change_outcome
            for change_outcome in change_outcomes
            if (change_outcome.from_period, change_outcome.to_period) == (largest_increase_reader.from_period, largest_increase_reader.to_period)
            and change_outcome.change > 0
        ),
        key=lambda change_outcome: -change_outcome.change,
    )
    if not ranked_change_outcomes:
        return ReadFact(None, f"no increase from {largest_increase_reader.from_period} to {largest_increase_reader.to_period}")
    evidence: str = ", ".join(f"{change_outcome.category} {change_outcome.change:+}" for change_outcome in ranked_change_outcomes[:3])
    return ReadFact(ranked_change_outcomes[0].category, f"largest increases: {evidence}")


def is_amount_accepted(actual_amount: Decimal | None, expected_amounts: Iterable[str]) -> bool:
    return actual_amount is not None and any(
        abs(actual_amount - Decimal(expected_amount)) <= AMOUNT_TOLERANCE for expected_amount in expected_amounts
    )


def grade_amount_check(amount_check: AmountCheck, grading_reference: GradingReference, workflow_outcome: WorkflowOutcome) -> CheckGrade:
    expected_amounts: tuple[str, ...] = grading_reference.expected_fact_values_by_fact_name[amount_check.fact_name]
    reference_ledger: ReferenceLedger = grading_reference.reference_ledger
    ledger_bucket: LedgerBucket = reference_ledger.ledger_bucket_by_fact_name[amount_check.fact_name]
    bucket_total: BucketTotal | None = workflow_outcome.find_bucket(ledger_bucket.category, ledger_bucket.period)
    reported_amount: Decimal | None = bucket_total.amount if bucket_total is not None else None
    passed: bool = is_amount_accepted(reported_amount, expected_amounts)
    reason: str = (
        f"{amount_check.description} Expected {' or '.join(expected_amounts)} (within {AMOUNT_TOLERANCE}); "
        f"the workflow reported {reported_amount if reported_amount is not None else 'no total'} for {ledger_bucket.category} {ledger_bucket.period}."
    )
    traced_cause: TracedCause | None = None if passed else trace_bucket_difference(reference_ledger, ledger_bucket, workflow_outcome)
    return CheckGrade(to_check_status(passed=passed), reason, (traced_cause,) if traced_cause is not None else ())


def grade_fact_value_check(fact_value_check: FactValueCheck, grading_reference: GradingReference, workflow_outcome: WorkflowOutcome) -> CheckGrade:
    expected_fact_values: tuple[str, ...] = grading_reference.expected_fact_values_by_fact_name[fact_value_check.fact_name]
    read_fact_value: ReadFact = read_fact(fact_value_check.fact_reader, workflow_outcome)
    passed: bool = read_fact_value.fact_value is not None and read_fact_value.fact_value.lower() in [
        expected_fact_value.lower() for expected_fact_value in expected_fact_values
    ]
    reason: str = (
        f"{fact_value_check.description} Expected {' or '.join(expected_fact_values)} (any case); "
        f"the workflow's result gives {read_fact_value.fact_value} ({read_fact_value.evidence})."
    )
    if passed:
        return CheckGrade(CheckStatus.PASSED, reason)
    return CheckGrade(CheckStatus.FAILED, reason, trace_fact_value_failure(fact_value_check.fact_reader, grading_reference, workflow_outcome))


def trace_fact_value_failure(
    fact_reader: FactReader, grading_reference: GradingReference, workflow_outcome: WorkflowOutcome
) -> tuple[TracedCause, ...]:
    reference_ledger: ReferenceLedger = grading_reference.reference_ledger
    match fact_reader:
        case BudgetStatusReader():
            traced_cause: TracedCause | None = trace_bucket_difference(
                reference_ledger, LedgerBucket(fact_reader.category, fact_reader.period), workflow_outcome
            )
            return (traced_cause,) if traced_cause is not None else ()
        case UncategorizedCountReader():
            return tuple(trace_category_differences(reference_ledger, grading_reference.uncategorized_category, workflow_outcome.line_outcomes))
        case LargestIncreaseReader():
            return trace_period_change_differences(grading_reference, fact_reader, workflow_outcome)
        case RefundTreatmentReader() | DuplicateExcludedReader() | LinesCountedReader():
            # The reason already names each line's amount, treatment and category; that is the cause.
            return ()


def trace_period_change_differences(
    grading_reference: GradingReference, largest_increase_reader: LargestIncreaseReader, workflow_outcome: WorkflowOutcome
) -> tuple[TracedCause, ...]:
    """A wrong largest increase comes from a wrong total in either month; trace every totalled category of both months that
    differs. Uncategorized lines are never in a monthly total, so that category is left out rather than traced as missing."""
    reference_ledger: ReferenceLedger = grading_reference.reference_ledger
    categories: list[str] = [
        category
        for category in dict.fromkeys(ledger_entry.category for ledger_entry in reference_ledger.ledger_entries)
        if category != grading_reference.uncategorized_category
    ]
    traced_causes: list[TracedCause] = []
    for period in (largest_increase_reader.from_period, largest_increase_reader.to_period):
        for category in categories:
            traced_cause: TracedCause | None = trace_bucket_difference(reference_ledger, LedgerBucket(category, period), workflow_outcome)
            if traced_cause is not None:
                traced_causes.append(traced_cause)
    return tuple(traced_causes)


def grade_ledger_category_check(
    ledger_category_check: LedgerCategoryCheck, reference_ledger: ReferenceLedger, workflow_outcome: WorkflowOutcome
) -> CheckGrade:
    reference_line_numbers: list[int] = [
        ledger_entry.line_number
        for ledger_entry in reference_ledger.ledger_entries
        if ledger_entry.is_counted and ledger_entry.category == ledger_category_check.reference_category
    ]
    traced_causes: list[TracedCause] = trace_category_differences(
        reference_ledger, ledger_category_check.reference_category, workflow_outcome.line_outcomes
    )
    reason: str = (
        f"{ledger_category_check.description} Expected exactly the reference's {len(reference_line_numbers)} "
        f"{ledger_category_check.reference_category} lines {reference_line_numbers} in {ledger_category_check.reference_category}; "
        f"{len(traced_causes)} lines differ."
    )
    return CheckGrade(to_check_status(passed=not traced_causes), reason, tuple(traced_causes))


def grade_ledger_lines_check(
    ledger_lines_check: LedgerLinesCheck, reference_ledger: ReferenceLedger, workflow_outcome: WorkflowOutcome
) -> CheckGrade:
    traced_causes: list[TracedCause] = trace_line_differences(reference_ledger, workflow_outcome.line_outcomes)
    reason: str = (
        f"{ledger_lines_check.description} Compared the reference's {len(reference_ledger.ledger_entries)} lines with the "
        f"workflow's {len(workflow_outcome.line_outcomes)}; {len(traced_causes)} lines differ."
    )
    return CheckGrade(to_check_status(passed=not traced_causes), reason, tuple(traced_causes))


def trace_line_differences(reference_ledger: ReferenceLedger, line_outcomes: Sequence[LineOutcome]) -> list[TracedCause]:
    line_outcome_by_line_number: dict[int, LineOutcome] = {line_outcome.line_number: line_outcome for line_outcome in line_outcomes}
    traced_causes: list[TracedCause] = []
    ledger_entry: LedgerEntry
    for ledger_entry in reference_ledger.ledger_entries:
        line_differences: list[str] = describe_line_differences(ledger_entry, line_outcome_by_line_number.get(ledger_entry.line_number))
        if line_differences:
            cause_text: str = f"line {ledger_entry.line_number} {quote_source(ledger_entry.source_text)}: {'; '.join(line_differences)}"
            traced_causes.append(TracedCause(cause_text, f"{cause_text} ({ledger_entry.amount})"))
    reference_line_numbers: set[int] = {ledger_entry.line_number for ledger_entry in reference_ledger.ledger_entries}
    line_outcome: LineOutcome
    for line_outcome in line_outcomes:
        if line_outcome.line_number not in reference_line_numbers:
            cause_text = f"line {line_outcome.line_number} {quote_source(line_outcome.source_text)}: the reference has no expense on that line"
            traced_causes.append(TracedCause(cause_text, f"{cause_text} ({line_outcome.amount})"))
    return traced_causes


def describe_line_differences(ledger_entry: LedgerEntry, line_outcome: LineOutcome | None) -> list[str]:
    """How the workflow's reading of one line differs from the reference's; any accepted alternative category matches."""
    if line_outcome is None:
        return ["the workflow did not report it"]
    line_differences: list[str] = []
    if line_outcome.amount != ledger_entry.amount:
        line_differences.append(f"the workflow read the amount as {line_outcome.amount}; the reference reads {ledger_entry.amount}")
    if line_outcome.period != ledger_entry.period:
        line_differences.append(f"the workflow put it in {line_outcome.period}; the reference puts it in {ledger_entry.period}")
    is_counted: bool = is_counted_line(line_outcome)
    if is_counted != ledger_entry.is_counted:
        line_differences.append(
            "the workflow counted it; the reference does not" if is_counted else "the workflow excluded it; the reference counts it"
        )
    reference_categories: tuple[str, ...] = accepted_categories(ledger_entry)
    if line_outcome.category not in reference_categories:
        line_differences.append(f"the workflow put it in {line_outcome.category}; the reference puts it in {' or '.join(reference_categories)}")
    return line_differences


def grade_budget_targets_check(budget_targets_check: BudgetTargetsCheck, budget_outcomes: Sequence[BudgetOutcome]) -> CheckGrade:
    used_targets: set[tuple[str, Decimal]] = {(budget_outcome.category, budget_outcome.target) for budget_outcome in budget_outcomes}
    expected_targets: set[tuple[str, Decimal]] = set(budget_targets_check.target_by_category.items())
    invented_targets: list[str] = [f"{category} {target}" for category, target in sorted(used_targets - expected_targets)]
    missing_targets: list[str] = [f"{category} {target}" for category, target in sorted(expected_targets - used_targets)]
    reason: str = (
        f"{budget_targets_check.description} Expected targets {sorted(f'{category} {target}' for category, target in expected_targets)}; "
        f"the workflow compared against {sorted(f'{category} {target}' for category, target in used_targets)}"
        f" (invented {invented_targets}, missing {missing_targets})."
    )
    return CheckGrade(to_check_status(passed=not invented_targets and not missing_targets), reason)


def grade_judge_check(judge_check_description: str, judgment_attempt: JudgmentAttempt) -> CheckGrade:
    match judgment_attempt:
        case ErroredJudgment():
            return CheckGrade(CheckStatus.ERRORED, f"the judge did not return a verdict: {judgment_attempt.failure_description}")
        case CompletedJudgment():
            return CheckGrade(to_check_status(passed=judgment_attempt.passed), f"{judge_check_description} The judge said: {judgment_attempt.reason}")


def grade_run(
    graded_checks: Sequence[GradedCheck],
    grading_reference: GradingReference,
    workflow_outcome: WorkflowOutcome,
    judgment_attempt_by_check_name: Mapping[str, JudgmentAttempt],
) -> dict[str, CheckGrade]:
    check_grade_by_check_name: dict[str, CheckGrade] = {}
    for graded_check in graded_checks:
        match graded_check:
            case AmountCheck():
                check_grade_by_check_name[graded_check.check_name] = grade_amount_check(graded_check, grading_reference, workflow_outcome)
            case FactValueCheck():
                check_grade_by_check_name[graded_check.check_name] = grade_fact_value_check(graded_check, grading_reference, workflow_outcome)
            case LedgerCategoryCheck():
                check_grade_by_check_name[graded_check.check_name] = grade_ledger_category_check(
                    graded_check, grading_reference.reference_ledger, workflow_outcome
                )
            case LedgerLinesCheck():
                check_grade_by_check_name[graded_check.check_name] = grade_ledger_lines_check(
                    graded_check, grading_reference.reference_ledger, workflow_outcome
                )
            case BudgetTargetsCheck():
                check_grade_by_check_name[graded_check.check_name] = grade_budget_targets_check(graded_check, workflow_outcome.budget_outcomes)
            case JudgeCheck():
                check_grade_by_check_name[graded_check.check_name] = grade_judge_check(
                    graded_check.description, judgment_attempt_by_check_name[graded_check.check_name]
                )
    return check_grade_by_check_name


def create_errored_grades(graded_checks: Sequence[GradedCheck], failure_description: str) -> dict[str, CheckGrade]:
    return {graded_check.check_name: CheckGrade(CheckStatus.ERRORED, failure_description) for graded_check in graded_checks}


def select_graded_checks(checks: Sequence[EvalSuiteCheck]) -> list[GradedCheck]:
    return [check for check in checks if not isinstance(check, NotApplicableCheck)]


# ----------------------------------------------------------------------------------------------------------
# Assert, part 1d: the judge, one isolated `claude -p` session per judgment
# ----------------------------------------------------------------------------------------------------------


class JudgeSessionFailedException(EvalTechnicalBaseException):
    """The judge session could not start, did not finish, ended in error, or returned no readable verdict."""


@dataclass(frozen=True)
class JudgeSettings:
    executable_path: str
    model: str
    effort: str


def resolve_judge_settings(judge_model: str, judge_effort: str) -> JudgeSettings:
    """Resolve the Claude Code executable once, before any paid run; on Windows a bare name finds only claude.exe, not claude.cmd."""
    executable_path: str | None = shutil.which(JUDGE_EXECUTABLE_NAME)
    if executable_path is None:
        raise JudgeSessionFailedException(f"the judge needs Claude Code, but '{JUDGE_EXECUTABLE_NAME}' was not found on PATH")
    return JudgeSettings(executable_path, judge_model, judge_effort)


def judge(rubric_text: str, answer: str, judge_settings: JudgeSettings) -> CompletedJudgment:
    """LLM-as-judge: a separate tool-less session applies a written rubric and returns pass/fail with a reason."""
    prompt: str = (
        "# Grading task\n\nApply the rubric exactly as written to the answer. Give a one- or two-sentence reason.\n\n"
        f"<rubric>\n{rubric_text}\n</rubric>\n\n<answer>\n{answer}\n</answer>\n"
    )
    command: list[str] = [
        judge_settings.executable_path, "-p",
        "--model", judge_settings.model,
        "--effort", judge_settings.effort,
        "--restricted",               # ignore the user's settings, plugins and hooks
        "--strict-mcp-config",        # no MCP servers
        "--disable-slash-commands",   # no skills
        "--no-session-persistence",   # keep judge sessions out of the user's history
        "--tools", "",
        "--output-format", "json",
        "--json-schema", JUDGE_VERDICT_SCHEMA,
    ]  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="workflow-eval-judge-", ignore_cleanup_errors=True) as sandbox_directory:
        try:
            completed_process: subprocess.CompletedProcess[bytes] = subprocess.run(  # noqa: S603 - fixed flags and eval suite data
                command, input=prompt.encode(TEXT_ENCODING), capture_output=True, cwd=sandbox_directory, timeout=JUDGE_TIMEOUT_SECONDS, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as start_or_timeout_failure:
            raise JudgeSessionFailedException(f"the judge session did not run: {start_or_timeout_failure}") from start_or_timeout_failure
    return read_judge_verdict(completed_process.returncode, completed_process.stdout, completed_process.stderr)


def read_judge_verdict(exit_code: int, standard_output: bytes, standard_error: bytes) -> CompletedJudgment:
    standard_error_tail: str = standard_error.decode(TEXT_ENCODING, errors="replace")[-STANDARD_ERROR_TAIL_LENGTH:]
    # A CLI that fails before writing its result (unknown model, logged out, rejected flag) explains why only on standard error.
    if exit_code != 0 and not standard_output.strip():
        raise JudgeSessionFailedException(f"the judge session failed (exit {exit_code}) without a result: {standard_error_tail}")
    try:
        result_object: JsonObject = parse_json_object(standard_output.decode(TEXT_ENCODING, errors="replace"), "the judge result")
        if exit_code != 0 or read_bool(result_object, "is_error", "the judge result"):
            raise JudgeSessionFailedException(f"the judge session failed (exit {exit_code}): {standard_error_tail}")
        verdict_object: JsonObject = read_object(result_object, "structured_output", "the judge result")
        return CompletedJudgment(
            passed=read_bool(verdict_object, "passed", "the judge verdict"), reason=read_string(verdict_object, "reason", "the judge verdict")
        )
    except InvalidEvalSuiteException as invalid_eval_suite_exception:
        raise JudgeSessionFailedException(
            f"the judge returned an unreadable verdict: {invalid_eval_suite_exception}"
        ) from invalid_eval_suite_exception


def attempt_judgment(rubric_text: str, answer: str, judge_settings: JudgeSettings) -> JudgmentAttempt:
    try:
        return judge(rubric_text, answer, judge_settings)
    except JudgeSessionFailedException as judge_session_failed_exception:
        return ErroredJudgment(str(judge_session_failed_exception))


# ----------------------------------------------------------------------------------------------------------
# Assert, part 2: pass rates per check, the gate, and the scorecard
# ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunGrade:
    """`grading_duration_seconds` is the eval analysis of the run's result: composing the report the judge reads, reading
    every fact, the judge, and the code checks."""

    run_number: int
    check_grade_by_check_name: Mapping[str, CheckGrade]
    grading_duration_seconds: float


@dataclass(frozen=True)
class ScorecardRow:
    check_name: str
    grader: str
    minimum_pass_rate: float
    run_count: int
    passed_count: int
    errored_count: int

    @property
    def pass_rate(self) -> float:
        # An errored check is not a pass, so it stays in the denominator.
        return self.passed_count / self.run_count if self.run_count else 0.0

    @property
    def meets_minimum_pass_rate(self) -> bool:
        return self.pass_rate >= self.minimum_pass_rate


GRADER_NAME_BY_CHECK_TYPE: Final[Mapping[type[CheckDefinition], str]] = {
    AmountCheck: "amount (read from result)",
    FactValueCheck: "fact (read from result)",
    LedgerCategoryCheck: "lines (read from result)",
    LedgerLinesCheck: "every line (read from result)",
    BudgetTargetsCheck: "targets (read from result)",
    JudgeCheck: "judge",
}


def build_scorecard_rows(graded_checks: Sequence[GradedCheck], run_grades: Sequence[RunGrade]) -> list[ScorecardRow]:
    scorecard_rows: list[ScorecardRow] = []
    for graded_check in graded_checks:
        check_statuses: list[CheckStatus] = [run_grade.check_grade_by_check_name[graded_check.check_name].check_status for run_grade in run_grades]
        scorecard_rows.append(
            ScorecardRow(
                check_name=graded_check.check_name,
                grader=GRADER_NAME_BY_CHECK_TYPE[type(graded_check)],
                minimum_pass_rate=graded_check.minimum_pass_rate,
                run_count=len(check_statuses),
                passed_count=check_statuses.count(CheckStatus.PASSED),
                errored_count=check_statuses.count(CheckStatus.ERRORED),
            )
        )
    return scorecard_rows


def format_pass_rate(pass_rate: float) -> str:
    # Floored, so a displayed rate never reads as meeting a minimum the gate says it missed (2/3 shows 66%, not 67%).
    return f"{math.floor(round(pass_rate * 100, 6))}%"


def describe_row_below_minimum(scorecard_row: ScorecardRow) -> str:
    return (
        f"{scorecard_row.check_name}: expected at least {format_pass_rate(scorecard_row.minimum_pass_rate)}, actual "
        f"{format_pass_rate(scorecard_row.pass_rate)} ({scorecard_row.passed_count} of {scorecard_row.run_count} runs passed, "
        f"{scorecard_row.errored_count} errored)"
    )


@dataclass(frozen=True)
class RunTiming:
    """One run's times in seconds. Answer time is the workflow run that produced the result, before any grading started;
    grading time is the eval analysis of that result; total time is the two together."""

    run_number: int
    answer_duration_seconds: float
    grading_duration_seconds: float
    total_duration_seconds: float
    workflow_completed: bool


@dataclass(frozen=True)
class DurationSummary:
    """One kind of time across the runs it covers: the median, the shortest and the longest, in seconds."""

    median_seconds: float
    minimum_seconds: float
    maximum_seconds: float
    measured_run_count: int


@dataclass(frozen=True)
class TimingSummary:
    """The three times summarized over the runs whose workflow completed. A run that errored fails fast and would make a
    broken configuration look quick, so it is left out; its own times stay in the run timings."""

    run_count: int
    answer_duration_summary: DurationSummary | None
    grading_duration_summary: DurationSummary | None
    total_duration_summary: DurationSummary | None


def build_run_timings(trial_activities: Sequence[TrialActivity], run_grades: Sequence[RunGrade]) -> tuple[RunTiming, ...]:
    return tuple(
        RunTiming(
            run_number=run_grade.run_number,
            answer_duration_seconds=trial_activity.duration_seconds,
            grading_duration_seconds=run_grade.grading_duration_seconds,
            total_duration_seconds=trial_activity.duration_seconds + run_grade.grading_duration_seconds,
            workflow_completed=trial_activity.workflow_completed,
        )
        for trial_activity, run_grade in zip(trial_activities, run_grades, strict=True)
    )


def summarize_run_timings(run_timings: Sequence[RunTiming]) -> TimingSummary:
    completed_run_timings: list[RunTiming] = [run_timing for run_timing in run_timings if run_timing.workflow_completed]
    return TimingSummary(
        run_count=len(run_timings),
        answer_duration_summary=summarize_durations([run_timing.answer_duration_seconds for run_timing in completed_run_timings]),
        grading_duration_summary=summarize_durations([run_timing.grading_duration_seconds for run_timing in completed_run_timings]),
        total_duration_summary=summarize_durations([run_timing.total_duration_seconds for run_timing in completed_run_timings]),
    )


def summarize_durations(durations_seconds: Sequence[float]) -> DurationSummary | None:
    if not durations_seconds:
        return None
    return DurationSummary(statistics.median(durations_seconds), min(durations_seconds), max(durations_seconds), len(durations_seconds))


def format_duration(duration_seconds: float) -> str:
    if duration_seconds < TENTHS_SHOWN_BELOW_SECONDS:
        return f"{duration_seconds:.1f}s"
    whole_seconds: int = round(duration_seconds)
    if whole_seconds < SECONDS_PER_MINUTE:
        return f"{whole_seconds}s"
    if whole_seconds < SECONDS_PER_HOUR:
        return f"{whole_seconds // SECONDS_PER_MINUTE}m {whole_seconds % SECONDS_PER_MINUTE:02d}s"
    return f"{whole_seconds // SECONDS_PER_HOUR}h {whole_seconds % SECONDS_PER_HOUR // SECONDS_PER_MINUTE:02d}m"


def format_duration_summary(duration_summary: DurationSummary | None, run_count: int) -> str:
    if duration_summary is None:
        return NOT_RECORDED_TEXT
    summary_text: str = (
        f"{format_duration(duration_summary.median_seconds)} "
        f"({format_duration(duration_summary.minimum_seconds)} to {format_duration(duration_summary.maximum_seconds)})"
    )
    if duration_summary.measured_run_count < run_count:
        summary_text += f", {duration_summary.measured_run_count} of {run_count} runs"
    return summary_text


def format_timing_cells(timing_summary: TimingSummary) -> tuple[str, str, str]:
    return (
        format_duration_summary(timing_summary.answer_duration_summary, timing_summary.run_count),
        format_duration_summary(timing_summary.grading_duration_summary, timing_summary.run_count),
        format_duration_summary(timing_summary.total_duration_summary, timing_summary.run_count),
    )


TIMING_EXPLANATION: Final[str] = (
    "Answer time is the workflow run that produced the result, before any grading started. Grading time is the eval analysis of "
    "that result: composing the report the judge reads, reading every fact, the judge, and the code checks. Total time is the two "
    "added together. Each time is the median per run, then the shortest to the longest run. Runs whose workflow errored are "
    "left out of these times; a cell then says how many runs it covers."
)
ANSWER_TIME_HEADING: Final[str] = "Answer time (workflow run)"
GRADING_TIME_HEADING: Final[str] = "Grading time (eval analysis)"
TOTAL_TIME_HEADING: Final[str] = "Total time"


@dataclass(frozen=True)
class ScorecardHeader:
    eval_suite_name: str
    run_identity: str
    model_steps: tuple[ModelStep, ...]
    judge_model: str
    judge_effort: str
    repetitions: int
    trial_activities: tuple[TrialActivity, ...] = field(default=())


def list_failure_causes(run_grades: Sequence[RunGrade]) -> list[str]:
    # The summary carries no run-specific amounts, so one mistake made in several runs is listed once with every run that made it.
    occurrences_by_summary: dict[str, list[str]] = {}
    check_name: str
    check_grade: CheckGrade
    for run_grade in run_grades:
        for check_name, check_grade in run_grade.check_grade_by_check_name.items():
            for traced_cause in check_grade.causes:
                occurrences_by_summary.setdefault(traced_cause.summary, []).append(f"run {run_grade.run_number} / {check_name}")
    return [
        f"- {summary} · {len(occurrences)} check runs: {'; '.join(occurrences)}"
        for summary, occurrences in sorted(occurrences_by_summary.items(), key=lambda summary_and_occurrences: -len(summary_and_occurrences[1]))
    ]


def list_check_grades(run_grades: Sequence[RunGrade], check_status: CheckStatus) -> list[str]:
    return [
        f"- run {run_grade.run_number} / {check_name}: {check_grade.reason}"
        + "".join(f" Cause: {traced_cause.description}" for traced_cause in check_grade.causes)
        for run_grade in run_grades
        for check_name, check_grade in run_grade.check_grade_by_check_name.items()
        if check_grade.check_status == check_status
    ]


def render_scorecard_report(
    scorecard_header: ScorecardHeader, checks: Sequence[EvalSuiteCheck], scorecard_rows: Sequence[ScorecardRow], run_grades: Sequence[RunGrade]
) -> str:
    rows_below_minimum: list[ScorecardRow] = [scorecard_row for scorecard_row in scorecard_rows if not scorecard_row.meets_minimum_pass_rate]
    gate_line: str = (
        f"Gate: PASSED, all {len(scorecard_rows)} graded checks meet their minimum pass rate."
        if not rows_below_minimum
        else f"Gate: FAILED, {len(rows_below_minimum)} of {len(scorecard_rows)} graded checks are below their minimum pass rate."
    )
    model_step_lines: list[str] = [
        f"| {model_step.step_name} | {model_step.provider} | {model_step.model} | {model_step.reasoning_effort} |"
        for model_step in scorecard_header.model_steps
    ]
    table_lines: list[str] = [
        (
            f"| {scorecard_row.check_name} | {scorecard_row.grader} | {format_pass_rate(scorecard_row.minimum_pass_rate)} | "
            f"{scorecard_row.passed_count}/{scorecard_row.run_count} ({format_pass_rate(scorecard_row.pass_rate)})"
        )
        + (f", {scorecard_row.errored_count} errored" if scorecard_row.errored_count else "")
        + " |"
        for scorecard_row in scorecard_rows
    ]
    run_timings: tuple[RunTiming, ...] = build_run_timings(scorecard_header.trial_activities, run_grades)
    activity_lines: list[str] = [
        (
            f"- run {run_timing.run_number}: answer {format_duration(run_timing.answer_duration_seconds)}, grading "
            f"{format_duration(run_timing.grading_duration_seconds)}, total {format_duration(run_timing.total_duration_seconds)}; "
            f"{trial_activity.jev_request_count} Jev requests; LLM steps: "
        )
        + ("; ".join(trial_activity.llm_step_events) or "none")
        for run_timing, trial_activity in zip(run_timings, scorecard_header.trial_activities, strict=True)
    ]
    timing_line: str = " | ".join(("", str(len(run_timings)), *format_timing_cells(summarize_run_timings(run_timings)), "")).strip()
    failure_cause_lines: list[str] = list_failure_causes(run_grades)
    failed_check_lines: list[str] = list_check_grades(run_grades, CheckStatus.FAILED)
    errored_check_lines: list[str] = list_check_grades(run_grades, CheckStatus.ERRORED)
    report_lines: list[str] = [
        f"# Scorecard: {scorecard_header.eval_suite_name}",
        "",
        (
            f"Run `{scorecard_header.run_identity}`: {scorecard_header.repetitions} isolated runs of the workflow; "
            f"judge {scorecard_header.judge_model} at {scorecard_header.judge_effort} effort."
        ),
        "",
        "| model step | provider | model | reasoning effort |",
        "|---|---|---|---|",
        *model_step_lines,
        "",
        "Time per run:",
        "",
        f"| runs | {ANSWER_TIME_HEADING} | {GRADING_TIME_HEADING} | {TOTAL_TIME_HEADING} |",
        "|---|---|---|---|",
        timing_line,
        "",
        TIMING_EXPLANATION,
        "",
        "What each run did:",
        *activity_lines,
        "",
        gate_line,
        "",
        "| check | graded by | minimum | passed |",
        "|---|---|---|---|",
        *table_lines,
        "",
        f"Failure causes traced in the reference ledger ({len(failure_cause_lines)}); one cause in several runs is a systematic mistake:",
        *failure_cause_lines,
        "",
        f"Checks that did not pass ({len(failed_check_lines)}):",
        *failed_check_lines,
        "",
        f"Checks that errored ({len(errored_check_lines)}); each counts as not passed and is not listed above:",
        *errored_check_lines,
        "",
        "Checks carried over with an adaptation:",
        *(f"- {check.check_name}: {check.adaptation}" for check in checks if check.adaptation is not None),
        "",
        "Checks that do not apply to a code-controlled workflow (left out of the pass rates):",
        *(f"- {check.check_name}: {check.not_applicable_reason}" for check in checks if isinstance(check, NotApplicableCheck)),
    ]
    return "\n".join(report_lines) + "\n"


@dataclass(frozen=True)
class ScorecardCheckResult:
    check_name: str
    grader: str
    minimum_pass_rate: float
    run_count: int
    passed_count: int
    errored_count: int
    pass_rate: float
    meets_minimum_pass_rate: bool


@dataclass(frozen=True)
class ScorecardDocument:
    """scorecard.json: the same numbers as scorecard.md, for comparing configurations across runs."""

    scorecard_header: ScorecardHeader
    gate_passed: bool
    checks: tuple[ScorecardCheckResult, ...]
    failure_causes: tuple[str, ...]
    run_timings: tuple[RunTiming, ...]
    timing_summary: TimingSummary


def build_scorecard_document(
    scorecard_header: ScorecardHeader, scorecard_rows: Sequence[ScorecardRow], run_grades: Sequence[RunGrade]
) -> ScorecardDocument:
    run_timings: tuple[RunTiming, ...] = build_run_timings(scorecard_header.trial_activities, run_grades)
    return ScorecardDocument(
        scorecard_header=scorecard_header,
        gate_passed=all(scorecard_row.meets_minimum_pass_rate for scorecard_row in scorecard_rows),
        checks=tuple(
            ScorecardCheckResult(
                check_name=scorecard_row.check_name,
                grader=scorecard_row.grader,
                minimum_pass_rate=scorecard_row.minimum_pass_rate,
                run_count=scorecard_row.run_count,
                passed_count=scorecard_row.passed_count,
                errored_count=scorecard_row.errored_count,
                pass_rate=scorecard_row.pass_rate,
                meets_minimum_pass_rate=scorecard_row.meets_minimum_pass_rate,
            )
            for scorecard_row in scorecard_rows
        ),
        failure_causes=tuple(list_failure_causes(run_grades)),
        run_timings=run_timings,
        timing_summary=summarize_run_timings(run_timings),
    )


# ----------------------------------------------------------------------------------------------------------
# Comparison: the newest run of every configuration, from the scorecard.json files in the results folder
# ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigurationResult:
    """One run folder's result, for the comparison table. A summary is None when that run recorded no such time."""

    run_folder_name: str
    llm_provider: str
    llm_models: str
    jev_model: str
    run_count: int
    gate_passed: bool
    checks_below_minimum: tuple[str, ...]
    errored_check_count: int
    timing_summary: TimingSummary

    @property
    def configuration_key(self) -> tuple[str, str, str]:
        return (self.llm_provider, self.llm_models, self.jev_model)


def read_latest_configuration_results(results_directory: Path) -> list[ConfigurationResult]:
    """Run folder names start with their UTC start time, so name order is start order and the last one read is the newest."""
    latest_result_by_configuration_key: dict[tuple[str, str, str], ConfigurationResult] = {}
    for scorecard_path in sorted(results_directory.glob(f"*/{SCORECARD_JSON_FILE_NAME}")):
        configuration_result: ConfigurationResult = read_configuration_result(scorecard_path)
        latest_result_by_configuration_key[configuration_result.configuration_key] = configuration_result
    return sorted(latest_result_by_configuration_key.values(), key=lambda configuration_result: configuration_result.configuration_key)


class ScorecardUnreadableException(EvalTechnicalBaseException):
    """A scorecard the eval wrote cannot be read, is not valid JSON, or lacks a value the comparison needs."""


def read_configuration_result(scorecard_path: Path) -> ConfigurationResult:
    document_name: str = str(scorecard_path)
    try:
        return to_configuration_result(parse_json_object(scorecard_path.read_text(encoding=TEXT_ENCODING), document_name), scorecard_path)
    except (OSError, UnicodeDecodeError) as unreadable_scorecard:
        raise ScorecardUnreadableException(f"{document_name} cannot be read: {unreadable_scorecard}") from unreadable_scorecard
    except InvalidEvalSuiteException as invalid_eval_suite_exception:
        # The JSON readers are shared with the eval suite, so they report a malformed scorecard as an invalid suite file.
        raise ScorecardUnreadableException(f"not a readable scorecard: {invalid_eval_suite_exception}") from invalid_eval_suite_exception


def to_configuration_result(scorecard_object: JsonObject, scorecard_path: Path) -> ConfigurationResult:
    document_name: str = str(scorecard_path)
    scorecard_header: JsonObject = read_object(scorecard_object, "scorecard_header", document_name)
    model_steps: tuple[JsonObject, ...] = read_object_list(scorecard_header, "model_steps", document_name)
    llm_model_steps: list[JsonObject] = [
        model_step for model_step in model_steps if read_string(model_step, "reasoning_effort", document_name) != JEV_REASONING_EFFORT
    ]
    jev_models: list[str] = list(
        dict.fromkeys(
            read_string(model_step, "model", document_name)
            for model_step in model_steps
            if read_string(model_step, "reasoning_effort", document_name) == JEV_REASONING_EFFORT
        )
    )
    check_results: tuple[JsonObject, ...] = read_object_list(scorecard_object, "checks", document_name)
    return ConfigurationResult(
        run_folder_name=scorecard_path.parent.name,
        llm_provider=", ".join(dict.fromkeys(read_string(model_step, "provider", document_name) for model_step in llm_model_steps)) or "none",
        llm_models=", ".join(
            dict.fromkeys(
                f"{read_string(model_step, 'model', document_name)} ({read_string(model_step, 'reasoning_effort', document_name)})"
                for model_step in llm_model_steps
            )
        )
        or "none",
        jev_model=", ".join(jev_models) or "none",
        run_count=read_whole_number(scorecard_header, "repetitions", document_name),
        gate_passed=read_bool(scorecard_object, "gate_passed", document_name),
        checks_below_minimum=tuple(
            describe_check_below_minimum(check_result, document_name)
            for check_result in check_results
            if not read_bool(check_result, "meets_minimum_pass_rate", document_name)
        ),
        errored_check_count=sum(read_whole_number(check_result, "errored_count", document_name) for check_result in check_results),
        timing_summary=read_timing_summary(scorecard_object, scorecard_header, document_name),
    )


def describe_check_below_minimum(check_result: JsonObject, document_name: str) -> str:
    check_name: str = read_string(check_result, "check_name", document_name)
    passed_count: int = read_whole_number(check_result, "passed_count", document_name)
    run_count: int = read_whole_number(check_result, "run_count", document_name)
    return f"{check_name} {passed_count}/{run_count}"


def read_timing_summary(scorecard_object: JsonObject, scorecard_header: JsonObject, document_name: str) -> TimingSummary:
    if "timing_summary" in scorecard_object:
        summary_object: JsonObject = read_object(scorecard_object, "timing_summary", document_name)
        return TimingSummary(
            run_count=read_whole_number(summary_object, "run_count", document_name),
            answer_duration_summary=read_duration_summary(summary_object, "answer_duration_summary", document_name),
            grading_duration_summary=read_duration_summary(summary_object, "grading_duration_summary", document_name),
            total_duration_summary=read_duration_summary(summary_object, "total_duration_summary", document_name),
        )
    # Scorecards written before timing was summarized hold each run's workflow time only, and not whether it completed.
    trial_activities: tuple[JsonObject, ...] = read_object_list(scorecard_header, "trial_activities", document_name)
    return TimingSummary(
        run_count=len(trial_activities),
        answer_duration_summary=summarize_durations(
            [read_seconds(trial_activity, "duration_seconds", document_name) for trial_activity in trial_activities]
        ),
        grading_duration_summary=None,
        total_duration_summary=None,
    )


def read_duration_summary(json_object: JsonObject, field_name: str, document_name: str) -> DurationSummary | None:
    if json_object.get(field_name) is None:
        return None
    summary_object: JsonObject = read_object(json_object, field_name, document_name)
    return DurationSummary(
        read_seconds(summary_object, "median_seconds", document_name),
        read_seconds(summary_object, "minimum_seconds", document_name),
        read_seconds(summary_object, "maximum_seconds", document_name),
        read_whole_number(summary_object, "measured_run_count", document_name),
    )


def read_seconds(json_object: JsonObject, field_name: str, document_name: str) -> float:
    field_value: object = read_field(json_object, field_name, document_name)
    # bool is a subclass of int in Python, so true would otherwise pass as 1.
    # NaN fails the comparison, and infinity is not a duration.
    if isinstance(field_value, bool) or not isinstance(field_value, int | float) or not 0 <= field_value < math.inf:
        raise InvalidEvalSuiteException(f"{document_name}: '{field_name}' must be a finite number of seconds of zero or more, not {field_value!r}")
    return float(field_value)


def render_scorecard_comparison(eval_suite_name: str, configuration_results: Sequence[ConfigurationResult]) -> str:
    result_lines: list[str] = [
        " | ".join(
            (
                "",
                configuration_result.llm_provider,
                configuration_result.llm_models,
                configuration_result.jev_model,
                str(configuration_result.run_count),
                "passed" if configuration_result.gate_passed else "failed",
                ", ".join(configuration_result.checks_below_minimum) or "none",
                str(configuration_result.errored_check_count),
                *format_timing_cells(configuration_result.timing_summary),
                f"`{configuration_result.run_folder_name}`",
                "",
            )
        ).strip()
        for configuration_result in configuration_results
    ]
    report_lines: list[str] = [
        f"# Scorecard comparison: {eval_suite_name}",
        "",
        (
            f"Generated from each run folder's {SCORECARD_JSON_FILE_NAME}; for every configuration of LLM steps and Jev model only the "
            "newest run is shown. Runs recorded before grading was timed show their answer time only."
        ),
        "",
        TIMING_EXPLANATION,
        "",
        (
            f"| LLM steps | model (effort) | Jev model | runs | gate | checks below minimum | errored checks | {ANSWER_TIME_HEADING} | "
            f"{GRADING_TIME_HEADING} | {TOTAL_TIME_HEADING} | run folder |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *result_lines,
    ]
    if not configuration_results:
        report_lines.append(f"No run folder holds a {SCORECARD_JSON_FILE_NAME}.")
    return "\n".join(report_lines) + "\n"


# ----------------------------------------------------------------------------------------------------------
# Recorded artifacts: everything written for a run passes through one scrub, so no Windows user name is published
# ----------------------------------------------------------------------------------------------------------


USER_NAME_PLACEHOLDER: Final[str] = "<user>"
# A user name is scrubbed only where it names an account folder, in any path or JSON escaping, so expense text such as
# "Venmo - Sam" survives for a user named sam.
ACCOUNT_FOLDER_PREFIX_PATTERN: Final[str] = r"([\\/](?:users|home)[\\/]+)"
ACCOUNT_FOLDER_END_PATTERN: Final[str] = r"(?=$|[\\/\"'\s])"


def scrub_user_name(artifact_text: str, user_name: str | None) -> str:
    if not user_name:
        return artifact_text
    account_folder_pattern: str = ACCOUNT_FOLDER_PREFIX_PATTERN + re.escape(user_name) + ACCOUNT_FOLDER_END_PATTERN
    return re.sub(account_folder_pattern, lambda match: match.group(1) + USER_NAME_PLACEHOLDER, artifact_text, flags=re.IGNORECASE)


def write_artifact(artifact_path: Path, artifact_text: str) -> None:
    # The home folder's name is what appears in recorded paths, which is what must not be published.
    artifact_path.write_text(scrub_user_name(artifact_text, Path.home().name), encoding=TEXT_ENCODING)


def to_json_text(document: object) -> str:
    # Decimal and date values are written as their exact text.
    return json.dumps(document, indent=2, default=str)


def report_progress(progress_message: str) -> None:
    with contextlib.suppress(UnicodeEncodeError):
        print(progress_message, flush=True)  # noqa: T201 - step-boundary progress, visible with `pytest -s`


def compose_answer(expense_analysis_result: ExpenseAnalysisResult) -> str:
    """The production report for this result, exactly as the console app renders it; the judge reads this."""
    return ComposerExpenseAnalysisReport.compose(expense_analysis_result)
