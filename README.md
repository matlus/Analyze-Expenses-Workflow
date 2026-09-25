# Analyze Expenses Workflow

Architecture: [Markdown document](docs/architecture/expense-analysis-control-flow.md) · [interactive HTML page](docs/architecture/expense-analysis-control-flow.html) (open locally).

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
`CODING_ASSISTANT_SUBSCRIPTION` as `GPT_CODEX` or `GITHUB_COPILOT`. The example
file includes the OpenRouter endpoint, Jev model, per-operation model and
reasoning settings, and monthly budget targets. The selected SDK determines the
model family: Codex and GitHub Copilot use OpenAI. Claude Code maps to Anthropic
but its SDK gateway is not yet implemented, so selecting it fails at startup.
`CODING_ASSISTANT_MODEL_FAMILY` and per-operation `<OPERATION>_MODEL_FAMILY`
overrides are rejected; family-specific model and effort values remain available
for the family chosen by the SDK. Authentication to
the chosen coding assistant subscription must also be available locally.

Code parses clear date and amount fields. When it finds multiple candidates,
Jev selects a source span if its chosen-option probability meets the provisional
`JEV_PARSING_MINIMUM_CHOICE_PROBABILITY` setting. Remaining uncertain lines go
to the configured LLM extraction operation. Jev still handles expense
categorization; the family-specific categorization settings reserve a future LLM
fallback and are not invoked by the current pipeline. Legacy
`EXPENSE_LINE_EXTRACTION_MODEL` and `EXPENSE_FINDINGS_MODEL` settings remain
supported for the OpenAI family.

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
uv run --all-packages --all-groups ruff check .
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

## Layout

- `packages/analyze-expenses-workflow`: domain library and tests.
- `apps/analyze-expenses-workflow-app`: console entry point and report renderer.
- `examples/expense-analysis.md`: saved full report for the original expense log.
- `docs/`: implementation plan and experiment notes.
