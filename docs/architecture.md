# basicly Architecture

> **This document is the single authoritative architecture reference for `basicly`.**
> There is no separate plan/design-notes folder — this file is kept current as the
> only source of truth for architecture decisions, and beads (`br`) issues are broken
> down directly from it (§10).
>
> **Status convention**: this document describes the system as it exists in code
> today. Anything not yet built is explicitly marked **[Deferred]** and collected
> in §11; everything else is implemented and available.
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
  │   (single source)      │
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
policy, when to ask instead of guess. The
`enforced_by` schema field lists the commands that enforce a rule, and
`catalog_lint` requires each listed command to be cited in the fragment body —
a fragment that claims enforcement must point at the command, not restate the rule.

**3.2 Composability over templates.** Generated files are never hand-templated blobs;
they are assembled from fragments — one fragment per policy/practice/decision —
selected, sorted, and rendered per target — this is exactly how
[`loader.py`](../src/basicly/loader.py) and
[`planner.py`](../src/basicly/planner.py) work.

**3.3 Two-layer verification, deterministic first.** Deterministic, scriptable checks
catch a large class of problems cheaply (duplicate ids, missing fields, unknown
categories). Semantic problems — contradiction, ambiguity that parses fine but reads
badly to a model — need a capable reader. Both layers run against the same merged
fragment set, deterministic always first. Schema/duplicate-id
validation runs inside the normal load path
(`loader._validate_fragment`); duplicate-body, contradiction, ambiguity, and
scope-overlap checks live behind the `basicly catalog verify` command
(`catalog_verify.py`). Agent-assisted semantic
review (`basicly catalog review`, `review.py`) is
advisory, never a merge gate (§6).

**3.4 Source of truth and generated files are each a one-way street.** Users edit
fragments (core or overlay) and never the generated files; `basicly build` regenerates,
`basicly check` catches manual edits. `basicly install` edits only the managed core
catalog and never the user's overlay — the mechanism, not just the convention,
guarantees this (§4.3). Two guardrails defend the one-way street at different moments:
the `protect-generated` PreToolUse guard blocks an agent's edit to a marked generated
file at tool time (Claude-only, fail-open), and the `protect-generated-commit` pre-commit
hook (basicly-yw28) is the deterministic, agent-independent backstop — it blocks a commit
that stages a generated output whose content no longer matches the projection manifest's
recorded hash, so a tool-time bypass or a non-Claude agent is still caught before the
edit lands. A legitimate rebuild stages the regenerated file and the updated manifest
together, so their hashes agree and the commit passes.

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

| Tree                                                                                                                     | Owner (who writes here)                                                |
| ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| `src/basicly/` — engine (loader, planner, CLI, renderers)                                                                | `basicly` maintainers, ships with the tool                             |
| `.basicly/core/` — managed fragment + skill + agent + hook + target + template catalog                                   | `basicly install` only                                                 |
| `.basicly/state/install.json` — install provenance (version, timestamp, catalog hashes)                                  | `basicly install` only                                                 |
| `.basicly-local/` — user overlay (path-configurable via `basicly.toml`)                                                  | the consumer repo's users                                              |
| `basicly.toml` — path wiring                                                                                             | the consumer repo                                                      |
| Generated artifacts (`AGENTS.md`, `.claude/CLAUDE.md`, `.github/copilot-instructions.md`, skill/scoped-rule/agent files) | `basicly build` / `basicly skills-build` / `basicly agents-build` only |
| `.basicly/generated-manifest.json`                                                                                       | `basicly build` only                                                   |

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
    fragments/{boundaries,commands,decisions,project,security,testing,tools}/*.fragment.yaml
    skills/<skill-name>/skill.yaml    # projected to SKILL.md at target roots, see below
    agents/<slug>/agent.yaml          # + agents/blocks/<id>.block.yaml (§5)
    hooks/*.py + hooks.yaml           # git-stage + agent hook scripts and their manifest
    rubrics/*.rubric.yaml             # work-type behavioral rubrics (basicly-0122), advisory gate
    schemas/*.schema.json
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

**Skills**: `core/skills/` is the catalog location. Sources are authored as `skill.yaml` (name, description, and an
`instructions` block scalar), **not** the discoverable `SKILL.md` name: because some coding
agents auto-discover skills by scanning broadly for `SKILL.md`, a `SKILL.md` _source_ would
risk an agent loading both the catalog copy and the projected copy twice. `skills-build`
renders the discoverable `SKILL.md` at the target roots only, with a generated marker.
Fragments follow the same rule (`<id>.fragment.yaml` → projected `.md`), YAML is the single
catalog source format (targets and hooks were already YAML), and `basicly catalog lint`
enforces all of this (schema validity, no `.md`-named sources, no `.yml`). The chosen format
is YAML rather than Python — it needs no code execution, keeps prose lossless via block
scalars, and matches the existing catalog conventions.

A skill source directory follows the full [Agent Skills spec](https://agentskills.io/specification):
alongside `skill.yaml` it may bundle `references/`, `scripts/`, `assets/`, and any additional
files or directories. `skills-build` projects the **whole** source directory into each target
root — the rendered `SKILL.md` (carrying the generated marker) plus every other file copied
verbatim (bytes and mode) — so a skill can ship a long reference guide or a fixer script.
`skill.yaml` also accepts the spec's optional frontmatter (`license`, `compatibility`,
`allowed-tools`, `metadata`), rendered into the `SKILL.md` header; `technologies` stays
basicly-internal (§9 scoping) and is never emitted. The projected skill directory is a pure
projection target owned wholly by basicly and is **mirrored**: `skills-check` flags a stale or
orphaned resource, a rebuild prunes a resource dropped from the source, and deselecting a
skill's technology prunes the whole directory. `catalog lint` enforces the spec's naming rules
(name matches the directory; 1–64 lowercase `a-z0-9`/hyphen characters with no leading,
trailing, or consecutive hyphen) and warns (advisory) when a `SKILL.md` body exceeds ~500 lines
or a file reference reaches more than one level deep, per the spec's progressive-disclosure
guidance. (The upstream `skills-ref validate` tool checks the same frontmatter/naming rules;
it is not vendored into this repo, so `catalog lint` is the in-tree equivalent.)

**Skill taxonomy (core vs optional)**: every skill is one of two kinds, set by its
`technologies:` tag. An **untagged** skill is _universal core_ — it always ships
(`test-discipline`, the harness and tool skills). A **technologies-tagged** skill is
_optional_ — it ships only when the consuming repo selects that tag via `[catalog]
technologies` in `basicly.toml` (§9). So this repo ships `python` and `node` and excludes
`wsl` and the environment-tagged tool skills, which stay available to repos that opt in.
Tech-specific and situational guidance belongs in these optional skills, not in the
always-on files: enforcement stays in the deterministic hooks (`ruff`, `pytest`,
`markdownlint`) and a skill carries the judgment and pointers a linter cannot — never a
restated lint rule.

**Two skill roots, both mandatory**: skills project into `.claude/skills` (Claude Code's
only project skill root) and `.agents/skills` (the Agent Skills open-standard root Codex,
Copilot, and Cursor discover). The split mirrors how each agent finds its guidance: Claude
Code reads `.claude/CLAUDE.md` and discovers skills only under `.claude/`; Codex, Copilot,
and Cursor read `AGENTS.md` and discover skills under `.agents/`. Moving guidance into
skills drops none of the always-on files — `AGENTS.md` is the canonical open-standard
baseline, and `.claude/CLAUDE.md` and `.github/copilot-instructions.md` are per-agent
renders of the _same_ shared `applies_to: [all]` fragments, each self-contained (Claude and
Copilot do not reliably `@`-import `AGENTS.md`, so the shared content is inlined into each,
not imported — §4.4 detail 3). Both skill roots and all three always-on files stay. See the
[Agent Skills spec](https://agentskills.io/specification), the
[AGENTS.md spec](https://agents.md/), and the
[skills tooling and agent discovery paths](https://github.com/vercel-labs/skills/blob/main/README.md).

**Hooks** (projected and installed by
`hooks-build`): `core/hooks/` holds the actual hook scripts — git-stage gates
(`pre-commit.py`, `identity-guard.py`, `commit-msg.py`, `beads-commit-msg.py`,
`pre-push.py`, `secret-scan.py` — a stdlib scanner that blocks a commit whose
staged added lines carry a likely credential, with an inline
`pragma: allowlist secret` escape for reviewed false positives) plus agent-side
hooks (`protect-generated.py`, `tool-usage.py`) — as
first-class catalog artifacts — the deterministic, gating counterpart to
fragments/skills — described tool-agnostically in `core/hooks/hooks.yaml`.
(`identity-guard.py` blocks a commit whose git identity is unset or a hostname
fallback — a generic, no-personal-data gate; the `.scripts/setup_git_identity.py`
helper and the `tool-git` skill cover the per-host identity setup it guards.) This
repo dogfoods them directly: [`.pre-commit-config.yaml`](../.pre-commit-config.yaml)
points straight at `core/hooks/*.py`. `basicly hooks-build` projects the manifest
into a consumer's `.pre-commit-config.yaml` and then runs `pre-commit install` so the
gates are active — not merely written; a gate that is shipped but never installed is
inert, the exact failure that once let unguarded commits through (§8). The manifest's
`manager` field routes each hook to one of three surfaces: `git` (the pre-commit
config), `claude` (agent hooks in `.claude/settings.json`; the event derives from the
spec `stage`, with an optional per-spec `matcher`), and `copilot` (managed
`.github/hooks/basicly-<id>.json` files). The `tool-usage` hook rides both agent
managers: a PostToolUse counter tallying every shell command's pipeline heads into the
self-ignored `.basicly/usage/tool-usage.json` — token-free telemetry for culling idle
tools/skills from the catalog with real data.

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
there, and never overwrites an existing `basicly.toml`. When the existing file lacks
sections the shipped default now carries, install names them in a hint instead of
editing the file.

**Per-machine overlay — `basicly.local.toml`** (gitignored; install adds the
`.gitignore` entry): keys there override `basicly.toml` key-by-key for the harness
sections only (`[worktree]`, `[verify]`, `[policy]`, `[runner]`), so machine-specific
choices (a runner default, a lower worktree cap) stay out of the shared config. A key
set locally replaces the shared key wholesale (a local `checks`/`agents` list is not
concatenated). Projection config (`[paths]`, `[catalog]`) shapes repo-committed
outputs, so it is repo-level only and never reads the overlay.

#### 4.4 Generated artifacts

```text
AGENTS.md                                    # applies_to: [all]; inlines scoped fragments (codex can't path-scope)
.claude/CLAUDE.md                            # applies_to: [all] + [claude]; scoped fragments excluded (exclude_scoped)
.claude/rules/*.md                           # path-scoped fragments, `paths:` frontmatter (single source)
.github/copilot-instructions.md              # applies_to: [all] + [copilot], inlined (no @-import); scoped excluded
.claude/skills/*/SKILL.md                    # projected via `skills-build`
```

Which fragments land where is driven by each output's `filter` in `targets/*.yaml`:
`applies_to` selects by target, `has_scope: true` restricts an output to scoped
fragments (the `.claude/rules/` files), and `exclude_scoped: true` drops scoped
fragments from a baseline (the `CLAUDE.md` and `copilot-instructions.md` wrappers) —
see §7 detail 4. Codex gets the shared `AGENTS.md` baseline only, with scoped
fragments inlined because it has no path-scoping mechanism; `.codex/rules/*.rules`
is **[Deferred]** (§11).

**Scoped rules are single-sourced to `.claude/rules/`** (adopted 2026-07-16): VS Code
loads both `.claude/rules/*.md` and `.github/instructions/*.instructions.md` with no
dedup (it name-dedupes only skills), so a `.github/instructions/` twin double-loaded
every path-scoped rule for every VS Code consumer. The copilot target therefore no
longer emits `scoped_instructions`; a full `basicly build`/`install` sweeps previously
manifest-tracked `.github/instructions/*.instructions.md` files from consumers.
Trade-off, accepted: github.com-side Copilot (PR code review, cloud agent) loses
path-scoped rules and keeps only the root `copilot-instructions.md`.

---

## 5) Fragment model

**Summary**: one fragment = one Markdown file with YAML front matter = one
policy/practice/decision. Required fields: `id`, `description`, `category`,
`applies_to`. Extension fields (`source`, `override`, `replaces`, `extends`) exist with
safe defaults today.

### Details

Confirmed current schema ([`schema.py`](../src/basicly/schema.py)):

| Field         | Required | Values                                                                                                                                         | Notes                                                 |
| ------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `id`          | yes      | kebab-case, unique                                                                                                                             | duplicate id across core+overlay is a hard error      |
| `description` | yes      | one line                                                                                                                                       |                                                       |
| `category`    | yes      | `boundaries`, `code-style`, `commands`, `decisions`, `design`, `hooks`, `project`, `security`, `skills`, `testing`, `tools`, `ci-cd`, `quirks` |                                                       |
| `applies_to`  | yes      | target names or `all`                                                                                                                          |                                                       |
| `priority`    | no       | `critical`(4) `high`(3) `medium`(2, default) `low`(1)                                                                                          | sorts descending                                      |
| `scope.paths` | no       | glob list, default `["**"]`                                                                                                                    | non-default → scoped output                           |
| `status`      | no       | `active`(default) `draft` `deprecated`                                                                                                         | only `active` is projected                            |
| `source`      | no       | `core`(default) `user`                                                                                                                         | inferred from load root if omitted                    |
| `override`    | no       | bool, default `false`                                                                                                                          | must be `true` to replace a core fragment             |
| `replaces`    | no       | list of fragment ids                                                                                                                           | core fragments removed when this fragment is active   |
| `extends`     | no       | list of fragment ids                                                                                                                           | documentation only, narrows future conflict detection |

**Extension mechanism**: the planner
(`planner._apply_user_replacements`) removes core fragments listed in an active user
fragment's `replaces`, and the loader (`loader._validate_replacements`, run on every
`list`/`build`/`check`) enforces the integrity rules as hard errors: a fragment
declaring `replaces` must set `override: true`, every replaced id must exist in the
merged fragment set, and two user fragments may not replace each other.

Sorting is deterministic: priority (desc) → category (asc) → id (asc). Two `build`
runs on identical source produce byte-identical output.

### Agent composition model

Subagent definition files are the fourth catalog kind, generated — never
hand-edited — from YAML sources:

- **Sources**: `.basicly/core/agents/<slug>/agent.yaml` per agent, plus shared
  building blocks in `.basicly/core/agents/blocks/<id>.block.yaml` (`blocks` is
  a reserved slug). The overlay mirrors the layout under
  `.basicly-local/agents/`; an overlay source with the same slug/id needs
  `override: true` to replace the core one, new names simply add.
- **Composition**: every agent fills five ordered body slots — `role`,
  `startup`, `process`, `output_contract`, `constraints` — each a list of
  `{block: id}` refs or `{text: ...}` inline markdown. The skeleton comes from
  the `basicly-ajq` research: it is the structure Anthropic's official
  subagent examples and the community corpus best-in-class files converge on.
- **Description**: authored as four fields (`purpose`, `triggers`, `returns`,
  `posture`) the projector joins, so no part of a delegation-quality
  description can be forgotten. `tools` is a mandatory explicit allowlist —
  agents never silently inherit every tool. `model` defaults to `inherit`
  (omitted from output); a `claude:` map passes Claude-only frontmatter
  (e.g. `memory`, `maxTurns`) through verbatim.
- **Emission**: `.claude/agents/<slug>.md` only — the single root Claude Code
  and VS Code both parse natively (same single-source policy as
  `basicly-2f4`; a second root would double-load in VS Code, which dedupes
  only skills). Rendered files carry the generated marker inside the
  `protect-generated` hook's scan window, so tool-time edits are blocked.
- **Lint** (`catalog lint`): schema validation for both source kinds, plus
  composition rules — block refs must resolve, a `Read-only` posture may not
  grant write tools, and the composed body must stay under 30,000 characters
  (the strictest reader's prompt ceiling).

---

## 6) Verification pipeline

**Summary**: schema/duplicate-id validation runs on every load; the deterministic
content checks (duplicate-body, contradiction, ambiguity, scope-overlap) and the
standalone `basicly catalog verify` command (also wired as `basicly build --verify`)
are built, as is the advisory agent-assisted semantic review (`basicly catalog review`).

### Details

| Check | Mechanism |
| --- | --- |
| Required fields, known category/priority/status/target, extension-field types | `loader._validate_fragment`, runs on every `list`/`build`/`check` |
| Duplicate fragment `id` across core + overlay roots | `loader.load_fragments_from_roots` |
| `replaces` target exists / `override: true` required / no mutual user-user replaces | `loader._validate_replacements`, runs on every `list`/`build`/`check` |
| Duplicate/near-duplicate fragment bodies | `catalog_verify._duplicate_bodies` (difflib ratio) |
| Contradiction detection (static dictionary: tabs/spaces, pathlib/os.path, etc.) | `catalog_verify._contradictions`, curated pairs |
| Ambiguity detection (deny-list of vague phrases) | `catalog_verify._ambiguous_phrases` |
| Scope-overlap detection | `catalog_verify._scope_overlaps`, scoped pairs |
| Enforcement-pointer check (`enforced_by` field, §3.1) | `catalog_lint` requires each `enforced_by` command to be cited in the body |
| Standalone `basicly catalog verify` / `basicly build --verify` commands | named `catalog verify` because `basicly verify` is the loop CI-check runner; `build --verify` gates the write |
| Semantic review (`basicly catalog review`, agent reads rendered files for contradictions/ambiguity) | `review.py` builds the prompt, dispatches via the agent-agnostic runner, always exits 0 (advisory, not a merge gate) |

Both layers run in this order — deterministic gate first, always; semantic
review second, advisory, on demand or in CI as a report (not a blocker).

---

## 7) The three always-on files

**Summary**: `AGENTS.md`, `CLAUDE.md`, `copilot-instructions.md` are the foundation
every other artifact builds on. If they're noisy or ambiguous, everything downstream
inherits that failure.

### Details

1. **Size discipline**: a **shared soft cap of 8,000 chars** for the `claude` and
   `copilot` targets, and **12,000 for `codex`** (`max_size_warning` per
   `targets/*.yaml`). The shared-baseline reasoning still holds — all three always-on
   files project from the same `applies_to: [all]` fragment set and differ only by a
   small per-target defaults fragment — but the codex projection legitimately carries
   more: scoped fragments are inlined there for glob fidelity (detail 4), so its cap
   gets a documented allowance rather than a pretense of identical content. The numbers
   are a deliberate discipline choice, not platform limits: Claude Code's own
   degradation warning is ~40 KB, GitHub removed its former 4,000-char hard limit on
   `copilot-instructions.md` (it now only advises shortening past ~4,000 chars), and
   Codex reads AGENTS.md up to `project_doc_max_bytes` (32 KiB default, verified
   2026-07-15). A cap warning means split into a scoped rule, not shrink the prose.
   (Refs: GitHub removed the hard limit — github/docs#42761; Claude ~40 KB — Claude
   Code memory docs; Codex 32 KiB — learn.chatgpt.com/docs/agent-configuration/agents-md.)
2. **Enforced vs. judgment split**: enforced rules are one line pointing at the
   command/config; judgment rules are prose, and should be the shorter of the two
   sections.
3. **No duplication across always-on files**: `applies_to: [all]` fragments feed
   `AGENTS.md` and are inlined into `copilot-instructions.md` (Copilot cannot
   `@`-import `AGENTS.md`). Target-specific fragments add only genuinely different
   content.
4. **Scoped fragments stay out of the always-on baseline** (Claude & Copilot): a
   fragment with a non-default `scope.paths` is projected only to its path-gated file
   (`.claude/rules/*.md` via `paths:` — the single source; the former
   `.github/instructions/*.instructions.md` twin was retired 2026-07-16 because VS
   Code loads both roots without dedup, see §4.4) — and is **not**
   inlined into `CLAUDE.md`/`copilot-instructions.md`. This keeps the always-on file lean
   (a Python-only rule shouldn't cost every task its context budget) and is enforced by
   the `exclude_scoped: true` output filter (§4.4). **Exception — `AGENTS.md` (codex)**:
   scoped fragments are still inlined there, but the reason has changed (verified
   2026-07-15 against OpenAI's docs). Codex **does** now support both Agent Skills
   (SKILL.md open standard, discovered from `.agents/skills` at repo root/cwd with
   progressive disclosure — basicly's skill projection already targets this) and
   nested/path-scoped `AGENTS.md` (root→leaf concatenation, nearest file wins,
   `AGENTS.override.md` precedence). However, nested `AGENTS.md` scoping is
   **directory-based**, while basicly scoped fragments are **glob-based**
   (`**/*.py`) — a per-directory offload cannot faithfully express a glob scope, so
   inlining remains the correctness-preserving choice. This is why `AGENTS.md` runs
   larger than the other two baselines and why the codex cap carries an allowance
   (detail 1). Offloading via nested `AGENTS.md`/skills for directory-shaped scopes
   is **[Deferred]**. (Refs: learn.chatgpt.com/docs/build-skills;
   learn.chatgpt.com/docs/agent-configuration/agents-md; agentskills.io.)
5. **Self-contained per target**: each generated file stands alone; an agent should
   never need a second file to understand the baseline.
6. **Stable ordering**: priority → category → id, so diffs stay minimal.

---

## 8) CLI surface

**Summary**: the CLI has three surfaces — lifecycle (`install`, which replaced
the former `init`/`update` staging pair, `uninstall`, and the read-only
`status`), catalog (the consumer projection pairs `build`/`check`,
`skills-build`/`skills-check`, `agents-build`/`agents-check`,
`hooks-build`/`hooks-check`, `permissions-build`/`permissions-check`, `usage`,
plus the contributor authoring group
`catalog` with the verbs `lint`, `verify`, `review`, `new`, `list`), and harness
(`worktree`, `verify`, `policy`, `decompose`, `loop`, `runner`, `rubric`). The authoring
and inspection verbs moved under `basicly catalog <verb>` (a breaking change:
the old flat `list`/`skills-list`/`agents-list`/`*-new`/`catalog-lint`/`catalog-verify`/`review`
names were removed, not aliased).

### Details

**Lifecycle** — one command installs _and_ upgrades; a second removes:

| Command                       | Behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `basicly install`             | Idempotent converge: materialize the bundled core catalog, migrate/prune legacy layouts, scaffold overlay + `basicly.toml` (never overwriting existing user content), then `build` + `skills-build` (all default roots) + `agents-build` + `hooks-build` (with hook activation). The same command performs first install and every upgrade (provenance-guarded core sync, §9; `--force` overwrites kept hand-edits). Replaced the former `init`/`update` staging pair |
| `basicly uninstall [--purge]` | Removes managed core, state, manifest-listed generated files, projected skills and agents (generated-marker files only), and the managed hook block (deleting the config + uninstalling git hooks when nothing else remains); preserves the overlay + `basicly.toml` unless `--purge`; refuses in the authoring repo                                                                                                                                                  |
| basicly status [--json]       | Read-only snapshot for fleet loops and humans: installed catalog version vs running engine version, drift summary (the `check` comparison plus install-provenance drift), per-manager hook state (projection sync + git stage activation), technology selection, and overlay counts; never writes, always exits 0; `--json` emits a stable versioned schema; `--fleet [--root PATH]` rolls it across the housed repos as one json payload (h0f0)                      |
| `basicly health [--json]`     | Read-only per-agent health scoring and behavioral drift from the run-record log (`health.py`, y886): dispatch failure rate, a rework signal, and a bounded health score per agent, plus a rolling-baseline drift check flagging agents whose recent failure rate regressed; `--window N` sizes the recent window; `--fleet [--root PATH]` rolls it across the housed repos; never writes, always exits 0                      |

**Catalog**:

| Command | Behavior |
| --- | --- |
| `basicly build [--target NAME] [--verify]` | Renders enabled targets (or one), writes only changed bytes, updates the manifest, warns on size-cap overrun; `--verify` runs `catalog verify` first and writes nothing on failure |
| `basicly check` | Byte-for-byte staleness check of generated files + manifest; exit `1` on mismatch, no auto-fix |
| `basicly skills-build [--root ...\|--all-default-roots]` / `skills-check` | Same build/check contract, applied to the skill catalog |
| `basicly agents-build` / `agents-check` | Same build/check contract for the agent catalog: composes slot blocks into `.claude/agents/<slug>.md` (single-source emission, §5 agent composition model) |
| `basicly hooks-build [--no-install]` / `hooks-check` | Materializes catalog hook scripts, merges a managed `repo: local` block into `.pre-commit-config.yaml` (foreign hooks preserved, idempotent), and then runs `pre-commit install` for every managed stage so the gates are actually active (`--no-install` skips activation; graceful when pre-commit is absent). `hooks-check` reports projection drift and warns (non-fatal) when the git hooks are not installed |
| `basicly permissions-build` / `permissions-check` | Projects the catalog agent-permissions deny-list (`.basicly/core/permissions/permissions.yaml`) into the co-owned `.claude/settings.json` `permissions.deny`, the way hooks are managed: ensure-present (managed patterns merged in, consumer-added entries preserved, nothing pruned — an extra deny is fail-safe and a flat deny string has no per-entry marker), with a semantic subset-match drift check. Claude-only: Copilot CLI has no config-file deny (session-scoped `--deny-tool` flag only) and Codex forbids project-scope override of `sandbox_mode`/`approval_policy`, so those guardrails are invocation-only — the copilot runner injects the deny-list as `--deny-tool` flags at dispatch (`basicly-lqz5`), while Codex sandbox/approval defaults remain to wire (`basicly-t0kt`) |
| `basicly usage report` | Reports the tool/skill counts recorded by the `tool-usage` agent hook (token-free telemetry in `.basicly/usage/`) and names never-used catalog skills — the culling input (§4.3) |
| `basicly catalog list [fragment\|skill\|agent]` | Table of catalog sources of the given kind (default `fragment`); the authoring/inspection verbs live under the `catalog` group |
| `basicly catalog new <fragment\|skill\|agent> NAME [--category C] [--description D]` | Scaffold a new `<id>.fragment.yaml` / `skill.yaml` / `agent.yaml` source (§4.2 source format); `--category` sets a fragment's category, `--description` seeds the one-line summary |
| `basicly catalog lint` | Source-format gate: schema validation, no `.md`-named sources, single `.yaml` extension; wired as a pre-commit hook and CI step |
| `basicly catalog verify` | Deterministic content checks beyond the load-path validation: duplicate bodies, contradictions, ambiguity, scope overlaps (§6); named `catalog verify` because `basicly verify` is the loop check runner |
| `basicly catalog review [--runner NAME] [--dry-run]` | Advisory agent-assisted semantic review: renders the always-on files, dispatches a review prompt via the agent-agnostic runner (handoff when no CLI is on PATH), always exits 0. `--dry-run` prints the prompt without invoking an agent (§6) |
| `basicly rubric eval <issue> [--runner NAME] [--dry-run]` | Evaluates the issue's work-type behavioral rubric (`.basicly/core/rubrics/*.rubric.yaml`): deterministic checks run via the verify runner (exit code = yes/no), judged checks dispatch one agent prompt via the runner (handoff when no CLI). Reports an advisory `rubric` gate (`br gate report`) — non-required by default (a judged verdict never fails the gate; deterministic-first), promotable by adding `rubric` to `[policy] required_gates`. `--dry-run` prints the judged prompt (basicly-0122) |

**Harness** (§12):

| Command                                     | Behavior                                                                                                                                     |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `basicly worktree ...`                      | Sibling git-worktree lifecycle: create + provision (deps, hooks), list, cleanup (§12.5)                                                      |
| `basicly verify [--gate]`                   | Runs the consumer's `[[verify.checks]]` from `basicly.toml` per mode and optionally records a `br` gate (§12.3–12.4)                         |
| `basicly policy ...`                        | DoR/gate/rework/checkpoint checks; `policy checkpoint --approve` needs an interactive TTY or a one-time `--confirm` code (§12.2)             |
| `basicly decompose`                         | Turns a feature into child `br` issues + a computed dependency graph (§12.2)                                                                 |
| `basicly loop status\|advance\|run <issue>` | Drives an issue through the harness loop; a blocked step exits non-zero and names the input it needs (§12.2)                                 |
| `basicly runner list\|dry-run\|run`         | Agent-agnostic headless runner adapters (claude/codex/copilot + `manual` handoff); the loop build phase auto-dispatches through them (§12.8) |

The formerly planned `basicly conflicts`/`basicly overrides` reporting views are
**[Deferred]** — cut from scope; `catalog verify` output covers the reporting need.

---

## 9) Distribution mechanics

**Summary**: the consumer lifecycle is **one command for install and every upgrade**,
plus one for removal:

```sh
uvx --from git+https://github.com/niksavis/basicly@<ref> basicly install    # first time AND upgrades
uvx --from git+https://github.com/niksavis/basicly@<ref> basicly uninstall  # removal
```

Packaging, the bundled catalog, the unified `install`/`uninstall` commands, core
upgrade sync, and provenance tracking all live behind those two commands. The
live `git+<remote>@<ref>` path works for both `@main` and commit-pinned
`@<sha>` refs: install converges the repo, `basicly check` passes afterwards,
and an immediate re-run is a no-op.

### Details

- `pyproject.toml` declares a `[build-system]` table (hatchling),
  `tool.uv.package = true`, and a `[project.scripts]` `basicly = "basicly.cli:main"`
  entry point. `uv build` produces a wheel + sdist; `uvx --from <wheel> basicly`
  resolves `basicly.cli`, as does the equivalent `git+https://...@<ref>` form.
  `jinja2` and `rich` (terminal output) are `[project.dependencies]` runtime deps.
- The managed core catalog ships inside the distribution: hatchling
  `force-include` projects the dogfooded source `.basicly/core/` to `basicly/catalog/`
  in the wheel, and the sdist carries `.basicly/core/` so `git+` installs resolve it.
  `basicly.catalog.bundled_catalog_root()` prefers a source checkout (marker walk) and
  falls back to the packaged copy in installed wheels.
- **`basicly install` — one idempotent
  converge command** replacing the former `init` → `build` → `skills-build` →
  `hooks-build` staging and the separate `update` (both removed pre-release).
  Design finding (2026-07-15): `init` was never a technical prerequisite —
  everything it does is idempotent skip-existing — so a single command serves
  first install and every upgrade. Its converge contract: materialize or sync
  the bundled core (below), migrate/prune legacy layouts, scaffold the overlay +
  `basicly.toml` only if missing, keep the authoring-repo guard (bundled source
  == destination → leave in place), then rebuild all artifacts and install the
  hooks.
- **Provenance** (`state.py`): `install` writes
  `.basicly/state/install.json` (sibling of the configured core root) recording the
  basicly version, timestamp, and a per-file sha256 snapshot of the core as
  materialized — so a later hash mismatch means a hand-edit of managed content.
  `basicly check` surfaces drift (modified/removed core files) and an
  installed-vs-current version mismatch as advisory notes that never change its
  exit code. The authoring repo writes no state file.
- **Core upgrade sync** (`cli._sync_catalog`):
  on a repeat `install` from a newer ref, the managed core is synced to the bundled
  catalog: changed files overwritten, upstream-removed files deleted, the overlay
  and `basicly.toml` never touched. The provenance snapshot distinguishes upstream
  changes from user hand-edits of core files: a file matching the snapshot is
  upstream-owned (overwritten/deleted); one that differs is a hand-edit — warned
  and kept unless `--force`; files unknown to both bundle and snapshot are always
  kept. The post-sync snapshot records only bundle-matching files, so kept edits
  stay protected on the next run. `hooks-build` no longer copies scripts (install
  owns core content); it errors when the core was never materialized. Upgrading is
  therefore literally re-running the same pinned `uvx ... basicly install` command
  with a newer `@<ref>` (§3.6).
- **`basicly uninstall`** removes everything
  managed — core, state, manifest-listed generated files, projected skills
  (generated-marker files only), the managed hook block (deleting the config and
  uninstalling the git hooks when nothing else remains) — and preserves the user's
  overlay + `basicly.toml` unless `--purge`. It refuses to run in the authoring
  repo, where the core is the catalog source itself.
- **Technology scoping** — catalog selection by
  stack/environment tag. Sources (skills, fragments, agents, hooks) carry an
  optional `technologies:` list; an untagged source is universal and always
  ships. The vocabulary is a controlled list (`schema.TECHNOLOGIES`: stack tags
  like `python`/`go` plus environment tools like `zsh`/`tmux`), enforced by
  `catalog lint` across all four source types (the fragment loader also
  validates it, since overlay fragments bypass catalog lint). The consumer's
  selection is recorded as `[catalog] technologies` in `basicly.toml`
  (`basicly install --technologies python,zsh`; absent = everything ships) and
  applied at **projection time**: `build`/`skills-build`/`agents-build`/
  `hooks-build` and their checks skip non-overlapping sources, while the core
  sync stays full for provenance-simple upgrades. Narrowing the selection
  converges on rebuild: fragment outputs recompose (per-fragment outputs are
  swept via the generated manifest), projected skills/agents the selection
  excludes are pruned (generated-marker files only), and excluded managed hooks
  are stripped from `.pre-commit-config.yaml` / `.claude/settings.json` instead
  of stranding. Per-block technology conditioning inside agent slots is
  **[Deferred]** (§11).
- **Bootstrap shim** for consumers without
  `uv`/Python: `.scripts/bootstrap.sh` (POSIX sh, curl-able) and
  `.scripts/bootstrap.ps1` (PowerShell) install `uv` from astral.sh when
  absent, then run the same pinned `uv tool run --from git+...@<ref> basicly
  install` in the current repo. `--ref` pins the version (default `main`);
  every other argument passes through to `basicly install`. Both fail fast
  outside a git repository.

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
  initiatives (e.g. "make basicly uvx-installable"), `feature`/`task` for new
  work (including the deferred items in §11), `bug` for regressions, and
  `--parent` to link a `task` under a `feature`/`epic` instead of inventing a
  "story"/"sub-task" type.

---

## 11) Not yet implemented

Everything described elsewhere in this document exists in code today. The items
below are the only known exceptions — each is **[Deferred]**: consciously not
built until a real consumer need appears. None is tracked as an open issue
(the former tracking beads were closed as won't-do, 2026-07-16); file a fresh
task if demand appears.

1. **`.codex/rules/*.rules` scoped rules renderer**: Codex reads
   the shared `AGENTS.md` baseline today, with path-scoped fragments inlined for
   glob fidelity (§7 detail 4). A native scoped-rules projection would add
   per-path parity once a real Codex consumer needs it.
2. **Cursor as a target**: no renderer, no templates.
3. **Offloading directory-shaped scopes** via nested `AGENTS.md`/skills for the
   codex target (§7 detail 4).
4. **`basicly conflicts`/`basicly overrides` reporting views** — cut from scope;
   `catalog verify` output covers the reporting need (§8).
5. **Per-block technology conditioning inside agent slots** — technology scoping
   applies at whole-source granularity (§9); per-block conditioning is a v2
   idea.

## 12) The basicly harness — agent-agnostic development loop

**Summary**: The harness is an always-delivered
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
**Definition-of-Ready** (`br lint` required template sections; a non-empty structured
`acceptance_criteria` field satisfies the `## Acceptance Criteria` section without a body heading —
`br lint` ignores the field and has no config for it, so the credit lives in
`policy.definition_of_ready`, not upstream, basicly-58iu)
→ _[human checkpoint]_ → **fan-out build** (one worktree per dependency-unblocked node, ranked
by `br scheduler`, concurrency-capped) with a **serial merge queue** on the way back →
**Verify** (deterministic, blocking) + **Validate** (acceptance/traceability) → _[human
checkpoint]_ → **Ship** + **Teardown** → **epic retro**. A failed node enters a bounded
**rework loop (n=2)** then escalates to a human; any track can **escalate a tier** (carry work
forward, re-hit only the Decomposition checkpoint) without restarting. Default is
task-by-task; one-shot mode collapses the middle checkpoint. Concurrency cap is configurable
(default 4). The retro emits a findings list; per finding the user picks ignore / fix-now /
fix-later, and a bead is created for everything not ignored. Each _[human checkpoint]_
approval (`policy checkpoint <issue> <name> --approve`) is gated on an interactive terminal:
off a TTY — as any tool-invoked Bash runs — the command refuses and issues a one-time
confirm code that a human must echo back with `--confirm`, so a subagent driving the loop
cannot self-approve ship autonomously. This mitigates the shared-identity gap (a fork and its
human share one OS/git identity); it does not defeat a process deliberately re-running with
the code.

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
git merge-driver, unlike `bd`), never by hand-editing JSONL conflict markers. Git refuses to
update a branch checked out in another worktree, so the merge/ship transitions must run from
the **base checkout**; `advance` refuses the `build` and `ship` phases when invoked from a
linked worktree (git-dir ≠ git-common-dir), blocking cleanly rather than stranding a commit.

**12.6.1 Zero-touch tracker state.** Every loop-provisioned worktree shares the base
checkout's tracker via `br`'s git-ignored `.beads/redirect` file (written at provisioning;
the `beads-commit-msg` hook follows it too), so `br` reads/writes from any checkout hit the
one real DB/JSONL and there is no divergent copy to reconcile. The engine owns the tracker
commits at three points: provisioning commits the claim (so teammates who pull see it from
the moment work starts), the landing advance rolls accumulated `.beads/**` dirt in base into
one `chore(beads)` commit before merging (non-beads dirt still blocks), and ship commits the
close after `br close`. Agents never stage `.beads/` for loop-tracked work, and CI ignores
`.beads/**`-only pushes (the commit-msg hooks are the deterministic floor). A
redirect-capable `br` is a hard requirement of worktree tracker sharing (`br` 0.2.16 is the
known-good floor): a `br` that ignores the file would silently run a divergent tracker, so
provisioning probes `br where --json` from the new worktree and aborts with upgrade guidance
when the answer is not the base `.beads`.

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
differs per agent. Detection (`auto`) walks the big 3 in order (claude → codex → copilot),
selecting the first that is both on `PATH` and **capability-probed** (`basicly-bveo`): the
binary is run with `--help` and auto-selection skips it if the probe positively shows its
assumed headless flag (`-p`, `exec`) is gone — a dropped/renamed flag no longer gets picked
and then fails at dispatch. The probe is conservative (a probe that cannot run assumes
capable, so a flaky probe never false-skips a working agent) and never gates an _explicit_
choice; `runner list` surfaces each runner's PATH + capability. Any other agent is supported
by an explicit `[[runner.agents]]` command template in `basicly.toml`. A runner may pin an
optional **`model`** (`[[runner.agents]] model = "opus"`): the invocation seam folds it into
the command — substituting a `{model}` placeholder when the template has one (the escape hatch
for an agent whose flag is not `--model`), otherwise injecting `--model <value>` right after
the binary; no model leaves the argv unchanged. This is model/agent-property _awareness at the
invocation seam_, not a token-level inference client — per-track model choice stays out of
scope. There is no cross-agent CLI invocation standard, so an unknown agent's command is
**never guessed** — when
nothing matches, selection falls back to a **`manual` handoff runner** that shells out to
nothing and instead surfaces the exact prompt + worktree path,
deferring to the loop's block-and-resume contract and the one thing that _is_ standardized
across agents: the projected `AGENTS.md` guidance. `basicly runner dry-run` prints the exact
command an adapter would execute so it can be verified before any live invocation.
`loop advance` on a ready leaf provisions the worktree
and dispatches the selected runner headless inside it with an agent-neutral prompt (bead id +
`AGENTS.md` + `br show`; merging/pushing/closing stays with the loop), then blocks with the
run outcome; the `manual` handoff runner keeps the block-and-resume contract untouched, and a
failed run blocks with the runner name and exit code. Each dispatch also writes a
metadata-only **run-record** keyed by bead id into the self-ignored `.basicly/usage/`
(`run_record.py`, same atomic tmp-write pattern as `tool-usage`): wall-clock duration, exit
outcome (executed/handoff/failed), agent, the pinned model when the runner sets one, and
token/cost telemetry (`basicly-kjc5.1`, factory design §7.5). For telemetry, a loop dispatch
appends the adapter's usage-report flags (claude `--output-format json`, codex `--json`;
opt-in per call site — rubric judging and catalog review parse plain-text answers and stay
unflagged) and `runner.extract_usage` parses reported tokens/cost from the captured output;
when a CLI reports no usage (copilot, probed 2026-07) or the output does not parse, it falls
back to a chars/4 transcript estimate flagged `estimated` so calibration can down-weight it.
A `[[runner.agents]]` override sets `usage_format` (`claude-json`/`codex-jsonl`) to keep
exact extraction on a custom command. Only metadata is persisted — the command is stored with the prompt argument
elided, never the prompt body or captured output. This is the correlation foundation for
agent attribution, model provenance, and the cross-repo fleet rollup.

**Fleet rollup (`basicly-h0f0`).** `basicly status --fleet [--root PATH]` (`fleet.py`) is the
cross-repo view dimension 3 calls for: it discovers the basicly-installed repos under a workspace
root (immediate subdirs carrying a `.basicly/` dir; default root = the parent of the current repo)
and rolls up, per repo, the single-repo `status` snapshot plus a run-record summary (total runs,
counts by outcome, distinct agents/models) into one versioned JSON payload with fleet totals.
Read-only and resilient by construction: it writes nothing, and a repo whose snapshot raises is
captured as an `error` entry rather than failing the rollup — the command always exits 0. The
per-repo snapshot is produced **in-process** by the current engine (the `status_fn` the CLI
injects, so `fleet.py` never imports the CLI); each repo's payload still carries its own
`installed_version` vs `engine_version`, so version skew across the fleet stays visible. A
human-formatted table and a subprocess-per-repo model (each repo reporting via its own pinned
basicly) are out of scope — this is JSON-first and single-engine.

**Health scoring and behavioral drift (`basicly-y886`).** `basicly health` (`health.py`) turns
the run-record log into a per-agent health signal and a drift check. The signal source is
run-records _only_, by necessity: `br` gate results overwrite (no history, §12.7), so gate
pass/fail over time is not queryable — but a failed dispatch is a `failed` run and a rework
re-dispatch appends another record for the same bead, so the append-only log is a durable proxy
for the gate-fail + rework signal. Per agent it reports the dispatch failure rate (handoffs
excluded — they carry no outcome), a rework signal (beads the agent re-dispatched), and a bounded
`health_score` in `[0, 1]` where failure dominates and rework is a multiplicative drag. Drift is a
**rolling baseline read off the log's own timestamps**, not a stored snapshot: an agent's most
recent `--window` dispatched runs are compared against everything older, and a behavioral
regression is flagged when the recent failure rate exceeds the baseline by a fixed delta with a
minimum sample size in each window. `--fleet [--root PATH]` rolls the per-repo report across the
housed repos (reusing `fleet.discover_repos`). Everything is read-only, deterministic (no
wall-clock enters the payload), and advisory — nothing gates on a score; a token/cost health
dimension over the now-populated run-record telemetry (`basicly-kjc5.1`) and a persisted
time-series/charting layer are out of scope.

**Structured needs-input outcome (`basicly-o774`, D5/D6 convergence).** The stop-instead-of-guess
policy used to be soft prose in the `knowledge-priming`/`decision-protocol` fragments the model
could ignore; this makes it a first-class loop outcome. When a dispatched headless agent cannot
resolve a required fact it writes a small sentinel — `.basicly/usage/needs-input.json`
(`{"fact", "detail"}`) — into its worktree and stops without committing a guess. After a clean
(exit 0) dispatch the loop reads the sentinel (`needs_input.take`), maps it to the existing
block-and-resume contract (`_blocked(..., needs_input=<fact>)`), and **does not land** — the
missing fact is surfaced by `loop advance`/`status` like any other block. The sentinel is
consumed on read (valid or malformed) so a re-dispatch, once the fact is supplied, starts clean;
a missing sentinel is exactly today's "advance again to land it". A file — not a stdout marker —
carries the signal so it survives output redaction/truncation and needs no cross-agent output
convention. Scope is the headless path; the `manual` handoff runner is unchanged (the driver
already surfaces missing facts), and a manual-driver CLI is out of scope. The protocol is
projected into the dispatch prompt and the `harness-loop` fragment so agents know the contract.

**Output redaction and egress (`basicly-3p2i`).** Captured stdout/stderr is redacted at the
source before it enters a `RunResult`: high-signal secret shapes (private-key headers, provider
tokens, secret-named assignments — the sibling pattern set of the `secret-scan` hook, `redact.py`)
are replaced with a labeled placeholder, so no surface (CLI print, loop log) leaks a credential
an agent echoed. Network egress is _not_ sandboxed by basicly — it cannot portably restrict a
generic subprocess; egress control is delegated to the agent-layer sandbox (Codex `sandbox_mode`,
`basicly-t0kt`; Claude/Copilot config).

**Attribution (`basicly-140a`).** At landing the loop reads the bead's latest run-record
and stamps the dispatched runner into the audit trail: the `--no-ff` merge commit carries
`Harness-Runner: <agent>` and (when the run pinned one) `Harness-Model: <model>` git
trailers, and the recorded verify gate carries the agent as its `br gate report --actor`.
So history and the gate ledger distinguish which agent produced a landing instead of
collapsing onto the one human git identity. It is best-effort and non-fatal: with no
run-record the merge message and gate are unchanged.

**Bot identity and the trust model (`basicly-smzg`).** Attribution above is a _trailer_ on
commits still authored by the human git identity. A runner may go further and commit _as_ a
bot: an `[[runner.agents]]` entry may pin an optional **`git_name` + `git_email`** (both keys
or neither — the config parser rejects a lone half). When set, the dispatch seam overlays
`GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` on the agent's
inherited environment (`runner.git_identity_env`), so commits the agent makes in its worktree
read as the bot for both author and committer. It is opt-in and backward-compatible: no
identity configured leaves the child environment untouched. This does **not** relax any gate:
`identity-guard` validates the _effective_ identity git will actually stamp — it resolves the
author and committer via `git var GIT_AUTHOR_IDENT`/`GIT_COMMITTER_IDENT` (env-first, then
config), so a bot email must satisfy `basicly.identityAllowEmail` exactly as a human's would
(a disallowed bot email is blocked). Validating config alone would have missed the override,
since the env vars change what history records without touching config. The **append-only
tamper-evidence trust model** is the layering of the existing controls, not new enforcement:
the per-repo `identity-guard` gate bounds _who_ a commit may claim to be, and optional commit
signing (`git config commit.gpgsign true` + a `user.signingkey`) makes each commit
tamper-evident — with the permissions deny-list forbidding `--no-verify`/`--no-gpg-sign` as the
floor so the gates cannot be skipped. basicly does not _force_ signing (key management is
per-machine and out of a portable catalog's reach); it documents enabling it and guarantees
that once enabled it cannot be bypassed through the harness.

**12.9 Ship.** Ship is parameterized by the entry branch recorded at Intake: default → merge
to `main` + push `main` (no feature branches on the remote); if the entry branch is a feature
branch → merge to it, push, open a PR to `main`. Delivery is incremental per feature; teardown
follows each feature's merge.

**12.10 Reuse & positioning.** basicly's harness is a lean, clean-room, `br`-substrate-native,
agent-agnostic re-founding of the same goal as the sibling `agent-harness` (basicly's first,
company-owned, lefthook/pinned-pack, tracker-abstracted attempt): borrow its battle-tested
worktree/merge know-how (copy-mode deps, `git merge-tree` pre-flight probe, mode-aware
cleanup) as a reference, while keeping the **`br`-wrapping engine + agent-agnostic projection +
installable composable distribution** as the differentiators. From beads-blueprint, adapt
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
