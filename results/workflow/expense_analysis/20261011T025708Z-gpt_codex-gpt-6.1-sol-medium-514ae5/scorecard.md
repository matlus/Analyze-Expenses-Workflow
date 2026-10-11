# Scorecard: expense-analysis-workflow

Run `20261011T025708Z-gpt_codex-gpt-6.1-sol-medium-514ae5`: 5 isolated runs of the workflow; judge sonnet at low effort.

| model step | provider | model | reasoning effort |
|---|---|---|---|
| expense_line_jev_extraction | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_line_extraction | GPT_CODEX (OPENAI) | gpt-6.1-sol | medium |
| transaction_categorization | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_findings | GPT_CODEX (OPENAI) | gpt-6.1-sol | medium |

What each run did:
- run 1: 8s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings
- run 2: 8s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings
- run 3: 7s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings
- run 4: 7s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings
- run 5: 7s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings

Gate: FAILED, 14 of 14 graded checks are below their minimum pass rate.

| check | graded by | minimum | passed |
|---|---|---|---|
| dining-june-total | amount (read from result) | 80% | 0/5 (0%), 5 errored |
| dining-july-total | amount (read from result) | 80% | 0/5 (0%), 5 errored |
| dining-august-total | amount (read from result) | 80% | 0/5 (0%), 5 errored |
| dining-august-over-budget | fact (read from result) | 80% | 0/5 (0%), 5 errored |
| refund-netted | fact (read from result) | 80% | 0/5 (0%), 5 errored |
| shopping-july-total | amount (read from result) | 80% | 0/5 (0%), 5 errored |
| duplicate-flagged | fact (read from result) | 80% | 0/5 (0%), 5 errored |
| both-delta-charges-kept | fact (read from result) | 80% | 0/5 (0%), 5 errored |
| travel-august-total | amount (read from result) | 80% | 0/5 (0%), 5 errored |
| uncategorized-entry-count | fact (read from result) | 80% | 0/5 (0%), 5 errored |
| largest-increase-is-travel | fact (read from result) | 80% | 0/5 (0%), 5 errored |
| ambiguous-entries-not-forced | lines (read from result) | 80% | 0/5 (0%), 5 errored |
| three-supported-findings | judge | 80% | 0/5 (0%), 5 errored |
| no-invented-budgets | targets (read from result) | 80% | 0/5 (0%), 5 errored |

Failure causes traced in the reference ledger (0); one cause in several runs is a systematic mistake:

Checks that did not pass (0):

Checks that errored (70); each counts as not passed and is not listed above:
- run 1 / dining-june-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / dining-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / dining-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / dining-august-over-budget: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / refund-netted: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / shopping-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / duplicate-flagged: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / both-delta-charges-kept: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / travel-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / uncategorized-entry-count: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / largest-increase-is-travel: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / ambiguous-entries-not-forced: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / three-supported-findings: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 1 / no-invented-budgets: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / dining-june-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / dining-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / dining-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / dining-august-over-budget: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / refund-netted: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / shopping-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / duplicate-flagged: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / both-delta-charges-kept: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / travel-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / uncategorized-entry-count: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / largest-increase-is-travel: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / ambiguous-entries-not-forced: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / three-supported-findings: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 2 / no-invented-budgets: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / dining-june-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / dining-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / dining-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / dining-august-over-budget: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / refund-netted: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / shopping-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / duplicate-flagged: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / both-delta-charges-kept: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / travel-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / uncategorized-entry-count: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / largest-increase-is-travel: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / ambiguous-entries-not-forced: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / three-supported-findings: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 3 / no-invented-budgets: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / dining-june-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / dining-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / dining-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / dining-august-over-budget: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / refund-netted: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / shopping-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / duplicate-flagged: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / both-delta-charges-kept: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / travel-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / uncategorized-entry-count: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / largest-increase-is-travel: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / ambiguous-entries-not-forced: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / three-supported-findings: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 4 / no-invented-budgets: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / dining-june-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / dining-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / dining-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / dining-august-over-budget: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / refund-netted: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / shopping-july-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / duplicate-flagged: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / both-delta-charges-kept: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / travel-august-total: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / uncategorized-entry-count: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / largest-increase-is-travel: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / ambiguous-entries-not-forced: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / three-supported-findings: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed
- run 5 / no-invented-budgets: the workflow did not complete: LlmRequestFailedException: Codex subscription request failed

Checks carried over with an adaptation:
- duplicate-flagged: The workflow does not guess duplicates: the caller confirms the line (as the case does), and the reconciliation step excludes it only if it repeats the preceding source line. This check verifies that contract, not a model's judgment.
- ambiguous-entries-not-forced: The prompt eval needed a judge to read this from prose. The workflow reports each line's category and treatment, so code checks that every entry the reference leaves uncategorized is kept, uncategorized, as an unresolved outflow.
- no-invented-budgets: The workflow reads its two targets from configuration, which the case sets from budget_notes.txt; code checks that the budget comparisons use exactly those targets and no other category.

Checks that do not apply to a code-controlled workflow (left out of the pass rates):
- code-executed: Python computes every total by construction (ExpenseCalculationProcessor); no model session runs tools whose log could be inspected.
- tables-before-interpretation: The report layout is fixed code (ComposerExpenseAnalysisReport renders every table before the findings); no model decides the order.
