- **Module size is now gated as a token ratchet, and cyclomatic complexity is linted.**
  Nothing in this stack measured module length — ruff has no rule for it — so `cli.py` reached
  53,095 tokens with every gate green. The new `module-size` check (`.scripts/check_module_size.py`,
  wired into the `fast` and `full` verify sets, so it runs at commit time) measures every tracked
  `.py` under `src/`, `tests/`, `.scripts/` and `.basicly/core/` in tokens and refuses one that
  crosses `decompose.SCOPE_FILE_READ_CAP` — imported, never respelled, so the size a lane is
  refused at is the size the sizing governor budgets with. It is a ratchet rather than a hard cap:
  the 78 modules already over the cap are recorded at their go-live counts in
  `[tool.module_size.frozen]` and may only shrink, an entry that reaches the cap is deleted rather
  than lowered, and a deliberately cohesive module may carry a one-line `module-size-waiver:`
  reason whose count is itself ratcheted in both directions. Read it as an agent working-set gate,
  not a defect-density claim — the defect literature argues the other way, and the gate's docstring
  says which studies must not be cited in its support. Separately, ruff now selects `C90` with
  `max-complexity = 15`, measured at 0 violations on the tree it landed on and 14 at 10, so it
  binds the next function that crosses instead of arriving with a backlog and an argument
  (basicly-u2hl.5).
