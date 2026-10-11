# Scorecard comparison: expense_analysis

Generated from each run folder's scorecard.json; for every configuration of LLM steps and Jev model only the newest run is shown. Runs recorded before grading was timed show their answer time only.

Answer time is the workflow run that produced the result, before any grading started. Grading time is the eval analysis of that result: composing the report the judge reads, reading every fact, the judge, and the code checks. Total time is the two added together. Each time is the median per run, then the shortest to the longest run. Runs whose workflow errored are left out of these times; a cell then says how many runs it covers.

| LLM steps | model (effort) | Jev model | runs | gate | checks below minimum | errored checks | Answer time (workflow run) | Grading time (eval analysis) | Total time | run folder |
|---|---|---|---|---|---|---|---|---|---|---|
| CLAUDE_CODE (ANTHROPIC) | claude-haiku-5-5 (low) | jev-1.13 | 5 | failed | shopping-july-total 0/5 | 0 | 8.1s (7.8s to 8.5s) | not recorded | not recorded | `20261011T025557Z-claude_code-claude-haiku-5-5-low-8387db` |
| CLAUDE_CODE (ANTHROPIC) | claude-haiku-5-5 (medium) | jev-1.13 | 5 | failed | shopping-july-total 0/5 | 0 | 8.8s (8.4s to 11s) | not recorded | not recorded | `20261011T025428Z-claude_code-claude-haiku-5-5-medium-df818a` |
| CLAUDE_CODE (ANTHROPIC) | claude-opus-5-5 (medium) | jev-1.13 | 5 | failed | shopping-july-total 0/5 | 0 | 9.1s (9.0s to 10s) | not recorded | not recorded | `20261011T025133Z-claude_code-claude-opus-5-5-medium-b7f2f8` |
| CLAUDE_CODE (ANTHROPIC) | claude-sonnet-5-5 (medium) | jev-1.13 | 5 | failed | shopping-july-total 0/5 | 0 | 9.5s (8.5s to 11s) | not recorded | not recorded | `20261011T025303Z-claude_code-claude-sonnet-5-5-medium-cda349` |
| GITHUB_COPILOT (OPENAI) | gpt-6.1-sol (medium) | jev-1.13 | 5 | failed | shopping-july-total 0/5 | 0 | 9.8s (9.4s to 18s) | not recorded | not recorded | `20261011T030120Z-github_copilot-gpt-6.1-sol-medium-6f2ddb` |
| GPT_CODEX (OPENAI) | gpt-6-luna (low) | jev-1.13 | 5 | failed | shopping-july-total 0/5 | 0 | 8.2s (8.0s to 8.7s) | not recorded | not recorded | `20261011T025911Z-gpt_codex-gpt-6-luna-low-6ef311` |
| GPT_CODEX (OPENAI) | gpt-6-luna (medium) | jev-1.13 | 5 | failed | shopping-july-total 0/5 | 0 | 8.5s (7.9s to 9.2s) | not recorded | not recorded | `20261011T025748Z-gpt_codex-gpt-6-luna-medium-c01f3a` |
| GPT_CODEX (OPENAI) | gpt-6.1-sol (medium) | jev-1.13 | 5 | passed | none | 0 | 9.9s (9.5s to 10s) | 3.9s (3.7s to 4.5s) | 14s (13s to 14s) | `20261011T135118Z-gpt_codex-gpt-6.1-sol-medium-0276a5` |
