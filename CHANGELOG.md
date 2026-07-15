# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
