- **The `python-guidelines` skill carries the design calls no linter makes, and it activates on
  Python files rather than waiting to be asked.** Where an oversized module splits, whether a name
  or a docstring carries meaning, whether an abstraction earns its keep, `noqa` legitimacy,
  exception design, 3.14 idiom selection, free-threading safety, and the rule that a comment
  contradicting the code is a defect in which the code is what ships — none of which any rule in
  the stack can read.

  It stays a skill and takes a `paths: ["**/*.py"]` glob, which limits *and triggers* automatic
  activation, so it binds on every Python edit for **zero** always-on characters. The glob sits
  under the skill schema's `claude:` vendor fence because `paths` is outside the portable Agent
  Skills subset, which keeps every projected `SKILL.md` portable while still expressing the
  host-specific capability. Codex has no glob-based instruction scoping and still relies on model
  invocation there — a parity gap declared rather than papered over (`basicly-u2hl.13`,
  `basicly-u2hl.17`).
