# Context compaction: PWI decisions and outstanding discussion

## Current status

The owner stopped further code review and directed us to finish the accepted findings using the installed guidelines and reference implementations. Both active review workers were interrupted. The last review is incomplete; it provides no clean-review claim.

The last completed review reported 41 findings. The implementation passes addressed 34 accepted occurrences. Three artifact-persistence occurrences remain deferred, and four lifecycle occurrences retain the design explicitly confirmed by the owner. No finding was silently suppressed, and no occurrence override was inserted.

The repeated reviews took too long because broad review passes were restarted while some method-structure corrections were incomplete. Some later findings concerned existing code that earlier reviewers had missed. Moving a method or adding a helper did not always complete the responsibility separation required by the guidelines. A finding disappearing from a later report is not evidence that it was fixed.

## Architecture and validation applied

The separate context-compaction domain follows the expense and Meridian examples, adapted to the owner's explicit gateway-construction requirement:

1. The `DomainFacade` exposes domain operations through `ManagerContextCompaction`.
2. Every public manager operation with inputs calls its named validator first. The manager sequences peer validators rather than having one validator invoke a peer.
3. Configuration providers and the rollout reader validate foreign input before constructing domain models.
4. The Jev gateway validates response schema, exact question IDs, answer types, and finite probabilities before returning a domain decision batch.
5. The service locator constructs the transport and configured gateway. The manager asks for the gateway and owns its lifetime; tests substitute the transport and exercise the actual gateway and SDK.
6. Closure flows from facade to manager to gateway to SDK client. Stable references and lifecycle flags support idempotent closure, rejection after closure, and retry after cleanup failure.

The expense gateway's Choice operation and the context gateway's Noul operation follow the same infrastructure pattern. Their domain operations differ. The context locator now constructs a default HTTP transport explicitly; the expense sample's default hook currently returns `None`, allowing its SDK to supply one. Expense source files were not changed.

The implementation consultation included the installed PWI guidance and actual Meridian reference code and tests. The correction pass used the method-design, boundary-validation, service-locator, lifecycle, typing, naming, and test-mediator guidance. Guards, orchestration, and substantive processing were considered by responsibility rather than by method length alone.

## Accepted findings addressed

These finding numbers refer to the completed `context-compaction-architecture-corrected-20260926` report, also presented as a regular report without rerunning its reviewers.

| Findings | Applied correction |
| --- | --- |
| 5, 17 | Validate URL ports before constructing transport or resetting CLI outputs; reject context with zero total characters before baseline arithmetic. Regression tests cover both cases. |
| 16, 20-21, 23-27 | Separate rollout parsing, selection, accumulation, and construction; extract segment description work; separate prepared-context checks; move peer-validator sequencing to the manager. Immutable scan data replaces accumulated processor state. |
| 9, 12-15, 18-19, 22, 28-29, 41 | Use domain-specific names and accurate iterable/required-value annotations; clarify asserter argument roles; name the transport factory hook `make_jev_http_transport`. |
| 10-11 | Rename the Python attributes to `jev_state` and `trial_baselines`; explicitly serialize them as `state` and `baselines`. A CLI regression test checks complete request and manifest JSON. |
| 30-40 | Generate incidental tool IDs, name shared encoding values, and separate the test mediator's observable state from the concrete HTTP transport spy. |

Earlier accepted fixes remain in place: source/output collision protection, quoted-secret redaction, strict rollout-schema admission, aggregate configuration errors, domain exception translation, immutable data models, and cleanup guards. Collision and secret handling have targeted regression coverage.

## Deferred work and settled decisions

| Corrected findings | Topic | Current decision and proposed discussion |
| --- | --- | --- |
| 1-3 | Artifact persistence | Deferred by the owner. The CLI still writes artifacts directly, and `TrialOptions` still carries `output_dir`. Revisit callback ownership and location knowledge when artifact saving is designed. |
| 4, 6-8 | Lifecycle state: settled | The owner reconfirmed the approved expense-style lifetime design. Stable manager/gateway/client references and cleanup flags remain intentional for ownership, repeated closure, and cleanup retry. These generic class-design findings do not justify changing that required behavior. |
| 10-11 | JSON naming: implemented | Python uses `JevRequestRecord.jev_state` and `TrialManifest.trial_baselines`. Explicit serialization preserves the external `state` and `baselines` keys. |

An earlier raw-settings-helper recommendation (initial finding 71) remains unsupported: that private helper reads one intentionally nullable raw value; it does not retrieve a Settings DTO. The public provider validates the complete settings and returns `JevSettings` or raises an aggregate configuration error. Its absence from the later report is not the reason for rejecting it.

## Verification and evidence

After the accepted corrections:

- 50 tests passed; one symlink test was skipped because this Windows host cannot create symlinks. Hard-link and resolved-path protection tests passed.
- Ruff lint and formatting passed.
- Pyright reported zero errors and one vendor SDK typing warning.
- The teaching request, response application, and compacted context match the checked-in examples.
- No live Jev calls were made.

Saved review records under the isolated worktree's `.workspace_tmp/code-review-runs/`:

| Record | Meaning |
| --- | --- |
| `context-compaction-architecture-20260926` | Initial refactor snapshot: 105 findings. Preserved historical evidence; source hashes became stale after corrections. |
| `context-compaction-architecture-corrected-20260926` | Completed snapshot: 41 findings, native review and 45 chapter partitions. This predates the corrections above. |
| `context-compaction-architecture-regular-20260926` | Regular report admitted from the same unchanged completed reviewer artifacts, with provenance; not another model review. |
| `context-compaction-architecture-final-20260926` | Incomplete review stopped by the owner. `interruption.json` records the cancellation; no complete result was produced. |

The earlier snapshot comparison used source hashes and file/rule/symbol families because the core comparison command requires two converge-mode results. That comparison was not an exact occurrence-count reconciliation and is not a verification of the current source.

Verification was performed in an isolated worktree. The owner subsequently authorized committing, pushing, creating a PR, and merging these context-compaction changes. Expense workflow changes remain outside this delivery.
