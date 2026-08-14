- **A failed lane is repaired in its own worktree, briefed with the findings that rejected it.**
  Rework used to dispatch a fresh agent with the same fixed prompt every attempt — the same tier,
  the same framing, and no knowledge of why the last attempt failed — so a lane spent its rework cap
  without changing a variable. `basicly.repair_brief` assembles the actual gate evidence (the check,
  the command, the result) and `loop` dispatches the implementer in **repair mode** into the
  worktree that already holds the work.

  Repair is a mode of the implementer rather than a new role: it differs in prompt alone, not in
  tier, tools or artifact, so it maps to `implementer` and the mode travels in the brief
  (`basicly-u2hl.4`).
