# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## v0.5.1 - 2026-07-20

Delta: v0.5.0..v0.5.1

### Fixed

- **Install now activates git hooks on a fresh consumer repo**: hook activation
  runs pre-commit through `uv tool run` (uvx), which provisions the tool in an
  ephemeral environment, instead of `uv run`, which only resolved pre-commit when
  the consumer repo already declared it as a dependency and otherwise failed with
  "program not found". A target with no `.git` is now skipped with clear guidance
  (run `git init`, then `basicly hooks-build`) instead of an opaque pre-commit
  error, and the "run manually" hints point at `uvx pre-commit install`
  (basicly-x5gh).

## v0.5.0 - 2026-07-20

Delta: v0.4.0..v0.5.0

### Added

- **Per-agent health scoring and behavioral drift**: `basicly health [--json]
  [--window N] [--fleet]` derives a per-agent dispatch failure rate, a rework
  signal, and a bounded health score from the run-record log, and flags an agent
  whose recent failure rate regressed against a rolling baseline read off the
  log's own timestamps (basicly-y886).
- **Cross-repo fleet rollup**: `basicly status --fleet [--root PATH]` rolls each
  housed repo's status snapshot and run-record summary into one read-only JSON
  payload (basicly-h0f0).
- **Opt-in per-agent bot git identity**: a runner spec may pin a
  `git_name`/`git_email`; the dispatch seam commits the agent's work under that
  bot identity, and `identity-guard` validates the effective (env-aware) identity
  so a bot email is bound by the allow-email pattern (basicly-smzg).
- **Runner model field and attribution**: a runner adapter may pin a `model`,
  injected at the invocation seam and recorded in the run-record; landings and
  gate results carry the dispatched agent and model as `Harness-Runner` /
  `Harness-Model` attribution (basicly-45ld, basicly-140a).
- **Headless capability probe**: `auto` runner selection probes a candidate's
  headless flag before choosing it, so a renamed flag no longer gets picked and
  then fails at dispatch (basicly-bveo).
- **Action-boundary guardrails**: copilot deny-tool flags injected at dispatch
  (basicly-lqz5), captured runner output redacted for secret shapes at the source
  (basicly-3p2i), and a commit-time backstop blocking staged edits to generated
  files (basicly-yw28).
- **Human-checkpoint enforcement**: loop checkpoint approvals require an
  interactive terminal or a one-time confirm code, so a non-interactive process
  cannot self-approve ship (basicly-shgo).
- **Structured needs-input outcome**: a dispatched agent that cannot resolve a
  required fact writes a sentinel and the loop blocks instead of landing a guess
  (basicly-o774).
- **Agent-skills directories and skill taxonomy**: skills project as full
  agent-skills spec directories with optional frontmatter into both skill roots,
  split into universal core skills and technology-tagged optional skills (python,
  node, wsl) (basicly-q1w9 and children).
- **Structured acceptance-criteria for Definition of Ready**: the DoR gate
  accepts `br`'s structured `acceptance_criteria` field, not only a description
  heading (basicly-58iu).

### Fixed

- **Loop landing no longer strands uncommitted work**: a worktree whose build was
  not committed on its branch now blocks with clear guidance instead of
  misreporting a rebase conflict and burning rework attempts (basicly-4psl).
- **Ship refuses an unmerged worktree**: the ship transition blocks a node whose
  worktree branch has not landed, so a bead can no longer close with its code
  stranded (basicly-o0q3).
- **Pre-commit rewrite preserves unmanaged hooks**: projecting the managed hook
  block no longer drops a consumer's own comments or hook ordering (basicly-wd7u).
- **Windows path handling in the rubric runner**: a Windows executable path no
  longer breaks POSIX shell parsing on CI (basicly-5tjk).

## v0.4.0 - 2026-07-17

Delta: v0.3.1..v0.4.0

### Added

- **Per-run record at the dispatch seam**: every runner dispatch writes a
  metadata-only record keyed by bead id (agent, outcome, return code, duration,
  redacted command) to a self-ignored `.basicly/usage/run-records.json`
  (basicly-z6dh).
- **Catalog-managed agent deny-list**: a `permissions.yaml` catalog source
  projects a baseline Claude Code `deny` list into `.claude/settings.json`
  (`permissions build` / `permissions check`), and the repo dogfoods it
  (basicly-u0zg).
- **Stdlib secret-scan pre-commit gate**: a dependency-free hook scans staged
  added lines for common secret shapes, honoring a `pragma: allowlist secret`
  marker (basicly-yzyd).
- **Rubric-based behavioral eval**: `basicly rubric eval` runs YAML-authored
  rubric checks (deterministic first, judged advisory) and reports an advisory
  `rubric` gate (basicly-0122).

### Fixed

- **The loop no longer strands a commit**: `loop advance` refuses the build and
  ship transitions when run from a linked worktree, and worktree cleanup drops a
  session record whose branch is already gone (basicly-9niw).
- **Accurate tool-usage telemetry**: the counter no longer records
  backslash/dash heredoc bodies, flag-led pipeline segments, or inline
  `python -c` / `-m` code as tool names (basicly-v7eu).
- **Prefix-anchored commit id detection**: `beads-commit-msg` matches issue ids
  by the configured prefix (like `br`'s own commit scanner) instead of any
  hyphenated word, so ordinary phrases are never mis-flagged and the error names
  the real cause (basicly-jms0).
- **`.env` deny-list uses the form Claude Code accepts**: the guardrail keeps
  only the `Edit(...)` globs (which cover every file-mutation tool) and drops the
  `Write`/`MultiEdit`/`NotebookEdit` file rules Claude Code rejects at startup
  (basicly-7ihd).

## v0.3.1 - 2026-07-17

Delta: v0.3.0..v0.3.1

### Changed

- **CI runtimes bumped to Node 24**: every marketplace action pin
  (`actions/checkout`, `actions/setup-node`, `astral-sh/setup-uv`,
  `softprops/action-gh-release`) moved to its floating major that targets
  `node24`, clearing GitHub's Node 20 deprecation warning. No shipped-package
  change.

## v0.3.0 - 2026-07-17

Delta: v0.2.0..v0.3.0

### Changed

- **BREAKING — CLI namespace grouping**: the flat authoring and inspection
  subcommands moved under a `basicly catalog <verb>` group and the old names were
  removed (no aliases). `catalog-lint` → `catalog lint`, `catalog-verify` →
  `catalog verify`, `review` → `catalog review`, `list`/`skills-list`/`agents-list`
  → `catalog list [fragment|skill|agent]`, and
  `fragment-new`/`skills-new`/`agents-new` → `catalog new <fragment|skill|agent>`.
  The consumer projection pairs (`build`/`check`, `skills-build`/`skills-check`,
  `agents-build`/`agents-check`, `hooks-build`/`hooks-check`) and the harness
  commands stay top-level. Consumers who script the old names — including the
  scaffolded CI `catalog lint` step — must update them; re-run `basicly install`
  to refresh the scaffolded workflow.
- **Always-on size-warning cap raised to 9000** for the claude and copilot
  targets, calibrated to warn before the projected instruction files dilute
  attention rather than at an arbitrary round number; codex stays at 12000.
- **Every `br` invocation routes through one adapter seam**, giving tracker
  access a single, testable boundary.
- **Refreshed branding**: a redesigned logo and landing-page flow diagram.

### Added

- **`basicly status`**: a read-only snapshot of the harness/tracker/worktree
  state (with `--json`), safe to run anywhere — it never mutates and always
  exits 0.
- **`basicly usage`**: a report over the tool-usage telemetry, alongside a
  gitignored `basicly.local.toml` overlay that layers per-machine
  `[worktree]`/`[verify]`/`[policy]`/`[runner]` settings over the committed
  harness config.
- **Zero-touch tracker in loop worktrees**: worktrees share the base tracker
  through a `.beads/redirect` (capability probed at provisioning), and the engine
  owns tracker commits at provisioning, landing, and ship — agents no longer
  stage `.beads` on a harness branch.
- **Core-upgrade resilience**: the loader survives upgrades that remove a
  replaced fragment id and gates sources on `schema_version`.
- **OS-matrix release gating**: the release workflow runs on ubuntu/windows/macos
  with a fresh-repo install smoke test and attaches built wheels, and every
  release page now carries a copy-paste, tag-pinned `uvx` install command.
- **`session-finish` skill** and skill-invocation counting.
- **`hooks-check` diagnoses a missing `uv`**, and the committer requirements are
  documented in the README and CONTRIBUTING.

### Fixed

- **Harness-loop correctness**: hook scripts derive the repo root from cwd;
  staged and verify checks fail when the underlying `git` call fails; policy
  markers are matched token-exactly with a hook-floor compile test; the merge
  queue validates beads upfront, aborts failed merges, and guards dirty
  worktrees; co-owned writes are atomic with a byte-exact check and safe sweeps;
  the loop honors the configured base branch and concurrency cap; and
  `verify --issue` refuses to record a gate from a linked worktree so the landing
  advance records it from base.
- **Windows compatibility**: `basicly status` and the CLI degrade gracefully when
  `git` is absent from PATH, unencodable output is downgraded on narrow/cp1252
  consoles, unrunnable-command detection accepts the Windows "not found" detail,
  and the CLI test helpers stop stripping `PATH` from the subprocess env.
- **Tool-usage telemetry** counts only the real command at each quote-aware
  pipeline head — quoted-string bodies, flag values, and heredoc bodies are no
  longer miscounted as tools.
- **commit-msg** now names the offending character when a description is
  rejected, and the `conventional-commits` skill documents the lowercase-only
  charset (put version numbers and proper nouns in the body).
- **CI hygiene**: tracker-only pushes no longer trigger builds, the pytest gate
  runs in parallel via xdist (dropping a duplicate pre-commit step), workflow
  jobs have descriptive names, and the usage-report tests are hermetic against
  live telemetry.

## v0.2.0 - 2026-07-16

Delta: v0.1.3..v0.2.0

### Added

- **Tool-usage telemetry hook**: a PostToolUse hook for both Claude Code and
  GitHub Copilot counts every shell command's pipeline heads into
  `.basicly/usage/tool-usage.json` (self-ignored from git) — token-free,
  deterministic data on which terminal tools agents actually use, for tailoring
  the catalog with real evidence. Ships in the catalog and is dogfooded here.
- **Copilot hook manager**: `hooks.yaml` entries now target one of three
  managers — `git` (pre-commit config), `claude` (`.claude/settings.json`, with
  per-spec event and matcher), or `copilot` (managed
  `.github/hooks/basicly-<id>.json` files, synced and pruned like every other
  projection).
- **Runner auto-dispatch in the harness loop**: `basicly loop advance` on a
  ready leaf provisions the worktree and dispatches the selected headless
  runner inside it; the `manual` runner preserves the block-and-resume handoff
  (this repo pins `[runner] default = "manual"`).
- **Bootstrap shims**: `.scripts/bootstrap.sh` (curl-able POSIX sh) and
  `.scripts/bootstrap.ps1` install `uv` when absent, then run the pinned
  install — one command on a machine with no Python at all.
- **Rich terminal output**: styled status lines, real tables, and `--help`
  grouped by audience (consumer / contributor / harness); piped and CI output
  stays byte-identical plain text. Adds `rich` as a runtime dependency.
- **Branding and a landing page**: a project logo, README badges, a
  GitHub-rendered architecture diagram, a root `CONTRIBUTING.md`, and a
  GitHub Pages site at <https://niksavis.github.io/basicly/>.

### Changed

- **README rewritten user-first**: overview → quick start (copy-pasteable
  install, upgrade, uninstall) → reference; `PYTHONPATH=` relics removed, every
  flag explained, hook stages vs the pre-commit framework filename clarified.
- **architecture.md now describes shipped behavior plainly**: implementation
  status markers were removed everywhere except the genuinely deferred items,
  which are collected in one section.
- `.claude/settings.json` is committed: the deny-list is tracked in git and
  carries the tool-usage hook wiring.

## v0.1.3 - 2026-07-16

Delta: v0.1.2..v0.1.3

### Added

- **Technology scoping for the catalog**: sources (skills, fragments, agents,
  hooks) may declare `technologies: [python, zsh, ...]`; an untagged source is
  universal and always ships. `basicly install --technologies python,zsh`
  records the selection under `[catalog]` in `basicly.toml`; the projection
  commands then skip non-matching sources, previously projected skills/agents
  the selection excludes are pruned, and excluded managed hooks are stripped
  from `.pre-commit-config.yaml` and `.claude/settings.json`. The tag
  vocabulary is a controlled list enforced by `catalog-lint` and every loader,
  and the stack-specific skills (`tool-uv`, `tool-zsh`, `tool-tmux`,
  `tool-starship`, `tool-wezterm`) are tagged. With no selection recorded the
  full catalog ships, exactly as before.
- **Agents as a catalog kind**: subagents are authored as composable
  `agent.yaml` sources plus shared `*.block.yaml` building blocks, projected to
  `.claude/agents/` with schema validation, composition lint (unknown block
  refs, read-only postures granting write tools, portable size cap), and
  uninstall sweep. Three core agents ship: `code-reviewer`, `test-runner`,
  `security-auditor`.
- **A `quirks` fragment category** wired to the self-improvement retro: one
  real incident, one bullet (environment/timing/platform traps).

### Changed

- **Scoped rules are single-sourced**: the Copilot `scoped_instructions`
  output was retired in favor of one scoped-rules source per target, and
  `basicly build` now sweeps manifest-tracked outputs that drop out of the
  plan, so retiring an output converges consumers instead of stranding stale
  projections.
- The committed Claude settings deny `.env*` writes in addition to reads, and
  catalog guidance was pruned/tightened to fit projection size advisories.

### Fixed

- **Feature fan-in no longer collides with self-landed children**: a parent
  feature whose children each landed and closed through their own loop
  advances build -> verify instead of failing with "no worktree session
  named"; already-merged, torn-down children count as landed.
- Projected instruction files render lint-clean (their markdownlint ignores
  were dropped), and new worktrees receive uncommitted tracker state so the
  first in-worktree commit does not trip the beads hook.

## v0.1.2 - 2026-07-16

Delta: v0.1.1..v0.1.2

### Fixed

- **Release tags could ship stale package metadata**: the v0.1.1 tag was cut
  without a version bump, so `basicly --version` at that tag prints `0.1.0`
  and consumer `install.json` files get stamped with the stale
  `basicly_version`, breaking version-based upgrade/drift detection. The
  package version is now single-sourced from `src/basicly/__init__.py`
  (hatchling dynamic version) so `pyproject.toml` and the module can no
  longer drift, and it is correctly bumped for this release. The v0.1.1 tag
  itself is left untouched; re-running `basicly install` at this tag
  refreshes a consumer's recorded version.

### Added

- **Release gate for version mismatches**: the release workflow now fails
  before publishing when the pushed tag name and the package version
  disagree, so a tag can no longer ship mismatched metadata.

## v0.1.1 - 2026-07-16

Delta: v0.1.0..v0.1.1 (documentation-only patch)

### Changed

- **`tool-br` skill**: new Common Pitfalls bullet — never commit with a guessed
  issue id; `br create` assigns a random base, so run it alone, read the
  generated id from its output, and commit separately (chaining with `|| true`
  silently swallows the hook rejection).
- **`conventional-commits` skill**: description rule now states that version
  strings and filenames (dots/uppercase, e.g. a tag name or `AGENTS.md`) can
  never appear verbatim in a commit description and must be reworded, with a
  matching invalid example.

### Added

- The full agent-file state-of-the-art research report (building-blocks table,
  phrasing rules, determinism ledger, prioritized recommendations, source
  evaluations) is persisted as a comment on epic `basicly-84v` in the tracker.

## v0.1.0 - 2026-07-15

Delta: initial..v0.1.0

### Highlights

- **One-command lifecycle**: `basicly install` performs first install *and* every
  upgrade (idempotent converge: managed core sync with provenance guards,
  overlay + `basicly.toml` scaffolding that never overwrites user content, then
  fragment/skill/hook projection with git-hook activation). `basicly uninstall
  [--purge]` is the inverse. Install also initializes a beads (`br`) tracker
  workspace with a repo-derived prefix, scaffolds VS Code tasks
  (build/skills-build/hooks-build/update/uninstall) and a consumer CI gates
  workflow (`.github/workflows/basicly-gates.yml`).
- **Complete harness loop**: `basicly loop` drives tracked issues through
  intake → classify → build → verify → ship with engine-enforced human
  checkpoints, isolated sibling git worktrees per track, a serial merge queue,
  and a bounded rework policy — all state lives in the `br` tracker.
- **Deterministic gates, consumer-appropriate**: the shipped pre-commit/pre-push
  hooks run whatever `[[verify.checks]]` each repo configures (fast at commit,
  full at push) instead of a hard-coded stack; commit messages are gated on
  Conventional Commits + a tracked beads issue id; `catalog-lint` and
  markdownlint round out the local + CI floor. A repo with no checks configured
  is never blocked by tooling it lacks.
- **Curated catalog**: 26 skills, 17 always-on/scoped fragments, and the hook
  set project from YAML sources into each agent's native format — `CLAUDE.md` +
  `.claude/rules`, `AGENTS.md` (Codex, verified against July 2026 capabilities),
  `copilot-instructions.md` + `.github/instructions`, and skills into
  `.claude/skills` + `.agents/skills` (the `.github/skills` copy was dropped:
  Copilot reads all roots, so it only tripled discovery).
- **Customization without forking**: consumer overlays add or override
  (`override: true` + `replaces`) any core fragment from
  `.basicly-local/fragments/user/`; upgrades keep them byte-for-byte.
- **Validated end-to-end** in the `terminal` repo (first real consumer):
  install → customize → upgrade → uninstall/reinstall round-trip → a full
  harness-loop track, with every defect found during the run fixed in this
  release.

### Changed

- **BREAKING (CLI):** `basicly install` replaces `init` and `update` — one
  idempotent converge command performs first install *and* every upgrade
  (materialize the bundled catalog, scaffold overlay + `basicly.toml` without
  overwriting user content, then `build` + `skills-build` + `hooks-build` with
  hook activation). The legacy-layout migration and legacy-source pruning that
  `update` performed now run inside `install`.
- Upgrades really sync the managed core now: a repeat `install` overwrites core
  files changed upstream, deletes files the bundle no longer ships, and — using
  the provenance snapshot — keeps hand-edited core files with a warning
  (`--force` overwrites them); files of unknown origin are never deleted. The
  overlay and `basicly.toml` are untouched. `hooks-build` no longer copies hook
  scripts (core content is owned by `install`) and errors when the core was
  never materialized.
- **BREAKING (catalog source format):** catalog content is now authored as YAML
  sources — skills as `core/skills/<slug>/skill.yaml` and fragments as
  `core/fragments/**/<id>.fragment.yaml` — instead of the discoverable `SKILL.md`
  and `*.fragment.md` names. The projectors render the agent-loaded `.md` files
  (`SKILL.md`, `CLAUDE.md`, `AGENTS.md`, `copilot-instructions.md`, rules and
  instructions) at the target roots only, so a broadly-scanning agent can no
  longer double-load a skill. Rendered output is unchanged except for a
  "generated" marker on projected `SKILL.md` files.

### Added

- `basicly uninstall [--purge]`: removes everything basicly manages (core,
  state, manifest-listed generated files, projected skills carrying the
  generated marker, and the managed pre-commit block — deleting the config and
  uninstalling the git hooks when nothing else remains). The overlay and
  `basicly.toml` survive unless `--purge`; the authoring repo refuses.
- Install provenance: `basicly install` writes `.basicly/state/install.json`
  (basicly version, timestamp, per-file sha256 snapshot of the managed core as
  materialized), and `basicly check` reports hand-edited/removed core files and
  an installed-vs-current version mismatch as advisory notes. The authoring
  repo records no state.
- JSON Schemas for skill and fragment sources (`core/schemas/`), referenced from
  each source via a `# yaml-language-server` header for editor/agent validation.
- `catalog-authoring` skill and an always-on authoring fragment covering how to
  write and project catalog sources.
- `basicly skills-new` and `basicly fragment-new` scaffold commands.
- `basicly catalog-lint` gate (schema validation, no `.md`-named sources, single
  `.yaml` extension), wired as a pre-commit hook and a CI step.

### Migration

- `basicly install` prunes legacy discoverable-name sources (`SKILL.md`,
  `*.fragment.md`) from the managed core, so installing basicly over a
  pre-migration hand-copied catalog cleans up the old sources automatically. The
  user overlay (`.basicly-local/`) is never touched.

### Commit delta (auto-generated)

- docs(readme): add the pinned install command for the release (basicly-zrj.16) (8fec978)
- chore(beads): close the skills root-drop (basicly-sqn) (f02f4ed)
- feat(skills)!: drop the github-skills projection root to stop copilot triple discovery (basicly-sqn) (c7e2685)
- chore(beads): record sqn claim (basicly-sqn) (d1cb968)
- chore(beads): file the skills root-drop task and copilot dedup question (basicly-sqn) (e52f04a)
- chore(beads): close the terminal acceptance with the full run writeup (basicly-zrj.15) (b1c6b39)
- chore(beads): file the loop tracker-state race (basicly-djt) (4ca1e54)
- chore(beads): close the catalog-lint ladder fix (basicly-7o8) (e2087f6)
- fix(hooks): resolve the catalog-lint cli through a consumer-safe ladder (basicly-7o8) (37b72e6)
- chore(beads): record 7o8 claim (basicly-7o8) (6b7198a)
- chore(beads): file the consumer catalog-lint hook bug (basicly-7o8) (30b1616)
- chore(beads): close the legacy overlay warning fix (basicly-v1y) (dfbb868)
- fix(loader): warn loudly when legacy fragment-md sources are present (basicly-v1y) (20c0498)
- chore(beads): record v1y claim (basicly-v1y) (db83055)
- chore(beads): file the silent overlay legacy-md ignore bug (basicly-v1y) (760e427)
- chore(beads): close the legacy engine migration fix (basicly-u9o) (a5392a9)
- fix(cli): remove the legacy vendored engine dir during install migration (basicly-u9o) (245e758)
- chore(beads): record u9o claim (basicly-u9o) (7f7afff)
- chore(beads): file the legacy engine dir migration gap (basicly-u9o) (bb30672)
- chore(beads): close the consumer ci workflow scaffold (basicly-7kh) (df1f987)
- feat(cli): scaffold a consumer ci gates workflow on install (basicly-7kh) (0dc3e87)
- chore(beads): record 7kh claim (basicly-7kh) (5317d6d)
- chore(beads): file the agent-file sota adoption epic and children (basicly-84v) (59ae564)
- chore(beads): close the config-driven hooks fix (basicly-yp3) (29dba7e)
- fix(hooks)!: pre-commit and pre-push run configured verify checks not a hard-coded stack (basicly-yp3) (82e05d2)
- chore(beads): record yp3 claim (basicly-yp3) (9c1c6d7)
- chore(beads): file the config-driven hooks bug and consumer ci workflow feature (basicly-yp3) (46c3aeb)
- chore(beads): close the lockfile rename fix (basicly-cjb) (c82e3e2)
- fix(build): pin the npm package name so worktree installs stop renaming the lockfile (basicly-cjb) (48282b7)
- chore(beads): record cjb claim and dor rewrite (basicly-cjb) (5fdefcc)
- chore(beads): close the vscode tasks scaffold (basicly-0eo) (9fbdcfd)
- feat(cli): scaffold vscode tasks for the harness operations on install (basicly-0eo) (7681cbc)
- chore(beads): record 0eo claim (basicly-0eo) (80be717)
- chore(beads): record 0eo filing (basicly-0eo) (aa54225)
- chore(beads): close the install beads-init feature (basicly-em9) (7b99899)
- feat(cli): install initializes the beads workspace with a derived prefix (basicly-em9) (5af233b)
- chore(beads): record em9 filing and claim (basicly-em9) (73a67aa)
- chore(beads): close the codex reassessment (basicly-joj) (67ce2ba)
- docs(architecture): correct codex capabilities and set codex cap allowance (basicly-joj) (2d04834)
- chore(beads): record joj claim and verified codex research (basicly-joj) (c22583e)
- chore(beads): file the projector markdownlint cleanliness follow-up (basicly-gdi) (236aa2e)
- chore(beads): close the markdownlint gate wiring (basicly-4j0) (194cb53)
- chore(hooks): wire markdownlint-cli2 into pre-commit and ci (basicly-4j0) (6899497)
- chore(beads): record 4j0 claim and worktree binding (basicly-4j0) (f09ce5f)
- docs(architecture): unwrap line rendering as accidental plus-list (basicly-4j0) (1a54f36)
- chore(beads): file the worktree package-lock rename bug (basicly-cjb) (a12f893)
- chore(beads): close the obsolete copilot size-cap split issue (basicly-4ce) (107a42a)
- chore(beads): close the consumer robustness epic (basicly-zrj.13) (cb364ae)
- chore(beads): close the verify runner robustness fix (basicly-zrj.13.2) (0dd9e20)
- fix(verify): fail cleanly on unrunnable check commands and stop scaffolding python-only checks (basicly-zrj.13.2) (725c8b3)
- chore(beads): record zrj-13-2 claim scaffold decision and dor rewrite (basicly-zrj.13.2) (1456c69)
- chore(beads): close the beads hook workspace skip fix (basicly-zrj.13.1) (c7320e2)
- fix(hooks): skip beads id check cleanly when no workspace exists (basicly-zrj.13.1) (ea1f6e0)
- chore(beads): record zrj-13-1 claim and dor rewrite (basicly-zrj.13.1) (4df376f)
- chore(beads): close the worktree hook clobber fix (basicly-zrj.13.3) (2f72656)
- fix(worktree): reinstall base checkout hooks on teardown (basicly-zrj.13.3) (898952a)
- chore(beads): record zrj-13-3 claim and dor rewrite (basicly-zrj.13.3) (a991c87)
- chore(beads): close the pushed-ref install verification (basicly-zrj.14) (0da8e63)
- docs(architecture): record verified pushed-ref uvx install (basicly-zrj.14) (95384cd)
- chore(beads): record zrj-14 claim and worktree binding (basicly-zrj.14) (8d60b4f)
- chore(beads): prune orphaned duplicate issue and normalize tombstones (basicly-joj) (1f5ce62)
- chore(beads): recover the agents-md cap reassessment issue lost in reconcile (basicly-joj) (7141402)
- chore(beads): close the lifecycle epic and set the next pickup (basicly-zrj.12) (afed186)
- feat(cli): add basicly uninstall for clean removal (basicly-zrj.12.3) (e7ccc3e)
- chore(beads): record uninstall claim and dor rewrite (basicly-zrj.12.3) (34c4c18)
- feat(cli): provenance-guarded core upgrade sync in install (basicly-zrj.12.2) (ebe2f67)
- chore(beads): record core sync claim and dor rewrite (basicly-zrj.12.2) (b26c20f)
- feat(state): record install provenance and report drift in check (basicly-8fg) (f9ff97a)
- chore(beads): record 8fg claim and dor rewrite (basicly-8fg) (83d80ca)
- chore(beads): close the install task and file the worktree hook clobber bug (basicly-zrj.12.1) (c16ede2)
- feat(cli)!: replace init and update with one-command install (basicly-zrj.12.1) (9269575)
- chore(beads): record lifecycle claims and progress notes (basicly-zrj.12) (e773393)
- docs(architecture): redesign lifecycle around one-command install and uninstall (basicly-zrj.12.4) (943d499)
- chore: close fv6 and mark basicly-8fg as the next pickup (ca52c25)
- docs(catalog): resolve dependency-confirmation and test-command ambiguities (35f809f)
- chore: close the oversized-fragments issue (4856b92)
- docs(catalog): dedupe and reframe repeated always-on guidance (763d37c)
- chore: record lce progress and ship-decision note (b721559)
- docs(catalog): tighten oversized always-on fragments under the 8000-char cap (8243d7e)
- chore: close the semantic-review issue (acef9a5)
- feat(review): add advisory agent-assisted semantic review command (357b55f)
- chore: close the projection-unification issue (8f530a3)
- refactor: unify skills hooks and build onto a shared projection engine (8dfebc1)
- chore: close the catalog-verify issue (fb2b97f)
- feat(catalog): add catalog-verify content checks and build --verify (9a9eea7)
- chore: close the enforced-by lint issue (a137619)
- feat(catalog): add enforced-by field and enforcement-pointer lint (233419e)
- chore: close git-hook-gates umbrella and mark next task (9374ba5)
- chore: close the quality-gate verification rule issue (311cf32)
- docs: strengthen the quality-gate verification rule (cc17cdb)
- feat(catalog): prune legacy sources on basicly update (3398d41)
- docs: record the catalog yaml source migration (6ba2361)
- feat(catalog): add catalog-lint gate with pre-commit hook and ci (668b9b0)
- feat(catalog): add yaml source schemas authoring skill and scaffolds (bfa7fd9)
- feat(fragments): author fragments as yaml sources (20aa7cb)
- feat(skills): author skills as yaml sources rendered to target roots (1040009)
- chore: plan the catalog yaml source migration epic (54c924e)
- feat(loop): add agent-agnostic runner adapters (7c53d00)
- feat(loop): author projected harness-loop guidance (e357427)
- chore: plan the projected orchestration guidance session (bd3317f)
- feat(loop): wire the basicly loop cli (5c18f41)
- chore: record the resume pointer for the loop cli child (3d1f5cb)
- feat(loop): add the checkpoint-gated loop state machine (5b41a30)
- chore: plan the loop state machine session (63bc631)
- fix(ci): validate the full commit message in the commit-messages gate (bb172c5)
- feat(loop): add the classify step (0ec4158)
- chore: plan the classify-step session (5b5f3e9)
- feat(loop): add the resumable loop state model (0616b22)
- chore: plan the loop engine decompose-first session (e7a75a8)
- feat(decompose): add the feature decomposer and dependency graph builder (1138657)
- chore: record next-session plan for the decomposer (basicly-onb.4) (7279c41)
- chore: close the merge orchestrator feature (basicly-onb.5) (e6d2d88)
- feat(merge): add serial merge orchestrator for harness worktrees (4894974)
- chore: record next-session plan for the merge orchestrator (basicly-onb.5) (4010f4a)
- chore: close the gate policy engine feature (basicly-onb.3) (23feb87)
- feat(policy): add gate and checkpoint policy engine (221ddd6)
- chore: record next-session plan for the gate policy engine (basicly-onb.3) (4302ddc)
- chore: close the verify runner feature (basicly-onb.2) (8aa77e1)
- feat(verify): add config-driven verify runner with br gate reporting (273abd1)
- chore: record next-session plan for the verify runner (basicly-onb.2) (b401f87)
- chore: close the work-isolation feature and its tasks (basicly-onb.1) (da90df9)
- docs(skills): add agent-agnostic worktree-isolation skill (f0c285a)
- feat(worktree): add consent-gated claude bg-isolation setting (28a5dbd)
- test(worktree): cover provision command selection and base-untouched (8252551)
- chore: record next-session findings for the worktree isolation tasks (basicly-onb.1) (d809c57)
- chore: record worktree isolation task closures (basicly-onb.1) (b54109c)
- feat(worktree): add worktree cli subcommands and config (efdaa08)
- feat(worktree): add worktree cleanup and teardown (552e575)
- feat(worktree): add sibling worktree create and provision (74ff8e5)
- chore: set the committed project settings as the bg-isolation install target in the plan (basicly-onb.1.6) (0a6e175)
- chore: track the claude bg-isolation install step in the harness plan (basicly-onb.1.6) (cc1c76f)
- chore: plan the harness epic with feature and task tree (basicly-onb) (b0f2225)
- docs: specify the harness in architecture and fill tool-br skill gaps (basicly-43l) (f38c1c4)
- chore: add committed trusted-workstation claude permissions (basicly-oda) (caf01d2)
- feat: activate git hooks on hooks-build and flag uninstalled gates (basicly-ed2.3) (17df629)
- feat: ship identity-guard in the hooks manifest (basicly-ed2.2) (3a7267e)
- fix: accept dotted beads ids in commit-msg gate and align its skill (basicly-ed2.1) (0ffe253)
- feat: enforce replaces and override validation on fragment load (basicly-q49) (6514b01)
- docs: warn against hand-rolled bulk-create loops in tool-br skill (basicly-f3m) (a77cdec)
- feat: add dogfood-gate and verification-scope rules to quality gate (basicly-zrc) (6baf504)
- docs: align section 9 with implemented init and honest git install verification (basicly-zrj.11) (7e02fa4)
- fix: prefer source catalog over stale packaged copies and dedup the walker (basicly-zrj.10) (43f8d7b)
- fix: resolve one core root from config for init and hooks (basicly-zrj.8) (ef846bc)
- fix: quote hook script path in pre-commit entry string (basicly-zrj.9) (34bebb0)
- fix: compare and edit only managed hooks in pre-commit config (basicly-zrj.7) (b9ac894)
- docs: mark init and hooks projection implemented and close gates epic (basicly-zrj.3) (13a4833)
- feat: add hooks-build and hooks-check to install the gate hooks (basicly-lku, basicly-t51) (cb787dd)
- feat: add basicly init to scaffold a consumer repo (basicly-xwt) (a2737ca)
- chore: close the packaging epic after all children complete (basicly-zrj.1) (db3c816)
- docs: mark packaging resolved and document the uvx install flow (basicly-8u2) (d1cf4ec)
- feat: bundle core catalog into the package for init to materialize (basicly-juj) (e2d1623)
- build: enable packaging with hatchling backend (basicly-8a7) (251a810)
- build(deps): promote jinja2 to a runtime dependency (basicly-8if) (e2d59b5)
- chore: break down the initial release roadmap into beads epics and tasks (basicly-zrj) (72e5c96)
- feat: add generic git identity guard hook and per-host identity setup tooling (basicly-4on) (92f9efa)
- feat: exclude scoped fragments from baselines and refresh agent config catalog (basicly-0e9) (e3df46d)
- chore: pin beads prefix and ignore transient br artifacts and document gotchas (basicly-77f) (247a7bf)
- docs: close beads issues before the resolving commit not after (basicly-fcl) (3bcd369)
- chore: close basicly-9j9 (basicly-9j9) (08e5e6a)
- fix: sort imports in test-loader and test-skills for ruff i001 (basicly-9j9) (de95c83)
- chore: close basicly-akn (basicly-akn) (eccbc38)
- feat: harden commit-msg description rules and add self-improvement retro fragment (basicly-akn) (18040c3)
- chore: close basicly-1da (basicly-1da) (95f613b)
- fix: stop cli integration tests from mutating the real repo manifest (basicly-1da) (727af06)
- chore: close basicly-sr2 (basicly-sr2) (c9c7c40)
- fix: clarify description must be entirely lowercase in commit-msg hook and skill (basicly-sr2) (25f4e7b)
- feat: support conventional commits breaking-change marker and add commit skill (basicly-sr2) (404adab)
- feat: add basicly harness distribution engine and fragment catalog (basicly-7ph, basicly-idr) (0220a35)
