- **Every gate is classified by type, so "what happens when this one fails" is answered by the type
  rather than per call site.** `policy.GATE_TYPE_BY_GATE` types each gate the engine names as
  pre-flight, revision, escalation or abort, and defaults an unnamed one to revision. A pre-flight
  gate is additionally refused the tracker while it runs, so it cannot write state before the work
  it guards exists.

  The two rules that govern adding one are recorded with it: selection starts at pre-flight and
  moves only when a check must run after work is produced, and a cap is sized to the cost of one
  iteration — a landing bounce and a re-review of a three-line fix must not share a budget
  (`basicly-m4zv.6`).
