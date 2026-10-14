# Analyze Expenses Workflow

Architecture: [Markdown document](docs/architecture/expense-analysis-control-flow.md) · [interactive HTML page](docs/architecture/expense-analysis-control-flow.html) (open locally).

Teaching article: [Verified, Not Trusted: Expense Analysis, Part Two](https://matlus.com/writing/verified-not-trusted-expense-analysis-part-two/)
walks through the Python controller, Jev decisions, LLM extraction fallback,
source reconciliation, decimal calculations, and verified finding selection.

Eval article: [Verified, Not Trusted: Expense Analysis, Part Four](https://matlus.com/writing/verified-not-trusted-expense-analysis-part-four/)
explains reference keys, code graders, model judges and pass-rate gates through
this workflow's expense-analysis results.

This Python workspace analyzes a list of expense lines through the asynchronous
`DomainFacade.analyze_expenses(list[str])` entry point. Its frozen result contains
the transaction ledger, Jev category decisions, monthly totals, budget
comparisons, month-to-month changes, reconciliation checks, and three findings
grounded in calculated evidence. The console app renders that result as a
Markdown report; [a saved report](examples/expense-analysis.md) shows the
198-line worked input under this project's explicit expense category policy.

The manager obtains typed settings from `ConfigurationProvider`, selects a
subscription gateway for Codex or GitHub Copilot, and passes it to the LLM
processors. Jev classifies transactions through OpenRouter. Python parses and
calculates amounts, preserves refunds as negative values, and retains source
line references. See the [library design](packages/analyze-expenses-workflow/README.md),
the [implementation plan](docs/implementation-plan.md), and the
[Jev experiment](docs/jev-experiment.md) for details.

## Setup

Copy `.env.example` to `.env` and provide `OPEN_ROUTER_KEY`. Select
`CODING_ASSISTANT_SUBSCRIPTION` as `GPT_CODEX`, `GITHUB_COPILOT` or `CLAUDE_CODE`.
The example file includes the OpenRouter endpoint, Jev model, per-operation model
and reasoning settings, and monthly budget targets. The selected subscription
determines the model family: Codex and GitHub Copilot use OpenAI, and Claude Code
uses Anthropic through an isolated `claude -p` process per request (no tools,
MCP servers, skills, user settings or session history). With Claude Code, every
operation needs an `<OPERATION>_ANTHROPIC_MODEL`, and efforts are `low` through `max`.
`CODING_ASSISTANT_MODEL_FAMILY` and per-operation `<OPERATION>_MODEL_FAMILY`
overrides are rejected; family-specific model and effort values remain available
for the family chosen by the SDK. Every subscription runs the CLI you installed
and signed in to, found on PATH (`codex`, `copilot` or `claude`), never a copy bundled
with an SDK, so there is nothing else to configure and the models are the ones your CLI
offers. A missing CLI fails at startup with an error that names it.

Code parses clear date and amount fields. When it finds multiple candidates,
Jev selects a source span if its chosen-option probability meets the provisional
`JEV_PARSING_MINIMUM_CHOICE_PROBABILITY` setting. Remaining uncertain lines go
to the configured LLM extraction operation. Jev still handles expense
categorization; the family-specific categorization settings reserve a future LLM
fallback and are not invoked by the current pipeline. Model and effort settings
are read only under their family-qualified names, such as
`EXPENSE_LINE_EXTRACTION_OPENAI_MODEL`; the unqualified `EXPENSE_LINE_EXTRACTION_MODEL`
form is no longer read.

`JEV_MODEL` selects the System One model. It also accepts
`microsoft/microsoft-decision-1`, which OpenRouter serves through the same
System One endpoint with the same request and response shape, so no code change
is needed to switch. Settings, errors and log events still use the Jev name
whichever model is configured.

From the workspace root:

```powershell
uv sync --all-packages --all-groups
uv run --all-packages --all-groups analyze_expenses_workflow_app --input packages/analyze-expenses-workflow/tests/fixtures/personal_expenses.txt --confirmed-duplicate-line 109 --output expense-analysis.md
```

The app requires an explicit input path. The worked example's answer key confirms
that its line 109 repeats line 108, so the command explicitly excludes line 109.
See the [app guide](apps/analyze-expenses-workflow-app/README.md)
for file handling and report details.

## Verification

```powershell
uv run --all-packages --all-groups ruff check packages apps evals
uv run --all-packages --all-groups pyright
uv run --all-packages --all-groups pytest -q

# Focused offline routing experiment using a custom expense input file:
uv run --all-packages --all-groups pytest -q packages/analyze-expenses-workflow/tests/integration/test_expense_parsing_cascade_experiment.py
```

The Jev acceptance tests are opt-in because they call a live service:

```powershell
$env:RUN_LIVE_JEV_TESTS = '1'
uv run --all-packages --all-groups pytest -q packages/analyze-expenses-workflow/tests/acceptance
Remove-Item Env:RUN_LIVE_JEV_TESTS
```

To run the focused live parsing checks, set `RUN_LIVE_JEV_TESTS=1` for Jev,
`RUN_LIVE_LLM_TESTS=1` for the Codex fallback, or `RUN_LIVE_COPILOT_TESTS=1`
for the Copilot fallback with an OpenAI model. Run the
integration test file above. The Codex check defaults to `gpt-6-sol` at
`medium` effort; override it with `EXPENSE_PARSING_TEST_MODEL` and
`EXPENSE_PARSING_TEST_REASONING_EFFORT`.
These checks require working subscription authentication and are excluded from
the default test run.

The three-run Jev category stability probe is also opt-in. Its JUnit XML
property contains the selected labels and probabilities for each example:

```powershell
$env:RUN_LIVE_JEV_REPEATABILITY_TESTS = '1'
New-Item -ItemType Directory -Force .workspace_tmp | Out-Null
uv run --all-packages --all-groups pytest -q -o junit_family=legacy --junitxml=.workspace_tmp/category-repeatability.xml packages/analyze-expenses-workflow/tests/integration/test_jev_category_repeatability_experiment.py
Remove-Item Env:RUN_LIVE_JEV_REPEATABILITY_TESTS
```

## Eval

`evals/workflow` runs the real workflow end to end, several times in isolation,
and grades every run with the expectations of the Part Three prompt eval
([Skills and Evals](https://github.com/matlus/Skills-and-Evals), `evals/expense_analysis`).
The case files in `evals/expense_analysis/cases` are copied with one deliberate
difference: this copy of the reference ledger also accepts Jev's policy of putting
merchant-only Amazon, Target and Costco charges in Other, because the log does not
say what was bought (see the revision note in `expense_reference_key.md`). The
Skills and Evals ledger does not accept this policy. Facts are
read from the workflow's structured result instead of extracted from prose; only
"three supported findings" uses a judge (`sonnet` at `low` effort, as in Part Three).
`evals/expense_analysis/eval_suite.json` maps all 16 Part Three checks, marks
the two that cannot apply to a code-controlled graph, and states each adaptation.
A failed total is traced line by line against the reference ledger.

Each run of the eval writes a timestamped folder under `results/workflow/expense_analysis`
with `scorecard.md`, `scorecard.json`, and per-run `answer.md` (the production
report), `outcome.json`, `activity.json` and `grade.json`. The Windows user name
is scrubbed from every recorded file.

```powershell
uv run --all-packages --all-groups pytest evals/workflow -s --eval-repetitions 5 --workflow-subscription CLAUDE_CODE --workflow-model claude-opus-5-5 --workflow-effort medium
```

`--workflow-subscription`, `--workflow-model` and `--workflow-effort` set every
LLM step; `--workflow-setting NAME=VALUE` (repeatable) sets any single setting,
such as `EXPENSE_FINDINGS_OPENAI_REASONING_EFFORT=high` or `JEV_MODEL=jev-1.13`.
Settings not given keep their `.env` values. The eval's own tests run with the
ordinary test suite.

`scorecard.md` times every run in three columns: **Answer time (workflow run)**,
the workflow run that produced the result, before any grading started;
**Grading time (eval analysis)**, composing the report the judge reads, reading
every fact, the judge, and the code checks; and **Total time**, the two added
together. Each cell is the median across runs, then the shortest to the longest
run. `scorecard.json` holds every run's three times.

To compare configurations, build one table from every run folder's `scorecard.json`:

```powershell
uv run python evals/workflow/compare_scorecards.py
```

It writes `results/workflow/expense_analysis/scorecard_comparison.md`: one row per
configuration of LLM steps and Jev model (the newest run of each), with the gate,
the checks below their minimum, and the three times. Runs recorded before grading
was timed show their answer time only.

## Layout

- `packages/analyze-expenses-workflow`: domain library and tests.
- `apps/analyze-expenses-workflow-app`: console entry point and report renderer.
- `evals/`: the workflow eval, its case files, and the harness tests.
- `results/workflow/`: recorded eval runs and scorecards.
- `examples/expense-analysis.md`: saved full report for the original expense log.
- `docs/`: implementation plan and experiment notes.
