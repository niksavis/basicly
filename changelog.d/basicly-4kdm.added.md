- **Seven specialist roles drive the loop, and the engine now dispatches them by phase.** The
  factory's states have named their specialists in prose since the requirements were written, and
  nothing consumed the names: every dispatch ended at a bare `claude -p`, so one default runner
  served every phase. `basicly.roles` closes that with a table — `classify` → `decider`,
  `decompose` → `decomposer`, `build` and `repair` → `implementer`, `validate` → `validator`,
  `ship` → `curator`, `retrospective` → `retrospector` — and the runner puts `--agent <role>` on
  the argv.

  The map is **data, not judgment**: a phase resolves to exactly one role by lookup, so the choice
  costs no tokens, cannot drift between lanes and is not gameable. Resolution falls to the default
  runner rather than failing in three cases, each deliberate — a phase with no persona (verify is
  deterministic gates by decision), a family that cannot select one (codex ships no subagent root),
  and a role whose *projected* file is absent, checked against the file the host reads rather than
  the catalog source. A consumer on an older install therefore gets an unspecialised loop instead
  of a stopped one.

  Eleven agents are authored as catalog sources under `.basicly/core/agents/`, projected into both
  agent roots by `basicly agents-build` and **vendored to consumers by `basicly install`**: the
  seven loop roles above plus `code-reviewer`, `security-auditor`, `test-runner` and `researcher`.
  The projected `tools:` allowlist was verified to bind on copilot as well as claude, in the
  spellings we already emit, against a positive control (`basicly-4kdm`, `basicly-4xmu`).

- **Five loop skills, each paired to the role that loads it.** `decompose-plan`,
  `validate-as-consumer`, `repair-in-place`, `root-cause` and `python-guidelines` ship as catalog
  sources and are named in their agent's declared skills. `catalog lint` refuses a name that
  resolves to nothing, so the pairing is a checked relation rather than a sentence in a document
  (`basicly-4kdm`, `basicly-u2hl.52`).
