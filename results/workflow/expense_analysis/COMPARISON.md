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
next step uses it. Two kinds of model take part. Jev is a System 1 model: it answers a multiple-choice question at once,
without first reasoning step by step as reasoning models do, and gives a probability for each choice. A large language
model (LLM) is used only where Jev and code cannot do the job.

1. Code reads each line's date, description and amount. If code cannot read a line, Jev tries; if Jev is not confident,
   the LLM reads it, and code confirms the date and amount it returns appear in the line's own text. In this log, code
   read every line itself.
2. Jev puts each line in one category from a fixed list. Code rejects any category not on the list, then applies written
   rules: transfers and ATM withdrawals stay uncategorized, and known merchants get their known category.
3. Code removes the confirmed duplicate, subtracts the refund, and computes every total, budget comparison and change. It
   stops if the totals do not add up.
4. Code writes a list of candidate findings, each a complete sentence with its numbers, such as "August Dining & Coffee
   was $398.69 against a $260.00 target". The LLM picks the three most important by their IDs. It writes no text and
   computes no numbers. Code checks that the three picks are different and on the list, and asks again if they are not.
5. Code lays out the report from these results, using the candidates' own sentences.

## How the eval works

An eval runs the same task many times and grades each answer against a reference key: a hand-checked answer for every line
and total. Each run starts fresh. The eval grades 16 checks:

- **Totals (5):** Dining & Coffee in June, July and August; Shopping in July; Travel in August.
- **Handling (5):** the refund is subtracted; the duplicate is flagged; both Delta flight charges are kept; all eight
  transfers and ATM withdrawals are left uncategorized; none is forced into a spending category.
- **Conclusions (4):** August Dining & Coffee is over budget; Travel had the largest increase; only the two stated budgets
  are used; there are exactly three findings, each supported by the numbers.
- **Method (2):** Python did the arithmetic; the tables come before the interpretation.

In the workflow eval, code grades each check from the workflow's structured result, except the findings, which a second
model (the judge) grades against a written rubric. The prompt eval has to read the same facts out of a written answer, and
uses the judge for four checks. The two method checks apply only to a model writing its own answer; in the workflow, code
does both every time, so the workflow is graded on the other 14.

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
