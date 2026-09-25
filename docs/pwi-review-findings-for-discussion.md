# PWI review findings held for discussion

This records findings already produced on September 23, 2026. It does not start or complete another review. The latest configuration review remained **pending** because the reviewer could not launch the remaining chapter agents. Its source files were refactored after the findings were recorded, so the findings describe that earlier snapshot and have **not** been rechecked against current code.

## Latest accepted PWI findings

Source: `.workspace_tmp/code-review-runs/expense-config-audit-20260923/staging/method-design-code-review-guidelines-batch-001-attempt-1.json`. All five findings had warning severity.

| Symbol | Rule | Finding at review time |
| --- | --- | --- |
| `ConfigurationProvider._load_coding_assistant_settings` | Mixed Abstraction Levels | Retrieved a required value through a helper, then parsed the subscription enum and translated errors inline. |
| `ConfigurationProvider._load_budget_settings` | Mixed Abstraction Levels | Retrieved optional values through a helper, then applied defaults, validated the budget model, and translated errors inline. |
| `ConfigurationProvider._load_llm_operation_settings` | Mixed Abstraction Levels | Looked up settings through a helper while iterating operations and constructing and validating each model inline. |
| `ConfigurationProvider._load_jev_settings` | Mixed Abstraction Levels | Retrieved required values through a helper, then validated the Jev model and translated errors inline. |
| `SettingsProviderEnvironment.__init__` | Public Method Implements Rather Than Orchestrates | Performed dotenv discovery, filtering, precedence, and settings-map construction inside the public constructor. |

The subsequent refactor extracted the parsing/building and environment-loading steps. Local tests, Ruff, Pyright, and the two live Jev acceptance tests passed after that change. No PWI finding is marked resolved here because the changed snapshot was not reviewed.

The configuration run accepted a clean native Python review and three PWI chapter jobs; **16 of 19 chapter packs remained pending**. There is no final PWI verdict for that run.

## Earlier broad review context

`.workspace_tmp/code-review-runs/expense-audit-20260923/partial-validated-evidence.json` preserves 25 findings from an earlier 54-file snapshot: 2 Architecture Layers, 9 Need-to-Know Principle, and 14 Method Design. That run ended in `technical_failure` after accepting 10 of 63 chapter packs. Code changed after many of those findings, so this is historical discussion material, not a current open-issues list or a completed project review.

The review artifacts remain in `.workspace_tmp/code-review-runs/`. Keep the distinction between recorded findings, later code changes, and unreviewed chapter packs when discussing next steps.
