"""Compare the newest run of every workflow configuration: one table with the gate, the checks below their minimum, and
the answer, grading and total times, written to results/workflow/expense_analysis/scorecard_comparison.md.

Run it with:  uv run python evals/workflow/compare_scorecards.py
"""

from expense_analysis_workflow_eval import (
    COMPARISON_FILE_NAME,
    EVAL_SUITE_DIRECTORY,
    RESULTS_DIRECTORY,
    ConfigurationResult,
    read_latest_configuration_results,
    render_scorecard_comparison,
    report_progress,
    write_artifact,
)


def main() -> None:
    # A fresh clone has no results folder until the first eval runs; the comparison then says no run folder exists.
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    configuration_results: list[ConfigurationResult] = read_latest_configuration_results(RESULTS_DIRECTORY)
    comparison_report: str = render_scorecard_comparison(EVAL_SUITE_DIRECTORY.name, configuration_results)
    write_artifact(RESULTS_DIRECTORY / COMPARISON_FILE_NAME, comparison_report)
    report_progress(comparison_report)
    report_progress(f"Written to: {RESULTS_DIRECTORY / COMPARISON_FILE_NAME}")


if __name__ == "__main__":
    main()
