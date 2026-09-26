# Context compaction: PWI items for discussion

This list covers the context-compaction package only. The two completed review
snapshots are saved under `.workspace_tmp/code-review-runs/` in the
`context-compaction-converge-cycle2-20260925` and
`context-compaction-converge-cycle3-20260925` directories. The earlier attempt
did not complete its type-annotation chapter.

The latest reviewed snapshot had six findings in the facade, client ownership,
and validation groups below. Its other 36 findings have since received fixes
and deterministic checks. The prior review's three artifact-callback findings
were not reported by the latest reviewer, although the relevant persistence
behavior remains. They are preserved here for discussion rather than treated
as proven corrections. No occurrence overrides have been added.

The final edits still need a complete PWI re-review. The convergence skill's
default limit of two complete cycles has been reached, so the package is not
being declared clean or ready to merge.

## A domain facade and gateway package hierarchy

PWI recommends moving the CLI workflow behind a facade and placing the Jev
gateway under a Manager-owned `gateways/` package. These are two related
findings: `pwi.architecture-layers.service-interface-layer` and
`pwi.architecture-layers.typed-package-placement`.

The trial currently has one console host and a small flat package. Introducing
the proposed Manager hierarchy would establish a new convention for this
package. We should decide whether the trial needs that reusable service
boundary before choosing its package layout. The findings remain open.

## Artifact callbacks in the CLI trial loop

PWI reports two direct writes in `_run_live` and the `output_dir` carried in
`TrialOptions`. The rules are
`pwi.artifact-persistence-callbacks.direct-persistence-in-step` and
`pwi.artifact-persistence-callbacks.location-knowledge-below-entry`.

`_run_live` is presently a private CLI helper. If it becomes a reusable domain
operation, emitting artifacts through a host callback would separate that
operation from the filesystem. We should settle that boundary together with
the facade decision above. These three occurrences remain open.

## Ownership of the SDK client's lifetime

PWI's `pwi.class-design.stateful-dependency-injection` finding recommends that
the composition root own and close the SDK client while the gateway retains
an unchanged client reference.

The current gateway owns its client and acts as an async context manager.
After a successful close it clears the reference, making repeated closes
harmless and rejecting later requests. A failed close preserves the client so
cleanup can be retried. Both cleanup behaviors have tests. We should discuss
whether this resource-lifetime state should be exempt from the general rule
against changing injected collaborators, or whether client ownership should
move to the host. This occurrence remains open.

## Validation in the public retention function

Two `pwi.validation-exception-handling.validation-outside-boundaries` findings
recommend removing threshold and probability validation from
`apply_decisions`, because the CLI and gateway already validate those values.

`apply_decisions` is also callable directly, including when applying a saved
Jev response without a live gateway. Its checks protect those callers from
incomplete decisions and invalid probabilities. We should decide whether it
remains a public standalone function or becomes an internal operation whose
inputs are guaranteed by a validated record. These two occurrences remain
open.

The latest review adds a third occurrence for the size limit in `build_state`.
That limit is checked after the state has been built, because its serialized
size cannot be known beforehand. It prevents an oversized Jev request. We
should discuss whether that check belongs in the builder or in a separate
request-boundary operation; the guard remains in place.
