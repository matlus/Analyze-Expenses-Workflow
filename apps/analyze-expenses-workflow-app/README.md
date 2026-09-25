# Analyze Expenses Workflow App

This console host reads an expense text file, passes its transaction lines to
the asynchronous domain facade, and renders the returned analysis as Markdown.
The test fixture contains 198 transactions after a known introductory
paragraph. For other files, each nonblank line is submitted as an expense;
unrecognized introductory text remains visible to parsing and quality checks.
Empty lines between transactions are ignored.

From the workspace root, after configuring `.env`:

```powershell
uv run --all-packages --all-groups analyze_expenses_workflow_app --input packages/analyze-expenses-workflow/tests/fixtures/personal_expenses.txt --confirmed-duplicate-line 109 --output expense-analysis.md
```

To analyze another file, pass `--input path/to/expenses.txt`. A repeated line is
excluded only when its line number is supplied through
`--confirmed-duplicate-line`; the app verifies that it matches the preceding
source line. The worked example's answer key confirms line 109 as a duplicate.
The report includes
the transaction ledger, monthly category and overall totals, budget comparisons,
month-to-month changes, reconciliation checks, the calculation method, and three
findings. Source line numbers in the report refer to the transaction list passed
to the domain facade, starting at one after the introductory paragraph is removed.
The workspace's `examples/expense-analysis.md` is a saved live report for the
198-line fixture under the fixed expense category policy.
