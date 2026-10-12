# Scorecard comparison: expense_analysis

Generated from each run folder's scorecard.json; for every configuration of LLM steps and Jev model only the newest run is shown. Runs recorded before grading was timed show their answer time only.

Answer time is the workflow run that produced the result, before any grading started. Grading time is the eval analysis of that result: composing the report the judge reads, reading every fact, the judge, and the code checks. Total time is the two added together. Each time is the median per run, then the shortest to the longest run. Runs whose workflow errored are left out of these times; a cell then says how many runs it covers.

| LLM steps | model (effort) | Jev model | runs | gate | checks below minimum | errored checks | Answer time (workflow run) | Grading time (eval analysis) | Total time | run folder |
|---|---|---|---|---|---|---|---|---|---|---|
| CLAUDE_CODE (ANTHROPIC) | claude-haiku-5-5 (low) | jev-1.13 | 5 | passed | none | 0 | 8.1s (8.0s to 9.6s) | 4.0s (3.7s to 4.3s) | 12s (12s to 14s) | `20261012T202005Z-claude_code-claude-haiku-5-5-low-c91863` |
| CLAUDE_CODE (ANTHROPIC) | claude-haiku-5-5 (medium) | jev-1.13 | 5 | passed | none | 0 | 8.6s (7.9s to 9.3s) | 4.0s (3.8s to 5.4s) | 13s (12s to 15s) | `20261012T201857Z-claude_code-claude-haiku-5-5-medium-1772a7` |
| CLAUDE_CODE (ANTHROPIC) | claude-opus-5-5 (medium) | jev-1.13 | 5 | passed | none | 0 | 9.9s (9.4s to 10.0s) | 4.1s (3.7s to 4.3s) | 14s (14s to 14s) | `20261012T202500Z-claude_code-claude-opus-5-5-medium-ebccc6` |
| CLAUDE_CODE (ANTHROPIC) | claude-sonnet-5-5 (medium) | jev-1.13 | 5 | passed | none | 0 | 11s (9.7s to 16s) | 4.1s (4.0s to 4.3s) | 15s (14s to 20s) | `20261012T201735Z-claude_code-claude-sonnet-5-5-medium-1623ef` |
| GPT_CODEX (OPENAI) | gpt-6-luna (low) | jev-1.13 | 5 | passed | none | 0 | 8.3s (7.3s to 8.9s) | 3.9s (3.8s to 4.3s) | 12s (11s to 13s) | `20261012T202612Z-gpt_codex-gpt-6-luna-low-895410` |
| GPT_CODEX (OPENAI) | gpt-6-luna (medium) | jev-1.13 | 5 | passed | none | 0 | 8.6s (7.9s to 9.2s) | 4.1s (3.9s to 4.7s) | 13s (12s to 13s) | `20261012T202222Z-gpt_codex-gpt-6-luna-medium-c9382c` |
| GPT_CODEX (OPENAI) | gpt-6.1-sol (medium) | jev-1.13 | 5 | passed | none | 0 | 9.3s (8.8s to 10.0s) | 4.0s (3.8s to 4.2s) | 13s (13s to 14s) | `20261012T202112Z-gpt_codex-gpt-6.1-sol-medium-600a94` |
