# Workflow eval: comparison across models and efforts (2026-10-11)

The code-controlled workflow ran 5 isolated times per configuration on the Part Three case (`june_august_2026`, the same
expense log, budget notes, expected facts and reference ledger). Facts are read from the workflow's structured result;
only "three supported findings" is judged (Sonnet at low effort, as in Part Three). 14 of the 16 Part Three checks are
graded; `code-executed` and `tables-before-interpretation` do not apply to a code-controlled graph.

| LLM steps | model | effort | gate | checks below 5/5 | errored | run time | Jev requests per run | runs needing LLM extraction | distinct reports in 5 runs |
|---|---|---|---|---|---|---|---|---|---|
| Claude Code (Anthropic) | Opus 5.5 | medium | fail | shopping-july-total 0/5 | 0 | 9–10 s | 198 | 0 of 5 | 1 |
| Claude Code (Anthropic) | Sonnet 5.5 | medium | fail | shopping-july-total 0/5, three-supported-findings 4/5 | 0 | 8–11 s | 198 | 0 of 5 | 1 |
| Claude Code (Anthropic) | Haiku 5.5 | medium | fail | shopping-july-total 0/5 | 0 | 8–11 s | 198 | 0 of 5 | 1 |
| Claude Code (Anthropic) | Haiku 5.5 | low | fail | shopping-july-total 0/5, three-supported-findings 4/5 | 0 | 8 s | 198–199 | 0 of 5 | 1 |
| GitHub Copilot (OpenAI) | GPT-6.1 Sol | medium | fail | shopping-july-total 0/5 | 0 | 9–18 s | 198–199 | 0 of 5 | 1 |
| Codex (OpenAI) | GPT-6 Luna | medium | fail | shopping-july-total 0/5 | 0 | 8–9 s | 198 | 0 of 5 | 1 |
| Codex (OpenAI) | GPT-6 Luna | low | fail | shopping-july-total 0/5 | 0 | 8–9 s | 198 | 0 of 5 | 1 |

Every one of the 35 runs, across both families, four models and two efforts, produced the byte-identical report.

## Traced causes

- **shopping-july-total, 35 of 35 runs, every configuration.** The workflow reports July Shopping as $526.62; the
  reference is $756.72. The trace names exactly two lines: line 95 "7/13: Target, $88.28" and line 106
  "Amazon (2026-07-18) - $141.82", both put in Other. Jev chose Other for both, and the workflow's explicit policy
  confirmed it: "Merchant-only mixed retailer charge; item type is not stated." The reference key puts merchant-only
  Amazon and Target charges in Shopping and accepts no policy that moves them. This is a deliberate category policy
  in the workflow, not a model error, so no model or effort can change it. Either the policy or the reference must
  change for the check to pass.
- **three-supported-findings, 2 of 35 runs (Sonnet 5.5 medium run 4, Haiku 5.5 low run 4).** The report was the same
  report the judge passed in the other 33 runs. In these two runs the Sonnet judge rejected the shopping finding
  because the workflow's Shopping totals ($572.48 / $526.62 / $712.98) use the mixed-retailer policy, which is not
  one of the reference's policies. This is judge variance on identical input, and it traces to the same policy as
  the failure above.

## What varies, and what does not

- Jev's own category choice varied between runs on 10 lines (for example the REI refund on line 108, Aldi on line 87,
  Thai Orchid on line 64). Each of those lines names a known merchant, so the workflow's explicit merchant policy set
  the final category every time; the final categories, totals, budget comparisons, changes and findings never varied.
- No line needed LLM extraction in any run: code and Jev parsed all 198 lines. The only LLM call per run was the
  findings selection, which picks three evidence IDs from code-computed statements; every model picked the same three.
- 198 Jev requests per run (one per line for categorization); 199 in two runs, which looks like one SDK-level retry.

## Notes on the runs

- GPT-6.1 Sol was first run through Codex, where every run errored: "The 'gpt-6.1-sol' model is not supported when using
  Codex with a ChatGPT account." It was rerun through the GitHub Copilot subscription, which serves it. The errored
  Codex run (`20261011T025708Z-gpt_codex-gpt-6.1-sol-medium-514ae5`) is kept as a record and left out of the table.
- Part Three for comparison (prompt-based, same checks): Opus 5.5, GPT-6.1 Sol and GPT-6 Astra passed every check 5/5;
  Sonnet 5.5 passed with two checks at 4/5; Haiku 5.5 and GPT-6 Luna failed. GPT-6 Astra was not run here (outside the
  model ceiling set for these runs).

Run folders: `20261011T025133Z-…opus…`, `20261011T025303Z-…sonnet…`, `20261011T025428Z-…haiku-5-5-medium…`,
`20261011T025557Z-…haiku-5-5-low…`, `20261011T030120Z-github_copilot-gpt-6.1-sol…`, `20261011T025748Z-…luna-medium…`,
`20261011T025911Z-…luna-low…`; smoke run `20261011T025058Z-claude_code-claude-haiku-5-5-low-2b3ae9`.
