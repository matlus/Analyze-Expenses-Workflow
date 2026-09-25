# Jev: a practical reference

**Research date:** 24 September 2026
**Scope:** TypeSafe AI's Jev model, its documented interface, architectural patterns, worked examples, use cases, limitations, and a method for evaluating it in a new workflow.
**Evidence basis:** TypeSafe's public documentation and cookbooks. Cookbook numbers are vendor-published results on the stated examples. This guide does not claim to be an independent benchmark or a live test of Jev on a new dataset.

## Contents

1. [The short version](#1-the-short-version)
2. [What Jev is](#2-what-jev-is)
3. [The three question types](#3-the-three-question-types)
4. [Probabilities, confidence, and uncertainty](#4-probabilities-confidence-and-uncertainty)
5. [When Jev is a good fit](#5-when-jev-is-a-good-fit)
6. [When to use code, an LLM, or a human](#6-when-to-use-code-an-llm-or-a-human)
7. [How to design a Jev request](#7-how-to-design-a-jev-request)
8. [Four core workflow patterns](#8-four-core-workflow-patterns)
9. [The cookbook atlas](#9-the-cookbook-atlas)
10. [Industry use-case map](#10-industry-use-case-map)
11. [Reference architectures](#11-reference-architectures)
12. [Evaluation and deployment](#12-evaluation-and-deployment)
13. [Known limitations and failure modes](#13-known-limitations-and-failure-modes)
14. [Current technical and commercial facts](#14-current-technical-and-commercial-facts)
15. [Further reading](#15-further-reading)

## 1. The short version

Jev is TypeSafe AI's first *System One* model: it reads text or structured text and returns typed judgments that software can use directly. A request contains a **state** (the material to judge) and one or more **questions**. The answers are restricted to three forms: a choice among supplied options, a score on supplied levels, or a yes/no probability. Jev does not generate prose, write code, browse, execute a tool, or perform a multi-step plan. [Introduction](https://docs.typesafe.ai/introduction) · [System One](https://docs.typesafe.ai/concepts/system-one)

**A useful division of work:**

| Component | Give it responsibility for |
| --- | --- |
| Ordinary code | Parsing where rules are exact; calculations; dates; validation; candidate generation; policies and thresholds; side effects; audit records. |
| Jev | Narrow semantic judgments about supplied evidence: classify, detect, score, rank, match, or verify. |
| Generative LLM | Open-ended writing, summarization, explanation, code generation, difficult unconstrained extraction, and reasoning that cannot be decomposed into narrow judgments. |
| Person | Decisions whose evidence is inadequate, whose error cost is high, or for which the workflow requires accountable review. |

This is a **division of responsibilities**, not a requirement that every workflow use all four components. For a closed classification task, code plus Jev may be enough. For an agent that writes answers, Jev may serve as a router or verifier around an LLM. [How Jev differs from a coding-agent model](https://docs.typesafe.ai/introduction/coding-agents) · [Intent routing](https://docs.typesafe.ai/patterns/intent-routing)

**The fastest fit test:** Can the needed judgment be phrased as one question a knowledgeable reviewer could answer quickly from a compact piece of evidence, with the answer constrained to known options or a defined scale? If yes, Jev is worth testing. If the task primarily needs exact computation or new text, start elsewhere. [Primitives](https://docs.typesafe.ai/primitives) · [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

## 2. What Jev is

TypeSafe calls Jev a decision model rather than a conversational LLM. Its output contract is structured and probabilistic: software receives a typed answer and, where applicable, a distribution over the permitted answers. TypeSafe says its training objective, *reinforcement learning for calibrated decisions* (RLCD), is aimed at making probabilities useful across groups of predictions. That does not guarantee any individual decision is right or that calibration transfers unchanged to a particular business dataset. [System One](https://docs.typesafe.ai/concepts/system-one) · [AI primer](https://docs.typesafe.ai/introduction/machine-learning-primer)

The basic API shape is:

    {
      "state": {"message": "My card was charged twice. Please fix this."},
      "model": "jev-1.13.0",
      "questions": {
        "intent": {
          "type": "choice",
          "instructions": "What is the customer's main request?",
          "criteria": {
            "refund": "Return a charge to the customer.",
            "explanation": "Explain a charge without changing it.",
            "other": "Neither of the listed requests."
          }
        },
        "duplicate_charge_reported": {
          "type": "noul",
          "instructions": "Does the customer report being charged twice?"
        }
      }
    }

The response contains an answer under each question key, the versioned model ID, and token usage. The question keys identify answers for code; the model judges the instruction and criteria, not the key name. The example above illustrates the request contract; it is **not** a recorded Jev response. The endpoint is POST /v1/systemone, with Bearer API-key authentication. [API reference](https://docs.typesafe.ai/api) · [Quick start](https://docs.typesafe.ai/introduction/quickstart)

The **state** may be a string or a JSON object or array of text-oriented values. Name its parts clearly—for example, message, policy, transaction, and candidate—when a question compares them. All questions in one request see the same state and are evaluated independently. An answer to one question is **not** automatically supplied as context to another question in that request. If a later question truly depends on a first answer to retrieve evidence or construct its options, make a second request. [State](https://docs.typesafe.ai/concepts/state) · [Primitives](https://docs.typesafe.ai/primitives)

## 3. The three question types

| Type | Ask when | Returned values | Key design point |
| --- | --- | --- | --- |
| **Choice** | Exactly one option should win from a known set. | Selected choice, probability for every option, and confidence. | Define mutually distinct options; provide *other* or *none* when coverage is incomplete. |
| **Score** | The answer lies on an ordered, describable scale. | Probability-weighted score, level legend, probabilities, and confidence. | Describe observable situations at each level, not bare numbers or vague adjectives. |
| **Noul** | A yes/no proposition is useful. | Probability of yes, from 0 to 1. | Phrase the proposition directly and specify what counts as yes. No separate confidence field is returned. |

These are the documented response types. A Choice can have at most **255 options**. A Score has **2–10 levels**. Scores may land between levels because the reported score is a probability-weighted mean of the level positions; it is not an exact real-world measurement. [Primitives](https://docs.typesafe.ai/primitives) · [Choice](https://docs.typesafe.ai/primitives/choice) · [Score](https://docs.typesafe.ai/primitives/score) · [Noul](https://docs.typesafe.ai/primitives/noul) · [API reference](https://docs.typesafe.ai/api)

Examples:

- **Choice:** Which team should own this support ticket: billing, technical, sales, or other?
- **Score:** How severe is this bug: cosmetic; feature broken but a workaround exists; blocking with no workaround?
- **Noul:** Does the supplied passage support the specific claim?

A Choice selects *relative to its offered options*. It can still pick the “best” bad option, so an escape option or a separate “is there enough evidence?” check matters. Several Noul questions are not interchangeable with one Choice: each Noul judges an absolute proposition, while Choice compares options. Do not assume their probabilities obey arithmetic identities or reuse a threshold tuned for one primitive on another. [Primitives](https://docs.typesafe.ai/primitives) · [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) · [Line-by-line search cookbook](https://docs.typesafe.ai/cookbooks/semantic_find)

## 4. Probabilities, confidence, and uncertainty

For Choice, Jev returns a probability distribution across the options. For Score, it returns a distribution across the levels. **Confidence** is TypeSafe's one-number summary of how concentrated that distribution is; it is not simply the winning option's probability, and it is not a measured accuracy rate. Noul returns its yes probability but no separate confidence value. TypeSafe recommends setting action thresholds according to the consequences of an error, then testing them on the application's own data. [Confidence](https://docs.typesafe.ai/confidence) · [Score](https://docs.typesafe.ai/primitives/score)

Useful distinctions:

| Quantity | Meaning | What it does **not** establish |
| --- | --- | --- |
| Choice option probability | Jev's probability mass on that option within the supplied set. | That the option is true when the option set is incomplete or the question is poor. |
| Choice or Score confidence | How peaked the answer distribution is. | Empirical correctness on a new dataset. |
| Noul value | Jev's probability of yes for one proposition. | A measured intensity, such as “medium skill” when the value is 0.5. |
| Score | Weighted position on the rubric's level indexes. | An exact amount, count, percentage, or physical magnitude. |
| Repeatability | Whether repeated runs tend to return the same decision. | Whether that decision matches ground truth. |

TypeSafe's [Choice self-consistency cookbook](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook) repeats eight moderation questions about one borderline post 15 times. Jev's raw picked-label agreement with its plurality labels is reported as 90.8%; adding an uncertain outcome below the cookbook's illustrative top-option probability threshold of 0.60 increases reported application-decision agreement to 99.2%, with automatic decisions on 74.2% of answers. **That is a repeatability and abstention experiment, not proof that majority voting improves classification accuracy.** The [Noul version](https://docs.typesafe.ai/cookbooks/consistency_noul_cookbook) similarly uses a review band around uncertain probabilities. Use repeated calls to study stability; adopt voting only if a labeled evaluation shows it improves the intended metric.

## 5. When Jev is a good fit

Jev is worth a trial when most of these statements are true:

1. The input can be represented as compact text or JSON with the relevant evidence present.
2. The required output is a fixed label, an ordered rubric, a yes/no property, or a ranking based on such judgments.
3. The judgment requires natural-language understanding that exact rules or ordinary search do not supply reliably.
4. The downstream action can be expressed in code, including a path for uncertainty.
5. The question can be evaluated quickly without multi-step reasoning or precise arithmetic.
6. There are enough examples with trusted outcomes to measure the benefit against a baseline.

Common good fits are ticket and document triage; fixed-taxonomy classification; detecting a stated intent or property; scoring a passage for relevance; comparing two candidate entities; checking whether supplied evidence supports a claim; choosing among typed tools; and turning text into numeric features for a conventional predictive model. These are **candidate uses**, not automatic guarantees of quality. [Example use cases](https://docs.typesafe.ai/concepts/use-case-map) · [Cookbooks](https://docs.typesafe.ai/cookbooks)

### A decision table

| Need | Start with | Consider Jev when |
| --- | --- | --- |
| Exact totals, counts, dates, duplicate keys, or schema validation | Code | A semantic judgment is needed to identify which candidate value or record matters. |
| Fixed category, risk indicator, or route based on text | Jev plus code, evaluated against rules and an LLM baseline | Language and boundary cases defeat deterministic rules. |
| Free-form answer, summary, explanation, image interpretation, or code transformation | Generative model or specialized tool | Jev could route the task, select context, or verify specific claims. |
| Search across a large collection | Index, lexical search, or embeddings | Jev can rerank a shortlist or filter passages. |
| Consequential decision | Explicit policy and accountable review | Jev may organize evidence and prioritize cases; action gates require domain-specific evaluation. |

## 6. When to use code, an LLM, or a human

**Use code for exact operations.** TypeSafe explicitly identifies arithmetic, counting, date comparison, and exact validation as poor Jev tasks. Extract a date's stated components with Jev if needed; assemble and compare dates in code. Find candidate amount strings with a parser or regular expression; let Jev select the relevant span if its *role* is ambiguous; copy the span and parse its number in code. [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) · [Date extraction](https://docs.typesafe.ai/cookbooks/date_extraction_cookbook) · [Pre-parsed value extraction](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook)

**Use a generative LLM when the output must be created rather than selected.** Jev does not write a customer response, report, contract revision, or program. It also cannot reliably produce arbitrary new strings for unconstrained extraction. A generative model may draft or propose candidates; Jev can choose among them or check specific claims against source text. Jev is not a replacement for the LLM that operates a coding agent. [Jev with coding agents](https://docs.typesafe.ai/introduction/coding-agents) · [SDE cascade](https://docs.typesafe.ai/cookbooks/sde_cascade)

**Use a person when the evidence is missing or the cost of a mistaken action warrants review.** A high-confidence distribution can still be wrong. The review decision should use the actual consequences of the action and measured error rates on similar cases; illustrative cookbook thresholds are starting examples, not portable defaults. [Confidence](https://docs.typesafe.ai/confidence) · [Confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing)

## 7. How to design a Jev request

1. **Define the action first.** Specify what code will do with each answer and what it will do when evidence is inadequate.
2. **Keep the state relevant.** Include the source text and the records or policy needed for the judgment. Retrieve and filter large collections before sending them; irrelevant detail can reduce accuracy.
3. **Write one judgment per question.** Split “Is this urgent, valid, and eligible?” into separate judgments; combine them in code.
4. **Make criteria concrete.** State what belongs in each option and, for commonly confused options, what does not. Describe Score levels as observable situations.
5. **Provide an escape path.** Include other, none, unknown, or “not stated” where the offered choices may not fit.
6. **Point at specific fields.** When state is structured, name the relevant field paths in the instruction.
7. **Batch independent questions about the same state.** Include speculative questions your code may later ignore; extra questions still cost input tokens.
8. **Use a second call only for a real dependency.** Examples: the first decision determines what evidence to fetch, which candidate options to offer, or which newly formed blocks to classify.
9. **Keep exact source evidence.** For extraction, select from candidate spans and copy the source value verbatim; validate it in code.
10. **Version everything.** Log the original input reference, question and criteria version, model version, answer distributions, routing decision, and any reviewer correction.

These steps combine the [state guide](https://docs.typesafe.ai/concepts/state), [primitives guide](https://docs.typesafe.ai/primitives), [structured criteria guidance](https://docs.typesafe.ai/primitives/advanced), [fan-out pattern](https://docs.typesafe.ai/patterns/fan-out), and TypeSafe's [documented failure modes](https://docs.typesafe.ai/model-jaggedness/jev-1.13). The logging recommendation is an implementation inference: it makes a changing model and policy inspectable.

## 8. Four core workflow patterns

### Speculative fan-out

Ask all independent questions against one state in one call, even if some will only matter on one route. TypeSafe's support example asks for ticket category, bug severity, reproducibility, refund intent, and frustration together; code ignores bug answers if the ticket is not a bug. This avoids a second round trip for information that could have been asked up front. It is **one shared state with independent questions**, not a chain of reasoning among answers. [Pattern](https://docs.typesafe.ai/patterns/fan-out) · [Parallel-questions cookbook](https://docs.typesafe.ai/cookbooks/parallel_questions)

### Confidence-gated routing

The chosen label says *what* Jev thinks; the distribution or confidence informs *whether* the workflow should act. Lower-stakes routes can have different thresholds from irreversible or consequential actions. The published voice-banking example uses different gates for checking a balance and approving a transfer. Treat its numeric cutoffs as illustration, then tune your own against labeled outcomes. [Pattern](https://docs.typesafe.ai/patterns/confidence-routing) · [Confidence](https://docs.typesafe.ai/confidence)

### Composite scoring

Ask for separate, single-dimension Scores and combine normalized results with explicit weights in code. The resume example scores Python depth, leadership, system design, and generalist breadth, then weights them differently for a senior individual-contributor role and an engineering-manager role. The weights are a business rule; Jev supplies the semantic measurements. Evaluate whether those dimensions and weights match the intended decisions before automating them. [Pattern](https://docs.typesafe.ai/patterns/composite-scoring)

### Intent routing

Classify an incoming request and send it to ordinary code, a specialist LLM, or a person. TypeSafe's customer-service example handles order status with code, sends product or return questions to specialist LLMs, and routes unclear or complex complaints to a person. Jev can therefore replace an LLM used merely to *decide what handler to call*, while retaining an LLM where a generated answer is needed. [Pattern](https://docs.typesafe.ai/patterns/intent-routing)

## 9. The cookbook atlas

The [cookbook index](https://docs.typesafe.ai/cookbooks) currently lists **18** worked recipes. Each row below states the reusable mechanism, not a claim that the published result transfers to another dataset.

| Cookbook | What Jev does | What code or another model does |
| --- | --- | --- |
| [Self-consistency: Nouls](https://docs.typesafe.ai/cookbooks/consistency_noul_cookbook) | Repeats yes/no judgments to expose probability variation. | Keeps the probability visible and routes an uncertain band to review. |
| [Self-consistency: Choices](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook) | Repeats closed-label moderation judgments. | Adds an uncertain outcome; measures agreement and automatic-decision coverage separately. |
| [Parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions) | Answers 13 questions about one regulatory article in one request. | Collects the typed briefing; compare request cost and latency with separate calls. |
| [Re-ranking](https://docs.typesafe.ai/cookbooks/rerank_typesafe) | Scores query–passage relevance within a shortlist. | BM25 first selects 30 candidates; code reorders them. Jev cannot recover a missing candidate. |
| [Line-by-line search](https://docs.typesafe.ai/cookbooks/semantic_find) | Selects the most relevant line and separately judges whether an answer exists. | Keeps “best candidate” distinct from “actual evidence found.” |
| [Structure recovery](https://docs.typesafe.ai/cookbooks/autoformat) | Judges whether adjacent lines belong together, then labels the resulting blocks. | Joins lines, constructs blocks, and renders the recovered structure. The second call has a real dependency on the first. |
| [Function calling](https://docs.typesafe.ai/cookbooks/function_calling) | Selects a typed function and closed-set arguments. | Validates and executes the chosen ordinary function. Jev does not invoke it itself. |
| [Skill suggestion](https://docs.typesafe.ai/cookbooks/skill_suggestion) | Ranks skills by descriptions, then reassesses top candidates after their full text is available. | Retrieves full descriptions and permits a “use none” decision. |
| [Knowledge graph entity alignment](https://docs.typesafe.ai/cookbooks/entity_alignment) | Judges whether candidate entity pairs are different, possibly related, or the same; checks named attributes. | Generates pairs, compares numeric fields, and sends middle cases to a curator. |
| [Classifying RAG passages](https://docs.typesafe.ai/cookbooks/classifying_rag_passages) | Labels retrieved passages for relevance, usable evidence, conflicting assumptions, and injection risk. | Filters or routes passages before an answering LLM sees them. |
| [Double-checking citations](https://docs.typesafe.ai/cookbooks/citation_check) | Judges whether a located quotation's context supports, contradicts, or says nothing about a claim. | First checks for the quotation literally; routes uncertain semantic checks. |
| [Guardrails for LLMs](https://docs.typesafe.ai/cookbooks/llm_guardrails) | Checks input and output messages for defined hazards and severity. | Applies pass, review, block, or support policy around a generative model. |
| [Structured-data-extraction cascade](https://docs.typesafe.ai/cookbooks/sde_cascade) | Verifies fields proposed by a cheaper LLM against source material. | Escalates flagged cases to a stronger LLM; preserves source-grounded validation. |
| [Date extraction](https://docs.typesafe.ai/cookbooks/date_extraction_cookbook) | Selects the stated date mode and components. | Resolves relative dates, does calendar arithmetic, validates, and flags uncertainty. |
| [Pre-parsed value extraction](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook) | Picks the right value from regex-found candidates and can classify its role. | Finds candidate spans, copies the chosen literal value, and normalizes it. |
| [Hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification) | Chooses among taxonomy branches at successive levels. | Maintains multiple plausible paths with beam search instead of committing greedily. |
| [Autoresearch feature discovery](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery) | Converts text into probabilities and scores for proposed semantic features. | An LLM proposes features offline; a conventional model learns from them and held-out errors. |
| [Classification using confidence](https://docs.typesafe.ai/cookbooks/classification_using_confidence) | Classifies among detailed industry groups. | Reports a broader parent category when the detailed answer is uncertain. |

Three transferable lessons stand out:

- **First find candidates, then judge them.** This recurs in reranking, entity alignment, span extraction, and skill suggestion. Candidate recall puts a ceiling on the system's recall.
- **A forced best option is not evidence of a valid option.** The line-search recipe pairs selection with an existence check; classification recipes add other, none, uncertainty, or a broader label.
- **Verification should compare against evidence.** Repeating the same judgment over the same thin state may reveal instability, but a receipt, source passage, policy, or detailed candidate record gives a second-stage checker a stronger basis.

## 10. Industry use-case map

TypeSafe's [example use-case map](https://docs.typesafe.ai/concepts/use-case-map) is an idea catalog. The following are concrete Jev-sized judgments that one could test within those domains; they are not validated deployments.

| Domain | Example bounded judgment | Likely downstream route |
| --- | --- | --- |
| Recruiting | Does an application provide explicit evidence for each job-related requirement? | Evidence packet and recruiter review. |
| Lead generation | Does a company fit defined criteria; does an inquiry express purchase intent? | Prioritization or sales queue. |
| Customer support | Which issue and intent are present; is urgency or a refund request stated? | Database lookup, team routing, specialist LLM, or agent. |
| Insurance claims | What claim type is described; which required facts are missing? | Document collection or adjuster review. |
| Financial-crime investigation | Are specific risk indicators supported; do two records name the same entity? | Investigator triage with source evidence. |
| Legal and compliance | Is a clause present; does a source support a compliance claim? | Flag a specific passage for counsel or compliance staff. |
| E-commerce marketplaces | Which catalog category fits; do two listings describe the same item? | Normalize a listing or review an uncertain merge. |
| Moderation and trust and safety | Which policy category applies and how severe is the content? | Allow, warn, review, or block by defined policy. |
| Advertising | Does creative contain a prohibited claim; does the landing page support it? | Review before publication. |
| Gaming | Does chat contain abuse; what kind of player report is this? | Moderation or support queue. |
| Risk assessment | Which supported risk indicators and evidence-quality levels apply? | A transparent code-computed priority or reviewer queue. |
| Demand forecasting | Do inquiries or reviews express purchase intent, urgency, or supply concern? | Semantic features for a forecasting model alongside time-series data. |
| Graphs and knowledge graphs | Are candidate entities the same; do their recorded claims conflict? | Merge, leave distinct, or send to a curator. |

The map also includes search, scientific discovery, model routing, LLM guardrails, semantic code linting, and feature extraction. **Software modernization** is an adjacent inference rather than a named industry in that map: Jev could classify legacy artifacts against explicit migration criteria or flag convention violations; a coding model or developer would perform the actual transformation. [Example use cases](https://docs.typesafe.ai/concepts/use-case-map) · [Structure recovery](https://docs.typesafe.ai/cookbooks/autoformat)

## 11. Reference architectures

### A. Jev plus code: closed decisions

**Flow:** acquire input → exact parsing and validation → build a compact state → ask narrow Jev questions → apply code thresholds and policy → act or review → store evidence and outcome.

Use this for classification, triage, matching, and scoring when the outputs and actions are known in advance. A personal-expense category is one example, but the pattern applies equally to ticket routing and product normalization. Exact totals and dates stay in code. [Primitives](https://docs.typesafe.ai/primitives) · [Confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing)

### B. Jev as a selector before an LLM

**Flow:** classify request or retrieve candidates → Jev decides the route or useful context → code invokes the selected specialist LLM only where text generation is necessary.

Use this when a general LLM is currently spending time merely identifying intent, deciding which tool to use, or reading irrelevant retrieved passages. [Intent routing](https://docs.typesafe.ai/patterns/intent-routing) · [Classifying RAG passages](https://docs.typesafe.ai/cookbooks/classifying_rag_passages) · [Function calling](https://docs.typesafe.ai/cookbooks/function_calling)

### C. Jev as a verifier after an LLM

**Flow:** LLM proposes an answer or extraction → code performs exact source/schema checks → Jev asks narrow source-grounded verification questions → code accepts, escalates, or requests correction.

Use this for claim support, field extraction, policy checks, and agent guardrails. Verification is strongest when the source evidence is provided and the failure mode is stated precisely. [Citation checking](https://docs.typesafe.ai/cookbooks/citation_check) · [SDE cascade](https://docs.typesafe.ai/cookbooks/sde_cascade) · [LLM guardrails](https://docs.typesafe.ai/cookbooks/llm_guardrails)

### D. Jev as a feature generator

**Flow:** define semantic questions → Jev converts text to probabilities or scores → join with structured features → train and evaluate a conventional model → monitor performance over time.

Use this when there are many labeled examples and a prediction target, such as demand or churn. Do not assume more Jev features automatically help; hold out data and compare against simpler baselines. [Autoresearch feature discovery](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery) · [Example use cases](https://docs.typesafe.ai/concepts/use-case-map)

## 12. Evaluation and deployment

The cookbooks establish *possible designs*. Before using Jev for a new decision, measure that decision on representative local data.

1. **Define the outcome and its errors.** Specify the labels or action, acceptable abstention, cost of false positives and false negatives, and what a reviewer should see.
2. **Build a trusted evaluation set.** Include common cases, rare cases, neighboring labels, missing evidence, messy inputs, adversarial wording, and examples from different time periods or data sources. Keep a held-out portion for final selection.
3. **Compare realistic baselines.** Test code-only rules or search; Jev plus code; and an LLM workflow when one exists. Include latency, billed input tokens, total cost, accuracy by class, and the number routed to review.
4. **Test the whole route.** A high category accuracy is insufficient if the workflow takes the wrong action when confidence is low or evidence is absent. Report both *automatic coverage* and *error rate among automatic decisions*.
5. **Check probabilities empirically.** Measure whether answers grouped by predicted probability achieve corresponding observed frequencies on your task. Test thresholds against the cost of each action. The documented confidence value alone is not a substitute for this check.
6. **Audit changes.** Pin a versioned model ID once tuned, retain the question and criteria version, and rerun the evaluation before switching a model alias, changing descriptions, adding classes, or changing retrieval.
7. **Monitor production cases.** Sample accepted decisions for review, record corrections, and look for shifts by input source, language, category, and time.

This is an evaluation recommendation derived from Jev's documented probabilities, routing patterns, and version behavior. TypeSafe itself says its example thresholds depend on the application and that a moving alias can change answers. [Confidence](https://docs.typesafe.ai/confidence) · [Models](https://docs.typesafe.ai/models) · [Self-consistency: Choices](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook)

**Suggested decision record:** source identifier; the exact relevant source excerpt or a retrievable reference; state/question/criteria versions; returned model ID; typed answers and full distributions; code threshold and chosen route; reviewer correction; final outcome. Keep sensitive data retention and access consistent with the application's policy.

## 13. Known limitations and failure modes

TypeSafe maintains a [Jev 1.13 jaggedness page](https://docs.typesafe.ai/model-jaggedness/jev-1.13), last reviewed 17 September 2026. The important limitations are:

| Limitation | Practical response |
| --- | --- |
| Literal interpretation of wording and negation | Write the exact condition and boundary cases; keep instruction and criteria aligned. |
| Poor arithmetic, counting, numeric precision, and date comparison | Compute in code; ask Jev only to identify semantic roles or stated components. |
| Reduced accuracy with multiple hops of indirection | Point to the relevant field and split the task into small direct questions. |
| Reduced accuracy with large irrelevant state | Retrieve, filter, or shorten the state before the call. |
| Susceptibility to adversarial text in state | Treat untrusted source text as data, write precise criteria, and test injected or manipulative examples. |
| No guaranteed structural identities between separate questions or primitives | Do not infer one answer by negating another; test each actual question and enforce logical invariants in code. |
| No free-form generation | Use a generative model when new text, code, or an arbitrary string is required. |
| Uneven non-English performance | Test each language and content type on its own examples. |

Other practical limits: text only; bounded request context; a maximum of 255 Choice options and 10 Score levels; rate limits; and candidate-generation recall in any shortlist-based workflow. A closed output shape prevents invalid free-form formatting, but it does **not** prevent a semantically wrong label or a high-confidence error. [Models](https://docs.typesafe.ai/models) · [API reference](https://docs.typesafe.ai/api) · [Re-ranking cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe)

## 14. Current technical and commercial facts

**Snapshot, 24 September 2026. These facts can change; recheck the [models page](https://docs.typesafe.ai/models) before budgeting or deployment.**

| Item | Documented status |
| --- | --- |
| Stable version | jev-1.13.0; jev-latest currently points to it. |
| Preview alias | jev-preview currently also points to jev-1.13.0. Aliases can move. |
| Price | US $0.042 per million input tokens; output tokens free, according to the current models page. |
| Rate limits | 250,000 tokens/second and 1,200 requests/minute, currently; TypeSafe says these may change dynamically. |
| Context | 64,000 tokens per request; 32,000 for state plus the longest single question. |
| Input | Text only, as string, JSON object, or array of text-oriented values; no direct image, audio, or video input. |
| Endpoint | POST https://api.typesafe.ai/v1/systemone; GET /v1/models lists account-accessible aliases. |
| SDKs | Python and JavaScript/TypeScript; direct HTTP works from other languages. |
| Model customization | No per-customer fine-tuning or LoRA described; shape behavior through state, question instructions, and criteria. |
| Language | English is the strongest documented language; evaluate other languages separately. |
| Data handling | TypeSafe states customer requests and responses are not used to train Jev; enterprise zero-data-retention is offered. Check the actual agreement and retention terms for sensitive workloads. |

Sources: [Models](https://docs.typesafe.ai/models) · [API reference](https://docs.typesafe.ai/api) · [SDKs](https://docs.typesafe.ai/sdk) · [Legal documents](https://docs.typesafe.ai/legal)

For production, prefer the versioned model ID after an evaluation; record the response's model field. The SDKs retry rate-limited or overloaded calls with backoff by default. Direct HTTP integrations should handle 429 and 529 with backoff, and should treat 401 and 422 as configuration or validation errors rather than normal semantic uncertainty. The Python SDK can log request and response bodies at debug level, so avoid debug logging sensitive state without an appropriate policy. [Models](https://docs.typesafe.ai/models) · [API errors and rate limits](https://docs.typesafe.ai/api) · [Python SDK usage](https://docs.typesafe.ai/sdk/python/usage)

The Python SDK documents TypeSafe-compatible routes through OpenRouter and Vercel AI Gateway. Those routes use their own base URLs, credentials, and model IDs. Verify gateway pricing, limits, and data terms separately; the direct TypeSafe figures above do not automatically describe an intermediary. [Python SDK usage](https://docs.typesafe.ai/sdk/python/usage)

## 15. Further reading

**Start here:** [Introduction](https://docs.typesafe.ai/introduction) → [State](https://docs.typesafe.ai/concepts/state) → [Primitives](https://docs.typesafe.ai/primitives) → [Confidence](https://docs.typesafe.ai/confidence) → [Patterns](https://docs.typesafe.ai/patterns) → [Cookbooks](https://docs.typesafe.ai/cookbooks).

**To integrate:** [Quick start](https://docs.typesafe.ai/introduction/quickstart) · [API reference](https://docs.typesafe.ai/api) · [Python SDK](https://docs.typesafe.ai/sdk/python) · [JavaScript SDK](https://docs.typesafe.ai/sdk/javascript) · [Models](https://docs.typesafe.ai/models).

**To challenge a proposed use:** [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) · [Self-consistency: Choices](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook) · [Classification using confidence](https://docs.typesafe.ai/cookbooks/classification_using_confidence) · [Example use cases](https://docs.typesafe.ai/concepts/use-case-map).

---

**Status of this guide:** Source-grounded research and design reference. It deliberately distinguishes TypeSafe's documented interface and published examples from deployment advice that must be validated for each workflow. Recheck version, price, limits, SDK behavior, and data terms when using it later.
