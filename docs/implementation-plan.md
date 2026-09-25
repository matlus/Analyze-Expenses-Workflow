# Full expense-analysis workflow plan

## Goal

Turn the original 198 expense lines into an inspectable analysis through the
public asynchronous `DomainFacade.analyze_expenses(list[str])` method. The
manager returns immutable domain data sufficient to render the demonstration
prompt's ledger, monthly tables, budget comparisons, changes, quality checks,
and three evidence-backed findings.

## Policy and contracts

1. Define an immutable `ExpenseCategoryCatalog` with explicit descriptions and
   stable identifiers. Jev selects among these options. Merchant-only charges
   from mixed retailers such as Target, Amazon, and Costco belong to `Other`;
   transfers and cash withdrawals without a known spending purpose remain
   `Uncategorized`. Record intentional differences from the worked example.
2. Define immutable result models for source-line parsing, transaction review
   state, category decisions and probabilities, monthly and category totals,
   budget comparisons, month-to-month changes, quality checks, and findings.
   Keep source line numbers through every stage.
3. Keep subscription, model, and reasoning-effort settings typed and specific
   to each LLM operation and model family. The manager selects and owns gateways and passes
   domain-facing protocols to processors. A Jev operation has no reasoning
   effort setting.

## Pipeline

1. Validate every input line and extract date, description, and signed amount.
   Offer ambiguous date and amount candidates to Jev for a bounded choice;
   send unresolved fields to evidence-checked LLM extraction. Never silently
   drop a source line.
2. Classify each line with Jev using the catalog. Preserve its probabilities
   and the source line. Apply the explicit `Other`/`Uncategorized` policy.
3. Verify caller-confirmed duplicate line numbers against adjacent verbatim
   source lines and identify refund transactions. Keep both in the ledger,
   exclude only confirmed duplicates from totals, and
   preserve refunds as negative amounts. Distinguish unresolved outflows from
   unresolved credits and known spending.
4. Compute all totals and budget comparisons in Python using `Decimal`. Rank
   month-to-month changes from those calculated values. Report both observed
   cash outflow (including unresolved transfers/withdrawals) and classified
   spending (excluding them); never label the former as verified spending.
5. Validate line coverage, inclusion decisions, and reconciliation. Ask the
   configured LLM operation to select three IDs from computed evidence. Render
   the exact evidence statements in code, retaining their source line numbers.
6. Render the typed result in the console app with tables before findings and
   calculation provenance available to the caller.

## Verification gates

- Unit tests cover parsing variants, refund signs, duplicate decisions,
  category policy, and arithmetic with exact decimal values.
- Offline integration tests exercise the parsing, categorization,
  reconciliation, and calculation processors over all 198 source lines with a
  controlled Jev protocol implementation. Live acceptance tests exercise the
  domain facade and subscription gateways.
- Opt-in live tests verify Jev and the selected subscription SDK against the
  full source fixture. A separate subscription smoke check verifies the other
  gateway. Report model differences separately from policy differences.
- Run Ruff, Pyright, all routine tests, and the installed PWI code-review
  workflow over the explicit source scope. Resolve supported findings and
  repeat verification.

## Evidence and limits

The worked example and answer key are comparison evidence, not the category
policy for this implementation. The original 198-line fixture remains intact.
The answer key includes $646.73 of ambiguous cash movements in its $11,758.68
grand total. Our classified-spending total will exclude those movements and
reconcile them separately. The $1,200 shopping budget comparison must flag
that mixed-retailer merchant-only charges are assigned `Other`, so Shopping
alone does not cover every potentially relevant purchase.
The repository currently has no Git metadata, so PWI review scope will be
specified explicitly. Live SDK capability and authentication will be verified
before declaring a subscription path operational.
