# Scorecard: expense-analysis-workflow

Run `20261011T025911Z-gpt_codex-gpt-6-luna-low-6ef311`: 5 isolated runs of the workflow; judge sonnet at low effort.

| model step | provider | model | reasoning effort |
|---|---|---|---|
| expense_line_jev_extraction | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_line_extraction | GPT_CODEX (OPENAI) | gpt-6-luna | low |
| transaction_categorization | OpenRouter (Jev) | jev-1.13 | not supported |
| expense_findings | GPT_CODEX (OPENAI) | gpt-6-luna | low |

What each run did:
- run 1: 8s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 2: 8s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 3: 9s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 4: 8s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected
- run 5: 8s, 198 Jev requests; LLM steps: progress: Extracting uncertain expense lines; complete: No uncertain expense lines required extraction; progress: Selecting evidence-based expense findings; complete: Three expense findings selected

Gate: FAILED, 1 of 14 graded checks are below their minimum pass rate.

| check | graded by | minimum | passed |
|---|---|---|---|
| dining-june-total | amount (read from result) | 80% | 5/5 (100%) |
| dining-july-total | amount (read from result) | 80% | 5/5 (100%) |
| dining-august-total | amount (read from result) | 80% | 5/5 (100%) |
| dining-august-over-budget | fact (read from result) | 80% | 5/5 (100%) |
| refund-netted | fact (read from result) | 80% | 5/5 (100%) |
| shopping-july-total | amount (read from result) | 80% | 0/5 (0%) |
| duplicate-flagged | fact (read from result) | 80% | 5/5 (100%) |
| both-delta-charges-kept | fact (read from result) | 80% | 5/5 (100%) |
| travel-august-total | amount (read from result) | 80% | 5/5 (100%) |
| uncategorized-entry-count | fact (read from result) | 80% | 5/5 (100%) |
| largest-increase-is-travel | fact (read from result) | 80% | 5/5 (100%) |
| ambiguous-entries-not-forced | lines (read from result) | 80% | 5/5 (100%) |
| three-supported-findings | judge | 80% | 5/5 (100%) |
| no-invented-budgets | targets (read from result) | 80% | 5/5 (100%) |

Failure causes traced in the reference ledger (1); one cause in several runs is a systematic mistake:
- Shopping 2026-07: line 95 "7/13: Target, $88.28": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping; line 106 "Amazon (2026-07-18) - $141.82": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping · 5 check runs: run 1 / shopping-july-total; run 2 / shopping-july-total; run 3 / shopping-july-total; run 4 / shopping-july-total; run 5 / shopping-july-total

Checks that did not pass (5):
- run 1 / shopping-july-total: July shopping is $756.72, which is only true when the refund is netted. Expected 756.72 (within 0.01); the workflow reported 526.62 for Shopping 2026-07. Cause: Shopping 2026-07: reported 526.62, reference 756.72, a difference of -230.10. Explained by: line 95 "7/13: Target, $88.28": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-88.28); line 106 "Amazon (2026-07-18) - $141.82": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-141.82).
- run 2 / shopping-july-total: July shopping is $756.72, which is only true when the refund is netted. Expected 756.72 (within 0.01); the workflow reported 526.62 for Shopping 2026-07. Cause: Shopping 2026-07: reported 526.62, reference 756.72, a difference of -230.10. Explained by: line 95 "7/13: Target, $88.28": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-88.28); line 106 "Amazon (2026-07-18) - $141.82": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-141.82).
- run 3 / shopping-july-total: July shopping is $756.72, which is only true when the refund is netted. Expected 756.72 (within 0.01); the workflow reported 526.62 for Shopping 2026-07. Cause: Shopping 2026-07: reported 526.62, reference 756.72, a difference of -230.10. Explained by: line 95 "7/13: Target, $88.28": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-88.28); line 106 "Amazon (2026-07-18) - $141.82": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-141.82).
- run 4 / shopping-july-total: July shopping is $756.72, which is only true when the refund is netted. Expected 756.72 (within 0.01); the workflow reported 526.62 for Shopping 2026-07. Cause: Shopping 2026-07: reported 526.62, reference 756.72, a difference of -230.10. Explained by: line 95 "7/13: Target, $88.28": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-88.28); line 106 "Amazon (2026-07-18) - $141.82": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-141.82).
- run 5 / shopping-july-total: July shopping is $756.72, which is only true when the refund is netted. Expected 756.72 (within 0.01); the workflow reported 526.62 for Shopping 2026-07. Cause: Shopping 2026-07: reported 526.62, reference 756.72, a difference of -230.10. Explained by: line 95 "7/13: Target, $88.28": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-88.28); line 106 "Amazon (2026-07-18) - $141.82": the workflow put it in Other 2026-07 (Jev chose Other; the workflow's policy decided: Merchant-only mixed retailer charge; item type is not stated); the reference puts it in Shopping (-141.82).

Checks that errored (0); each counts as not passed and is not listed above:

Checks carried over with an adaptation:
- duplicate-flagged: The workflow does not guess duplicates: the caller confirms the line (as the case does), and the reconciliation step excludes it only if it repeats the preceding source line. This check verifies that contract, not a model's judgment.
- ambiguous-entries-not-forced: The prompt eval needed a judge to read this from prose. The workflow reports each line's category and treatment, so code checks that every entry the reference leaves uncategorized is kept, uncategorized, as an unresolved outflow.
- no-invented-budgets: The workflow reads its two targets from configuration, which the case sets from budget_notes.txt; code checks that the budget comparisons use exactly those targets and no other category.

Checks that do not apply to a code-controlled workflow (left out of the pass rates):
- code-executed: Python computes every total by construction (ExpenseCalculationProcessor); no model session runs tools whose log could be inspected.
- tables-before-interpretation: The report layout is fixed code (ComposerExpenseAnalysisReport renders every table before the findings); no model decides the order.
