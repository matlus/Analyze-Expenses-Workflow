"""The eval: run the real workflow N times in isolation, grade every run, and gate each check on its minimum pass rate.

Run it with:  uv run pytest evals/workflow -s --eval-repetitions 1
Swap the models:  --workflow-subscription CLAUDE_CODE --workflow-model claude-opus-5-5 --workflow-effort medium
"""

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import cast

import pytest

from analyze_expenses_workflow.domain_facades import DomainFacade, ExpenseAnalysisResult
from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from expense_analysis_workflow_eval import (
    EVAL_SUITE_DIRECTORY,
    RESULTS_DIRECTORY,
    CheckGrade,
    CheckStatus,
    CompletedTrial,
    ErroredTrial,
    EvalSuite,
    GradedCheck,
    JudgeCheck,
    JudgeSettings,
    JudgmentAttempt,
    ModelStep,
    RunGrade,
    ScorecardHeader,
    ScorecardRow,
    ServiceLocatorEval,
    SettingOverrideRequest,
    TrialActivity,
    TrialAttempt,
    TrialMonitor,
    WorkflowOutcome,
    attempt_judgment,
    attempt_trial,
    build_scorecard_document,
    build_scorecard_rows,
    build_setting_overrides,
    capture_llm_step_events,
    compose_answer,
    compose_run_label,
    create_errored_grades,
    create_run_directory,
    describe_model_steps,
    describe_row_below_minimum,
    grade_run,
    load_eval_suite,
    parse_setting_assignments,
    project_workflow_outcome,
    render_scorecard_report,
    report_progress,
    resolve_judge_settings,
    select_graded_checks,
    select_model_steps,
    to_json_text,
    write_artifact,
)

SCORECARD_FILE_NAME: str = "scorecard.md"


@pytest.mark.workflow_eval
def test_ExpenseAnalysisWorkflow_WhenRunRepeatedlyInIsolation_ThenEveryGradedCheckMeetsItsMinimumPassRate(pytestconfig: pytest.Config) -> None:
    # Arrange: the suite, and the run's configuration and judge resolved and validated before any paid run starts.
    eval_suite: EvalSuite = load_eval_suite(EVAL_SUITE_DIRECTORY)
    subscription_value: str | None = cast("str | None", pytestconfig.getoption("--workflow-subscription"))
    setting_value_by_name: dict[str, str] = build_setting_overrides(
        SettingOverrideRequest(
            case_setting_value_by_name=eval_suite.eval_case.setting_value_by_name,
            model_step_selection=select_model_steps(
                subscription_value,
                cast("str | None", pytestconfig.getoption("--workflow-model")),
                cast("str | None", pytestconfig.getoption("--workflow-effort")),
            ),
            extra_setting_value_by_name=parse_setting_assignments(cast("list[str]", pytestconfig.getoption("--workflow-setting"))),
        )
    )
    model_steps: tuple[ModelStep, ...] = describe_model_steps(ConfigurationProvider(setting_value_by_name))
    judge_settings: JudgeSettings = resolve_judge_settings(
        str(pytestconfig.getoption("--eval-judge-model")), str(pytestconfig.getoption("--eval-judge-effort"))
    )
    repetitions: int = int(str(pytestconfig.getoption("--eval-repetitions")))
    input_line_offset: int = eval_suite.eval_case.first_expense_line_number - 1
    expense_lines: list[str] = list(eval_suite.eval_case.expense_lines)
    confirmed_duplicate_line_numbers: tuple[int, ...] = tuple(
        eval_suite.eval_case.to_workflow_line_number(line_number) for line_number in eval_suite.eval_case.confirmed_duplicate_line_numbers
    )
    graded_checks: list[GradedCheck] = select_graded_checks(eval_suite.checks)
    judge_checks: list[JudgeCheck] = [graded_check for graded_check in graded_checks if isinstance(graded_check, JudgeCheck)]
    run_directory: Path = create_run_directory(RESULTS_DIRECTORY, compose_run_label(model_steps))
    report_progress(f"eval suite '{eval_suite.eval_suite_name}': {repetitions} runs, results in {run_directory.name}")
    for model_step in model_steps:
        report_progress(f"  {model_step.step_name}: {model_step.provider} {model_step.model} ({model_step.reasoning_effort})")

    run_grades: list[RunGrade] = []
    trial_activities: list[TrialActivity] = []
    for run_number in range(1, repetitions + 1):
        run_label: str = f"run {run_number}/{repetitions}"
        report_progress(f"{run_label}: workflow starting")
        # A new service locator and Domain Facade per run, so no gateway, client or session is shared between runs.
        service_locator_eval: ServiceLocatorEval = ServiceLocatorEval(setting_value_by_name)

        # Act: one isolated run of the real workflow through its public entry point.
        async def analyze_expenses_in_isolation(service_locator_eval: ServiceLocatorEval = service_locator_eval) -> ExpenseAnalysisResult:
            async with DomainFacade(service_locator_eval) as domain_facade:
                return await domain_facade.analyze_expenses(expense_lines, confirmed_duplicate_line_numbers=confirmed_duplicate_line_numbers)

        with capture_llm_step_events() as llm_step_event_handler:
            trial_monitor: TrialMonitor = TrialMonitor(
                time.perf_counter(), service_locator_eval.jev_request_counting_transport, llm_step_event_handler
            )
            trial_attempt: TrialAttempt = asyncio.run(attempt_trial(analyze_expenses_in_isolation(), trial_monitor))

        # Assert, part 1: grade this run from its structured result, and judge what needs judgment.
        run_folder: Path = run_directory / "runs" / f"run-{run_number:02d}"
        run_folder.mkdir(parents=True)
        trial_activities.append(trial_attempt.trial_activity)
        write_artifact(run_folder / "activity.json", to_json_text(asdict(trial_attempt.trial_activity)))
        # Grading time starts once the workflow has returned, so it never counts toward the answer time.
        grading_started_at: float = time.perf_counter()
        check_grade_by_check_name: dict[str, CheckGrade]
        match trial_attempt:
            case ErroredTrial():
                workflow_failure: str = f"the workflow did not complete: {trial_attempt.failure_description}"
                check_grade_by_check_name = create_errored_grades(graded_checks, workflow_failure)
                write_artifact(run_folder / "failure.txt", trial_attempt.failure_description)
            case CompletedTrial():
                answer: str = compose_answer(trial_attempt.expense_analysis_result)
                workflow_outcome: WorkflowOutcome = project_workflow_outcome(
                    trial_attempt.expense_analysis_result, input_line_offset, eval_suite.reference_category_by_workflow_category
                )
                write_artifact(run_folder / "answer.md", answer)
                write_artifact(run_folder / "outcome.json", to_json_text(asdict(workflow_outcome)))
                report_progress(f"{run_label}: workflow finished in {trial_attempt.trial_activity.duration_seconds:.0f}s, judging")
                judgment_attempt_by_check_name: dict[str, JudgmentAttempt] = {
                    judge_check.check_name: attempt_judgment(judge_check.rubric_text, answer, judge_settings) for judge_check in judge_checks
                }
                check_grade_by_check_name = grade_run(graded_checks, eval_suite.grading_reference, workflow_outcome, judgment_attempt_by_check_name)
        grading_duration_seconds: float = time.perf_counter() - grading_started_at
        write_artifact(run_folder / "grade.json", json.dumps({name: asdict(grade) for name, grade in check_grade_by_check_name.items()}, indent=2))
        run_grades.append(RunGrade(run_number, check_grade_by_check_name, grading_duration_seconds))
        check_statuses: list[CheckStatus] = [check_grade.check_status for check_grade in check_grade_by_check_name.values()]
        graded_progress_message: str = (
            f"{run_label}: {check_statuses.count(CheckStatus.PASSED)} passed, {check_statuses.count(CheckStatus.FAILED)} failed, "
            f"{check_statuses.count(CheckStatus.ERRORED)} errored; graded in {grading_duration_seconds:.0f}s"
        )
        report_progress(graded_progress_message)

    # Assert, part 2: pass rates against each check's minimum.
    actual_scorecard_rows: list[ScorecardRow] = build_scorecard_rows(graded_checks, run_grades)
    scorecard_header: ScorecardHeader = ScorecardHeader(
        eval_suite.eval_suite_name, run_directory.name, model_steps, judge_settings.model, judge_settings.effort, repetitions, tuple(trial_activities)
    )
    scorecard_report: str = render_scorecard_report(scorecard_header, eval_suite.checks, actual_scorecard_rows, run_grades)
    write_artifact(run_directory / SCORECARD_FILE_NAME, scorecard_report)
    write_artifact(
        run_directory / "scorecard.json", to_json_text(asdict(build_scorecard_document(scorecard_header, actual_scorecard_rows, run_grades)))
    )
    report_progress(scorecard_report)

    actual_scorecard_rows_below_minimum: list[ScorecardRow] = [
        scorecard_row for scorecard_row in actual_scorecard_rows if not scorecard_row.meets_minimum_pass_rate
    ]
    gate_failure_heading: str = (
        f"{len(actual_scorecard_rows_below_minimum)} of {len(actual_scorecard_rows)} graded checks are below their minimum pass rate "
        f"(see {run_directory.name}/{SCORECARD_FILE_NAME} for each cause):"
    )
    assert not actual_scorecard_rows_below_minimum, "\n".join(
        [gate_failure_heading, *map(describe_row_below_minimum, actual_scorecard_rows_below_minimum)]
    )
