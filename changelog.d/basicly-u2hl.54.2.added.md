The advance out of `validate` now refuses on a failed or missing consumer gate, and the two
refusals are different. A `validate-as-consumer` result recorded **failed** by an engine
provider spends one bounded rework attempt through the existing `_rework` path and escalates
into the decision queue at `max_rework`. A **missing** result blocks without spending an
attempt — nobody has looked yet, so there is no finding to repair, and charging it would burn
the budget that exists for repairing findings and then escalate a unit whose validation had
never run. A result whose provider is outside `ENGINE_GATE_PROVIDERS` still leaves the gate
missing, but is now named in the refusal rather than silently ignored. Neither refusal merges,
tears down a worktree, closes the bead or commits tracker state.
