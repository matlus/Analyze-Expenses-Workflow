# Expense analysis: code-controlled workflow vs. prompt-controlled model

## The task

The input is a personal expense log for June to August 2026 (198 lines, inconsistent formats, no categories) and a note
with two monthly budget targets. A correct answer categorizes every line, leaves bank transfers and ATM withdrawals
uncategorized, handles one refund and one duplicate, totals each category by month, compares the two budgets, finds the
largest increase, and gives exactly three findings supported by the numbers.

## The two approaches

**Prompt-controlled.** A coding assistant (Claude Code or Codex) gets the files and a detailed prompt, then decides how to
do every step, runs its own Python code, and writes the report. These results use the stricter of the two prompts in the
Skills and Evals workspace (`robust_tables_first.md`), which spells out each step, says not to force transfers into a
category, and asks for exactly three findings checked against the tables.

**Code-controlled workflow.** A program runs fixed steps in a fixed order, and code checks each step's output before the
next step uses it. Code reads the lines and does all the arithmetic. Jev, a System 1 model (it answers a multiple-choice
question at once, without reasoning step by step first), puts each line in a category from a fixed list. A large language
model (LLM) returns the IDs of three findings from a list of sentences that code wrote. Code requires certain findings and
rejects any answer that leaves one out; in this case it required three, so code decided all three. Code writes the report.

## How it was tested

Both approaches were graded by an eval: the same task run many times, each answer checked against a hand-checked
reference key. Each run is graded on the same checks, such as "July shopping is $756.72" and "all eight transfers are left
uncategorized". The prompt must pass each check in 80% of runs; the workflow must pass in 100%, because code decides every
step that has one right answer. Each of the workflow's seven configurations (a model plus a reasoning effort) ran 10
times; the prompt results have 4 or 5 runs per model. The workflow also has a check the prompt
eval cannot make, because its output shows every line: all 198 lines compared with the reference key, each line's date,
merchant, amount and sign, category, and whether it was counted. All 70 runs pass it. Adding the merchants to the
reference key found one line the workflow read wrongly: it recorded the merchant on the REI refund as "Return processed -
REI". That was fixed before these runs. [What an eval is](../../../docs/articles/what-an-eval-is.md) explains the checks
and how they are graded.

## Results

A run is fully correct when every check was right.

| Model (effort) | Prompt: fully correct runs | Prompt: checks wrong at least once | Workflow: fully correct runs |
|---|---|---|---|
| Claude Opus 5.5 (medium) | 6 of 8 | 1 (findings) | 10 of 10 |
| Claude Sonnet 5.5 (medium) | 4 of 5 | 2 (a flight left out) | 10 of 10 |
| Claude Haiku 5.5 (medium) | 1 of 5 | 5 | 10 of 10 |
| GPT-6.1 Sol (medium) | 4 of 4 | 0 | 10 of 10 |
| GPT-6 Luna (medium) | 0 of 5 | 7 | 10 of 10 |
| Claude Haiku 5.5 (low) | not run | | 10 of 10 |
| GPT-6 Luna (low) | not run | | 10 of 10 |

All 70 workflow runs produced the same report, character for character. A typical run took 8 to 16 seconds.

## Findings

**The workflow's answer did not depend on the model.** The smallest model at the lowest effort gave the same report as the
largest, because the model never touches the totals.

**The prompt's failures were ordinary mistakes that changed from run to run:** purchases left out (three IKEA charges, a
Delta flight), wrong categories (pharmacy as Shopping, bowling as Dining), transfers forced into spending categories,
categories merged ("Transport / travel"), and findings that broke the rules (more than three, or one contradicting the
run's own table).

**An instruction is not assurance.** The stricter prompt asked for every one of those things explicitly, and models still
did otherwise. A better prompt raises the odds of a correct answer; it cannot guarantee one.

**Verified, not trusted.** A single good run from Sonnet or Opus would have looked trustworthy. Only repeated, graded runs
showed the failures. The workflow is verified twice: while it runs, code checks every model output before using it, and
afterward the eval grades the whole answer against the reference key. The prompt-controlled approach has only the second
check, after the answer is already written.

**When the steps are known and the stakes are high, use a fixed workflow.** Give code the parts that have one right answer,
give the model only the choices that need judgment, and keep an eval on both.

## Limits

The prompt results come from 4 or 5 runs per model. The two reference keys differ in one place: this workflow's key
accepts merchant-only Amazon and Target charges as either Shopping or Other, and the prompt's key does not, which can
affect the July shopping check.

## Details

[scorecard_comparison.md](scorecard_comparison.md) has the workflow's per-configuration table. Each run folder holds the
report (`answer.md`), the grades (`grade.json`) and the structured result (`outcome.json`). The prompt results are in the
Skills and Evals repository under `results/pwi/expense_analysis`.
