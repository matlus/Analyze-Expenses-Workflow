# Expense analysis control flow

This document is the source for a control-flow diagram of the current application. It defines the box names, actors, short descriptions, connections, and the data passed between steps. A diagram can be drawn from this document without consulting an existing picture. The implementation links at the end let a programmer check or update the description when the code changes.

## Purpose and boundaries

The application reads a loosely written expense log and produces an inspectable Markdown report. The report keeps each source line in a ledger, shows how that line was treated, calculates monthly spending and budget comparisons, and presents three findings tied to calculated evidence. A reader can trace a total or finding back to the input lines that support it.

The flow has three kinds of work:

| Role in the diagram | Responsibility |
| --- | --- |
| **Code** | Read and parse text, preserve source evidence, apply category policy, decide ledger treatment, calculate money, validate totals, and render the report. This role could be implemented in another language. |
| **System 1 / Jev** | Answer a bounded choice question: select a date or amount from candidates found in a source line, or choose one category from the fixed expense catalog. |
| **Large language model (LLM)** | Propose fields for lines that remain uncertain, or select three IDs from findings already written by code. Code validates both responses. |

Jev's choices and the LLM's proposals are inputs to the coded workflow. Neither service writes the ledger, performs the financial calculations, or edits the final report. The current implementation uses Python and can use either the Codex or GitHub Copilot gateway for its LLM calls. Before analysis, configuration selects that gateway and the model and reasoning effort for LLM extraction and finding selection; Jev uses its separate OpenRouter settings. These are setup details, not separate boxes in the flow.

The controller proceeds from file input to parsing, Jev ambiguity choices, LLM field completion, per-line category tasks, gathering, ledger reconciliation, calculations, balance checks, finding selection, and report delivery. The category tasks are the only scatter/gather part of that sequence.

The JSON blocks below make in-memory objects and service exchanges readable. Dates and decimal amounts are shown as strings. Unless a block is identified as a service response, it illustrates data shape rather than claiming that the application stores that object as JSON.

## How to draw the diagram from this document

Use each numbered heading as a box title and its **Short description** as the smaller line inside the box. Show the **Actor** as a visible badge or other consistent marker. Keep inputs, outputs, examples, and explanations outside the boxes. Place boxes 01 through 05 vertically. At 05, fan out into one lane per source line: a Jev category choice above a coded policy check. Draw a few lanes side by side and mark the last lane as repeating for more lines; there are not exactly three workers. Incomplete lines bypass both choice and policy boxes and return an empty category slot. All lanes converge at 06. Place boxes 06 through 13 vertically below the gather.

Connect successive vertical boxes with straight downward arrows. Use curved arrows with enough vertical space to make the fan-out from 05 and fan-in to 06 legible. The gather waits for every line task before 07 starts. The Jev choice and coded policy check within each lane are sequential; different lanes can run concurrently, with at most eight Jev category calls active at once. Steps 03 and 04 are sequential recovery stages and pass data through when no recovery is needed. Do not add a configuration box. Use rounded cards on a light background. A colored accent on the left side of each card should stop within the straight section of that side, short of both rounded corners. Include a legend. If actors are color-coded, keep the same color for each actor throughout; a separate gate or output tint may indicate 05, 06, 09, or 13 without hiding that those steps are code.

## Example input

The running example is a small, invented UTF-8 input file. It is designed to exercise the branches below. It is not a recorded execution of the live decision services.

```text
2026-06-01 | Kroger | $82.18
2026-06-01 | Kroger | $82.18
2026-06-02 | Cafe | charge $7.50; available balance $102.00
2026-06-03 | ATM withdrawal | $40.00
2026-06-04 | Bank transfer | -$15.00
2026-07-01 | Kroger | $50.00
2026-07-02 | Target | $20.00
2026-07-03 | Unknown store | amount missing
```

For this example the caller confirms **line 2** as a duplicate of line 1. The illustrative monthly targets are **$100.00 for Dining & Coffee** and **$100.00 for Shopping**. To make the path concrete, suppose Jev abstains on line 3's amount, the LLM extracts `$7.50` from that line, and neither service can establish an amount for line 8. The category choices shown later are also illustrative; their policy overrides and all arithmetic follow the code. Live Jev and LLM responses may differ.

There is no required CSV column order. Dates may be named, numeric, or ISO-style; currency symbols and separators vary. The application numbers every retained line from 1, including blank lines. For its supplied fixture it removes a specific two-line preamble and one immediately following blank separator. This example has no blank lines, so its line numbers are 1 through 8. The explicit years make year inference unnecessary here.

The repeated Kroger line is not removed when reading the file. Its exclusion happens later, after the caller's confirmation and an exact adjacent-line check. Every input line remains represented in the final ledger.

## The numbered flow

### 01 Read expense file

#### Actor: Code

**Short description:** Load the ordered source lines.

The console entry point takes an `--input` path and reads the file as UTF-8 with an optional byte-order mark. It preserves blank and unrecognized lines in order. Only when the first two lines match the fixture preamble exactly does it remove them and one immediately following blank separator. It also rejects an `--output` path that points to the input file.

**Input:** A text file path, optional output path, and confirmed duplicate line numbers. **Output:** Eight strings in their original order, passed to `DomainFacade.analyze_expenses` with `(2,)` as the duplicate confirmation. No line has been categorized or excluded yet.

### 02 Validate and parse lines

#### Actor: Code

**Short description:** Extract dates, descriptions, and signed amounts.

Code first rejects an empty list or a non-string entry. It then scans the lines for year evidence. It may infer one year when only one distinct explicit year appears and the sequence does not appear to cross a year boundary. Otherwise, a date without a year stays unresolved. Every example line already names 2026. A retained blank line also gets a line number; missing fields are reported for that line rather than ending the batch.

For each line, the parser looks for one date and one transaction amount. It supports named, numeric, and ISO-style dates; signed amounts, dollar signs, comma separators, and parenthesized negatives. It constructs a date and a decimal amount, removes matched date and amount text to form a description, and records issues such as `multiple_dates`, `multiple_amounts`, `missing_amount`, or `year_not_inferable`. A single amount labeled as an account balance is not accepted as the transaction amount. An explicit refund or return label can turn an otherwise positive amount into a negative credit. The original line remains attached to the result.

**Input:** The eight retained strings. **Output:** One `ParsedExpenseLine` per string, in the same order. The actual parser fields relevant to this example are:

| Line | Date | Description after removing date and amount tokens | Amount | Issue |
| --- | --- | --- | ---: | --- |
| 1 | 2026-06-01 | Kroger | $82.18 | none |
| 2 | 2026-06-01 | Kroger | $82.18 | none |
| 3 | 2026-06-02 | Cafe \| charge ; available balance | unknown | `multiple_amounts` |
| 4 | 2026-06-03 | ATM withdrawal | $40.00 | none |
| 5 | 2026-06-04 | Bank transfer | -$15.00 | none |
| 6 | 2026-07-01 | Kroger | $50.00 | none |
| 7 | 2026-07-02 | Target | $20.00 | none |
| 8 | 2026-07-03 | Unknown store \| amount missing | unknown | `missing_amount` |

For line 3, the parser sees two currency amounts and declines to choose one. A readable object for that line is:

```json
{
  "line_number": 3,
  "source_text": "2026-06-02 | Cafe | charge $7.50; available balance $102.00",
  "occurred_on": "2026-06-02",
  "description": "Cafe | charge ; available balance",
  "amount": null,
  "issues": ["multiple_amounts"]
}
```

An unresolved field is `None` in the current implementation and appears as `null` in these JSON illustrations.

### 03 Resolve ambiguous values

#### Actor: System 1 / Jev

**Short description:** Choose among dates or amounts found in the source text.

This step runs only for a line marked with multiple date or amount candidates. Code finds the candidate occurrences in the source text and asks Jev to choose a candidate or `none`. For a date it asks which occurrence is the transaction date rather than a posting or reference date. For an amount it asks which signed amount affects the account rather than a balance or original price. Jev does not extract an unrestricted value; its answer must name one of the supplied choices. The current gateway sends the question through the TypeSafe SDK's System One operation.

Line 3 presents `$7.50` and `$102.00`. The latter is marked as an available balance, not a charge. The choice request is conceptually:

```json
{
  "state": "2026-06-02 | Cafe | charge $7.50; available balance $102.00",
  "question": {
    "instructions": "Which candidate is the signed transaction amount affecting this account, rather than a balance or original price?",
    "criteria": {
      "candidate_0": "The source occurrence 1: $7.50",
      "candidate_1": "The source occurrence 2: $102.00",
      "none": "None of the candidate values is the transaction field."
    }
  }
}
```

Jev returns a selected choice, confidence, and a probability for every offered choice. For the running example, assume it abstains:

```json
{
  "choice": "none",
  "confidence": 1.0,
  "probabilities": {
    "candidate_0": 0.0,
    "candidate_1": 0.0,
    "none": 1.0
  }
}
```

Code accepts a date or amount only if the selected choice is recognized and its probability reaches the configured minimum. It parses the selected source occurrence itself, rejects an occurrence identified as a balance, and clears the corresponding issue only when it obtains a valid value. A `none` choice, low probability, or Jev failure leaves the issue unresolved for the next step. Under the assumed response, line 3 still has `amount: null` and `multiple_amounts`. Line 8 has `missing_amount`, not multiple candidates, so it passes through without a Jev choice.

**Input:** Line 3 and its two source occurrences. **Output in this example:** The eight parsed lines, unchanged, with line 3 still pending. Lines without multiple candidates pass through.

### 04 Complete uncertain fields

#### Actor: Large language model (LLM)

**Short description:** Propose missing fields; code verifies source evidence.

The LLM receives only lines that still carry parsing issues after the Jev pass. Code sends the source text, line number, already known fields, issues, an optional inferred year, and a strict response schema. It asks for a JSON object containing one entry per pending line in the same order. For every newly proposed date, description, or amount, the model must provide the exact supporting substring from the source text. It may return `null` when it cannot establish a field. In this example the pending lines are 3 and 8.

An illustrative response for those same two lines is:

```json
{
  "lines": [
    {
      "line_number": 3,
      "source_text": "2026-06-02 | Cafe | charge $7.50; available balance $102.00",
      "occurred_on": null,
      "date_evidence": null,
      "description": null,
      "description_evidence": null,
      "amount": "7.50",
      "amount_evidence": "$7.50",
      "amount_evidence_index": 0
    },
    {
      "line_number": 8,
      "source_text": "2026-07-03 | Unknown store | amount missing",
      "occurred_on": null,
      "date_evidence": null,
      "description": null,
      "description_evidence": null,
      "amount": null,
      "amount_evidence": null,
      "amount_evidence_index": null
    }
  ]
}
```

`null` for an already known field means “leave the existing value alone.” Code checks line numbers and copied source text, preserves known values, parses proposed date and amount evidence again, rejects a balance as amount evidence, and checks that description evidence is present and descriptive. If the same amount text occurs more than once, the response must identify its zero-based source occurrence with `amount_evidence_index`; without that index, code cannot accept either occurrence. It accepts only values consistent with the source. It may retry pending lines once, including lines still incomplete or failed schema or evidence checks. A gateway failure ends extraction attempts and leaves pending lines marked `llm_extraction_failed` for the report. In this example line 3 becomes `amount: "7.50"` and its amount issue clears. No source substring gives line 8 a price, so after the last attempt its amount stays `null` and it carries `missing_amount` and `llm_extraction_failed`. If no lines have issues, there is no LLM extraction call.

**Input:** Pending lines 3 and 8. **Output:** The original ordered eight-line collection, with line 3's verified amount filled and line 8 still incomplete.

### 05 Scatter line tasks

#### Actor: Code

**Short description:** Start one task per line; incomplete lines return no category.

The manager starts one asynchronous task for every parsed line. Within each task, code checks for a date, amount, and description. An incomplete line returns an empty categorization slot (`None`) without calling Jev. A complete line enters the category decision shown in the repeated boxes below. A semaphore allows at most eight category calls to Jev at one time. The Jev ambiguity pass and LLM extraction before this point are sequential stages; this category work is the flow's explicit concurrent section. Lines 1 through 7 can be categorized; line 8 immediately contributes `None` because its amount is unknown.

**Input:** Eight ordered `ParsedExpenseLine` objects. **Output:** Eight running or completed line tasks, one result slot per line.

#### Choose category · line A / line B / line …

##### Actor: System 1 / Jev

The three diagram cards show the same operation for different lines. A, B, and the ellipsis are visual placeholders, not three fixed workers.

| Diagram box | Short description |
| --- | --- |
| Choose category · line A | Jev uses the fixed category choices. |
| Choose category · line B | An independent per-line decision. |
| Choose category · line … | More lines share the eight-call limit. |

For a complete line, code gives Jev the original source text and a choice question containing every category in the fixed catalog. The instructions say to use only the transaction's evidence, put a merchant-only mixed-retailer charge in `other` when the item is unknown, and put a purpose-free transfer or cash withdrawal in `uncategorized`. Jev returns a category choice, confidence, and probabilities for the offered categories. This is another bounded System One decision, separate from the earlier date or amount question.

For the running example, assume Jev proposes the following choices. They illustrate the exchange; a live service need not choose the same categories:

| Line | Source clue | Illustrative Jev proposal |
| --- | --- | --- |
| 1, 2, 6 | Kroger | `groceries` |
| 3 | Cafe and $7.50 charge | `dining_coffee` |
| 4 | ATM withdrawal | `other` |
| 5 | Bank transfer | `other` |
| 7 | Target, item unknown | `shopping` |
| 8 | No amount | No Jev call |

**Input:** Each complete line's source text and fixed category definitions. **Output:** One proposed category, confidence, and per-category probabilities for each of lines 1 through 7.

#### Apply category rules · A / B / …

##### Actor: Code

Code then applies explicit policy to each Jev proposal. The final category may differ from `model_category`: a merchant-only Target, Amazon, or Costco line becomes `other`; named merchants in the catalog receive their known category; and a cash movement without a stated spending purpose becomes `uncategorized`. The decision retains Jev's original category, confidence, probabilities, and a policy note when a rule changes or explains the category.

| Diagram box | Short description |
| --- | --- |
| Apply category rules · A | Code enforces merchant and cash rules. |
| Apply category rules · B | Code may override Jev's proposal. |
| Apply category rules · … | Each task returns a category or no result. |

The outcomes for the same lines are `groceries` for 1, 2, and 6; `dining_coffee` for 3; `uncategorized` for 4 and 5 despite the illustrative `other` proposals; and `other` for 7 despite the illustrative `shopping` proposal. Line 7 is a merchant-only Target charge: without an item description, the code cannot claim it was Shopping. Line 8 bypasses both repeated boxes.

**Input:** Jev's proposed category, its scores, and the parsed description. **Output:** `ExpenseCategorization` for each complete line, retaining both the proposal and final category; `None` for line 8.

### 06 Gather line results

#### Actor: Code

**Short description:** Converge all tasks and restore source-line order.

The task group waits for every line task to finish. The manager reads their results in the same order as the input lines, producing one categorization slot per parsed line. The gather makes the next step's line-by-line reconciliation possible. A category request that fails here propagates through the task group; it is not treated as a successful `None` result.

**Input:** All eight per-line task results. **Output:** The ordered categories `[groceries, groceries, dining_coffee, uncategorized, uncategorized, groceries, other, null]`. The final `null` represents line 8's missing categorization, not an omitted source line.

### 07 Reconcile the ledger

#### Actor: Code

**Short description:** Apply confirmed duplicates and transaction treatments.

Code verifies that each nonempty category result belongs to the corresponding source line. It then creates one `ExpenseTransaction` for every parsed line and assigns one treatment. The names below are internal result values. Here, **outflow** means an amount leaving the account, while **credit** means a negative amount in this ledger's signed-amount convention. Neither label proves what the money was used for.

| Treatment | Example line | Meaning in this example |
| --- | --- | --- |
| `included_spending` | 1, 3, 6, 7 | Complete, categorized charges. A categorized refund would stay here with a negative amount. |
| `excluded_duplicate` | 2 | The caller confirmed this exact copy of adjacent line 1. It stays in the ledger but contributes no second $82.18 charge. |
| `uncategorized_outflow` | 4 | The $40 ATM withdrawal leaves the account, but the record does not say what was purchased. It is reported separately from classified spending. |
| `uncategorized_credit` | 5 | The -$15 bank transfer has no established spending category. It stays visible but is not subtracted from spending or added to the reported outflow. |
| `unparsed` | 8 | The amount remains unknown. The source text stays visible; no amount from this line enters a total. |

An invalid duplicate confirmation raises an error instead of removing the line. The application never guesses which repeated entries were accidental.

**Input:** Eight parsed lines, eight aligned category slots, and confirmed duplicate line 2. **Output:** Eight ordered `ExpenseTransaction` records. Line 2 has `duplicate_of_line_number: 1`; each other record has its treatment and retains its source line.

### 08 Calculate totals and comparisons

#### Actor: Code

**Short description:** Sum monthly amounts, budgets, and changes with decimal arithmetic.

Code groups included spending by month and category and sums signed decimal amounts. Uncategorized outflows are totaled separately, then added to classified spending to show **observed outflow**, the amount of known charges plus cash leaving the account whose purpose is unknown. It is not a claim that every dollar was spent on a known purchase. The -$15 transfer on line 5 is excluded from that measure because the application does not know what the credit represents. Code compares Dining & Coffee and Shopping with their monthly targets, using `actual - target` for variance. For each adjacent pair of reported months, it calculates category change as `current - previous`. Each total and comparison carries its contributing source line numbers.

```text
classified spending = sum of signed included amounts by month and category
observed outflow    = classified spending + unresolved outflow
budget variance     = actual category spending - monthly target
monthly change      = current category amount - previous category amount
```

The running example yields:

| Month | Category | Classified amount | Source lines |
| --- | --- | ---: | --- |
| June 2026 | Dining & Coffee | $7.50 | 3 |
| June 2026 | Groceries | $82.18 | 1 |
| July 2026 | Groceries | $50.00 | 6 |
| July 2026 | Other | $20.00 | 7 |

Thus June has $89.68 of classified spending plus line 4's $40.00 unresolved cash withdrawal, or $129.68 observed outflow. July has $70.00 of classified spending and no unresolved outflow. Across both months, classified spending is $159.68 and observed outflow is $199.68. Line 2's duplicate $82.18, line 5's -$15.00 credit, and line 8's unknown amount do not enter these sums.

With the example's $100.00 targets, June Dining & Coffee is $7.50 against $100.00, a variance of -$92.50; July is $0.00 against $100.00, a variance of -$100.00. Shopping is $0.00 against $100.00 in both months because line 7's merchant-only Target charge is in Other. **Shopping coverage** is a note about which purchases the Shopping total includes. The July comparison flags line 7 because that $20.00 charge could have been for a shopping item, but the item is unknown. It is not a software test-coverage measure. From June to July, Groceries falls from $82.18 to $50.00, a change of -$32.18 using lines 1 and 6.

**Input:** The reconciled ledger and the two example targets. **Output:** Monthly category totals, monthly totals, budget comparisons, month-to-month changes, and reconciliation data in `ExpenseCalculationResult`. Neither Jev nor the LLM performs this arithmetic.

### 09 Check the totals balance

#### Actor: Code

**Short description:** Require the source lines and monetary totals to reconcile.

Code checks that source line numbers are unique, that the transaction treatments account for every line, that category totals equal monthly classified spending, and that classified spending plus unresolved outflow equals observed outflow. It also records duplicate, unparsed, credit, refund, included, and unresolved line numbers for the report. A failed balance check stops the analysis rather than publishing totals that disagree.

For the eight example lines, the reconciliation is:

```json
{
  "source_line_count": 8,
  "included_line_numbers": [1, 3, 6, 7],
  "duplicate_line_numbers": [2],
  "unresolved_line_numbers": [4],
  "unresolved_credit_line_numbers": [5],
  "unparsed_line_numbers": [8],
  "refund_line_numbers": [],
  "classified_spending": "159.68",
  "unresolved_outflow": "40.00",
  "observed_outflow": "199.68",
  "is_balanced": true
}
```

The five line-number groups contain each of 1 through 8 exactly once. The four included charges sum to $159.68; $159.68 + $40.00 = $199.68.

**Input:** `ExpenseCalculationResult.reconciliation`. **Output:** The balanced calculation result shown above, or an error.

### 10 Select three findings

#### Actor: Large language model (LLM)

**Short description:** Choose evidence IDs; code supplies the final wording.

Code turns calculated budget breaches, month-to-month changes, Shopping category limits, and reconciliation facts into candidate findings. Each candidate already has an ID, a complete statement with numbers, and supporting source line numbers. It also marks certain IDs as required when the calculations call for them. In this example the largest category change and the Shopping category limitation are required; there is no Dining & Coffee budget breach.

The LLM receives the candidates and required IDs and is asked to return exactly three distinct IDs. It is explicitly told not to write findings or calculate numbers. Three of the example's code-written candidates are:

| Evidence ID | Statement already written by code | Source lines |
| --- | --- | --- |
| `change-2026-06-01-2026-07-01-groceries` | From June 2026 to July 2026, Groceries fell by $32.18, from $82.18 to $50.00. | 1, 6 |
| `shopping-coverage` | Shopping was below its stated monthly target in 2 of 2 reported months. These Shopping totals exclude merchant-only mixed-retailer charges assigned to Other, so the comparison does not cover all shopping-like purchases. | 7 |
| `reconciliation-unresolved` | Unresolved outflows totaled $40.00 and were kept separate from classified spending. | 4 |

An illustrative valid response choosing these candidates is:

```json
{
  "findings": [
    {"evidence_id": "change-2026-06-01-2026-07-01-groceries"},
    {"evidence_id": "shopping-coverage"},
    {"evidence_id": "reconciliation-unresolved"}
  ]
}
```

Code checks that the IDs are distinct, supplied, and include every required ID. It then substitutes the exact code-written statements and source lines. It retries a malformed or unsupported selection once, then raises an error if it still cannot obtain three grounded findings. A gateway failure also stops finding selection rather than publishing an incomplete report. Another valid third ID might be selected on a live run, but the two required IDs must remain.

**Input:** Calculated evidence items and required IDs. **Output in this example:** Exactly three `ExpenseFinding` objects for the IDs shown, containing code-written text and source-line references.

### 11 Assemble the analysis

#### Actor: Code

**Short description:** Collect the ledger, calculations, findings, and method.

The manager packages the parsed lines, nonempty categorizations, full transaction ledger, calculations, three findings, method description, and calculation formulas into `ExpenseAnalysisResult`. This is the domain result returned through `DomainFacade`. It keeps the intermediate evidence available to the report composer rather than reducing the analysis to a final number. In the running example it contains eight parsed lines and transactions, seven categorizations, two monthly totals, the balanced reconciliation above, and three findings.

A compact view of the assembled result is:

```json
{
  "parsed_line_numbers": [1, 2, 3, 4, 5, 6, 7, 8],
  "categorization_line_numbers": [1, 2, 3, 4, 5, 6, 7],
  "transaction_line_numbers": [1, 2, 3, 4, 5, 6, 7, 8],
  "monthly_observed_outflow": {"2026-06": "129.68", "2026-07": "70.00"},
  "reconciled": true,
  "finding_ids": [
    "change-2026-06-01-2026-07-01-groceries",
    "shopping-coverage",
    "reconciliation-unresolved"
  ]
}
```

These keys are a readable projection of the domain object, not a claim that the application serializes it in this form. The complete object retains the amounts and source references shown in steps 07 through 10.

**Input:** The validated products of steps 02 through 10. **Output:** One `ExpenseAnalysisResult`.

### 12 Render the report

#### Actor: Code

**Short description:** Compose the Markdown tables, method, and findings.

The report composer turns `ExpenseAnalysisResult` into Markdown. It renders a transaction ledger, monthly spending by category, monthly totals, budget comparisons, month-to-month changes, quality and reconciliation checks, method and calculation formulas, and the three findings. The ledger retains source text, parsing issues or policy notes, and treatment. Totals and findings cite source line numbers.

The monthly-total table for the running example appears as a table in this document and in the report:

| Month | Classified spending | Unresolved outflow | Observed outflow | Source lines |
| --- | --- | --- | --- | --- |
| June 2026 | $89.68 | $40.00 | $129.68 | 1, 3, 4 |
| July 2026 | $70.00 | $0.00 | $70.00 | 6, 7 |

The report's quality section also shows `Source lines: 8`, `Confirmed duplicate lines: 2`, `Unresolved outflow lines: 4`, `Unresolved credit lines: 5`, `Unparsed lines: 8`, and `Reconciled: Yes`. Its three findings use the code-written statements and source lines selected in step 10. The full ledger still contains all eight lines, including those excluded from totals.

With the illustrative ID selection in step 10, the report ends with these findings:

1. From June 2026 to July 2026, Groceries fell by $32.18, from $82.18 to $50.00. (Source lines: 1, 6.)
2. Shopping was below its stated monthly target in 2 of 2 reported months. These Shopping totals exclude merchant-only mixed-retailer charges assigned to Other, so the comparison does not cover all shopping-like purchases. (Source lines: 7.)
3. Unresolved outflows totaled $40.00 and were kept separate from classified spending. (Source lines: 4.)

**Input:** `ExpenseAnalysisResult`. **Output:** A Markdown string. The composer formats existing values; it does not ask either decision service to recalculate or rewrite them.

### 13 Deliver the report

#### Actor: Code

**Short description:** Print to console; optionally write a Markdown file.

The console entry point writes the Markdown report to standard output. When `--output` was provided, it also writes the same text to that UTF-8 file. The output path must differ from the input file.

**Input:** The Markdown string containing the example's eight-line ledger, totals, checks, and findings, plus an optional output path. **Output:** The same report on the console and, when requested, in a Markdown file.

## Where to inspect the implementation

| Flow area | Source |
| --- | --- |
| Example input | [`personal_expenses.txt`](../../packages/analyze-expenses-workflow/tests/fixtures/personal_expenses.txt) |
| File input and report delivery | [`main.py`](../../apps/analyze-expenses-workflow-app/src/analyze_expenses_workflow_app/main.py) |
| Overall sequence and scatter/gather | [`manager_expense_analysis.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/manager_expense_analysis.py) |
| Parsing and source evidence | [`expense_line_parsing_processor.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_line_parsing_processor.py), [`expense_line_evidence_parser.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/evidence_parsers/expense_line_evidence_parser.py) |
| Jev candidate and category decisions | [`expense_line_jev_extraction_processor.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_line_jev_extraction_processor.py), [`transaction_categorization_processor.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/transaction_categorization_processor.py) |
| LLM extraction and finding selection | [`expense_line_extraction_llm_processor.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/llm_processors/expense_line_extraction_llm_processor.py), [`expense_findings_llm_processor.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/llm_processors/expense_findings_llm_processor.py) |
| Ledger, calculations, and report | [`expense_reconciliation_processor.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_reconciliation_processor.py), [`expense_calculation_processor.py`](../../packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_calculation_processor.py), [`composer_expense_analysis_report.py`](../../apps/analyze-expenses-workflow-app/src/analyze_expenses_workflow_app/composers/composer_expense_analysis_report.py) |

## Rebuild the diagram and explorer

The box titles, actors, short descriptions, repeated-lane labels, and right-side details come from this document. The [build script](build_expense_analysis_control_flow.py) applies the layout rules above and writes the standalone interactive HTML beside this file. The HTML embeds the SVG diagram. The script requires `markdown-it-py` and `Pygments` only while building; the generated HTML needs no server or external packages to open.

```powershell
uv run --with markdown-it-py --with pygments python docs/architecture/build_expense_analysis_control_flow.py
```
