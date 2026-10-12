# Scorecard: expense-analysis-workflow

Run `20261012T122320Z-gpt_codex-gpt-6.1-sol-medium-f99773`: 5 isolated runs of the workflow; judge sonnet at low effort.

| model step | provider | model | reasoning effort |
|---|---|---|---|
| expense_line_jev_extraction | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_line_extraction | GPT_CODEX (OPENAI) | gpt-6.1-sol | medium |
| transaction_categorization | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_findings | GPT_CODEX (OPENAI) | gpt-6.1-sol | medium |

Time per run:

| runs | Answer time (workflow run) | Grading time (eval analysis) | Total time |
|---|---|---|---|
| 5 | 16s (14s to 19s) | 4.1s (4.1s to 4.6s) | 21s (18s to 23s) |

Answer time is the workflow run that produced the result, before any grading started. Grading time is the eval analysis of that result: composing the report the judge reads, reading every fact, the judge, and the code checks. Total time is the two added together. Each time is the median per run, then the shortest to the longest run. Runs whose workflow errored are left out of these times; a cell then says how many runs it covers.

What each run did:
- run 1: answer 16s, grading 4.6s, total 21s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 2: answer 14s, grading 4.1s, total 18s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 3: answer 15s, grading 4.1s, total 19s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 4: answer 16s, grading 4.1s, total 21s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 5: answer 19s, grading 4.2s, total 23s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected

Gate: PASSED, all 14 graded checks meet their minimum pass rate.

| check | graded by | minimum | passed |
|---|---|---|---|
| dining-june-total | amount (read from result) | 80% | 5/5 (100%) |
| dining-july-total | amount (read from result) | 80% | 5/5 (100%) |
| dining-august-total | amount (read from result) | 80% | 5/5 (100%) |
| dining-august-over-budget | fact (read from result) | 80% | 5/5 (100%) |
| refund-netted | fact (read from result) | 80% | 5/5 (100%) |
| shopping-july-total | amount (read from result) | 80% | 5/5 (100%) |
| duplicate-flagged | fact (read from result) | 80% | 5/5 (100%) |
| both-delta-charges-kept | fact (read from result) | 80% | 5/5 (100%) |
| travel-august-total | amount (read from result) | 80% | 5/5 (100%) |
| uncategorized-entry-count | fact (read from result) | 80% | 5/5 (100%) |
| largest-increase-is-travel | fact (read from result) | 80% | 5/5 (100%) |
| ambiguous-entries-not-forced | lines (read from result) | 80% | 5/5 (100%) |
| three-supported-findings | judge | 80% | 5/5 (100%) |
| no-invented-budgets | targets (read from result) | 80% | 5/5 (100%) |

Failure causes traced in the reference ledger (0); one cause in several runs is a systematic mistake:

Checks that did not pass (0):

Checks that errored (0); each counts as not passed and is not listed above:

Checks carried over with an adaptation:
- duplicate-flagged: The workflow does not guess duplicates: the caller confirms the line (as the case does), and the reconciliation step excludes it only if it repeats the preceding source line. This check verifies that contract, not a model's judgment.
- ambiguous-entries-not-forced: The prompt eval needed a judge to read this from prose. The workflow reports each line's category and treatment, so code checks that every entry the reference leaves uncategorized is kept, uncategorized, as an unresolved outflow.
- no-invented-budgets: The workflow reads its two targets from configuration, which the case sets from budget_notes.txt; code checks that the budget comparisons use exactly those targets and no other category.

Checks that do not apply to a code-controlled workflow (left out of the pass rates):
- code-executed: Python computes every total by construction (ExpenseCalculationProcessor); no model session runs tools whose log could be inspected.
- tables-before-interpretation: The report layout is fixed code (ComposerExpenseAnalysisReport renders every table before the findings); no model decides the order.
