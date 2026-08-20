- **The board snapshot can now carry the `lanes` section, and only when a caller supplies
  the lane facts.** `lanes[].phase` is required by the contract and its authority is
  `loop_state.read_node_state`, which reads the policy config's required-gate set - a
  source the file-only producer does not open. So the facts arrive as
  `board_fields.LaneFacts` from a caller that drives the loop, exactly as the supervisor
  lock facts already do, and with none supplied the section is omitted rather than filled
  with a derived phase that would diverge from the engine's for any unit owing validation.
  An empty sequence still emits `[]`, which is the different claim that the caller can see
  lanes and there are none (`basicly-06pvsc`).
