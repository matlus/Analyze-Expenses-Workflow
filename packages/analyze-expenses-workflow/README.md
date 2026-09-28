# Analyze Expenses Workflow

Import `DomainFacade` and its public result and exception types from
`analyze_expenses_workflow.domain_facades`. The package root retains the same
exports for existing callers; the facade namespace owns the single `__all__`
list. Implementation classes remain under `managers` and `models`.

The public entry point is `DomainFacade.analyze_expenses(list[str])`. It is
asynchronous and returns a frozen `ExpenseAnalysisResult` containing the
source-line ledger, category decisions and probabilities, calculated tables,
reconciliation, three source-backed findings, and the calculation method.

`ManagerExpenseAnalysis` owns the sequence and both external gateways. It
obtains typed settings from `ConfigurationProvider`, selects either the Codex
or Copilot subscription gateway, and passes its protocol to the two LLM
processors. The line-extraction processor uses structured JSON only when
Python parsing leaves a field uncertain. The findings processor asks the LLM
to select evidence IDs; Python writes the numerical statements from validated
calculations. `TransactionCategorizationProcessor` calls Jev through the
TypeSafe/OpenRouter gateway, applies the immutable expense category catalog,
and records any explicit policy override.

The returned `ExpenseAnalysisResult` has these inspectable parts:

- `parsed_lines` and `transactions` retain every input line and its treatment;
- `categorizations` retain Jev's choice, probabilities, and policy note;
- `calculations` contains monthly category totals, monthly overall totals,
  budget comparisons, month-to-month deltas, and a reconciliation record;
- `findings` contains exactly three typed evidence statements with source lines;
- `method` and `calculation_code` explain the calculation contract.

The facade namespace exports each type in that result graph, including the
category, transaction treatment, parsed line, calculation records, and finding.
Internal configuration and processors are not part of that namespace. Exception
diagnostic values such as action, log event, and severity remain readable through
public exception properties; tests can compare their values without importing
internal diagnostic enum types.

The remaining processors parse, reconcile, and calculate. The caller may pass
confirmed duplicate line numbers; each must match the preceding source line
before exclusion. Refund labels without an explicit minus sign are treated as
credits; refunds reduce their category total. Transfers and cash withdrawals without
a stated spending purpose remain `Uncategorized` and are reported separately
from verified spending. Merchant-only Target, Amazon, and Costco entries are
`Other`; this makes the coded Shopping totals intentionally different from
the worked example. Every calculation and finding retains source-line
references so a caller can inspect the decision.

The `.env` file selects `CODING_ASSISTANT_SUBSCRIPTION` as `GPT_CODEX` or
`GITHUB_COPILOT`; both select the OpenAI family. Each LLM operation has its own
model and reasoning effort, such as `EXPENSE_LINE_EXTRACTION_OPENAI_MODEL` and
`EXPENSE_LINE_EXTRACTION_OPENAI_REASONING_EFFORT`. Family override keys are
rejected. The legacy unqualified operation keys remain supported for OpenAI.
Jev uses
`OPEN_ROUTER_KEY`, `OPEN_ROUTER_BASE_URL`, and `JEV_MODEL`. Monthly target
settings are `DINING_COFFEE_MONTHLY_BUDGET` and `SHOPPING_MONTHLY_BUDGET`.
See the root `.env.example` for working defaults; the actual `.env` is ignored.
