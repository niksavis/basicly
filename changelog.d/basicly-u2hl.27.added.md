- **`basicly loop improve` runs the second loop shape: a control loop over a property of the
  codebase, rather than over a requirement.** The delivery loop takes a requirement and ships a
  change; this one holds module size against the 4,000-token agent working-set cap and chips at the
  standing debt on a schedule. Set point, sensor and dampener already existed and are used rather
  than restated — `read_cost.SCOPE_FILE_READ_CAP`, `.scripts/check_module_size.py`, and the frozen
  ratchet in `[tool.module_size]` that stops the property getting worse meanwhile. What was missing
  was the controller and the actuator, and `.scripts/improvement_controller.py` is both.

  **The engine disposes.** Selection is arithmetic over the sensor's measurements: the unwaived
  module furthest above the cap, ties broken by path, so two runs over one tree pick the same
  target and no model chooses it. It reads the sensor's *measurements* and never its findings — a
  frozen module sitting at 60,089 tokens is exactly what the ratchet permits and exactly what this
  loop exists to reduce, so a loop driven by the gate's failures would have nothing to do on a
  green tree. A waived module is never a target: the waiver is a recorded decision, and
  re-targeting it would re-open it every run.

  **One unlanded lane at a time**, and the bound is basicly-u2hl.23's `wip.WipAdmission` rather
  than a second record beside it. Its occupancy set is deliberately wider than BUILD's
  `wip.DOWNSTREAM_PHASES`: a lane this loop filed still counts while it is being built, because a
  second target selected over the same tree is the duplicate work the bound exists to prevent. A
  run with a lane open files nothing and names what to land; a run with none files exactly one.

  **The drop is reported.** One run selects one of sixty-nine candidates and prints the count it
  did not select — a silent top-1 reads as "nothing else is over the cap" (`basicly-u2hl.27`).
