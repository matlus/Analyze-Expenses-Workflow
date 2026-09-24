# Jev classification experiment on the original expense log

The local test fixture is a copy of the Drive `personal_expenses.txt` source.
Its two introductory lines and following blank line are excluded from the
domain call, leaving 198 transaction lines. The expected-category TSV was
derived from the example's answer key merchant mapping. The answer key allows
either Travel or Transportation for the August 16 Uber, and so does the test.
No Drive file was changed.

The opt-in acceptance test calls `DomainFacade.analyze_expenses` with all 198
lines, preserves source order, and compares every returned category with the
TSV. It checks Jev classification only. Parsing amounts and dates, removing
duplicates, and netting refunds are later workflow steps.

## Observed runs

| Category wording | Exact matches | Mismatches | Notes |
| --- | ---: | ---: | --- |
| Neutral category descriptions | 192/198 | 6 | Eight ambiguous transfers and withdrawals were uncategorized as expected. |
| Broad retail examples added after the first run | 181/198 | 17 | Many Costco and Barnes & Noble lines shifted incorrectly to Shopping. |

The neutral descriptions are retained in the code. Their six mismatches were:
Steam classified as Bills & Utilities; four merchant-only Amazon or Target
charges classified as Uncategorized; and the REI refund classified as
Uncategorized rather than Shopping. One Target error had confidence 0.95.

These are observations from individual live runs, not a measured long-run
accuracy rate. Jev decisions can vary across calls. The strict full-log test
currently fails and is intentionally kept as a diagnostic of the remaining
classification gaps. A passing result would not establish that the same
wording generalizes to new expenses.
