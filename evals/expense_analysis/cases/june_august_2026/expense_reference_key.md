# Expense analysis reference key

This key covers a synthetic teaching log for June through August 2026 and was recalculated from the expense file before consulting the earlier answer key. All 30 category cells in that earlier key reproduce exactly under its stated conventions. The revisions clarify assumptions, distinguish logged entries from confirmed spending, and correct its claim about which category is largest most months.

Revised 2026-10-10: Steam in Shopping is now an accepted alternative policy, like books in Shopping.

Revised 2026-10-11 (this workspace's copy only, for the code-controlled workflow): merchant-only Amazon, Target and Costco charges may be put in Other, because the log does not say what was bought. The Skills and Evals copy of this key does not accept this policy. The main tables are unchanged. The entry-level form of this key, which the eval harness uses to explain wrong totals, is `evals/expense_analysis/cases/june_august_2026/reference_ledger.json` in the Skills and Evals workspace.

## Conventions for the main tables

The main tables preserve the earlier facilitator conventions for continuity. They are a conditional reference, not the only valid answer obtainable from the two input files:

- Count the two identical Trader Joe's entries on July 22 once, retaining source line 111 and excluding line 112. The input alone establishes a likely duplicate, not proof of a single purchase.
- Put Barnes & Noble in Entertainment. An equally reasonable broad Shopping policy gives different shopping totals.
- Put Steam in Entertainment. Steam is a store that sells games, so Shopping is an equally reasonable choice that gives a different June shopping total.
- Put the August 16 Uber charge of $24.10 in Travel. This is an assumed trip allocation. The log gives a date and merchant but does not state the purpose.
- Keep both August 14 Delta charges, $412.60 and $450.60. They have different amounts. Neither the shared date nor the merchant is a reason to delete either charge.
- Preserve the July 18 REI refund as negative $68.00 in Shopping. Use net category totals.
- Keep all eight unexplained transfers and cash withdrawals in Uncategorized. Include them only in a separately labeled sum of logged entries.
- Interpret yearless dates as 2026 from the explicit dates in the same log. Treat dollar amounts as USD for this exercise. These are declared assumptions, not additional source fields.

## Monthly totals

Amounts are USD, to the cent. Uncategorized entries are not established consumer expenses. The period is a three-month window, not a calendar quarter.

| Category | June | July | August | Three months |
| --- | --- | --- | --- | --- |
| Groceries | $624.34 | $829.20 | $982.79 | $2,436.33 |
| Dining and coffee | $212.74 | $165.14 | $398.69 | $776.57 |
| Shopping | $960.07 | $756.72 | $1,061.93 | $2,778.72 |
| Transport | $461.41 | $373.67 | $538.48 | $1,373.56 |
| Subscriptions | $55.45 | $55.45 | $55.45 | $166.35 |
| Utilities | $143.13 | $143.13 | $143.13 | $429.39 |
| Health and fitness | $344.25 | $271.17 | $222.80 | $838.22 |
| Entertainment | $204.12 | $135.51 | $317.26 | $656.89 |
| Travel | $0.00 | $0.00 | $1,655.92 | $1,655.92 |
| Uncategorized | $271.20 | $105.79 | $269.74 | $646.73 |
| Categorized net entries | $3,005.51 | $2,729.99 | $5,376.45 | $11,111.95 |
| All signed logged entries | $3,276.71 | $2,835.78 | $5,646.19 | $11,758.68 |

## Budget comparisons

The budget note specifies under $260 per month for eating out and coffee, and roughly $1,200 per month for shopping. The latter is an approximate planning target. Negative variance means below target. No target is invented for another category, and monthly targets are not treated as transferable allowances between months.

| Month | Category | Net entries | Monthly target | Amount above or below target | Variance |
| --- | --- | --- | --- | --- | --- |
| 2026-06 | Dining and coffee | $212.74 | $260.00 | -$47.26 | -18.18% |
| 2026-06 | Shopping | $960.07 | $1,200.00 | -$239.93 | -19.99% |
| 2026-07 | Dining and coffee | $165.14 | $260.00 | -$94.86 | -36.48% |
| 2026-07 | Shopping | $756.72 | $1,200.00 | -$443.28 | -36.94% |
| 2026-08 | Dining and coffee | $398.69 | $260.00 | +$138.69 | 53.34% |
| 2026-08 | Shopping | $1,061.93 | $1,200.00 | -$138.07 | -11.51% |

## Month over month changes

Percentage change uses the earlier month's category total as its denominator. A change from zero has no defined percentage rate, including zero to zero.

| Category | June to July | Change | July to August | Change |
| --- | --- | --- | --- | --- |
| Groceries | +$204.86 | 32.81% | +$153.59 | 18.52% |
| Dining and coffee | -$47.60 | -22.37% | +$233.55 | 141.43% |
| Shopping | -$203.35 | -21.18% | +$305.21 | 40.33% |
| Transport | -$87.74 | -19.02% | +$164.81 | 44.11% |
| Subscriptions | $0.00 | 0.00% | $0.00 | 0.00% |
| Utilities | $0.00 | 0.00% | $0.00 | 0.00% |
| Health and fitness | -$73.08 | -21.23% | -$48.37 | -17.84% |
| Entertainment | -$68.61 | -33.61% | +$181.75 | 134.12% |
| Travel | $0.00 | Not defined | +$1,655.92 | Not defined |
| Uncategorized | -$165.41 | -60.99% | +$163.95 | 154.98% |

The largest absolute category change from June to July is Groceries, +$204.86, narrowly above Shopping's $203.35 decrease. From July to August, Travel increases by $1,655.92, followed by Shopping at $305.21 and Dining and coffee at $233.55.

## Reconciliation

| Month | Rows included | Positive entries | Refunds | All signed entries | Uncategorized | Categorized net entries |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06 | 62 | $3,276.71 | $0.00 | $3,276.71 | $271.20 | $3,005.51 |
| 2026-07 | 56 | $2,903.78 | -$68.00 | $2,835.78 | $105.79 | $2,729.99 |
| 2026-08 | 79 | $5,646.19 | $0.00 | $5,646.19 | $269.74 | $5,376.45 |

The raw file has 201 lines: 198 transaction rows, two introductory lines, and one blank line. All 198 transaction rows parse under the declared date assumptions. No transaction row is silently discarded. The ledger preserves both candidate duplicate rows, their original text, and inclusion flags for each scenario.

Raw positive entries total $11,880.80. The $68.00 refund gives $11,812.80 across all signed source entries. Excluding one possible duplicate gives $11,758.68. Within that conditional result, $11,111.95 is categorized net entries and $646.73 remains unresolved. Cash withdrawals and transfers may duplicate spending recorded elsewhere, so neither grand total is proof of actual consumption.

## What changes under another reasonable policy

| Choice | Consequence |
| --- | --- |
| Retain both identical Trader Joe's rows | July Groceries is $883.32 rather than $829.20. July all signed entries are $2,889.90 rather than $2,835.78. Three-month all signed entries are $11,812.80 rather than $11,758.68. |
| Treat Barnes & Noble as Shopping | Move $60.70 in June and $156.38 in August from Entertainment to Shopping. Shopping becomes $1,020.77, $756.72, and $1,218.31. August is then $18.31, or 1.53%, above the approximate shopping target. All-entry totals are unchanged. |
| Treat Steam as Shopping | Move $47.58 in June from Entertainment to Shopping. June Shopping becomes $1,007.65, or $1,068.35 if Barnes & Noble is also Shopping, and June Entertainment becomes $156.54 (or $95.84 with both moves). The three-month Shopping total becomes $2,826.30, or $3,043.38 with both moves. July and August are unchanged, so every budget result above still holds. All-entry totals are unchanged. |
| Put merchant-only Amazon, Target and Costco charges in Other | Move $387.59, $230.10 and $348.95 from Shopping, and $196.90, $224.63 and $405.69 from Groceries, to Other in June, July and August. Shopping becomes $572.48, $526.62 and $712.98 ($1,812.08 total), still under the approximate target every month. Groceries becomes $427.44, $604.57 and $577.10, and Other is $584.49, $454.73 and $754.64. Travel is still the largest July-to-August increase. All-entry totals are unchanged. |
| Keep August 16 Uber in Transport | August Travel is $1,631.82 and Transport is $562.58. All-entry totals are unchanged. |

These choices can be varied independently. The package includes three fully computed scenarios: all source rows with broad Shopping and all Uber in Transport, that same policy with one candidate duplicate excluded, and the facilitator conventions used above. Each has monthly totals, budget comparisons, all category changes, and reconciliation tables. The Steam choice was added after the package was built and is not one of its computed scenarios; the workspace ledger computes it.

The shopping conclusion must carry its policy. “Shopping is below target in all three months” holds for the facilitator scenario and fails for the broad Shopping scenario. The dining budget finding does not change under any of these scenarios.

## Entries that remain unresolved

| Line | Date | Description | Amount |
| --- | --- | --- | --- |
| 47 | 2026-06-20 | Zelle Payment | $102.66 |
| 51 | 2026-06-21 | ATM Cash Withdrawal | $100.00 |
| 60 | 2026-06-25 | Venmo - Sam | $68.54 |
| 91 | 2026-07-09 | Venmo - Sam | $75.00 |
| 117 | 2026-07-29 | ATM Cash Withdrawal | $30.79 |
| 125 | 2026-08-02 | Zelle Payment | $120.00 |
| 141 | 2026-08-07 | PayPal Transfer | $69.90 |
| 161 | 2026-08-13 | ATM Cash Withdrawal | $79.84 |

For each row, the useful follow-up is its purpose and whether it is already represented by another logged purchase. The log does not establish that a transfer was a purchase or that a withdrawal was fully spent. The merchant-based assumptions also have limits: Costco can include non-grocery purchases, pharmacies can sell non-health items, and general retailers do not identify what was bought. Keep the stated policy unless item-level evidence is supplied.

## Category policy

| Category | Merchant policy for the main tables |
| --- | --- |
| Groceries | Trader Joe's, Aldi, Kroger, Costco, Whole Foods |
| Dining and coffee | Local Coffee Co, Chipotle, McDonald's, Rustic Tap Brewery, Panera Bread, Starbucks, Sushi Kame, Thai Orchid, Bagel Corner, Luigi's Pizza |
| Shopping | Amazon, Best Buy, Home Depot, Target, REI, Old Navy, IKEA |
| Transport | Shell, Chevron, BP Gas, Lyft, Uber except source line 173, City Parking Authority, Jiffy Lube |
| Subscriptions | iCloud+ 200GB, Amazon Prime, Netflix, Planet Fitness App, Spotify Premium |
| Utilities | Xfinity Internet, Verizon Wireless |
| Health and fitness | Planet Fitness, Riverside Physical Therapy, Walgreens, CVS Pharmacy, Dr. Hensley Dental Copay |
| Entertainment | Steam, Spotify Concert, AMC Theatres, Bowl-O-Rama, Ticketmaster, Barnes & Noble |
| Travel | Delta Air Lines, Airport Parking, Marriott, Hertz Rental Car, plus the assumed allocation of Uber on August 16 |
| Uncategorized | Zelle Payment, Venmo - Sam, ATM Cash Withdrawal, PayPal Transfer |

Planet Fitness and Planet Fitness App stay separate. So do Amazon and Amazon Prime, and Spotify Concert and Spotify Premium. The policy labels do not establish that the gym charges are visits, that app charges are membership fees, or that each airline charge represents a separate flight.

## Three teaching conclusions after the tables

1. Dining and coffee exceeds its monthly target in August alone: $398.69 against $260, a $138.69 or 53.34% excess. June and July are below target. The sequence declines and then rises, so it does not establish a steady upward trend. Three-month dining totals $776.57, but that aggregate should not conceal the August breach.
2. Travel is the largest driver of the July-to-August increase. Clearly travel-related merchants account for $1,631.82 in August, or $1,655.92 if the August 16 Uber is allocated to Travel. The input suggests a cluster of travel activity, but does not prove one trip or the number of flights.
3. The answer depends on accounting choices as well as arithmetic. Shopping totals $2,778.72 across the window under facilitator conventions and is the largest category over the whole period. It leads only in June: Groceries leads in July and Travel in August. A broad shopping definition changes August's budget result, while $646.73 of unclear entries prevents a fully classified spending total.

## How to grade the exercise

Require a complete parsed ledger, explicit merchant rules, correct signs, a flagged duplicate candidate, both Delta charges, and unresolved transfers. Accept alternative reasonable category policies when they are disclosed and the arithmetic follows them. Do not mark a model wrong solely because it leaves the August 16 Uber in Transport, puts books in Shopping, or puts Steam in Shopping. If the author confirms the Trader Joe's repetition was deliberately one duplicated purchase, the deduplicated scenario becomes the fixture's reference count. That confirmation is information beyond the raw log.

The previous key's 30 category amounts and $11,758.68 total are reproducible. Its claim that Shopping is largest in “most months” is incorrect under its own table. Its descriptions of one trip, two flights, and specific gym-charge purposes are more certain than the raw descriptions justify.

## Reproduce the calculations

The accompanying ZIP contains the full runnable source code and the 198-row ledger, with 1-based source line numbers and original source text. It requires Python 3 and only standard-library modules.

```sh
python compute_answer_key.py
python validate_answer_key.py
python build_reference_key.py
```

Money is parsed with Decimal and stored as integer cents before aggregation. The essential calculation is:

```python
cents = int(Decimal(amount_text.replace("$", "")) * 100)
monthly[month][category] += cents
budget_variance = monthly[month][category] - budget_cents
month_change = current_month_cents - previous_month_cents
```

The full parser handles the inconsistent date positions and punctuation, preserves the negative refund, and records year inference. A separate validation script sums raw amounts independently, checks all transaction rows against source text, reconciles category sums to source totals, and compares all 30 earlier-key cells after applying its stated conventions. It passes 442 checks, including source byte counts, exact duplicate identity, and the two distinct Delta amounts.

Key package files:

- `transaction_ledger.csv`: all 198 original rows, parsed fields, broad-policy categories, facilitator categories, refund and duplicate flags, and scenario inclusion flags
- `compute_answer_key.py`: complete parser, category policies, aggregation, and table export
- `validate_answer_key.py` and `validation_results.json`: independent controls and results
- `computed_results.json`: every computed scenario in one machine-readable file
- `monthly_totals_*.csv`, `budget_comparison_*.csv`, `month_changes_*.csv`, and `reconciliation_*.csv`: complete tables for each scenario
- `personal_expenses.txt`, `budget_notes.txt`, and `robust_prompt.md`: source text snapshots
- `prior_key_comparison.csv`: all 30 old-key category cells, recomputed values, and zero differences

## Sources

- [Expense log](https://drive.google.com/file/d/1OrWXVxJe4PZ3QTXR2-nNt7yWavNlBzhq/view), 6,061 bytes, retrieved September 30, 2026
- [Budget notes](https://drive.google.com/file/d/1AgniCDbI7hUIk1Vd_B4k9hDIbx-EBFC7/view), 591 bytes
- [Detailed demonstration prompt](https://drive.google.com/file/d/1gl4pGaqJreFRj8pJKYo0jCxqjMlJe5ah/view), 1,247 bytes
- [Earlier facilitator key](https://docs.google.com/document/d/1KzQkflesc7OJr3WoVc4xBDjsUtrA0msvw3f86dsA0QU/edit), consulted only after the independent source computation

The expense snapshot SHA-256 is `23f06bd1f2a7206c65aea4ee4bb7debb7e5b2a146e9ec8cd135c80ca61a23833`. Source files were read without modifying Drive content.
