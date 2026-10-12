import json
import secrets
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from expense_analysis_workflow_eval import (
    AmountCheck,
    CheckGrade,
    CheckStatus,
    ConfigurationResult,
    DurationSummary,
    EvalSuiteCheck,
    GradedCheck,
    ModelStep,
    NotApplicableCheck,
    RunGrade,
    ScorecardDocument,
    ScorecardHeader,
    ScorecardRow,
    TracedCause,
    TrialActivity,
    build_scorecard_document,
    build_scorecard_rows,
    format_duration,
    format_pass_rate,
    read_latest_configuration_results,
    render_scorecard_comparison,
    render_scorecard_report,
    scrub_user_name,
    to_json_text,
)

MINIMUM_PASS_RATE: float = 0.8


def create_random_text(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def create_check_name() -> str:
    return create_random_text("check")


def create_amount_check(adaptation: str | None = None) -> AmountCheck:
    return AmountCheck(create_check_name(), create_random_text("description"), MINIMUM_PASS_RATE, adaptation, fact_name="total")


def create_grading_duration_seconds() -> float:
    return (secrets.randbelow(30_000) + 100) / 1000


def create_run_grades(graded_check: GradedCheck, check_statuses: Sequence[CheckStatus], traced_cause: TracedCause | None = None) -> list[RunGrade]:
    run_grades: list[RunGrade] = []
    run_number: int
    check_status: CheckStatus
    for run_number, check_status in enumerate(check_statuses, start=1):
        traced_causes: tuple[TracedCause, ...] = (traced_cause,) if traced_cause is not None and check_status == CheckStatus.FAILED else ()
        check_grade: CheckGrade = CheckGrade(check_status, create_random_text("reason"), traced_causes)
        run_grades.append(RunGrade(run_number, {graded_check.check_name: check_grade}, create_grading_duration_seconds()))
    return run_grades


def create_trial_activity(*, workflow_completed: bool = True) -> TrialActivity:
    return TrialActivity(float(secrets.randbelow(60) + 1), secrets.randbelow(400) + 1, (create_random_text("progress: step"),), workflow_completed)


def create_scorecard_header(trial_activities: Sequence[TrialActivity], llm_model: str | None = None, jev_model: str | None = None) -> ScorecardHeader:
    return ScorecardHeader(
        eval_suite_name=create_random_text("suite"),
        run_identity=create_random_text("run"),
        model_steps=(
            ModelStep("transaction_categorization", "OpenRouter (Jev)", jev_model or create_random_text("jev"), "not supported"),
            ModelStep("expense_findings", "CLAUDE_CODE (ANTHROPIC)", llm_model or create_random_text("claude"), "medium"),
        ),
        judge_model=create_random_text("judge"),
        judge_effort="low",
        repetitions=len(trial_activities),
        trial_activities=tuple(trial_activities),
    )


def assert_report_lines(scenario: str, actual_report: str, expected_report_lines: Sequence[str]) -> None:
    missing_lines: list[str] = [expected_line for expected_line in expected_report_lines if expected_line not in actual_report.splitlines()]
    assert not missing_lines, (
        f"{scenario}: the scorecard report is missing {len(missing_lines)} of {len(expected_report_lines)} expected lines:\n"
        + "\n".join(missing_lines)
        + f"\n--- report ---\n{actual_report}"
    )


def test_ScorecardRow_WhenFourOfFiveRunsPass_ThenMeetsAnEightyPercentMinimum() -> None:
    expected_passed_count: int = 4
    expected_run_count: int = 5
    amount_check: AmountCheck = create_amount_check()
    run_grades: list[RunGrade] = create_run_grades(
        amount_check, [CheckStatus.PASSED] * expected_passed_count + [CheckStatus.FAILED] * (expected_run_count - expected_passed_count)
    )

    actual_scorecard_rows: list[ScorecardRow] = build_scorecard_rows([amount_check], run_grades)

    assert (actual_scorecard_rows[0].passed_count, actual_scorecard_rows[0].run_count, actual_scorecard_rows[0].meets_minimum_pass_rate) == (
        expected_passed_count,
        expected_run_count,
        True,
    )


def test_ScorecardRow_WhenARunErrors_ThenTheErrorCountsAgainstThePassRate() -> None:
    expected_passed_count: int = 3
    expected_errored_count: int = 1
    expected_pass_rate: float = 0.6
    amount_check: AmountCheck = create_amount_check()
    run_grades: list[RunGrade] = create_run_grades(
        amount_check, [CheckStatus.PASSED] * expected_passed_count + [CheckStatus.ERRORED] * expected_errored_count + [CheckStatus.FAILED]
    )

    actual_scorecard_rows: list[ScorecardRow] = build_scorecard_rows([amount_check], run_grades)

    assert (
        actual_scorecard_rows[0].passed_count,
        actual_scorecard_rows[0].errored_count,
        actual_scorecard_rows[0].pass_rate,
        actual_scorecard_rows[0].meets_minimum_pass_rate,
    ) == (expected_passed_count, expected_errored_count, expected_pass_rate, False)


def test_PassRateText_WhenTwoOfThreeRunsPass_ThenFloorsSoItCannotReadAsMeetingAHigherMinimum() -> None:
    expected_percentage_text: str = "66%"

    actual_percentage_text: str = format_pass_rate(2 / 3)

    assert actual_percentage_text == expected_percentage_text


def test_ScorecardReport_WhenOneCauseRecursAcrossRuns_ThenListsItOnceWithEveryRunAndFailsTheGate() -> None:
    amount_check: AmountCheck = create_amount_check()
    traced_cause: TracedCause = TracedCause(f"Shopping 2026-07: line 95 put in Other {secrets.token_hex(4)}", "with amounts")
    run_grades: list[RunGrade] = create_run_grades(amount_check, [CheckStatus.FAILED, CheckStatus.FAILED, CheckStatus.PASSED], traced_cause)
    trial_activities: list[TrialActivity] = [create_trial_activity() for _ in run_grades]
    scorecard_rows: list[ScorecardRow] = build_scorecard_rows([amount_check], run_grades)
    expected_report_lines: list[str] = [
        "Gate: FAILED, 1 of 1 graded checks are below their minimum pass rate.",
        f"| {amount_check.check_name} | amount (read from result) | 80% | 1/3 (33%) |",
        f"- {traced_cause.summary} · 2 check runs: run 1 / {amount_check.check_name}; run 2 / {amount_check.check_name}",
        (
            f"- run 1: answer {format_duration(trial_activities[0].duration_seconds)}, "
            f"grading {format_duration(run_grades[0].grading_duration_seconds)}, "
            f"total {format_duration(trial_activities[0].duration_seconds + run_grades[0].grading_duration_seconds)}; "
            f"{trial_activities[0].jev_request_count} Jev requests; LLM steps: {trial_activities[0].llm_step_events[0]}"
        ),
    ]

    actual_report: str = render_scorecard_report(create_scorecard_header(trial_activities), [amount_check], scorecard_rows, run_grades)

    assert_report_lines("A cause recurring in two of three runs", actual_report, expected_report_lines)


def test_ScorecardReport_WhenChecksDoNotApplyOrAreAdapted_ThenListsEachWithItsReasonOutsideThePassRates() -> None:
    amount_check: AmountCheck = create_amount_check()
    adapted_amount_check: AmountCheck = create_amount_check(adaptation=create_random_text("adaptation"))
    not_applicable_check: NotApplicableCheck = NotApplicableCheck(
        create_check_name(), create_random_text("description"), MINIMUM_PASS_RATE, None, not_applicable_reason=create_random_text("reason")
    )
    eval_suite_checks: list[EvalSuiteCheck] = [amount_check, adapted_amount_check, not_applicable_check]
    run_grades: list[RunGrade] = [
        RunGrade(
            1,
            {
                amount_check.check_name: CheckGrade(CheckStatus.PASSED, create_random_text("reason")),
                adapted_amount_check.check_name: CheckGrade(CheckStatus.PASSED, create_random_text("reason")),
            },
            create_grading_duration_seconds(),
        )
    ]
    scorecard_rows: list[ScorecardRow] = build_scorecard_rows([amount_check, adapted_amount_check], run_grades)
    expected_report_lines: list[str] = [
        "Gate: PASSED, all 2 graded checks meet their minimum pass rate.",
        f"- {adapted_amount_check.check_name}: {adapted_amount_check.adaptation}",
        f"- {not_applicable_check.check_name}: {not_applicable_check.not_applicable_reason}",
    ]

    actual_report: str = render_scorecard_report(create_scorecard_header([create_trial_activity()]), eval_suite_checks, scorecard_rows, run_grades)

    assert_report_lines("An adapted and a not-applicable check", actual_report, expected_report_lines)


def create_timed_run_grades(amount_check: AmountCheck, grading_durations_seconds: Sequence[float]) -> list[RunGrade]:
    return [
        RunGrade(run_number, {amount_check.check_name: CheckGrade(CheckStatus.PASSED, create_random_text("reason"))}, grading_duration_seconds)
        for run_number, grading_duration_seconds in enumerate(grading_durations_seconds, start=1)
    ]


def test_ScorecardReport_WhenRunsHaveAnswerAndGradingTimes_ThenTimePerRunShowsTheMedianAndRangeOfEach() -> None:
    amount_check: AmountCheck = create_amount_check()
    trial_activities: list[TrialActivity] = [TrialActivity(duration_seconds, 198, (), True) for duration_seconds in (8.4, 9.6, 65.4)]
    run_grades: list[RunGrade] = create_timed_run_grades(amount_check, (4.0, 12.4, 5.0))
    expected_report_lines: list[str] = [
        "| runs | Answer time (workflow run) | Grading time (eval analysis) | Total time |",
        "| 3 | 9.6s (8.4s to 1m 05s) | 5.0s (4.0s to 12s) | 22s (12s to 1m 10s) |",
    ]

    actual_report: str = render_scorecard_report(
        create_scorecard_header(trial_activities), [amount_check], build_scorecard_rows([amount_check], run_grades), run_grades
    )

    assert_report_lines("Three runs with answer and grading times", actual_report, expected_report_lines)


def test_ScorecardReport_WhenAWorkflowRunErrored_ThenItsTimesAreLeftOutOfTheTimePerRunSummary() -> None:
    amount_check: AmountCheck = create_amount_check()
    trial_activities: list[TrialActivity] = [
        TrialActivity(8.0, 198, (), True),
        TrialActivity(0.4, 3, (), False),
        TrialActivity(12.0, 198, (), True),
    ]
    run_grades: list[RunGrade] = create_timed_run_grades(amount_check, (4.0, 0.0, 6.0))
    expected_report_lines: list[str] = [
        "| 3 | 10s (8.0s to 12s), 2 of 3 runs | 5.0s (4.0s to 6.0s), 2 of 3 runs | 15s (12s to 18s), 2 of 3 runs |",
    ]

    actual_report: str = render_scorecard_report(
        create_scorecard_header(trial_activities), [amount_check], build_scorecard_rows([amount_check], run_grades), run_grades
    )

    assert_report_lines("One errored run among three", actual_report, expected_report_lines)


def test_ScorecardDocument_WhenRunsAreTimed_ThenEachRunsTotalIsItsAnswerTimePlusItsGradingTime() -> None:
    amount_check: AmountCheck = create_amount_check()
    run_grades: list[RunGrade] = create_run_grades(amount_check, [CheckStatus.PASSED, CheckStatus.PASSED])
    trial_activities: list[TrialActivity] = [create_trial_activity() for _ in run_grades]
    expected_total_durations: list[float] = [
        trial_activity.duration_seconds + run_grade.grading_duration_seconds
        for trial_activity, run_grade in zip(trial_activities, run_grades, strict=True)
    ]

    actual_scorecard_document: ScorecardDocument = build_scorecard_document(
        create_scorecard_header(trial_activities), build_scorecard_rows([amount_check], run_grades), run_grades
    )

    actual_total_durations: list[float] = [run_timing.total_duration_seconds for run_timing in actual_scorecard_document.run_timings]
    assert actual_total_durations == expected_total_durations, f"expected totals {expected_total_durations}; actual {actual_total_durations}"


def create_run_folder_name(run_timestamp: str, llm_model: str) -> str:
    return f"{run_timestamp}-claude_code-{llm_model}-medium-{secrets.token_hex(3)}"


def write_scorecard_folder(results_directory: Path, run_folder_name: str, llm_model: str, jev_model: str) -> ScorecardDocument:
    amount_check: AmountCheck = create_amount_check()
    run_grades: list[RunGrade] = create_run_grades(amount_check, [CheckStatus.PASSED, CheckStatus.FAILED])
    scorecard_document: ScorecardDocument = build_scorecard_document(
        create_scorecard_header([create_trial_activity() for _ in run_grades], llm_model, jev_model),
        build_scorecard_rows([amount_check], run_grades),
        run_grades,
    )
    (results_directory / run_folder_name).mkdir(parents=True)
    (results_directory / run_folder_name / "scorecard.json").write_text(to_json_text(asdict(scorecard_document)), encoding="utf-8")
    return scorecard_document


def test_ComparisonResults_WhenAConfigurationRanTwice_ThenOnlyItsNewestRunIsKept(tmp_path: Path) -> None:
    llm_model: str = create_random_text("claude")
    other_llm_model: str = create_random_text("claude")
    jev_model: str = create_random_text("jev")
    newest_run_folder_name: str = create_run_folder_name("20261011T100000Z", llm_model)
    older_run_folder_name: str = create_run_folder_name("20261010T100000Z", llm_model)
    other_run_folder_name: str = create_run_folder_name("20261009T100000Z", other_llm_model)
    # The newest folder is written first, so neither creation nor modification order can stand in for the name order the
    # comparison relies on.
    write_scorecard_folder(tmp_path, newest_run_folder_name, llm_model, jev_model)
    write_scorecard_folder(tmp_path, older_run_folder_name, llm_model, jev_model)
    write_scorecard_folder(tmp_path, other_run_folder_name, other_llm_model, jev_model)
    expected_run_folder_names: list[str] = sorted([newest_run_folder_name, other_run_folder_name])

    actual_configuration_results: list[ConfigurationResult] = read_latest_configuration_results(tmp_path)

    actual_run_folder_names: list[str] = sorted(configuration_result.run_folder_name for configuration_result in actual_configuration_results)
    assert actual_run_folder_names == expected_run_folder_names, f"expected {expected_run_folder_names}; actual {actual_run_folder_names}"


def test_ComparisonResults_WhenAScorecardPredatesTiming_ThenItsAnswerTimeComesFromEachRunsActivityAndGradingIsNotRecorded(tmp_path: Path) -> None:
    run_folder_name: str = create_run_folder_name("20261011T025133Z", create_random_text("claude"))
    scorecard_document: ScorecardDocument = write_scorecard_folder(tmp_path, run_folder_name, create_random_text("claude"), create_random_text("jev"))
    scorecard_path: Path = tmp_path / run_folder_name / "scorecard.json"
    scorecard_object: dict[str, object] = json.loads(scorecard_path.read_text(encoding="utf-8"))
    for timing_field_name in ("run_timings", "timing_summary"):
        del scorecard_object[timing_field_name]
    scorecard_path.write_text(json.dumps(scorecard_object), encoding="utf-8")
    answer_durations: list[float] = [trial_activity.duration_seconds for trial_activity in scorecard_document.scorecard_header.trial_activities]
    expected_answer_duration_summary: DurationSummary = DurationSummary(
        (answer_durations[0] + answer_durations[1]) / 2, min(answer_durations), max(answer_durations), len(answer_durations)
    )
    expected_row_ending: str = f"| not recorded | not recorded | `{run_folder_name}` |"
    expected_outcome: tuple[DurationSummary | None, bool] = (expected_answer_duration_summary, True)

    actual_configuration_results: list[ConfigurationResult] = read_latest_configuration_results(tmp_path)
    actual_comparison: str = render_scorecard_comparison(create_random_text("suite"), actual_configuration_results)

    actual_outcome: tuple[DurationSummary | None, bool] = (
        actual_configuration_results[0].timing_summary.answer_duration_summary,
        any(comparison_line.endswith(expected_row_ending) for comparison_line in actual_comparison.splitlines()),
    )
    assert actual_outcome == expected_outcome, (
        f"expected (answer time summary, a row ending {expected_row_ending!r}) {expected_outcome}; actual {actual_outcome}; "
        f"comparison:\n{actual_comparison}"
    )


def test_UserNameScrub_WhenAnArtifactNamesTheUserAccountFolder_ThenReplacesEveryPathFormButKeepsExpenseText() -> None:
    user_name: str = f"user{secrets.token_hex(4)}"
    artifact_text: str = (
        f"C:\\Users\\{user_name}\\AppData and C:/USERS/{user_name.upper()}/.claude and C:\\\\Users\\\\{user_name}\\\\x "
        f"and Venmo - {user_name.title()}"
    )
    expected_artifact_text: str = (
        f"C:\\Users\\<user>\\AppData and C:/USERS/<user>/.claude and C:\\\\Users\\\\<user>\\\\x and Venmo - {user_name.title()}"
    )

    actual_artifact_text: str = scrub_user_name(artifact_text, user_name)

    assert actual_artifact_text == expected_artifact_text


def test_UserNameScrub_WhenAFailureDescriptionNamingAPathIsWrittenAsJson_ThenRemovesTheUserName() -> None:
    user_name: str = f"user{secrets.token_hex(4)}"
    # str() of an OSError shows the path with repr(), which doubles each backslash, and JSON doubles them again.
    file_not_found_error: FileNotFoundError = FileNotFoundError(2, "No such file or directory", f"C:\\Users\\{user_name}\\AppData\\x.json")
    artifact_text: str = json.dumps({"reason": f"the workflow did not complete: {file_not_found_error}"})

    actual_artifact_text: str = scrub_user_name(artifact_text, user_name)

    assert user_name not in actual_artifact_text, f"expected the user name to be removed; actual {actual_artifact_text!r}"
