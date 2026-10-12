# Scorecard comparison: expense_analysis

Generated from each run folder's scorecard.json; for every configuration of LLM steps and Jev model only the newest run is shown. Runs recorded before grading was timed show their answer time only.

Answer time is the workflow run that produced the result, before any grading started. Grading time is the eval analysis of that result: composing the report the judge reads, reading every fact, the judge, and the code checks. Total time is the two added together. Each time is the median per run, then the shortest to the longest run. Runs whose workflow errored are left out of these times; a cell then says how many runs it covers.

| LLM steps | model (effort) | Jev model | runs | gate | checks below minimum | errored checks | Answer time (workflow run) | Grading time (eval analysis) | Total time | run folder |
|---|---|---|---|---|---|---|---|---|---|---|
| CLAUDE_CODE (ANTHROPIC) | claude-haiku-5-5 (low) | jev-1.13 | 5 | passed | none | 0 | 9.6s (8.2s to 9.9s) | 4.1s (3.9s to 4.3s) | 14s (12s to 14s) | `20261012T123402Z-claude_code-claude-haiku-5-5-low-b59980` |
| CLAUDE_CODE (ANTHROPIC) | claude-haiku-5-5 (medium) | jev-1.13 | 5 | passed | none | 0 | 9.7s (8.8s to 13s) | 4.8s (3.9s to 13s) | 14s (14s to 22s) | `20261012T123235Z-claude_code-claude-haiku-5-5-medium-7dfb06` |
| CLAUDE_CODE (ANTHROPIC) | claude-opus-5-5 (medium) | jev-1.13 | 5 | passed | none | 0 | 9.9s (9.7s to 18s) | 4.0s (3.9s to 4.5s) | 14s (14s to 22s) | `20261012T122958Z-claude_code-claude-opus-5-5-medium-48b91a` |
| CLAUDE_CODE (ANTHROPIC) | claude-sonnet-5-5 (medium) | jev-1.13 | 5 | passed | none | 0 | 9.5s (9.2s to 12s) | 4.0s (3.6s to 4.3s) | 13s (13s to 16s) | `20261012T123121Z-claude_code-claude-sonnet-5-5-medium-bf5cf5` |
| GPT_CODEX (OPENAI) | gpt-6-luna (low) | jev-1.13 | 5 | passed | none | 14 | 8.4s (7.7s to 8.6s), 4 of 5 runs | 4.0s (3.8s to 4.3s), 4 of 5 runs | 12s (12s to 13s), 4 of 5 runs | `20261012T123833Z-gpt_codex-gpt-6-luna-low-0fb069` |
| GPT_CODEX (OPENAI) | gpt-6-luna (medium) | jev-1.13 | 5 | passed | none | 0 | 8.7s (7.8s to 40s) | 4.2s (4.0s to 4.4s) | 13s (12s to 44s) | `20261012T123652Z-gpt_codex-gpt-6-luna-medium-d59bc4` |
| GPT_CODEX (OPENAI) | gpt-6.1-sol (medium) | jev-1.13 | 5 | passed | none | 0 | 16s (13s to 18s) | 3.9s (3.8s to 4.2s) | 20s (16s to 22s) | `20261012T123513Z-gpt_codex-gpt-6.1-sol-medium-d899df` |
