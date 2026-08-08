- **A plan gate on entry to BUILD refuses a unit of work the loop cannot hold to.** Every child
  in a plan must now declare all five of: acceptance criteria, at least one scope glob, a
  dependency list, a token budget (`budget_tokens`) and an integrity level (`integrity`, one of
  `L1`/`L2`/`L3`). A plan missing any of them is refused when it is loaded — by
  `basicly decompose --plan`, by `--children`, and by the loop's own child-plan proposer, which
  blocks for a human rather than recording it — and the refusal names every missing field on
  every child in one message instead of one per round trip. The inspection sits before BUILD,
  which is where nearly all the tokens go, so a plan defect is found while it is still cheap.
- **Decompose emits a dependency graph instead of deriving one from scope overlap alone.** A
  child's `depends_on` names sibling titles (the plan is written before any issue exists), and
  each declared edge is recorded on the tracker as a `blocks` dependency, so `br dep tree`
  carries ordering that no glob comparison can express — B needing A's decision when the two
  touch no common file. Declared edges are unioned with the scope-derived serial chain and
  deduplicated. A cycle in the declared graph is refused **naming its members**, and no issue is
  created: a half-recorded decomposition is worse than none. An empty `depends_on` is a
  declaration; an absent one is not, and is refused.
- **Each created child records its plan fields in a `## Plan` section**, and
  `plan_gate.build_entry_verdict` reads them back to decide whether a lane may be dispatched,
  naming the field a unit is missing. It fails closed on an unreadable record (`basicly-u2hl.1`).
