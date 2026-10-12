# Scorecard: expense-analysis-workflow

Run `20261012T123833Z-gpt_codex-gpt-6-luna-low-0fb069`: 5 isolated runs of the workflow; judge sonnet at low effort.

| model step | provider | model | reasoning effort |
|---|---|---|---|
| expense_line_jev_extraction | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_line_extraction | GPT_CODEX (OPENAI) | gpt-6-luna | low |
| transaction_categorization | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_findings | GPT_CODEX (OPENAI) | gpt-6-luna | low |

Time per run:

| runs | Answer time (workflow run) | Grading time (eval analysis) | Total time |
|---|---|---|---|
| 5 | 8.4s (7.7s to 8.6s), 4 of 5 runs | 4.0s (3.8s to 4.3s), 4 of 5 runs | 12s (12s to 13s), 4 of 5 runs |

Answer time is the workflow run that produced the result, before any grading started. Grading time is the eval analysis of that result: composing the report the judge reads, reading every fact, the judge, and the code checks. Total time is the two added together. Each time is the median per run, then the shortest to the longest run. Runs whose workflow errored are left out of these times; a cell then says how many runs it covers.

What each run did:
- run 1: answer 0.1s, grading 0.0s, total 0.1s; 9 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction
- run 2: answer 8.3s, grading 3.8s, total 12s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 3: answer 8.6s, grading 3.9s, total 13s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 4: answer 8.5s, grading 4.3s, total 13s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 5: answer 7.7s, grading 4.2s, total 12s; 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected

Gate: PASSED, all 14 graded checks meet their minimum pass rate.

| check | graded by | minimum | passed |
|---|---|---|---|
| dining-june-total | amount (read from result) | 80% | 4/5 (80%), 1 errored |
| dining-july-total | amount (read from result) | 80% | 4/5 (80%), 1 errored |
| dining-august-total | amount (read from result) | 80% | 4/5 (80%), 1 errored |
| dining-august-over-budget | fact (read from result) | 80% | 4/5 (80%), 1 errored |
| refund-netted | fact (read from result) | 80% | 4/5 (80%), 1 errored |
| shopping-july-total | amount (read from result) | 80% | 4/5 (80%), 1 errored |
| duplicate-flagged | fact (read from result) | 80% | 4/5 (80%), 1 errored |
| both-delta-charges-kept | fact (read from result) | 80% | 4/5 (80%), 1 errored |
| travel-august-total | amount (read from result) | 80% | 4/5 (80%), 1 errored |
| uncategorized-entry-count | fact (read from result) | 80% | 4/5 (80%), 1 errored |
| largest-increase-is-travel | fact (read from result) | 80% | 4/5 (80%), 1 errored |
| ambiguous-entries-not-forced | lines (read from result) | 80% | 4/5 (80%), 1 errored |
| three-supported-findings | judge | 80% | 4/5 (80%), 1 errored |
| no-invented-budgets | targets (read from result) | 80% | 4/5 (80%), 1 errored |

Failure causes traced in the reference ledger (0); one cause in several runs is a systematic mistake:

Checks that did not pass (0):

Checks that errored (14); each counts as not passed and is not listed above:
- run 1 / dining-june-total: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / dining-july-total: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / dining-august-total: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / dining-august-over-budget: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / refund-netted: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / shopping-july-total: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / duplicate-flagged: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / both-delta-charges-kept: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / travel-august-total: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / uncategorized-entry-count: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / largest-increase-is-travel: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / ambiguous-entries-not-forced: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / three-supported-findings: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
- run 1 / no-invented-budgets: the workflow did not complete: ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)

Checks carried over with an adaptation:
- duplicate-flagged: The workflow does not guess duplicates: the caller confirms the line (as the case does), and the reconciliation step excludes it only if it repeats the preceding source line. This check verifies that contract, not a model's judgment.
- ambiguous-entries-not-forced: The prompt eval needed a judge to read this from prose. The workflow reports each line's category and treatment, so code checks that every entry the reference leaves uncategorized is kept, uncategorized, as an unresolved outflow.
- no-invented-budgets: The workflow reads its two targets from configuration, which the case sets from budget_notes.txt; code checks that the budget comparisons use exactly those targets and no other category.

Checks that do not apply to a code-controlled workflow (left out of the pass rates):
- code-executed: Python computes every total by construction (ExpenseCalculationProcessor); no model session runs tools whose log could be inspected.
- tables-before-interpretation: The report layout is fixed code (ComposerExpenseAnalysisReport renders every table before the findings); no model decides the order.
