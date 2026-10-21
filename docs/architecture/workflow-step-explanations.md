# Expense analysis: the five steps explained

Companion notes for the “Code controls the flow” diagram. These are speaking notes and reader explanations, not five dense slides.

## How to read the diagram

Start at the top of the code controller and follow the numbered steps downward. A horizontal arrow means the controller calls a worker to do a particular job. The worker returns to the controller; the diagram leaves out return arrows to keep it readable.

The controller decides which work is needed, checks the returned result, and decides what runs next. Some checks happen in the called processors rather than directly in the top-level controller method. They are still checks performed by application code—not promises delegated to the model.

The colors show who does the work:

- **Teal — code:** parsing rules, validation, business policy, bookkeeping, calculations, and rendering.
- **Amber — Jev:** choosing between options supplied by code.
- **Violet — LLM:** extracting unresolved fields or choosing report highlights.

“Code verifies” means code checks specific properties it can establish. It does not mean code can prove every semantic judgment correct. This distinction matters throughout the workflow: a supported, well-formed answer can still be the wrong interpretation.

The short examples below are illustrative, not claims about a particular recorded model response. The explanations follow repository revision `e13c09b533f7b85e394f247ac2b15f2a70e6e57d`.

---

## Step 1 — Parse expense lines

### What this step is doing

**Turn each source line into a structured record, without asking a model to interpret it first.**

The input is a list of expense lines. For each line, code tries to read the transaction date, description, and signed amount. It keeps the original text and its line number alongside those fields.

For example, a line containing one recognizable date, a merchant description, and one transaction amount can often be parsed using ordinary rules. There is no reason to ask a model to repeat work code can already do.

### What code checks

The parser looks for supported date and amount formats. It checks that a date is valid and distinguishes a transaction amount from an amount marked as a balance. It uses decimal arithmetic for money and handles explicit refund wording so a refund is represented as a negative amount when supported by the source.

A missing year is not automatically filled with the current year. The parser can use a year supported by the supplied data when its year-inference rules allow it; otherwise the year remains unresolved. This avoids silently assigning an expense to the wrong period.

The description is taken from the remaining source text after recognized date and amount evidence is removed and the text is cleaned up.

### What happens when parsing is uncertain

Suppose a line says:

> Cafe charge $18.50; balance $2,400.00

There are two amount occurrences. Code can recognize the numbers, but recognizing a number is not the same as establishing which number represents the transaction. The parser records the ambiguity rather than picking one arbitrarily.

Other problems include a missing amount, multiple dates, an invalid date, an unresolved year, or a missing description. These are recorded as issues on the line. Fields that cannot be established remain unknown; fields already established are retained.

### What comes back to the controller

A structured record for **every source line**, containing:

- Its original text and line number.
- Any date, description, and amount already established.
- A list of unresolved issues.

Nothing disappears simply because parsing was unsuccessful. The controller now has enough information to decide whether semantic help is needed.

**Why this step exists:** use predictable rules for ordinary cases, preserve the evidence, and identify the precise work a model needs to do.

---

## Step 2 — Resolve uncertain fields

### What this step is doing

**Recover missing or ambiguous transaction fields, using semantic interpretation only where parsing left a problem.**

A line with no remaining issues does not need model extraction. For an uncertain line, there are two kinds of help: Jev can choose among source candidates, and the LLM can attempt structured extraction if issues remain.

### First: Jev chooses among source candidates

In this workflow, Jev is used through a choice interface. Code provides the source text, the question, and the allowed options. Jev returns a selected option, a confidence value, and probabilities for the options.

For an ambiguous amount, the question is essentially:

> Which source amount is the signed transaction amount, rather than a balance or original price?

For an ambiguous date:

> Which source date is the transaction date, rather than a reference or posting date?

Code extracts the candidate occurrences from that one source line. These are **not candidate expenses from the entire input**. They are possible values for one field within one expense line.

The options also include **“none”**. This lets Jev indicate that none of the supplied values is the required transaction field rather than forcing a choice.

### Jev example — Choose the transaction amount

The question and choice structure follow the current code. The expense line and returned scores below are illustrative, not a recorded Jev response.

#### The problem code has identified

Suppose a source line contains:

> **2026-10-01 Cafe charge $18.50; balance $2,400.00**

Code can recognize the date and both amounts. The question is not “Which numbers appear?” but **“Which amount represents the transaction?”**

Code prepares a bounded choice for Jev rather than asking it to generate an amount.

#### Sent to Jev

**1. Question/instructions**

> Which candidate is the signed transaction amount affecting this account, rather than a balance or original price?

**2. Data**

The complete original line, so Jev can interpret the amounts in context:

> 2026-10-01 Cafe charge $18.50; balance $2,400.00

**3. Choices**

Code builds these options from the amount occurrences it found:

| Choice | Meaning supplied to Jev |
|---|---|
| `candidate_0` | The source occurrence 1: **$18.50** |
| `candidate_1` | The source occurrence 2: **$2,400.00** |
| `none` | None of the candidate values is the transaction field. |

**This is a multiple-choice request, not a yes/no request.** Jev must choose from the supplied options. It is not being asked to invent a new value. The `none` option lets it decline all the candidates.

#### What Jev could return

An illustrative result might be:

| Returned information | Example |
|---|---|
| Selected choice | `candidate_0` |
| Probability for `candidate_0` | 0.94 |
| Probability for `candidate_1` | 0.04 |
| Probability for `none` | 0.02 |
| Separate confidence value | 0.92 |

In plain language: **“Of the supplied options, I choose the $18.50 occurrence.”**

#### What code does next

Code does not accept the choice merely because Jev selected it.

- It checks the **selected option’s probability** against the configured minimum—**0.80 by default**. The separate confidence value is not this gate.
- It parses the selected source occurrence itself and checks that it is acceptable, including rejecting an occurrence identified as a balance.
- If those checks pass, it fills the amount field with **18.50** and clears that amount ambiguity.

If the selected probability were **0.62**, Jev chose `none`, or code rejected the selected occurrence, the amount would remain unresolved. **Remaining issues can then go to LLM extraction.**

The teaching point is: **code supplies the evidence and allowed answers; Jev makes the semantic choice; code decides whether to accept it.**

### What a probability does—and does not—mean

A probability is a model-provided score used by the application to decide whether to accept a choice. It is not proof that the selected value is correct, and a score of 0.90 is not automatically evidence that nine out of ten comparable choices will be right. That would require calibration against suitable evaluation data.

For this extraction step, code compares the probability attached to the **selected option** with a configured minimum. The default in the source is **0.80**. This gate uses the selected option’s probability, not simply the separate confidence field.

For an illustrative choice:

- Selected transaction amount has probability 0.93: it passes the probability gate, but code must still check the selected source value.
- Selected amount has probability 0.62: code does not accept it as the resolved field.
- Jev selects “none”: the field stays unresolved, regardless of that option’s probability.

Code then parses the selected source occurrence itself. For an amount, it also rejects an occurrence identified as a balance and applies the source-supported sign/refund rules. For a date, the selected source value must parse as a valid date with an allowed year.

A high probability cannot override those checks.

### When the controller calls the LLM

After the Jev pass, any line with issues remaining goes to structured LLM extraction. This includes:

- A candidate choice that did not meet the probability threshold.
- A “none” choice or a candidate that code could not accept.
- Missing fields or other problems that were not eligible for Jev’s multiple-candidate choice.
- Certain temporary Jev failures that leave the original parse intact.

Jev is not a compulsory model call for every line. Its role here is specifically to resolve eligible multiple-date and multiple-amount ambiguities before the broader extraction fallback.

### What the LLM is asked to return

The LLM receives the uncertain source lines, the fields already known, and the outstanding issues. It must return structured JSON, not an explanation in prose.

For each newly supplied field, it must provide the matching source evidence. It must preserve the source identity and must not change a field already established by code. If a field cannot be established, it can remain unknown.

### How code checks the LLM response

Code checks the response schema, expected line numbers and order, unchanged source text, preservation of known values, and supporting evidence for new values. Dates and amounts must agree with their source evidence. If identical amount text occurs more than once, the evidence can identify the particular occurrence. An occurrence identified as a balance is not accepted as the transaction amount.

These checks prevent invented amounts, altered source records, and unsupported field substitutions. They still cannot prove that every source-supported semantic interpretation is correct.

### What happens if this still does not work

Extraction has at most **two semantic attempts**. On validation failures, code can request a corrected response. Lines that have already been resolved are retained while pending lines are retried.

After the allowed attempts, unresolved lines remain in the result with their issues and an extraction-failure marker. Certain request failures also preserve the unresolved records. Other unrecoverable errors propagate rather than being turned into apparently successful data.

**What comes back:** the original source records, updated only with accepted fields, plus any remaining issues.

**Why this step exists:** let models interpret the difficult parts while keeping their answers tied to the source and subject to application acceptance rules.

---

## Step 3 — Categorize expenses

### What this step is doing

**Choose the kind of expense, now that the transaction fields are available.**

This is a different question from Step 2. Extraction asks, “What date and amount does the line contain?” Categorization asks, “What kind of spending does it describe?”

Only lines with a date, amount, and description are categorized. Incomplete lines keep their place in the input but receive no categorization at this stage.

### How Jev is used here

Code supplies Jev with the transaction text and a fixed category catalog. Each category has a criterion describing when it should apply.

Jev must choose from that list rather than invent a new category. Its response includes the proposed category, confidence, and category probabilities. Code checks the structured response, the expected option names, and whether the names belong to the known category catalog.

This is a good fit for a bounded semantic task: a merchant or transaction description often needs interpretation, but the application already knows the allowed answers.

### Jev example — Choose expense category

The question follows the current code. The transaction and returned scores below are illustrative, not a recorded Jev response.

#### The problem we need Jev to resolve

Suppose the source line is:

> **2026-10-01 Riverside Cafe — lunch and coffee — $18.50**

Code has established the date, description, and amount. Now we need a different answer: **“What kind of spending is this?”**

#### Sent to Jev

**1. Question/instructions**

> Select the expense category using only evidence in this transaction. Choose Other for a merchant-only mixed retailer charge when the purchased item is not stated. Choose Uncategorized for a transfer or cash withdrawal with no stated spending purpose.

**2. Data**

The complete original transaction line:

> 2026-10-01 Riverside Cafe — lunch and coffee — $18.50

**3. Choices**

Code supplies the fixed category catalog. Each option includes a criterion explaining when it applies—not just a category name.

Here are five of the **20 actual options**:

| Choice | Meaning supplied to Jev |
|---|---|
| `dining_coffee` | Prepared meals, takeout, cafes, coffee, and drinks at restaurants or bars. |
| `groceries` | Food and beverages bought for consumption at home when the purchase is identifiable as groceries. |
| `shopping` | Identifiable retail goods such as clothing, electronics, books, sporting goods, and home goods. |
| `other` | Known spending with an unknown purchase type, including merchant-only Target, Amazon, and Costco charges. |
| `uncategorized` | The line does not establish whether or why money was spent; includes bank transfers and cash withdrawals without a stated purchase. |

The request includes the other 15 categories too. **This is another multiple-choice request—not a yes/no question.** Jev must choose an allowed category rather than invent one.

#### What Jev could return

An illustrative result might be:

| Returned information | Example |
|---|---|
| Selected category | `dining_coffee` |
| Probability for `dining_coffee` | 0.95 |
| Probabilities for the other 19 categories | Recorded individually; collectively 0.05 in this example |
| Separate confidence value | 0.93 |

In plain language: **“The transaction describes lunch and coffee at a cafe, so I choose Dining & Coffee.”**

#### What code does next

Code validates the returned structure and category names, then applies its explicit business policies.

For this example, no policy override applies, so **Dining & Coffee remains the final category**. Code retains the proposal, final category, confidence, and probabilities.

Those policies also protect against unsupported precision. For example, a line containing only **“Amazon — $45.00”** does not reveal what was bought. Code uses **Other** for that merchant-only mixed-retailer charge, even if Jev proposes Shopping.

**Unlike Step 2, this stage has no probability threshold that triggers LLM fallback.** Low confidence is recorded, but a valid proposal remains the final category unless code policy overrides it.

The distinction between the two Jev calls is simple: **Step 2 chooses a field value from the source; Step 3 chooses a category from the application’s catalog.**

### What code does with the proposal

Code applies explicit business rules after Jev proposes a category:

- **Known expense merchant:** use the category in the merchant catalog.
- **Merchant-only mixed retailer:** use `Other` when the source does not establish the item type.
- **Transfer or cash withdrawal with no stated spending purpose:** use `Uncategorized` rather than inventing a purpose for the money movement.
- **No applicable override:** retain Jev’s proposed category.

For example, a merchant-only mixed-retailer charge does not establish whether the purchase was clothing, groceries, or something else. A valid category response is not evidence of the purchased item. Code therefore applies the mixed-retailer rule rather than letting a plausible guess become precise spending data.

The result retains both the model’s proposal and the final category, along with the probabilities, confidence, and any policy note. An override does not erase the history of the decision.

### What happens when confidence or probabilities are low

**In the current implementation, categorization has no low-confidence threshold branch and no LLM fallback.**

Confidence and probabilities are recorded, but they do not automatically trigger another model call or send the transaction to `Uncategorized`. The explicit policies above are based on transaction evidence, not confidence.

If the response is valid and no policy overrides it, Jev’s proposed category remains the final category even when its confidence is low. That is an important limit to describe honestly. Adding a confidence gate, a review queue, or an LLM fallback here would be a change to the workflow—not an existing feature.

A malformed response or failed category operation raises an error; it is not silently converted into a successful category.

**What comes back:** one aligned categorization slot per source record, preserving the proposed and final decisions.

**Why this step exists:** use semantic judgment for the category choice, but keep the category vocabulary, evidence rules, and policy authority in code.

---

## Step 4 — Reconcile and calculate

### What this step is doing

**Account for every source line, decide how it affects the ledger, and calculate the results in code.**

A list of plausible categories is not yet an expense analysis. The application must establish what was included, excluded, unresolved, or impossible to parse—and explain the totals.

### Give every line a treatment

Code checks that each categorization is aligned with the correct source line. It then creates a ledger entry for every line and assigns a treatment:

- Included classified spending.
- Excluded confirmed duplicate.
- Unparsed because required transaction fields remain missing.
- Uncategorized outflow.
- Uncategorized credit.

An unresolved line remains visible. A transfer without a spending purpose is not mixed into a guessed spending category. Uncategorized outflows are reported separately from classified spending.

### Do not silently remove apparent duplicates

Two equal-looking lines might be two real transactions. The model is not given authority to delete one.

The caller can supply confirmed duplicate line numbers. Code accepts an exclusion only when the confirmation matches an earlier adjacent source line with matching source text and parsed fields. An invalid confirmation fails validation.

This is narrower than general duplicate detection: the caller supplies the confirmation, and code checks the required match.

### Calculate money and comparisons

Code sums included amounts by month and category using decimal arithmetic. Refunds remain negative and reduce the relevant totals.

It calculates:

- Monthly category totals.
- Classified spending and separately reported unresolved outflows.
- Observed outflow, defined here as classified spending plus unresolved outflow.
- Budget variance: **actual minus target**.
- Change between adjacent observed months: **current minus previous**.

The totals and comparisons retain their contributing source-line references. A zero in a category is not automatically presented as full knowledge of spending: for example, mixed-retailer charges placed in `Other` can make Shopping coverage incomplete, and code records that limitation.

### Verify the ledger and totals

Reconciliation checks account for the source-line partitions and confirm that the ledger-derived amounts agree with the calculated category and monthly totals. Failed reconciliation is not treated as a clean result.

This verifies bookkeeping and arithmetic. It does **not** independently prove that every upstream semantic category was correct. A consistently miscategorized expense can still add up correctly; that is why evidence, policy, and evaluations matter in addition to arithmetic checks.

**What comes back:** a source-linked ledger, totals, budget comparisons, month changes, coverage notes, and reconciliation checks.

**Why this step exists:** do not ask a model to perform or quietly repair the accounting. Make every line’s treatment and every calculated value inspectable.

---

## Step 5 — Produce verified report

### What this step is doing

**Choose report highlights from code-built findings, validate the selection, and use the original text.**

The model is not asked to rewrite the analysis into prose. Code has already calculated the numbers and constructed candidate finding sentences from those results.

### Code builds the findings first

Candidate findings can describe budget breaches, changes between months, coverage limitations, and reconciliation information. Each candidate contains:

- A unique evidence ID.
- The exact sentence to use.
- Supporting source-line references.

For example, code can create a sentence describing a category’s actual spending, target, and amount over target. The numbers and formatting come from code, not model arithmetic.

Code also determines required findings where applicable—for example, the relevant Dining & Coffee budget breach, the largest category change, and a Shopping coverage limitation. These requirements constrain what the model may omit.

### What the LLM does

The LLM receives the candidate findings and required coverage. It chooses **three** findings by returning their IDs in JSON.

A simple way to explain this is:

> Code prepares the available report highlights. The model chooses which three to show, within code’s coverage rules.

The selection is a judgment task, not a writing task. Its freedom may be quite narrow when code requires all three selections.

### What code verifies

Code checks that the response has the required structure and that:

1. There are exactly three selections.
2. The selections are distinct.
3. Every selected ID was supplied by code.
4. All required IDs are present.

An invalid selection can receive one corrective retry: **two semantic attempts in total**. If the model still fails, the operation raises an error rather than returning an unvalidated set of findings.

### How the final text is produced

Once the selection is accepted, code looks up the corresponding original sentences and source references. Those sentences are used unchanged. The LLM cannot introduce a new amount or soften a coverage warning by rewriting it, because its response contains IDs rather than free-form report text.

The final result carries the structured analysis and selected findings for code-based presentation. This diagram groups candidate construction, selection, validation, and report output into one high-level stage; it is not claiming that the LLM renders the whole report.

**What comes back:** three accepted findings with code-written text and supporting source references, alongside the structured expense analysis.

**Why this step exists:** use the model’s judgment to choose highlights without handing it control of the financial facts or their wording.

---

## The uncertainty rules at a glance

| Situation | Current behavior |
|---|---|
| Parsing establishes all required fields without issues | No model extraction is needed. |
| Multiple source dates or amounts | Offer eligible source candidates to Jev. |
| Selected Jev extraction probability below the configured minimum | Leave the field unresolved; the line can proceed to LLM extraction. Default minimum: 0.80. |
| Jev chooses “none,” or code rejects the selected field | Keep it unresolved and use extraction fallback if issues remain. |
| LLM field output fails schema or evidence checks | Retry within the two-attempt limit; preserve unresolved lines if they cannot be recovered. |
| Jev category confidence is low | Record it. There is no confidence-triggered category fallback in this implementation. |
| Category conflicts with merchant/transfer policy | Code applies the policy and retains the proposal and policy note. |
| Line still lacks required transaction fields | Keep it in the ledger as unparsed, without inventing a complete transaction. |
| Duplicate exclusion is not caller-confirmed and validated | Do not silently exclude the line. |
| Report selections are unsupported, repeated, or omit required findings | Request correction within the two-attempt limit; fail if still invalid. |

## What “verified, not trusted” means here

The application verifies the properties it can check: source identity, supported field values, response structure, allowed choices, business rules, line accounting, arithmetic, required finding coverage, and unchanged report text.

Model interpretations can still be wrong within those boundaries. Evaluations are needed to measure those errors; source references and explicit unresolved outcomes help people inspect the exceptions. This walkthrough is a code review, not a new live-model evaluation or proof that all classifications are correct.

The purpose is to reduce the work a reviewer has to reconstruct—not merely to say there is a human in the loop. The system should do the repeatable checks first and give the reviewer evidence and visible limitations.

**Instruction is not assurance.** The prompt describes the desired behavior; application checks enforce the parts the system can establish.

## Source references

Source links are pinned to the reviewed revision:

- [Top-level controller](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/manager_expense_analysis.py)
- [Parsing](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_line_parsing_processor.py)
- [Jev field resolution](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_line_jev_extraction_processor.py)
- [LLM extraction and evidence checks](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/llm_processors/expense_line_extraction_llm_processor.py)
- [Categorization and policy](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/transaction_categorization_processor.py)
- [Ledger reconciliation](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_reconciliation_processor.py)
- [Calculations and checks](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/processors/expense_calculation_processor.py)
- [Findings construction, selection, and validation](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/llm_processors/expense_findings_llm_processor.py)
- [Jev settings and default extraction threshold](https://github.com/matlus/Analyze-Expenses-Workflow/blob/e13c09b533f7b85e394f247ac2b15f2a70e6e57d/packages/analyze-expenses-workflow/src/analyze_expenses_workflow/managers/configuration_providers/settings_models/jev_settings.py)
