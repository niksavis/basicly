- **`basicly status` names the agent-hook tier this machine actually delivers, instead of letting a
  projected file imply it.** The `Hooks` table's `activation` column was `-` for the `claude` and
  `copilot` managers: git activation was reported, and the two agent surfaces were reported as
  projected and nothing more. A projected hook only fires where its host runs, so on a machine
  without that host the file is present and enforces nothing — the built-and-never-connected shape
  this repo keeps rediscovering. Each agent manager now reads `active` or
  `unavailable (<host> absent)`, with a line under the table naming which surfaces are active, which
  are not, and that the git hooks remain the commit-time floor either way. `basicly status --json`
  carries the same two facts per agent manager as `host` and `surface_present`; the payload is
  additive, so `schema_version` is unchanged.

  The probe behind it is `hooks.agent_hook_surface_present`, which resolves the host binary through
  an injected `which` like `runner.is_available` does — the suite hides the ambient agent CLIs on
  purpose, so an injected resolver is the only way a test can assert either answer.

  **The enforcement itself is now tested by running it, not by reading it.**
  `test_projected_agent_hook_fires_and_its_refusal_reaches_the_agent` plays the host against the
  projected `.claude/settings.json`: it selects a group by its `matcher`, substitutes
  `${CLAUDE_PROJECT_DIR}`, runs the command verbatim on an `Edit` payload, and asserts the block
  code *and* the refusal text — the exit code alone does not discriminate, because
  `python <missing>.py` also exits 2 (`basicly-0p8n`).

  **Both hosts have a hook surface, re-probed 2026-08-15 against the installed binaries**: claude
  2.1.233 and Copilot CLI 1.0.79, whose `copilot help config` documents a `hooks` key and
  `disableAllHooks`. The 2026-08-08 "copilot has no hook surface at all" finding was an artifact of
  its probe and is already retracted in `.basicly/core/kit/tier/README.md`. What copilot still does
  not receive is the `protect-generated` guard — it gets only the telemetry hook — and that gap is
  `basicly-66ix`, not this change.
