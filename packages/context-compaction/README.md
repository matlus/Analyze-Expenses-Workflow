# Context compaction trial

The [concept guide](../../docs/context-compaction/README.md) explains the evidence-selection pattern and where else it could be useful.

This package tests Jev as a **context selector** for a real Codex task. It reads a bounded portion of a local rollout JSONL, protects chat messages, groups each tool call with its result, and asks Jev which tool events are still needed for the current user goal. It retains selected text apart from credential redaction. The original rollout is never edited.

Trial windows must contain text content blocks. The reader rejects image, audio, and other nontext message blocks because this prototype cannot preserve them in a compacted context. Common credential formats are redacted from complete tool text before excerpting, and before local result writes; inspect a new source window for other sensitive content before using a live run.

The workspace root's ignored `.env` already supplies `OPEN_ROUTER_KEY`, `OPEN_ROUTER_BASE_URL`, and `JEV_MODEL` for the same TypeSafe SDK route used by the expense package. Environment variables override that file. Keep credentials out of command arguments and results.

Run from the workspace root. Replace `<rollout>` with the source path in the prepared manifests or another local Codex JSONL file:

```powershell
uv sync --all-packages --all-groups
uv run --all-packages context-compaction <rollout> --start-line 83 --cutoff-line 184 --output-dir .workspace_tmp/context-compaction/activation --required-tool tool_166 --runs 3
uv run --all-packages context-compaction <rollout> --start-line 62 --cutoff-line 207 --output-dir .workspace_tmp/context-compaction/certificate --required-tool tool_197 --runs 3
```

Each command asks Jev the same retention questions three times with each of two instruction wordings. The output includes a manifest, the exact redacted Jev requests, `live_results.json`, and the resulting compacted contexts. `--runs 0` prepares the input without calling Jev. Reusing an output directory clears its previous generated live results and compacted contexts before preparing the next trial. The held-out answer is in the manifest and is excluded from requests.

Read the reduction alongside `required_tools_retained` and the simple recency baselines. A lower character count is useful only when the context still supports a correct answer. The `excerpt` mode is available for a larger window, but its judgments are less conclusive because Jev sees only selected portions of each tool result.

The first live results are summarized in [RESULTS.md](RESULTS.md). The raw decision records remain in ignored `.workspace_tmp/context-compaction/` directories.
