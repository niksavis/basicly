`validate` is a real loop phase, sequential after `verify` and before `ship`, with its
own handler and an entry in `[policy.evidence]`. Its gate, `validate-as-consumer`, binds
only where a unit's recorded `[harness-classification]` marker names L3 — L1 and L2 cross
the state in the advance they always did, and a unit carrying no marker is unaffected, so
work already in flight neither gains a rung nor is refused. A unit resting in `validate`
counts against the downstream WIP bound.

Two supporting fixes this rests on. The `verify` and `ship` rungs are now derived from the
per-gate fields of `GateStatus` rather than the aggregate `can_advance`, so requiring a
second gate no longer drops a merged unit back to `build` and re-runs a landing that
already succeeded. And intake now passes the bead's declared `## Scope` to `classify`,
which it never did: `integrity.assign(())` hit its `unclassified` fallback, so **every
unit the loop had ever classified was recorded L2** and no L3-gated behaviour could fire.
