# First real Codex context trial

## Source and method

The source was one 667,560-byte local Codex rollout about connecting `matlus.com` to GitHub Pages. Its SHA-256 is `f8e207b4e67b7a7f8ccad5cc3467c217f232ea7054c77d1d0cd5feb4c63efc63`. Two bounded checkpoints from this task were evaluated. Each sent the full selected chat and tool-event text to Jev through the workspace's configured OpenRouter route. User and assistant messages were always retained. A tool call, its result, and a continuation poll were grouped as one candidate.

Jev answered one Noul question per tool event. The experiment used `JEV_MODEL=jev-1.13`; the response identified `typesafe/jev-1.13-20260917`. The retention threshold was 0.5. Each of two question wordings ran three times. The held-out assistant answer was excluded from every Jev request.

| Checkpoint | Original context | Wording | Tool events kept in all 3 runs | Character reduction | Keep-newest-tool baseline |
|---|---:|---|---|---:|---:|
| HTTPS activation | 18,253 characters | Current need | `tool_95`, `tool_119`, `tool_166` | 0.0% | 48.1% |
| HTTPS activation | 18,253 characters | Careful loss | `tool_166` | 48.1% | 48.1% |
| Certificate follow-up | 67,099 characters | Current need | `tool_166`, `tool_197` | 51.5% | 58.0% |
| Certificate follow-up | 67,099 characters | Careful loss | `tool_197` | 58.0% | 58.0% |

The keep/drop pattern was stable across the three repeats of each wording. Jev's returned probabilities varied slightly. Under the careful-loss wording, the required latest event scored 0.57 in the activation case and 0.54–0.60 in the certificate case. The second range is close to the 0.5 threshold; the threshold is an experimental choice, not a proven safety boundary.

## Evidence check

The retained `tool_166` result contains the live HTTPS response, `www` redirect, certificate approval, and HTTPS-enforcement state used in the activation answer. The retained `tool_197` result contains the later HTTPS and HTTP checks, API enforcement state, and GitHub's up-to-24-hour guidance used in the certificate answer. The user's correction about the disabled checkbox remains in protected chat. All six careful-loss runs passed the narrow required-tool check.

This establishes **proposed context reduction with the identified evidence retained** for two checkpoints. It does not establish that a downstream LLM would produce an equally good answer, nor that Jev beats a simple recency rule on these examples: the careful-loss decisions exactly matched that baseline. A broader trial should include goals that genuinely require an older tool result, downstream answer replay, and more conversations before adopting automatic deletion.

## Artifacts

The ignored workspace directories `.workspace_tmp/context-compaction/activation` and `.workspace_tmp/context-compaction/certificate` contain the full manifests, redacted Jev requests, every score and decision, token usage, and each proposed compacted context. The original rollout was not modified. The OpenRouter key remained in the ignored root `.env` and was not written to the artifacts.

After the reader and redaction fixes, both prepared Jev request files were byte identical to those used for the 12 live calls (SHA-256 `d015f4b8ad5812403cbb646a641fb16cfb203771f844a0d281d9f154bedc3461` and `7fd93b20714e43e83ff1fb9a7ea05a5146950277af80a950324a8332a61b3183`). The live results therefore still apply to the final text-only trial windows.
