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

**Code-controlled workflow.** A program runs a fixed sequence of steps. Code reads the lines, does all the arithmetic and
lays out the report. Jev, a model service that answers multiple-choice questions, picks each line's category from a fixed
list. A general model (an LLM) only chooses three findings from a list that code computed. It would also read any line
that code cannot, but no line in this log needed that.

## How the eval works

An eval runs the same task many times and grades each answer against a reference key: a hand-checked answer for every line
and total. Each run starts fresh. There are 16 checks, such as "July shopping is $756.72". A second model, the judge,
grades the three findings against a written rubric. Two checks apply only to a model writing its own answer (it ran
Python; tables come first), so the workflow is graded on 14.

A check passes when it is right in enough runs: 80% for the prompt, because a model's answer varies, and 100% for the
workflow, because code decides every step that has one right answer.

A configuration is a model plus a reasoning effort (how much the model reasons before answering). Each of the workflow's
seven configurations ran 10 times on 2026-10-12. The prompt results are from 2026-10-09, with 4 or 5 runs per model.

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
showed the failures. In the workflow, tests verify the code that does the work, and the eval checks every run. It even
caught an infrastructure fault: in one round of GPT-6 Luna (low), a run failed in a Jev request before any model ran,
most likely a temporary connection error. That round was run again, and the new round is shown here.

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
