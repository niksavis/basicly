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
   `basicly install` (upgrade, §9) never destroys that customization.
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
               │ basicly install                   │ edited directly
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
  │            advisory)   │          │ installed by basicly   │
  │ Renderers per target   │          │ install / hooks-build  │
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
policy, when to ask instead of guess. **[Implemented]** (`basicly-a8e`): the
`enforced_by` schema field lists the commands that enforce a rule, and
`catalog_lint` requires each listed command to be cited in the fragment body —
a fragment that claims enforcement must point at the command, not restate the rule.

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
scope-overlap checks, plus the standalone `basicly catalog-verify` command, are
**[Implemented]** (`basicly-ihs`, `catalog_verify.py`). Agent-assisted semantic
review (`basicly review`) is **[Implemented]** (`basicly-qps`, `review.py`):
advisory, never a merge gate (§6, §11).

**3.4 Source of truth and generated files are each a one-way street.** Users edit
fragments (core or overlay) and never the generated files; `basicly build` regenerates,
`basicly check` catches manual edits. `basicly install` edits only the managed core
catalog and never the user's overlay — the mechanism, not just the convention,
guarantees this (§4.3).

**3.5 Addition and override, never silent replacement.** Consumers extend the catalog
by adding a new fragment id, or by overriding a core fragment with
`override: true` + `replaces: [...]`. There is no third mechanism — no silent
shadowing, no "last fragment wins." An unexplained conflict is always an error.

**3.6 Hermetic, curated, pinned distribution.** The catalog is versioned as a whole,
the same way `.pre-commit-config.yaml` pins a hook `rev:`. Re-running `basicly
install` from a newer pinned ref is the only, explicit, reviewable action that moves
a consumer to a newer catalog version (§9).

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
trees with three separate write-owners. Only `basicly build`/`install` write to
generated/core paths; only the user writes to the overlay.

### Details

| Tree                                                                                                               | Owner (who writes here)                       | Status                                                                          |
| ------------------------------------------------------------------------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------------- |
| `src/basicly/` — engine (loader, planner, CLI, renderers)                                                          | `basicly` maintainers, ships with the tool    | **[Implemented]** — moved from `.basicly/basicly/` to a normal `src` layout     |
| `.basicly/core/` — managed fragment + skill + hook + target + template catalog                                     | `basicly install` only                        | **[Implemented]** (`fragments/`, `skills/`, `hooks/`, `targets/`, `templates/`) |
| `.basicly/state/install.json` — install provenance (version, source ref, catalog hashes)                          | `basicly install` only                        | **[Planned]** (`basicly-8fg`)                                                   |
| `.basicly-local/` — user overlay (path-configurable via `basicly.toml`)                                            | the consumer repo's users                     | **[Implemented]**                                                               |
| `basicly.toml` — path wiring                                                                                       | the consumer repo                             | **[Implemented]**                                                               |
| Generated artifacts (`AGENTS.md`, `.claude/CLAUDE.md`, `.github/copilot-instructions.md`, skill/scoped-rule files) | `basicly build` / `basicly skills-build` only | **[Implemented]**                                                               |
| `.basicly/generated-manifest.json`                                                                                 | `basicly build` only                          | **[Implemented]**                                                               |

#### 4.1 Engine

Lives at [`src/basicly/`](../src/basicly/): `cli.py`, `config.py`,
`loader.py`, `planner.py`, `schema.py`, `renderers/`, `skills.py`. It has no
import-time dependency on specific fragment content, only on the schema below.

This mirrors what a real consumer repo would look like after installing `basicly` via
`uvx` (§9): the engine is normal installable package source, entirely
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

**Skills — [Implemented]**: `core/skills/` is the catalog location (moved from a sibling
`.basicly/skills/`). Sources are authored as `skill.yaml` (name, description, and an
`instructions` block scalar), **not** the discoverable `SKILL.md` name: because some coding
agents auto-discover skills by scanning broadly for `SKILL.md`, a `SKILL.md` _source_ would
risk an agent loading both the catalog copy and the projected copy twice. `skills-build`
renders the discoverable `SKILL.md` at the target roots only, with a generated marker.
Fragments follow the same rule (`<id>.fragment.yaml` → projected `.md`), YAML is the single
catalog source format (targets and hooks were already YAML), and `basicly catalog-lint`
enforces all of this (schema validity, no `.md`-named sources, no `.yml`). The chosen format
is YAML rather than Python — it needs no code execution, keeps prose lossless via block
scalars, and matches the existing catalog conventions.

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

`basicly install` only ever writes under the managed core and state paths; it creates
`paths.overlay_fragments/.../user/` if missing but never writes fragment content
there, and never overwrites an existing `basicly.toml`.

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
`.codex/rules/*.rules` is **[Deferred]** (§11.11).

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

**Summary**: schema/duplicate-id validation runs on every load; the deterministic
content checks (duplicate-body, contradiction, ambiguity, scope-overlap) and the
standalone `basicly catalog-verify` command (also wired as `basicly build --verify`)
are built, as is the advisory agent-assisted semantic review (`basicly review`).

### Details

| Check                                                                                      | Status                                                                                |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| Required fields, known category/priority/status/target, extension-field types              | **[Implemented]** — `loader._validate_fragment`, runs on every `list`/`build`/`check` |
| Duplicate fragment `id` across core + overlay roots                                        | **[Implemented]** — `loader.load_fragments_from_roots`                                |
| `replaces` target exists / `override: true` required / no mutual user-user replaces        | **[Implemented]** — `loader._validate_replacements`, runs on every `list`/`build`/`check` |
| Duplicate/near-duplicate fragment bodies                                                   | **[Implemented]** (`basicly-ihs`) — `catalog_verify._duplicate_bodies` (difflib ratio) |
| Contradiction detection (static dictionary: tabs/spaces, pathlib/os.path, etc.)            | **[Implemented]** (`basicly-ihs`) — `catalog_verify._contradictions`, curated pairs   |
| Ambiguity detection (deny-list of vague phrases)                                           | **[Implemented]** (`basicly-ihs`) — `catalog_verify._ambiguous_phrases`               |
| Scope-overlap detection                                                                    | **[Implemented]** (`basicly-ihs`) — `catalog_verify._scope_overlaps`, scoped pairs    |
| Enforcement-pointer check (`enforced_by` field, §3.1)                                      | **[Implemented]** (`basicly-a8e`) — `catalog_lint` requires each `enforced_by` command to be cited in the body |
| Standalone `basicly catalog-verify` / `basicly build --verify` commands                    | **[Implemented]** (`basicly-ihs`) — named `catalog-verify` because `basicly verify` is the loop CI-check runner; `build --verify` gates the write |
| Semantic review (`basicly review`, agent reads rendered files for contradictions/ambiguity) | **[Implemented]** (`basicly-qps`) — `review.py` builds the prompt, dispatches via the agent-agnostic runner, always exits 0 (advisory, not a merge gate) |

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

**Summary**: the catalog surface (`list`, `build`, `check`, `skills-*`,
`fragment-new`, `skills-new`, `catalog-lint`, `catalog-verify`, `review`,
`hooks-build`/`hooks-check`) and the harness surface (`worktree`, `verify`,
`policy`, `decompose`, `loop`, `runner`) are implemented, as is `basicly install`
(which replaced the `init`/`update` staging pair). `uninstall` and the core
upgrade sync inside `install` are the remaining planned lifecycle pieces
(`basicly-zrj.12`).

### Details

**Lifecycle** — one command installs *and* upgrades; a second removes:

| Command                                       | Status                            | Behavior                                                                                                                                                                                                                                                        |
| --------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `basicly install`                             | **[Implemented]** (`basicly-zrj.12.1`) | Idempotent converge: materialize the bundled core catalog, migrate/prune legacy layouts, scaffold overlay + `basicly.toml` (never overwriting existing user content), then `build` + `skills-build` (all default roots) + `hooks-build` (with hook activation). The same command performs first install and every upgrade. Replaced the former `init`/`update` staging pair. Upgrade caveat: core sync is still materialize-missing until `basicly-zrj.12.2` lands (§9 upgrade semantics) |
| `basicly uninstall [--purge]`                 | **[Planned]** (`basicly-zrj.12.3`) | Removes managed core, state, generated artifacts, projected skills, and the managed hook block; preserves the overlay + `basicly.toml` unless `--purge`                                                                                                          |

**Catalog**:

| Command                                                                                   | Status            | Behavior                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `basicly list`                                                                            | **[Implemented]** | Table of active fragments                                                                                                                                                                      |
| `basicly build [--target NAME] [--verify]`                                               | **[Implemented]** | Renders enabled targets (or one), writes only changed bytes, updates the manifest, warns on size-cap overrun; `--verify` runs `catalog-verify` first and writes nothing on failure             |
| `basicly check`                                                                           | **[Implemented]** | Byte-for-byte staleness check of generated files + manifest; exit `1` on mismatch, no auto-fix                                                                                                 |
| `basicly skills-list` / `skills-build [--root ...\|--all-default-roots]` / `skills-check` | **[Implemented]** | Same build/check contract, applied to the skill catalog                                                                                                                                        |
| `basicly skills-new` / `basicly fragment-new`                                            | **[Implemented]** | Scaffold a new `skill.yaml` / `<id>.fragment.yaml` source (§4.2 source format)                                                                                                                 |
| `basicly catalog-lint`                                                                    | **[Implemented]** | Source-format gate: schema validation, no `.md`-named sources, single `.yaml` extension; wired as a pre-commit hook and CI step                                                                |
| `basicly catalog-verify`                                                                  | **[Implemented]** (`basicly-ihs`) | Deterministic content checks beyond the load-path validation: duplicate bodies, contradictions, ambiguity, scope overlaps (§6); named `catalog-verify` because `basicly verify` is the loop check runner |
| `basicly hooks-build [--no-install]` / `hooks-check`                                       | **[Implemented]** | Materializes catalog hook scripts, merges a managed `repo: local` block into `.pre-commit-config.yaml` (foreign hooks preserved, idempotent), and then runs `pre-commit install` for every managed stage so the gates are actually active (`--no-install` skips activation; graceful when pre-commit is absent). `hooks-check` reports projection drift and warns (non-fatal) when the git hooks are not installed |
| `basicly review [--runner NAME] [--dry-run]`                                              | **[Implemented]** (`basicly-qps`) | Advisory agent-assisted semantic review: renders the always-on files, dispatches a review prompt via the agent-agnostic runner (handoff when no CLI is on PATH), always exits 0. `--dry-run` prints the prompt without invoking an agent (§6) |

**Harness** (§12):

| Command                                       | Status            | Behavior                                                                                                                              |
| --------------------------------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `basicly worktree ...`                        | **[Implemented]** | Sibling git-worktree lifecycle: create + provision (deps, hooks), list, cleanup (§12.5)                                                 |
| `basicly verify [--gate]`                     | **[Implemented]** | Runs the consumer's `[[verify.checks]]` from `basicly.toml` per mode and optionally records a `br` gate (§12.3–12.4)                    |
| `basicly policy ...`                          | **[Implemented]** | DoR, gate, rework, and checkpoint policy checks; `policy checkpoint <issue> <name> --approve` records the human checkpoints (§12.2)     |
| `basicly decompose`                           | **[Implemented]** | Turns a feature into child `br` issues + a computed dependency graph (§12.2)                                                            |
| `basicly loop status\|advance\|run <issue>`   | **[Implemented]** | Drives an issue through the harness loop; a blocked step exits non-zero and names the input it needs (§12.2)                            |
| `basicly runner list\|dry-run\|run`           | **[Implemented]** | Agent-agnostic headless runner adapters (claude/codex/copilot + `manual` handoff); auto-dispatch from the loop build phase is **[Partial]** (`basicly-7ca`) (§12.8) |

The formerly planned `basicly conflicts`/`basicly overrides` reporting views are
**[Deferred]** — cut from scope; `catalog-verify` output covers the reporting need.

---

## 9) Distribution mechanics

**Summary**: the consumer lifecycle is **one command for install and every upgrade**,
plus one for removal:

```sh
uvx --from git+https://github.com/niksavis/basicly@<ref> basicly install    # first time AND upgrades
uvx --from git+https://github.com/niksavis/basicly@<ref> basicly uninstall  # removal
```

Packaging and the bundled catalog are **[Implemented]** (verified from a locally
built wheel); the unified `install`/`uninstall` commands, core upgrade sync, and
provenance tracking are **[Planned]** (`basicly-zrj.12`). Caveat: the live
`git+<remote>@<ref>` path is validated structurally (the sdist carries the catalog)
but has not been exercised against a pushed ref yet (`basicly-zrj.14`).

### Details

- **[Implemented]** `pyproject.toml` declares a `[build-system]` table (hatchling),
  `tool.uv.package = true`, and a `[project.scripts]` `basicly = "basicly.cli:main"`
  entry point. `uv build` produces a wheel + sdist; `uvx --from <wheel> basicly`
  resolves `basicly.cli` (verified). The equivalent `git+https://...@<ref>` form is
  expected to work via the sdist but is unverified until exercised against a pushed
  ref (`basicly-zrj.14`). `jinja2` is a `[project.dependencies]` runtime dep (§11.2).
- **[Implemented]** The managed core catalog ships inside the distribution: hatchling
  `force-include` projects the dogfooded source `.basicly/core/` to `basicly/catalog/`
  in the wheel, and the sdist carries `.basicly/core/` so `git+` installs resolve it.
  `basicly.catalog.bundled_catalog_root()` prefers a source checkout (marker walk) and
  falls back to the packaged copy in installed wheels. Verified end-to-end from a
  clean dir: `init` → `build` → `skills-build` → `hooks-build`, all checks passing.
- **[Implemented]** (`basicly-zrj.12.1`) **`basicly install` — one idempotent
  converge command** replacing the former `init` → `build` → `skills-build` →
  `hooks-build` staging and the separate `update` (both removed pre-release).
  Design finding (2026-07-15): `init` was never a technical prerequisite —
  everything it does is idempotent skip-existing — so a single command serves
  first install and every upgrade. Its converge contract: materialize or sync
  the bundled core (below), migrate/prune legacy layouts, scaffold the overlay +
  `basicly.toml` only if missing, keep the authoring-repo guard (bundled source
  == destination → leave in place), then rebuild all artifacts and install the
  hooks.
- **[Planned]** (`basicly-zrj.12.2`, `basicly-8fg`) **Core upgrade sync +
  provenance.** On a repeat `install` from a newer ref, the managed core is synced to
  the bundled catalog: changed files overwritten, upstream-removed files deleted, the
  overlay and `basicly.toml` never touched. `.basicly/state/install.json` records the
  installed version, source ref, timestamp, and per-file catalog hashes so `install`
  can distinguish upstream changes from user hand-edits of core files (warn + skip by
  default, `--force` to overwrite) and `check` can report drift. Upgrading is
  therefore literally re-running the same pinned `uvx ... basicly install` command
  with a newer `@<ref>` (§3.6).
- **[Planned]** (`basicly-zrj.12.3`) **`basicly uninstall`** removes everything
  managed — core, state, manifest-listed generated files, projected skills, the
  managed hook block — and preserves the user's overlay + `basicly.toml` unless
  `--purge`.
- A `curl` bootstrap shim for consumers without `uv`/Python is **[Planned]** (§11.11);
  catalog selection ("install fragments + hooks but not skills") is **[Deferred]**
  (§11.11).

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
4. **`enforced_by` schema field and the enforcement-pointer check** — **[Resolved]**
   (`basicly-a8e`): the field is in the fragment schema and `catalog_lint` fails any
   fragment whose `enforced_by` command is not cited in its body, so §3.1 is now
   mechanically checked, not review-only.
5. **Deterministic verify beyond schema/duplicate-id** — **[Resolved]** (`basicly-ihs`):
   `catalog_verify` adds duplicate-body, contradiction, ambiguity, and scope-overlap
   checks behind `basicly catalog-verify` and `basicly build --verify` (named
   `catalog-verify` because `basicly verify` is the loop CI-check runner).
   **`basicly review`** (agent-assisted semantic review) is now **[Resolved]**
   (`basicly-qps`, `review.py`): advisory, dispatched via the agent-agnostic
   runner, always exits 0 — a report, never a merge gate (§6).
6. **`hooks-build`/`hooks-check` projection** — **[Resolved]** (`basicly-lku`,
   `basicly-t51`): a tool-agnostic `core/hooks/hooks.yaml` manifest drives
   `basicly hooks-build`, which materializes the scripts and merges a managed
   `repo: local` block into a consumer's `.pre-commit-config.yaml` (foreign hooks
   preserved, idempotent); `hooks-check` reports drift.
7. **Skill/fragment source format — [Done].** Catalog content is authored as YAML
   (`skill.yaml`, `<id>.fragment.yaml`) and projected to the discoverable `.md` at target
   roots only; JSON Schemas, the `catalog-authoring` skill, `skills-new`/`fragment-new`
   scaffolds, and the `catalog-lint` gate (pre-commit + CI) support and enforce it.
   The update path (folded into `install`, §11.8) prunes any leftover legacy
   `SKILL.md`/`*.fragment.md` sources from the managed core, so installing over a
   pre-migration hand-copied catalog self-cleans.
8. **One-command lifecycle** — **[Partial]** (`basicly-zrj.12` + children, §9):
   `basicly install` is **[Implemented]** (`basicly-zrj.12.1` — init + build +
   skills + hooks + upgrade in one idempotent converge command, replacing
   `init`/`update`). Still open: real core upgrade sync (`basicly-zrj.12.2` —
   install still materialize-missing, the largest pre-release gap), provenance
   tracking (`basicly-8fg`), and `basicly uninstall` (`basicly-zrj.12.3`).
   Release-blocking.
9. **Consumer-repo robustness** — **[Planned]** (`basicly-zrj.13`): the
   `beads-commit-msg` hook must skip cleanly in a repo with no `.beads` workspace
   (`basicly-zrj.13.1`), and the verify runner must fail cleanly — not traceback —
   on a missing check command, with scaffolded defaults that don't assume Python
   tooling (`basicly-zrj.13.2`). Release-blocking (the first consumer hits both).
10. **v0.1.0 acceptance & release** — **[Planned]**: exercise the pushed-ref
    `git+` install (`basicly-zrj.14`), run the end-to-end acceptance in the
    `terminal` repo — install → customize → upgrade → harness loop → ship
    (`basicly-zrj.15`) — then cut the tag (`basicly-zrj.16`, gated also on the
    `copilot-instructions.md` size-cap split `basicly-4ce`).
11. **`curl` bootstrap script, catalog selection/flavors, and `.codex/rules/*.rules`
    scoped rules** are **[Planned]**/**[Deferred]** post-release (`basicly-zrj.6`).
12. **Cursor as a target** is **[Deferred]**; no renderer, no templates.
13. **The basicly harness** — **[Implemented]** (§12, epic `basicly-onb` closed): the
    agent-agnostic development loop (work isolation + workflow + hard verify/validate
    gates) built thin over `br`. Remaining **[Partial]**: auto-dispatching the loop
    build phase through the selected runner (`basicly-7ca`, post-release).

## 12) The basicly harness — agent-agnostic development loop

**Summary**: **[Implemented]** (epic `basicly-onb`; the one remaining gap is
auto-dispatch, `basicly-7ca`, see §12.8) The harness is an always-delivered
_core_ that binds work isolation, a workflow loop, and hard verify/validate gates into a
predictable machine, driven identically by any coding agent (Claude, Codex, Copilot). Its
thesis is _lean-over-substrate_: it wraps the `br` (beads-rust) tracker's existing primitives
(gate ledger, scheduler, dependency graph, lint) and builds only the missing mechanics
(worktree lifecycle, merge queue, verify runner, loop state machine). Guidance is projected
per target like every other fragment/skill; enforcement is deterministic gates.

### Details

**12.1 Work model.** A unit of work is classified into a **Work Class** that is exactly a
`br` issue type — `bug`, `chore`, `task`, `feature`, `epic`. (`br`'s statuses are
`open · in_progress · blocked · deferred · closed`; there is **no** `rework` status, so the
rework loop below is modeled with gate results + comments, not a status.) The class selects a
**track**, and tracks nest fractally: an Epic track runs Feature tracks, which run Task
tracks; `bug`/`chore` are leaf tracks. There is no separate "node" concept — a decomposed
leaf is a child `br` issue linked with `br dep add`.

**12.2 The loop.** Intake (any input) → **Classify** (agent proposes, engine records the `br`
type) → _[human checkpoint]_ → **Decompose** into child issues + a `br dep` graph, gated by a
**Definition-of-Ready** (`br lint` required template sections; acceptance criteria present)
→ _[human checkpoint]_ → **fan-out build** (one worktree per dependency-unblocked node, ranked
by `br scheduler`, concurrency-capped) with a **serial merge queue** on the way back →
**Verify** (deterministic, blocking) + **Validate** (acceptance/traceability) → _[human
checkpoint]_ → **Ship** + **Teardown** → **epic retro**. A failed node enters a bounded
**rework loop (n=2)** then escalates to a human; any track can **escalate a tier** (carry work
forward, re-hit only the Decomposition checkpoint) without restarting. Default is
task-by-task; one-shot mode collapses the middle checkpoint. Concurrency cap is configurable
(default 4). The retro emits a findings list; per finding the user picks ignore / fix-now /
fix-later, and a bead is created for everything not ignored.

**12.3 Components — build vs reuse.** The engine we build is thin: worktree lifecycle; merge
orchestrator + serial merge queue + conflict-resolver; a **verify runner** (runs the
consumer's configured checks — adapted from beads-blueprint's `validate.py`, made
config-driven rather than Python-specific); the loop state machine + checkpoints; the
classifier; the concurrency cap. Everything else is delegated to `br`: **gate ledger**
(`br gate report`/`br gate list`, with required-gate status), **scheduling** (`br scheduler`,
explainable additive scoring), **dependency graph + readiness** (`br dep`/`br ready`/
`br blocked`), **Definition-of-Ready** (`br lint`), **retro capture** (`br comments`), and
**swarm/stale-claim diagnosis** (`br coordination`). basicly reimplements none of these.

**12.4 Gates — deterministic blocks, semantic advises.** Deterministic checks (tests, lint,
type, build; the existing commit-msg/identity/beads hooks) report a **required** gate via
`br gate report --status pass|fail`; a failed required gate blocks loop advancement.
AI-semantic verification reports a **non-required** gate — advisory, never blocking (§3.3
deterministic-first, semantic-second, applied to the loop). The block-vs-advise policy and
the n=2 rework rule live in the harness engine; `br gate` only stores the verdicts.

**12.5 Work isolation.** Non-trivial work runs in a **sibling** git worktree
`<repo>.worktrees/<name>` on branch `harness/<name>` (never in-repo `.claude/worktrees/`,
which pollutes basicly's own tree-walk and provisions no deps). Creating a worktree provisions
its toolchain (`uv sync`, `npm install`) and installs the gates (`pre-commit install`) — a
worktree without the toolchain runs _no_ gates, the exact failure that once let unguarded
commits through. Trivial mechanical work goes straight to the source branch. Cleanup
(`git worktree remove` + delete the merged branch) runs immediately after a node lands;
copy-mode deps make removal safe.

**12.6 Merge model.** Parallelism is **parallel-build, serial-merge**: nodes build
concurrently in their worktrees but land one at a time through a **merge queue** in dependency
(topological) order, owned by a **merge orchestrator**, re-verifying after each merge. The
**decomposer** marks nodes parallel-safe only when it can predict **file-disjoint** scopes;
when it cannot, it emits a fixed serial order. A **conflict-resolver** (agent + scripts +
skills) handles residual conflicts under the same n=2→human rule. Tracker state
(`.beads/issues.jsonl`) is reconciled with **`br sync --merge`** (a 3-way merge; `br` has no
git merge-driver, unlike `bd`), never by hand-editing JSONL conflict markers.

**12.7 State & resumability.** `br` is the single source of truth — the harness keeps no
durable side-state. In-flight worktree/branch bindings are stashed on the issue via
`br update --external-ref`; design/architecture constraints ride _down_ a dependency tree via
`br`'s inheritable `--agent-context`. Resume (after a crash, or when switching agents because
one is rate-limited) is re-reading `br`: in-progress issues + their external-ref + recorded
gate results + the ready set, reconciled against live worktrees. This is what makes the loop
cross-agent — start on Claude, resume on Codex or Copilot.

**12.8 Agent-agnostic runner.** Each agent drives the _same_ loop through a thin **runner**
adapter (invocation command, headless flags, prompt injection, output capture), selected by
capability detection or an explicit flag. The loop logic is agent-neutral; only the runner
differs per agent. Detection (`auto`) probes the big 3 on `PATH` (claude → codex → copilot);
any other agent is supported by an explicit `[[runner.agents]]` command template in
`basicly.toml`. There is no cross-agent CLI invocation standard, so an unknown agent's command
is **never guessed** — when nothing matches, selection falls back to a **`manual` handoff
runner** that shells out to nothing and instead surfaces the exact prompt + worktree path,
deferring to the loop's block-and-resume contract and the one thing that _is_ standardized
across agents: the projected `AGENTS.md` guidance. `basicly runner dry-run` prints the exact
command an adapter would execute so it can be verified before any live invocation.
**[Partial]**: the runner abstraction and CLI exist; wiring `runner.select_runner` +
`runner.run` into the loop build phase so a node's coding is auto-dispatched headless in
its worktree is `basicly-7ca` (post-release) — until then the loop uses the handoff contract.

**12.9 Ship.** Ship is parameterized by the entry branch recorded at Intake: default → merge
to `main` + push `main` (no feature branches on the remote); if the entry branch is a feature
branch → merge to it, push, open a PR to `main`. Delivery is incremental per feature; teardown
follows each feature's merge.

**12.10 Reuse & positioning.** basicly's harness is a lean, clean-room, `br`-substrate-native,
agent-agnostic re-founding of the same goal as the sibling `agent-harness` (basicly's first,
company-owned, lefthook/pinned-pack, tracker-abstracted attempt): borrow its battle-tested
worktree/merge know-how (copy-mode deps, `git merge-tree` pre-flight probe, mode-aware
cleanup) as a reference, while keeping the **`br`-wrapping engine + agent-agnostic projection
+ installable composable distribution** as the differentiators. From beads-blueprint, adapt
the `validate.py` gate-runner structure into the verify runner. `bv` (beads-viewer) is an
**optional human viewer only** — redundant with `br scheduler` at runtime, never a harness
dependency.

## 13) References

- pre-commit: <https://pre-commit.com/>
- Trunk Code Quality: <https://docs.trunk.io/code-quality/overview>
- MegaLinter: <https://github.com/oxsecurity/megalinter>
- Claude Agent SDK: <https://code.claude.com/docs/en/agent-sdk/overview>
- OpenAI SDKs and CLI: <https://developers.openai.com/api/docs/libraries>
- Cursor SDK: <https://cursor.com/blog/typescript-sdk>
- Pydantic AI: <https://pydantic.dev/docs/ai/overview/>
- Fowler series (context priming, design-first, context anchoring): <https://martinfowler.com/articles/reduce-friction-ai/>
