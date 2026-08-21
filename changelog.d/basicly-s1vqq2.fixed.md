- **`basicly board` now derives a loop phase for every record instead of eight, and the cap is
  removed rather than raised.** Measured on this repository before the fix, the wall's loop
  region read `intake 8 · classify 0 · decompose 0 · build 0 · verify 0 · validate 0 · ship 0`
  over 234 active units - not an idle factory but `board_facts.PHASE_LIMIT`, and a reader could
  not tell the two apart. `loop_state.read_node_state` is the only route to `derive_phase` and
  it reads the whole event log seven times per record, so a phase for the whole population was
  the 138 s the cap existed to avoid.

  **One fold, then the same derivation over it.** `loop_state.phase_map` reads the log once and
  folds it once, through a new `tracker.all_views` seam - one view already carries the status,
  the `external_ref` binding, the markers, the gate rows and the edges that `derive_phase`
  takes - so the population is one read and the phase is arithmetic over it. Measured on this
  repository's own log: **1036 records in 0.125 s**, against **128.1 s** for the 236 active
  ones through the per-record route, and the two paths agree on all 236. `basicly board` now
  builds in **534-555 ms** over four runs, with a phase on **236 of 236 units** where the
  region used to read `intake 8`. `PHASE_LIMIT` is gone, so no reader is left believing a
  bound still applies.

  **It calls the real derivation, and so do its inputs.** `policy.classify_gates` and
  `validate_gate.required_in` are the pure halves of `policy.gate_status` and
  `validate_gate.required_config`, split out so a caller holding folded rows classifies them
  the same way rather than spelling the rule a second time. The kit ships a `derive_phase` of
  its own and it is deliberately not the one used: it folds the ledger alone and cannot see the
  integrity level a unit's validate gate hangs off, so it reads `verify` where the engine reads
  `validate` - and renders identically. `tests/test_board_facts.py` pins the fold count at one
  with a spy rather than a duration, and pins that L3 case against its L2 control
  (`basicly-s1vqq2`).
