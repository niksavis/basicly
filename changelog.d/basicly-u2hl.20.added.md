- **The plan gate refuses a planned child that cannot name its end-to-end demonstration.**
  Every child in a plan now declares a sixth field, `demonstration`: how it is exercised through
  the consumer surface — a command to run, a request to make, or a test — with the runnable part
  in backticks. A child that names none is refused at plan time by `basicly decompose --plan`, by
  `--children`, and by the loop's child-plan proposer, naming the child; so is one whose
  demonstration is prose naming nothing runnable, on the same rule that already refuses a `## Scope`
  entry that is not a backticked glob. A child with no consumer-visible behaviour is a horizontal
  slice, and a horizontal slice leaves verify nothing to derive a check from — the refusal moves
  that discovery to the point where splitting the plan is still cheap. The field is recorded in the
  child's `## Plan` section and reads back with the rest (`basicly-u2hl.20`).
- **`basicly.plan_entry`** now holds the build-entry predicate that decides whether a recorded bead
  may be dispatched (`build_entry_verdict`, `entry_verdict_for`, `EntryVerdict`), split out of
  `basicly.plan_gate` along the boundary that module's docstring already drew: judging a proposed
  plan against reading a recorded one back. It deliberately does **not** require a demonstration —
  every bead recorded before the field existed carries a `## Plan` heading without one, so on that
  population its absence cannot be told from a defect (`basicly-u2hl.20`).
