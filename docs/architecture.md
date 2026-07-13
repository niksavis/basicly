# basicly Architecture

> **This document is the single authoritative architecture reference for `basicly`.**
> There is no separate plan/design-notes folder — this file is kept current as the
> only source of truth for architecture decisions, and beads (`br`) issues are broken
> down directly from it (§10).
>
> **Status legend** used throughout: **[Implemented]** — exists in code today and is
> verified working · **[Partial]** — some of the mechanism exists, gaps noted inline ·
> **[Planned]** — designed, not yet built · **[Deferred]** — explicitly out of scope
> for now.
>
> **How to read this document**: each numbered Part opens with a short **Summary**
> you can scan alone to get the full picture, followed by **Details** you only need
> when implementing or debugging that part. Skip straight to the Part you need.

## 0) Idea

`basicly` is a **harness distribution system** for coding agents: a curated,
versioned catalog that a repository installs, customizes, and projects into the
native context files each coding agent actually reads. The catalog has two equally
first-class halves:

1. **Guidance** (suggestive, non-deterministic) — fragments and skills, Markdown a
   model reads and may or may not follow.
2. **Gates** (deterministic) — git hook scripts that mechanically block a bad
   commit/push regardless of whether the model read or followed the guidance.

Both halves must be available together for an agent to do its best job — guidance
without gating is easily ignored; gating without guidance gives the agent no context
for _why_ a check exists or how to satisfy it up front.

## 1) Goal

`basicly` succeeds when:

1. A repository can install the catalog, get working `AGENTS.md`/`CLAUDE.md`/
   `copilot-instructions.md` files, and never hand-edit them again.
2. A user can add or override guidance without forking the catalog, and a later
   `basicly update` never destroys that customization.
3. The three always-on files stay small, unambiguous, and free of restated linter
   rules — because that duplication measurably hurts agent task success (§3.1).
4. Changing "the security policy" (or any single concern) means editing exactly one
   fragment, and every affected output regenerates consistently.
5. Contradictions, duplicates, and ambiguity in the catalog are caught before they
   reach a generated file — deterministically where possible, by an agent reviewer
   where not.

## 2) Overview

Three roles, one repo can dogfood all of them at once (as this repo does today):

```text
  SOURCE OF TRUTH — human-edited, git-tracked
  ┌────────────────────────┐          ┌────────────────────────┐
  │ Catalog                │          │ User overlay           │
  │ fragments, skills,     │          │ .basicly-local/        │
  │ hooks (versioned)      │          │ additions & overrides  │
  └────────────┬───────────┘          └────────────┬───────────┘
               │ basicly update                    │ edited directly
               ▼ (writes core only)                │ by the consumer
  ┌────────────────────────┐                       │
  │ .basicly/core/         │                       │
  │ (managed, read-only)   │                       │
  └────────────┬───────────┘                       │
               └─────────────────┬─────────────────┘
                                 │ merge (add / override)
               ┌─────────────────┴─────────────────┐
               │                                   │
      GUIDANCE — suggestive               GATES — deterministic
      (fragments + skills)                (hooks)
               │                                   │
               ▼                                   ▼
  ┌────────────────────────┐          ┌────────────────────────┐
  │ Planner   select/sort  │          │ .pre-commit-config.yaml│
  │ Verify    (semantic:   │          │   -> .git/hooks        │
  │            planned)    │          │ consumer install:      │
  │ Renderers per target   │          │ §11 gap (not built)    │
  └────────────┬───────────┘          └────────────┬───────────┘
               ▼                                   ▼
  ┌────────────────────────┐          at commit / push time,
  │ AGENTS.md (codex:      │          block a bad change even
  │   scoped inlined)      │          if the guidance above
  │ .claude/CLAUDE.md      │          was never followed
  │ .github/copilot-*.md   │
  │ + scoped, path-gated:  │
  │   .claude/rules/*      │
  │   .github/instructions/│
  └────────────┬───────────┘
               ▼
      Coding agents & humans — read the generated files
      (read-only); the gates enforce no matter what
```

Everything a coding agent or human reads is **generated**. Everything a user edits is
a **fragment** (core, never touched directly, or overlay, always theirs). Nothing else
is in scope for the core engine today — see §11 for what is deliberately not built yet.

---

## 3) Guiding principles

**Summary**: point at enforcement instead of restating it; compose from fragments, not
templates; verify deterministically first and semantically second; never hand-edit
either the source or the generated files; extend only by addition or explicit
override; distribute the catalog as a pinned, versioned whole; keep every target
idiomatic from one tool-agnostic source; keep everything in plain git-tracked files.

### Details

**3.1 Context minimalism — point at enforcement, don't restate it.**
_LLM-generated context files that duplicate what a linter/hook already enforces
measurably hurt agent task success and inflate cost._ If a rule is mechanically
enforced (ruff, pyright, bandit, markdownlint, a commit-msg hook, pre-push tests), the
always-on file must reference the command that enforces it, not restate the rule in
prose. Prose is reserved for what a linter cannot check: judgment calls, escalation
policy, when to ask instead of guess. There is no mechanical check for this yet — an
`enforced_by` schema field and lint rule are tracked as a gap (§11.4); today it's
enforced by review discipline only.

**3.2 Composability over templates.** Generated files are never hand-templated blobs;
they are assembled from fragments — one fragment per policy/practice/decision —
selected, sorted, and rendered per target. **[Implemented]**: this is exactly how
[`loader.py`](../src/basicly/loader.py) and
[`planner.py`](../src/basicly/planner.py) work today.

**3.3 Two-layer verification, deterministic first.** Deterministic, scriptable checks
catch a large class of problems cheaply (duplicate ids, missing fields, unknown
categories). Semantic problems — contradiction, ambiguity that parses fine but reads
badly to a model — need a capable reader. Both layers run against the same merged
fragment set, deterministic always first. **[Partial]**: schema/duplicate-id
validation is implemented inside the normal load path
(`loader._validate_fragment`); duplicate-body, contradiction, ambiguity, and
scope-overlap checks, plus a standalone `verify` command, are **[Planned]** (§6, §11).

**3.4 Source of truth and generated files are each a one-way street.** Users edit
fragments (core or overlay) and never the generated files; `basicly build` regenerates,
`basicly check` catches manual edits. `basicly update` edits only the managed core
catalog and never the user's overlay — the mechanism, not just the convention,
guarantees this (§4.3).

**3.5 Addition and override, never silent replacement.** Consumers extend the catalog
by adding a new fragment id, or by overriding a core fragment with
`override: true` + `replaces: [...]`. There is no third mechanism — no silent
shadowing, no "last fragment wins." An unexplained conflict is always an error.

**3.6 Hermetic, curated, pinned distribution.** The catalog is versioned as a whole,
the same way `.pre-commit-config.yaml` pins a hook `rev:`. `basicly update` is the
only, explicit, reviewable action that moves a consumer to a newer catalog version.

**3.7 Idiomatic per-target projection from one authored source.** Fragment bodies stay
tool-agnostic; only the renderer/template layer knows each target's native activation
syntax (Claude's `paths:`, Copilot's `applyTo:`, filesystem conventions like
`.claude/skills/*/SKILL.md`).

**3.8 Everything lives in plain, git-tracked files.** No daemon, no hidden state, no
network calls at build time. `git diff`/`git blame` are the audit trail; `basicly
check` is the offline CI staleness gate.

---

## 4) Directory & distribution contract

**Summary**: engine code, managed core catalog, and user overlay are three separate
trees with three separate write-owners. Only `basicly build`/`update` write to
generated/core paths; only the user writes to the overlay.

### Details

| Tree                                                                                                               | Owner (who writes here)                       | Status                                                                          |
| ------------------------------------------------------------------------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------------- |
| `src/basicly/` — engine (loader, planner, CLI, renderers)                                                          | `basicly` maintainers, ships with the tool    | **[Implemented]** — moved from `.basicly/basicly/` to a normal `src` layout     |
| `.basicly/core/` — managed fragment + skill + hook + target + template catalog                                     | `basicly update` only                         | **[Implemented]** (`fragments/`, `skills/`, `hooks/`, `targets/`, `templates/`) |
| `.basicly-local/` — user overlay (path-configurable via `basicly.toml`)                                            | the consumer repo's users                     | **[Implemented]**                                                               |
| `basicly.toml` — path wiring                                                                                       | the consumer repo                             | **[Implemented]**                                                               |
| Generated artifacts (`AGENTS.md`, `.claude/CLAUDE.md`, `.github/copilot-instructions.md`, skill/scoped-rule files) | `basicly build` / `basicly skills-build` only | **[Implemented]**                                                               |
| `.basicly/generated-manifest.json`                                                                                 | `basicly build` only                          | **[Implemented]**                                                               |

#### 4.1 Engine

Lives at [`src/basicly/`](../src/basicly/): `cli.py`, `config.py`,
`loader.py`, `planner.py`, `schema.py`, `renderers/`, `skills.py`. It has no
import-time dependency on specific fragment content, only on the schema below.

This mirrors what a real consumer repo would look like after installing `basicly` via
`uvx` (not yet built, §9): the engine is normal installable package source, entirely
separate from `.basicly/`, which holds only catalog data a consumer repo would
actually have on disk. This repo dogfoods itself, so both trees coexist here, but
neither one depends on the other's location — `.basicly/` never contains engine code
and `src/basicly/` never contains catalog data.

#### 4.2 Managed core

```text
.basicly/
  core/
    fragments/{boundaries,commands,decisions,project,security,testing,tools}/*.fragment.md
    skills/<skill-name>/SKILL.md      # source format gap, see below
    hooks/{pre-commit,identity-guard,commit-msg,beads-commit-msg,pre-push}.py
    targets/{claude,copilot,codex}.yaml
    templates/{claude,copilot,codex}/*.j2
  generated-manifest.json
```

Confirmed current core catalog fragment categories on disk: `boundaries`, `commands`
(git-discipline), `decisions`, `project`, `security`, `testing` (test-discipline, a
path-scoped example), and `tools` (non-interactive-shell, tool-usage). The user overlay
(`.basicly-local/fragments/user/`) adds a `code-style` fragment as a real, dogfooded
example of repo-specific content (Python conventions, project scope/tooling facts) that
intentionally does not belong in the generic core catalog. The schema also recognizes
`code-style`, `design`, `hooks`, `skills`, `ci-cd` as valid categories with no core
fragments in them yet. **Important distinction**: category `hooks` labels a _fragment
that describes hook usage_ — it is not the mechanism that ships an actual hook script;
the actual scripts live in `core/hooks/` (below).

**Skills — [Implemented, with a known format gap]**: `core/skills/` is the catalog
location (moved from a sibling `.basicly/skills/`). The source format is still
Markdown+YAML-frontmatter (`SKILL.md`) — this is a known gap (§11): because some
coding agents auto-discover skills by scanning broadly for `SKILL.md` files, keeping
the catalog _source_ in that exact filename risks an agent finding both the catalog
copy and the projected copy and loading a skill twice. The decided fix is to author
skills in Python (or another structured, non-`SKILL.md`-named format) and project them
to `SKILL.md` only at the designated target roots — not yet executed.

**Hooks — [Implemented as a catalog location, projected and installed by
`hooks-build`]**: `core/hooks/` holds the actual git hook scripts (`pre-commit.py`,
`identity-guard.py`, `commit-msg.py`, `beads-commit-msg.py`, `pre-push.py`) as
first-class catalog artifacts — the deterministic, gating counterpart to
fragments/skills — described tool-agnostically in `core/hooks/hooks.yaml`.
(`identity-guard.py` blocks a commit whose git identity is unset or a hostname
fallback — a generic, no-personal-data gate; the `.scripts/setup_git_identity.py`
helper and the `tool-git` skill cover the per-host identity setup it guards.) This
repo dogfoods them directly: [`.pre-commit-config.yaml`](../.pre-commit-config.yaml)
points straight at `core/hooks/*.py`. `basicly hooks-build` projects the manifest
into a consumer's `.pre-commit-config.yaml` and then runs `pre-commit install` so the
gates are active — not merely written; a gate that is shipped but never installed is
inert, the exact gap that once let unguarded commits through (§8, §11.6).

#### 4.3 User overlay

```text
.basicly-local/
  fragments/user/         # addition + override fragments; e.g. code-style/python-style,
                          # project/project-defaults (repo-specific facts kept out of core)
```

Configurable via `basicly.toml`:

```toml
[paths]
core_fragments = ".basicly/core/fragments"
overlay_fragments = [".basicly-local/fragments"]
targets = ".basicly/core/targets"
templates = ".basicly/core/templates"
manifest = ".basicly/generated-manifest.json"
```

`basicly update` (§6.2) only ever writes under `paths.core_fragments`; it creates
`paths.overlay_fragments/.../user/` if missing but never writes fragment content there.

#### 4.4 Generated artifacts

```text
AGENTS.md                                    # applies_to: [all]; inlines scoped fragments (codex can't path-scope)
.claude/CLAUDE.md                            # applies_to: [all] + [claude]; scoped fragments excluded (exclude_scoped)
.claude/rules/*.md                           # path-scoped claude fragments, `paths:` frontmatter
.github/copilot-instructions.md              # applies_to: [all] + [copilot], inlined (no @-import); scoped excluded
.github/instructions/*.instructions.md       # path-scoped copilot fragments, `applyTo:` frontmatter
.claude/skills/*/SKILL.md                    # projected via `skills-build`
```

Which fragments land where is driven by each output's `filter` in `targets/*.yaml`:
`applies_to` selects by target, `has_scope: true` restricts an output to scoped
fragments (the `.claude/rules/` and `.github/instructions/` files), and
`exclude_scoped: true` drops scoped fragments from a baseline (the `CLAUDE.md` and
`copilot-instructions.md` wrappers) — see §7 detail 4. Codex gets the shared `AGENTS.md`
baseline only, with scoped fragments inlined because it has no path-scoping mechanism;
`.codex/rules/*.rules` is **[Deferred]** (§11.9).

---

## 5) Fragment model

**Summary**: one fragment = one Markdown file with YAML front matter = one
policy/practice/decision. Required fields: `id`, `description`, `category`,
`applies_to`. Extension fields (`source`, `override`, `replaces`, `extends`) exist with
safe defaults today.

### Details

Confirmed current schema ([`schema.py`](../src/basicly/schema.py)):

| Field         | Required | Values                                                                                                                               | Notes                                                 |
| ------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| `id`          | yes      | kebab-case, unique                                                                                                                   | duplicate id across core+overlay is a hard error      |
| `description` | yes      | one line                                                                                                                             |                                                       |
| `category`    | yes      | `boundaries`, `code-style`, `commands`, `decisions`, `design`, `hooks`, `project`, `security`, `skills`, `testing`, `tools`, `ci-cd` |                                                       |
| `applies_to`  | yes      | target names or `all`                                                                                                                |                                                       |
| `priority`    | no       | `critical`(4) `high`(3) `medium`(2, default) `low`(1)                                                                                | sorts descending                                      |
| `scope.paths` | no       | glob list, default `["**"]`                                                                                                          | non-default → scoped output                           |
| `status`      | no       | `active`(default) `draft` `deprecated`                                                                                               | only `active` is projected                            |
| `source`      | no       | `core`(default) `user`                                                                                                               | inferred from load root if omitted                    |
| `override`    | no       | bool, default `false`                                                                                                                | must be `true` to replace a core fragment             |
| `replaces`    | no       | list of fragment ids                                                                                                                 | core fragments removed when this fragment is active   |
| `extends`     | no       | list of fragment ids                                                                                                                 | documentation only, narrows future conflict detection |

**Extension mechanism — [Implemented]**: the planner
(`planner._apply_user_replacements`) removes core fragments listed in an active user
fragment's `replaces`, and the loader (`loader._validate_replacements`, run on every
`list`/`build`/`check`) enforces the integrity rules as hard errors: a fragment
declaring `replaces` must set `override: true`, every replaced id must exist in the
merged fragment set, and two user fragments may not replace each other.

Sorting is deterministic: priority (desc) → category (asc) → id (asc). Two `build`
runs on identical source produce byte-identical output.

---

## 6) Verification pipeline

**Summary**: schema/duplicate-id validation already runs on every load. Duplicate-body,
contradiction, ambiguity, scope-overlap checks, a standalone `verify` command, and
agent-assisted semantic review are designed but not built.

### Details

| Check                                                                                      | Status                                                                                |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| Required fields, known category/priority/status/target, extension-field types              | **[Implemented]** — `loader._validate_fragment`, runs on every `list`/`build`/`check` |
| Duplicate fragment `id` across core + overlay roots                                        | **[Implemented]** — `loader.load_fragments_from_roots`                                |
| `replaces` target exists / `override: true` required / no mutual user-user replaces        | **[Implemented]** — `loader._validate_replacements`, runs on every `list`/`build`/`check` |
| Duplicate/near-duplicate fragment bodies                                                   | **[Planned]**                                                                         |
| Contradiction detection (static dictionary: tabs/spaces, pathlib/os.path, etc.)            | **[Planned]**                                                                         |
| Ambiguity detection (deny-list of vague phrases)                                           | **[Planned]**                                                                         |
| Scope-overlap detection                                                                    | **[Planned]**                                                                         |
| Enforcement-pointer check (`enforced_by` field, §3.1)                                      | **[Planned]** — field doesn't exist in the schema yet                                 |
| Standalone `basicly verify` / `basicly build --verify` commands                            | **[Planned]**                                                                         |
| Semantic review (`basicly review`, agent reads rendered diff for contradictions/ambiguity) | **[Planned]** — advisory, not a merge gate, once built                                |

When built, both layers run in this order — deterministic gate first, always; semantic
review second, advisory, on demand or in CI as a report (not a blocker).

---

## 7) The three always-on files

**Summary**: `AGENTS.md`, `CLAUDE.md`, `copilot-instructions.md` are the foundation
every other artifact builds on. If they're noisy or ambiguous, everything downstream
inherits that failure.

### Details

1. **Size discipline**: a single **unified soft cap of 8,000 chars** across all three
   targets (`max_size_warning` in every `targets/*.yaml`). One cap, not per-target caps,
   because all three always-on files project from the same `applies_to: [all]` fragment
   set — they are ~95% identical, differing only by a small per-target defaults fragment
   (~180–300 chars each). You cannot shrink one projection without cutting the shared
   source, so divergent caps are incoherent. The number is a deliberate discipline choice,
   not a platform limit: Claude Code's own degradation warning is ~40 KB, and GitHub
   removed its former 4,000-char hard limit on `copilot-instructions.md` (it now only
   advises shortening files past ~4,000 chars). 8,000 sits comfortably above the shared
   body and well under the strictest real threshold, leaving room for a few high-value
   additions while still forcing §3.1 minimalism. A cap warning means split into a scoped
   rule, not shrink the prose. (Refs: GitHub removed the hard limit — github/docs#42761;
   Claude ~40 KB — Claude Code memory docs.)
2. **Enforced vs. judgment split**: enforced rules are one line pointing at the
   command/config; judgment rules are prose, and should be the shorter of the two
   sections.
3. **No duplication across always-on files**: `applies_to: [all]` fragments feed
   `AGENTS.md` and are inlined into `copilot-instructions.md` (Copilot cannot
   `@`-import `AGENTS.md`). Target-specific fragments add only genuinely different
   content.
4. **Scoped fragments stay out of the always-on baseline** (Claude & Copilot): a
   fragment with a non-default `scope.paths` is projected only to its path-gated file
   (`.claude/rules/*.md` via `paths:`, `.github/instructions/*.instructions.md` via
   `applyTo:`) — both are real, auto-activating features of those tools — and is **not**
   inlined into `CLAUDE.md`/`copilot-instructions.md`. This keeps the always-on file lean
   (a Python-only rule shouldn't cost every task its context budget) and is enforced by
   the `exclude_scoped: true` output filter (§4.4). **Exception — `AGENTS.md` (codex)**:
   Codex has no path-scoping mechanism, so scoped fragments are inlined there to avoid
   dropping them; this is why `AGENTS.md` runs larger than the other two baselines.
5. **Self-contained per target**: each generated file stands alone; an agent should
   never need a second file to understand the baseline.
6. **Stable ordering**: priority → category → id, so diffs stay minimal.

---

## 8) CLI surface

**Summary**: `list`, `update`, `build` (+ `--target`), `check`, `skills-list`,
`skills-build`, `skills-check` exist today. `verify`, `conflicts`, `overrides`,
`review`, `init` are designed, not built.

### Details

| Command                                                                                   | Status            | Behavior                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `basicly list`                                                                            | **[Implemented]** | Table of active fragments                                                                                                                                                                      |
| `basicly update`                                                                          | **[Implemented]** | Refreshes managed core layout only; migrates legacy `.basicly/fragments/` → `.basicly/core/fragments/` + `.basicly-local/fragments/user/` on first run; never touches existing overlay content |
| `basicly build [--target NAME]`                                                           | **[Implemented]** | Renders enabled targets (or one), writes only changed bytes, updates the manifest, warns on size-cap overrun                                                                                   |
| `basicly check`                                                                           | **[Implemented]** | Byte-for-byte staleness check of generated files + manifest; exit `1` on mismatch, no auto-fix                                                                                                 |
| `basicly skills-list` / `skills-build [--root ...\|--all-default-roots]` / `skills-check` | **[Implemented]** | Same build/check contract, applied to the skill catalog                                                                                                                                        |
| `basicly init`                                                                            | **[Implemented]** | Materializes the bundled core catalog into `.basicly/core/`, scaffolds `.basicly-local/fragments/user/` + `basicly.toml`; idempotent, never overwrites existing files                          |
| `basicly hooks-build [--no-install]` / `hooks-check`                                       | **[Implemented]** | Materializes catalog hook scripts, merges a managed `repo: local` block into `.pre-commit-config.yaml` (foreign hooks preserved, idempotent), and then runs `pre-commit install` for every managed stage so the gates are actually active (`--no-install` skips activation; graceful when pre-commit is absent). `hooks-check` reports projection drift and warns (non-fatal) when the git hooks are not installed |
| `basicly verify`                                                                          | **[Planned]**     | Deterministic gate as a standalone command (§6)                                                                                                                                                |
| `basicly build --verify`                                                                  | **[Planned]**     | Runs verify first; no files written on failure                                                                                                                                                 |
| `basicly conflicts` / `basicly overrides`                                                 | **[Planned]**     | Reporting views over verify output                                                                                                                                                             |
| `basicly review`                                                                          | **[Planned]**     | Agent-assisted semantic review (§6)                                                                                                                                                            |

---

## 9) Distribution mechanics

**Summary**: `uvx` install works — the package builds, the `basicly` entry point
resolves from a fresh install, the core catalog ships inside the distribution, and
`basicly init` scaffolds a consumer repo (§8). The `curl` bootstrap is the remaining
distribution gap. Caveat: the full flow is verified from a locally built wheel; the
live `git+<remote>@<ref>` path is validated structurally (the sdist carries the
catalog) but has not been exercised against a pushed ref yet.

### Details

- **[Implemented]** `pyproject.toml` declares a `[build-system]` table (hatchling),
  `tool.uv.package = true`, and a `[project.scripts]` `basicly = "basicly.cli:main"`
  entry point. `uv build` produces a wheel + sdist; `uvx --from <wheel> basicly`
  resolves `basicly.cli` (verified). The equivalent `git+https://...@<ref>` form is
  expected to work via the sdist but is unverified until the repo is pushed.
  `jinja2` is a `[project.dependencies]` runtime dep (§11.2).
- **[Implemented]** The managed core catalog ships inside the distribution: hatchling
  `force-include` projects the dogfooded source `.basicly/core/` to `basicly/catalog/`
  in the wheel, and the sdist carries `.basicly/core/` so `git+` installs resolve it.
  `basicly.catalog.bundled_catalog_root()` prefers a source checkout (marker walk) and
  falls back to the packaged copy in installed wheels. Verified end-to-end from a
  clean dir: `init` → `build` → `skills-build` → `hooks-build`, all checks passing.
- **[Implemented]** The primary consumer surface is `uvx --from
git+https://github.com/<org>/basicly@<ref> basicly init` (§8): it materializes the
  bundled catalog and scaffolds `basicly.toml` + the overlay. A `curl` bootstrap shim
  for consumers without `uv`/Python is still **[Planned]** (§11.8).
- Catalog selection ("install fragments + hooks but not skills") and version/provenance
  tracking (`.basicly/state/install.json`) are **[Planned]**, not built (§11.5, §11.8).

---

## 10) Development workflow for this repo

**Summary**: this repo tracks its own implementation work with `br` (beads), not a
separate issue tracker. Every commit must reference a tracked issue id — enforced by a
git hook, not just convention.

### Details

- Workspace: `.beads/`, prefix `basicly`, defaults `priority: 2` (Medium),
  `type: task`. Full taxonomy, priority scale, and hierarchy convention (`--parent`,
  since `br` has no separate story/sub-task type) are documented once, in
  [`.beads/config.yaml`](../.beads/config.yaml) and the
  [`tool-br` skill](../.basicly/core/skills/tool-br/SKILL.md) — not restated here, per
  §3.1.
- Enforcement: [`commit-msg.py`](../.basicly/core/hooks/commit-msg.py)
  (conventional-commit format, permits a trailing issue-id parenthetical) and
  [`beads-commit-msg.py`](../.basicly/core/hooks/beads-commit-msg.py)
  (requires the referenced id to exist in `.beads/issues.jsonl`) both run at the
  `commit-msg` git stage, wired independently in
  [`.pre-commit-config.yaml`](../.pre-commit-config.yaml).
- These hooks are both this repo's own dev-process tooling **and** the literal
  catalog source (§4.2) — dogfooding is direct, not a copy.
- Practical implication for planning work as beads issues: use `epic` for large
  initiatives (e.g. "make basicly uvx-installable"), `feature`/`task` for the
  gaps in §11, `bug` for regressions, and `--parent` to link a `task` under a
  `feature`/`epic` instead of inventing a "story"/"sub-task" type.

---

## 11) Known gaps and roadmap

Ordered roughly by blocking-ness. Each is a candidate beads epic/feature/task.

1. **Packaging** — **[Resolved]** (`basicly-8a7`, `basicly-juj`): added a
   `[build-system]` table (hatchling), flipped `tool.uv.package = true`, and bundled the
   core catalog into the distribution (`force-include` → `basicly/catalog/`). `uvx`
   installation now resolves `basicly.cli` from a built wheel and from `git+` (§9).
2. **`jinja2` runtime dependency** — **[Resolved]** (`basicly-8if`): moved from the dev
   group to `[project.dependencies]` alongside `pyyaml`.
3. **Override validation** — **[Resolved]** (`basicly-q49`): `loader._validate_replacements`
   enforces `replaces` target existence, the `override: true` requirement, and
   user-user mutual-replace rejection as hard errors on every load (§5, §6).
4. **`enforced_by` schema field and the enforcement-pointer check don't exist yet**;
   until then, §3.1 is a principle without a mechanical check.
5. **Deterministic `verify` beyond schema/duplicate-id, and `basicly review`, are
   unbuilt** (§6).
6. **`hooks-build`/`hooks-check` projection** — **[Resolved]** (`basicly-lku`,
   `basicly-t51`): a tool-agnostic `core/hooks/hooks.yaml` manifest drives
   `basicly hooks-build`, which materializes the scripts and merges a managed
   `repo: local` block into a consumer's `.pre-commit-config.yaml` (foreign hooks
   preserved, idempotent); `hooks-check` reports drift.
7. **Skill source format is still Markdown (`SKILL.md`), not Python.** Decided (§4.2)
   but not executed: converting `core/skills/*/SKILL.md` to a structured Python source
   that projects to `SKILL.md` is a schema + loader + projector change across the
   skills subsystem, not yet started.
8. **`curl` bootstrap script, catalog selection/flavors, `.basicly/state/`
   provenance tracking, and `.codex/rules/*.rules` scoped rules** are all
   **[Planned]**/**[Deferred]** with no code yet.
9. **Cursor as a target** is **[Deferred]**; no renderer, no templates.

## 12) References

- pre-commit: <https://pre-commit.com/>
- Trunk Code Quality: <https://docs.trunk.io/code-quality/overview>
- MegaLinter: <https://github.com/oxsecurity/megalinter>
- Claude Agent SDK: <https://code.claude.com/docs/en/agent-sdk/overview>
- OpenAI SDKs and CLI: <https://developers.openai.com/api/docs/libraries>
- Cursor SDK: <https://cursor.com/blog/typescript-sdk>
- Pydantic AI: <https://pydantic.dev/docs/ai/overview/>
- Fowler series (context priming, design-first, context anchoring): <https://martinfowler.com/articles/reduce-friction-ai/>
