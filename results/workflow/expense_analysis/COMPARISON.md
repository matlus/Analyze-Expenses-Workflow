# Workflow eval: consistency across models, efforts and sweeps (2026-10-12)

The code-controlled workflow was run in two full sweeps over the same 7 configurations: two coding assistant subscriptions
(Claude Code for Anthropic, Codex for OpenAI), five models, and two reasoning efforts. Each configuration ran 5 isolated
times per sweep on the Part Three case (`june_august_2026`), so the two sweeps made 70 workflow runs in all. Jev
(`jev-1.13`) categorizes every line, and the LLM steps use the configuration's model. 14 of the 16 Part Three checks are
graded. Facts are read from the workflow's structured result, and only "three supported findings" uses a judge (Sonnet at
low effort). This repo's reference ledger accepts the workflow's merchant-only mixed retailer policy (see
`expense_reference_key.md`). GitHub Copilot is a supported subscription, but it is tested on another machine and is not
part of these sweeps.

## Result

Every configuration passed its gate in both sweeps. 69 of the 70 runs completed, and **all 69 produced the same report,
byte for byte** (`answer.md`, MD5 `49e4e9b95040dd9903d150e2daa08e33`). Every one of those 69 runs passed all 14 checks,
including the judged findings check. The one run that did not complete crashed in a Jev call before any LLM step ran. It
is described below.

| LLM steps | model | effort | sweep 1 gate | sweep 1 checks | sweep 2 gate | sweep 2 checks | answer time, median (sweep 1 / 2) | total time, median (sweep 1 / 2) |
|---|---|---|---|---|---|---|---|---|
| Claude Code (Anthropic) | Opus 5.5 | medium | passed | 14 at 5/5 | passed | 14 at 5/5 | 10s / 9.9s | 15s / 14s |
| Claude Code (Anthropic) | Sonnet 5.5 | medium | passed | 14 at 5/5 | passed | 14 at 5/5 | 9.7s / 9.5s | 14s / 13s |
| Claude Code (Anthropic) | Haiku 5.5 | medium | passed | 14 at 5/5 | passed | 14 at 5/5 | 8.7s / 9.7s | 13s / 14s |
| Claude Code (Anthropic) | Haiku 5.5 | low | passed | 14 at 5/5 | passed | 14 at 5/5 | 9.2s / 9.6s | 14s / 14s |
| Codex (OpenAI) | GPT-6.1 Sol | medium | passed | 14 at 5/5 | passed | 14 at 5/5 | 16s / 16s | 21s / 20s |
| Codex (OpenAI) | GPT-6 Luna | medium | passed | 14 at 5/5 | passed | 14 at 5/5 | 9.1s / 8.7s | 13s / 13s |
| Codex (OpenAI) | GPT-6 Luna | low | passed | 14 at 5/5 | passed | 14 at 4/5, run 1 errored | 8.3s / 8.4s | 12s / 12s |

An errored run counts as not passed. 4/5 is 80%, which is the minimum for every check, so the gate still passes.

## What is consistent

- **The report.** Every completed run, across both model families, five models, two efforts and two sweeps, produced the
  identical report. That includes the line categories, monthly totals, budget comparisons, month-to-month changes and the
  three findings. The choice of model does not change the answer.
- **The work done.** Every completed run made exactly 198 Jev requests, one per line. No line needed LLM extraction, so the
  only LLM call per run was choosing the three findings, and every model chose the same three:
  1. August Dining & Coffee was $398.69 against a $260.00 target, $138.69 over.
  2. Travel rose by $1,631.82 from July to August.
  3. Shopping was below its target in all three months, with a stated caveat that merchant-only mixed retailer charges
     are counted in Other.
- **The judge.** The judge passed the findings in 69 of 69 runs. The previous runs (2026-10-11) had two judge rejections
  on this same report. Both traced to the Shopping policy, which the reference now accepts.

## What varied

- **One run crashed.** Run 1 of sweep 2's GPT-6 Luna low (`…123833Z…`) failed after 9 Jev requests, in 0.1 seconds. No LLM
  step had started, so the model is not the cause. A short Jev/OpenRouter error is the likely cause, but it cannot be
  confirmed. The recorded failure is only `ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`, because
  `TransactionCategorizationProcessor` runs categorizations in an `asyncio.TaskGroup`, and the eval's failure description
  follows `__cause__`/`__context__` but not the group's sub-exceptions. The underlying error was not recorded.
- **Speed.** The answer took 8 to 10 seconds for six of the seven configurations. GPT-6.1 Sol through Codex was slower,
  at about 16s. Grading took about 4 seconds everywhere. Two single runs were outliers: 1m 08s (GPT-6 Luna low, sweep 1)
  and 40s (GPT-6 Luna medium, sweep 2). Both made the normal 198 Jev requests and produced the same report, so the delay
  was latency, not extra work.

## Comparison with the prompt-based eval

Part Three in Skills and Evals grades the same checks against model-controlled orchestration. There, Opus 5.5, GPT-6.1
Sol and GPT-6 Astra passed every check 5/5, Sonnet 5.5 passed with two checks at 4/5, and Haiku 5.5 and GPT-6 Luna failed.
In this workflow, the smallest models and the lowest efforts produce the same answer as the largest ones, because code
controls every computation and the LLM only chooses among code-computed findings. Note that Part Three's ledger does not
accept the mixed retailer policy, so the two evals do not grade `shopping-july-total` the same way.

## Run folders

| configuration | sweep 1 | sweep 2 |
|---|---|---|
| Claude Code Opus 5.5 medium | `20261012T121809Z-claude_code-claude-opus-5-5-medium-99eae3` | `20261012T122958Z-claude_code-claude-opus-5-5-medium-48b91a` |
| Claude Code Sonnet 5.5 medium | `20261012T121930Z-claude_code-claude-sonnet-5-5-medium-09254c` | `20261012T123121Z-claude_code-claude-sonnet-5-5-medium-bf5cf5` |
| Claude Code Haiku 5.5 medium | `20261012T122052Z-claude_code-claude-haiku-5-5-medium-32f656` | `20261012T123235Z-claude_code-claude-haiku-5-5-medium-7dfb06` |
| Claude Code Haiku 5.5 low | `20261012T122204Z-claude_code-claude-haiku-5-5-low-57b07c` | `20261012T123402Z-claude_code-claude-haiku-5-5-low-b59980` |
| Codex GPT-6.1 Sol medium | `20261012T122320Z-gpt_codex-gpt-6.1-sol-medium-f99773` | `20261012T123513Z-gpt_codex-gpt-6.1-sol-medium-d899df` |
| Codex GPT-6 Luna medium | `20261012T122507Z-gpt_codex-gpt-6-luna-medium-f1b475` | `20261012T123652Z-gpt_codex-gpt-6-luna-medium-d59bc4` |
| Codex GPT-6 Luna low | `20261012T122618Z-gpt_codex-gpt-6-luna-low-270b8c` | `20261012T123833Z-gpt_codex-gpt-6-luna-low-0fb069` |

`scorecard_comparison.md` is generated by `evals/workflow/compare_scorecards.py` and shows only the newest run of each
configuration, which here is sweep 2. This file covers both sweeps and is written by hand.
