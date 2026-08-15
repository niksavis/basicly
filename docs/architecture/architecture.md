# basicly Architecture

> **This document is the single authoritative architecture reference for `basicly`.**
> Beads (`br`) issues are broken down directly from it (§10). Where it and any other
> document disagree, this one wins.
>
> **Its relationship to [`docs/requirements/`](../requirements/)**: a design document explores
> and settles _one_ question in depth — the factory, the roster, gates and rework,
> catalog efficacy, steering surfaces, the tracker. This file carries the _result_:
> the current system in §§0–13 and the agreed direction in §14. A design document is
> therefore the detail behind a §14 row, never a competing account of how the system
> works, and it is archived once absorbed. Evidence for the design documents lives in
> [`docs/research/`](../research/2026-07-26-sota-review.md), and the order the §14 rows
> get built in lives in [`docs/plan/`](../plan/implementation-plan.md).
>
> **Status convention — two states, never blurred.** §§0–13 describe the system
> **as it exists in code today**; deliberate small omissions are marked
> **[Deferred]** and collected in §11. §14 describes the **target state** — where
> the architecture is going — and is the only forward-looking section. §15 is a
> **derived view** of those two: the per-capability roadmap the README and the
> landing page render, adding no claim of its own.
>
> A claim in §§0–13 is a statement about running code. A claim in §14 is a
> statement of intent with a design document behind it. Reading the second as the
> first is the specific error this convention exists to prevent: a decision
> recorded in a design document is not evidence that it is enforced anywhere.
>
> **How to read this document**: each numbered Part opens with a short **Summary**
> you can scan alone to get the full picture, followed by **Details** you only need
> when implementing or debugging that part. Skip straight to the Part you need.
>
> **This is the reference quadrant, not the entry point.** A consumer starting from
> `basicly install` wants [`docs/tutorial/first-loop.md`](../tutorial/first-loop.md)
> and then [`docs/how-to/`](../how-to/); §13.1 records how that layer relates to
> this one.

## 0) Idea

`basicly` is a **harness for coding agents that ships its own development
process**. It is distributed as a curated, versioned catalog a repository installs,
customizes, and projects into the native context files each coding agent actually
reads — and, in the same package, the workflow that drives work through those
agents plus the state that workflow runs on.

That is deliberately more than a guidance bundle. Four pillars, each independently
useful and only jointly sufficient:

1. **Catalog — guidance** (suggestive, non-deterministic). Fragments and skills:
   Markdown a model reads and may or may not follow (§§4–7).
2. **Gates — enforcement** (deterministic). Git hook scripts and the verify
   pipeline, which mechanically block a bad commit, push, or landing regardless of
   whether the model read or followed the guidance (§4.2, §6).
3. **The loop — an SDLC of its own** (deterministic engine, agent-supplied
   judgment). Intake → classify → decompose → build → verify → validate → ship,
   driven by `basicly loop` over the tracker, run single-track or as
   parallel lanes behind a supervisor and a serial merge queue (§12). Teardown is
   folded into the ship advance and the retrospective is a conditional process over
   the gate-failure ledger, not a rung (§12.13); neither is a phase.
4. **The tracker — work state as a graph.** Issues, dependencies, gates,
   checkpoints and evidence, from which the loop **derives** the current phase
   rather than remembering it (§12.1).

Pillars 1 and 2 are the classic harness bargain, and both halves must be present:
guidance without gating is easily ignored; gating without guidance gives the agent
no context for _why_ a check exists or how to satisfy it up front.

**Pillars 3 and 4 are the distinction.** A harness that ships agents, skills, and
automation scripts tells an agent _how to work_. `basicly` additionally owns _the
process_ and _the state_, and enforces both in code:

- **The process is a command, not a procedure in prose.** Anything fully
  deterministic is executable as one command an agent triggers and waits on. If an
  agent must perform a _sequence_ of mechanical steps, the engine is missing a
  command — and the tokens, latency, and chance of getting a mechanical step wrong
  are all waste (§12, D10).
- **The state is derived, not remembered.** Because phase is a pure function of
  tracker state and the engine keeps no side-state, a crashed, switched, or
  compacted session resumes by re-reading the tracker. This makes a whole class of
  orchestration failure — re-dispatching already-completed work after losing the
  thread — structurally impossible rather than merely unlikely.
- **Authority is asymmetric.** Agents propose; the engine disposes. No model holds
  authority over the tracker, the schedule, or a required gate, at any autonomy
  level.
- **Enforcement is code, not a request.** The model choosing to run a formatter is
  a different thing from the formatter running automatically. Where a hook can
  enforce a rule, the rule is a hook and the prose only points at it (§3.1).
- **The phases are engine code, and deliberately not configuration.** Decided
  2026-07-30 after two independently-built projects were reviewed that had made
  phases declarative — a YAML node DAG and an engine driving a lifecycle declared in
  front matter. Most rungs of `derive_phase` are mechanical enough to express as
  data; the `verified` term is not. It reads
  `gates.can_advance and (worktree is not None or has_children)`, and the ship rung
  adds `gates.can_advance and (worktree is None or verified)` — together the
  _landed_ invariant found by incident `basicly-k35r`, where approving ship before
  the landing wedged the phase with no route back to the merge. The leading
  `gates.can_advance` on the ship rung is the second half of that invariant, added
  after `basicly-jr0l.49`: a missing binding does not by itself mean _torn down
  after the merge_, because a leaf that never built has no binding either, so
  without it an out-of-order ship approval closed an unstarted bead with zero work
  done. The green required gate is the discriminator — the build→verify landing
  records it and nothing else does. In YAML that becomes a boolean expression
  language, and the invariant then lives where the type checker cannot see it, the
  test suite cannot easily target it, and review will not catch a subtle edit. The
  general form is worth stating once, because it applies past this decision:
  **every rule that moves from code to data leaves the type checker, the test
  suite, and code review.** What a consumer would plausibly want to vary — required
  gates, the rework cap, verify checks per mode, autonomy levels — is already
  configuration in `basicly.toml`.

The four pillars are also the axis along which this document is organised: §§4–11
are the catalog and its projection, §12 is the loop and the tracker it runs on, and
§14 is where each pillar is going next.

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

The four goals the loop and the tracker add, which a guidance-only harness does not
have to meet:

1. A unit of work can be driven end to end by any supported agent, and the phase it
   is parked in is **derivable** from the tracker alone — so a session can be
   crashed, compacted, or swapped for a different agent family mid-track and
   resumed without replay.
2. Every deterministic step in that workflow is reachable as **one command**; no
   agent is asked to remember a mechanical sequence (§12, D10).
3. A required gate can only ever be passed by deterministic checks. Judged
   verification is advisory or routes a decision — never a green light.
4. The harness's own claims are **measured, not asserted**: a rule that no longer
   binds and a skill that never fires both cost context on every turn and deliver
   nothing, and neither is currently visible. Closing that gap is §14.4.

## 2) Overview

The system has **two planes**. The _distribution plane_ below turns authored
catalog sources into the files agents read and the hooks that bind them; the
_execution plane_ (§12) drives a unit of work through the loop over the tracker.
They meet at exactly two points: the loop dispatches agents whose context is the
projected guidance, and the loop's verify step runs the same gates the hooks run.

### Distribution plane

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
a **fragment** (core, never touched directly, or overlay, always theirs). See §11 for
the small pieces of the projection deliberately not built yet.

### Execution plane

```text
  TRACKER — the only state; phase is derived from it, never remembered
  ┌──────────────────────────────────────────────────────────────────┐
  │ issues · dependency graph · gates · checkpoints · evidence       │
  └───────────────┬──────────────────────────────────┬───────────────┘
        derive    │                                  │  record
        phase     ▼                                  ▲  (engine only)
  ┌──────────────────────────────┐                   │
  │ Engine — deterministic       │                   │
  │ basicly loop / loop supervise│───────────────────┘
  │  ranks the ready set,        │
  │  provisions worktrees,       │        ┌────────────────────────┐
  │  assembles dispatch bundles, │───────▶│ Agent — a pure function│
  │  validates every proposal,   │◀───────│ fresh context, headless│
  │  lands through a serial      │        │ Claude / Codex / Copilot│
  │  merge queue                 │        └────────────────────────┘
  └──────────────┬───────────────┘         proposes: plan, commit,
                 │                          decision, verdict
                 ▼
  GATES — deterministic; the same checks the hooks run
  verify (fast | full | staged) + validate; a required gate
  is never passed by a model, at any autonomy level
```

The engine disposes and the agent proposes: input is a bundle assembled
deterministically from tracker state, output is a structured proposal the engine
validates against policy before it becomes state. Human checkpoints sit at classify,
decompose, and ship; an explicit, auditable autonomy grant can delegate some of them
to a decider agent, and no grant can delegate a required gate.

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
[`loader.py`](../../src/basicly/loader.py) and
[`planner.py`](../../src/basicly/planner.py) work.

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

Lives at [`src/basicly/`](../../src/basicly/): `cli.py`, `config.py`,
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
    models/{anchors.yaml,model-map.json,model-map.schema.json}
    permissions/permissions.yaml      # agent-permissions deny-list, see §8
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

**Skills**: `core/skills/` is the catalog location. Sources are authored as `skill.yaml` (name, an
`invocation` axis, description, and an
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
basicly-internal (§9 scoping) and is never emitted.

**The invocation axis** (`invocation`, required, `model` or `user`) declares whether
anything can route to an entry. A **model-invoked** skill keeps its `description`, is
advertised to the agent, and therefore pays context load on every turn; a
**user-invoked** skill carries no description at all, costs nothing until a human types
it, and the empty pairing is enforced by `catalog lint`. It is declared rather than
inferred because "does this entry route correctly" is not a well-posed question until
the entry says whether routing applies to it — which makes this the prerequisite for
the routing evals of §14.4. Implemented in
[`skills.py`](../../src/basicly/skills.py). The projected skill directory is a pure
projection target owned wholly by basicly and is **mirrored**: `skills-check` flags a stale or
orphaned resource, a rebuild prunes a resource dropped from the source, and deselecting a
skill's technology prunes the whole directory. The **root** is owned too: `skills-check` also
reports any entry there that no source accounts for — a hand-authored `SKILL.md`, a loose
`README.md`, a projection whose source was deleted — since otherwise a skill the projector
never knew about passes every gate while reaching only one agent (`basicly-tcmy.8`). It
reports and never prunes those, because nothing describes them and the projected copy is the
only one. `catalog lint` enforces the spec's naming rules
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
`pragma: allowlist secret` escape for reviewed false positives;
`internal-info-scan.py` — its sibling for internal-only identifiers (a company
domain, an internal host, a machine username, a private repo name), which
publish silently because they read as ordinary text to anyone who does not
already know they are internal. Its denylist is **not** in the script: a gate
hard-coding the strings it suppresses would publish them into this repo and into
every consumer that installs the catalog, so the tokens live in the gitignored
`basicly.local.toml` as named `[[privacy.denied]]` rules and the report prints
only the rule name — pre-commit also runs in CI, whose logs are public. Inert
until configured, so a consumer is never blocked by a list it did not write)
plus agent-side
hooks (`protect-generated.py`, `tool-usage.py`) — as
first-class catalog artifacts — the deterministic, gating counterpart to
fragments/skills — described tool-agnostically in `core/hooks/hooks.yaml`.
(`identity-guard.py` blocks a commit whose git identity is unset or a hostname
fallback — a generic, no-personal-data gate; the `.scripts/setup_git_identity.py`
helper and the `tool-git` skill cover the per-host identity setup it guards.) This
repo dogfoods them directly: [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)
points straight at `core/hooks/*.py`. `basicly hooks-build` projects the manifest
into a consumer's `.pre-commit-config.yaml` and then runs `pre-commit install` so the
gates are active — not merely written; a gate that is shipped but never installed is
inert, the exact failure that once let unguarded commits through (§8).

**Why pre-commit and not lefthook** (decided `basicly-3s2p`; absorbed here 2026-08-08 when
the standalone decision note was deleted). The hooks are already runner-agnostic —
`hooks.yaml` is a tool-neutral spec list and every hook script is standalone Python with no
pre-commit API — so the only pre-commit-specific code is the projection layer in
`hooks.py`. The decisive fact is that **every projected hook runs `uv run python <script>`**:
uv and Python 3.14+ are required of a basicly committer whatever orchestrates the hooks, so
lefthook's headline advantage — a static Go binary with "no runtime dependency" — buys this
project nothing, while adding a binary-acquisition problem with no uv-native answer.
pre-commit rides the uv channel consumers already have.

Reopen the decision only if one of these changes: consumers stop reliably having uv on
`PATH`; basicly drops the Python/uv requirement for the checks themselves; hook execution
speed becomes a **measured** complaint that parallelism would fix; or pre-commit's
provisioning seam regresses beyond what the `uv tool run` fallback covers. The
runner-agnostic seam (the `manager` field, API-free scripts) is kept precisely so this stays
cheap to reopen. The manifest's
`manager` field routes each hook to one of three surfaces: `git` (the pre-commit
config), `claude` (agent hooks in `.claude/settings.json`; the event derives from the
spec `stage`, with an optional per-spec `matcher`), and `copilot` (managed
`.github/hooks/basicly-<id>.json` files). The `tool-usage` hook rides both agent
managers: a PostToolUse counter tallying every shell command's pipeline heads into the
self-ignored `.basicly/usage/tool-usage.json` — token-free telemetry for culling idle
tools/skills from the catalog with real data. A head is resolved _past a wrapper_ (`uv`,
`npx`, `env`, and their subcommands, flags, flag values and `VAR=val` prefixes), so the
wrapped tool is credited too and not only the wrapper: `env -C /repo uv run pytest -q`
records `env`, `uv` and `pytest`, where it used to lose `pytest` entirely.

**Model map**: `core/models/` resolves the portable **model tier** a catalog source
declares (§5) to a concrete model. `anchors.yaml` is the reviewed input — one anchor
model per (tier, vendor), plus the surface table and the capability rule — and
[`.scripts/generate_model_map.py`](../../.scripts/generate_model_map.py) resolves it
into the generated `model-map.json`, validated against its published
`model-map.schema.json`. Three axes, because all three change the answer: **tier x
vendor x surface**, which is 4 x 4 x 2 = **32 cells, 5 of them unavailable** (measured
2026-07-31). Cost _and_ token limits are recorded **per surface** rather than per
vendor, because both genuinely vary there — `gpt-5.6-luna` costs 0.2/1.2 USD per MTok
on `openai` and 1/6 on `github-copilot`, and `github-copilot` caps
`claude-haiku-4.5` input at 136,000 tokens where the Anthropic surface publishes no
input cap. An unavailable cell records a `status` and a `reason` and deliberately
carries **no `model` key**, so a consumer reading `["model"]` fails loudly instead of
being silently demoted onto another tier's model. Two constraints keep it inside §3.8:
the generator fetches models.dev at **authoring and check time only, never in the
dispatch path**, so nothing that dispatches an agent depends on the network and there
is deliberately no `[[verify.checks]]` entry for it; and `--check` **reports** drift
and never writes, because a community-contributed upstream edit must surface as a red
check rather than as a silent change to which model runs someone's code. The committed
map's _shape_ is gated offline by `tests/test_model_map.py`. Being under `core/`, the
map ships in the wheel and `basicly install` materializes it into a consumer repo (§9).
`basicly.models` reads it at dispatch to resolve a declared tier into the one id the
target surface accepts, and refuses the dispatch when the cell is unavailable rather
than substituting another tier's model (§12.8). The lookup is a plain read of committed
data, so the dispatch path stays offline and deterministic.

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

**Per-lane drop-in fragments — `basicly.d/<bead-id>.toml`** (committed, layered
between the two files above): a lane declares the `[[verify.checks]]` entry it wires
and the `[ratchet.<gate>]` numbers its change moved in a file named after its own
bead, so two lanes can never write one file — the same construction `changelog.d`
uses for `CHANGELOG.md` (`basicly-4746`), applied to the two anchors that bounced
three of five lanes on the 2026-08-08 pass (`basicly-ef7t`). Checks are **appended**
in filename order rather than replacing, because a fragment is one lane's addition;
the overlay above still replaces, because that is the machine saying _instead_. Every
ratchet number is a **delta** (`basicly.dropin`), never a total: two lanes each adding
one suppression both record the total 16 and the merged tree holds 17, whereas
addition composes in any landing order. Each fragment goes through the same schema as
`basicly.toml`, so a typo there is refused rather than ignored, and the pre-commit
hook runner assembles the same set so a lane's gate binds at commit time too.

**Both files are schema-checked on every load** (`config.CONFIG_SCHEMA`): an
unrecognised section or key raises, naming the file, the containing section, what
that section accepts, and which sections accept a name like it. A key the engine
ignores leaves the file stating one behaviour and the engine performing another,
and in the gitignored overlay there is no diff to review and no other gate — the
symptom is only ever the default the key was written to replace. The schema is an
allowlist over the whole config _surface_, not over this module's loaders: two
entries (`[[verify.checks]]`, re-parsed by the pre-commit hook runner, and
`[[privacy.denied]]`, read only by `internal-info-scan.py`) have no reader in
`config.py` at all.

**Which schema does the checking is a property of the tree, not of the process.** A
repo that ships its own `src/basicly/config.py` — this one, and each of its lane
worktrees — is checked against the `CONFIG_SCHEMA` declared in _that_ file, read
statically on every validation. Without this, `loop advance` could not land
a lane that adds a key: the landing runs from the base checkout, so the engine
validating the lane's `basicly.toml` is the pre-merge one, and it refused a name the
lane's own code introduces one commit later (basicly-69az, reproduced four times).
Static because the tree under test has not merged yet — importing it would run a
second engine inside the process landing it, and the question here is a set of names,
not behaviour. It fails closed: a schema this reader cannot model falls back to the
running engine's, and the refusal then names the ordering rule instead of reading as a
typo. A consumer repo ships no engine source and is unaffected.

_Forward compatibility._ The refusal is unconditional — no warn-then-error staging,
no narrowing to near-misses of a known key — so a repo pinned to an older basicly
whose config carries a key added since fails until it upgrades or removes the key.
That cost is accepted because the alternative is the silent divergence above, and
it is bounded by the message, which names the engine's version and says upgrading
is one of the two fixes. Staging was rejected as unendable (the engine ships from
`main`, so a warn phase has no graduation point) and unread (the reported incident
already printed a visibly wrong number that was skimmed past); near-miss narrowing
was rejected because it leaves a genuinely novel key silent, which is the same hole
one generation on. Surfaced by every command, and as a first-line verdict rather
than a traceback in `basicly loop preflight`.

#### 4.4 Generated artifacts

```text
AGENTS.md                                    # applies_to: [all]; inlines scoped fragments (our scopes are globs; codex scopes by directory)
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
fragments inlined because its only scoping axis is directory placement while our
scopes are globs, and because a nested `AGENTS.md` below the cwd is not loaded at
all (§7 detail 4). A native per-path renderer is **[Deferred]** (§11). Note that
Codex's own feature named **"Rules"** (`.codex/rules/`, Starlark
`prefix_rule(pattern=…)`) is a sandbox command-execution policy, not a per-file
instruction tier — the name collides, the mechanism does not.

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

Confirmed current schema ([`schema.py`](../../src/basicly/schema.py)):

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
  agents never silently inherit every tool. `tier` names the **model tier** the
  agent needs (`low`, `medium`, `high`, `maximum` — roster design R5),
  single-sourced from `schema.MODEL_TIERS` into a `tier` enum on
  `agent.schema.json` and kept in step by a tripwire test. It is **never
  emitted**: no family receives a `model` line, because a provider model id is
  not portable across agent families (models.dev spells one model
  `claude-haiku-4.5` for Copilot and `claude-haiku-4-5` for Anthropic); the
  resolution from a tier to a concrete per-surface id lives in the model map
  (§4.2). Replacing `model` with `tier` was a **breaking change** to the source
  format, so the deprecation is engineered rather than just documented: `model`
  is retained as a **deprecated** property on the schema — which sets
  `additionalProperties: false` — purely so `catalog lint` owns the actionable
  message ("declare the portable model tier instead") in place of a bare
  "additional properties are not allowed", and `model` stays in
  `agents.RESERVED_FRONTMATTER_KEYS` so the `claude:` passthrough cannot smuggle
  a provider id back in. A `claude:` map
  passes Claude-only frontmatter (e.g. `memory`, `maxTurns`) through verbatim.
- **Emission**: two roots, both written by `agents-build` and both compared by
  `agents-check` (`agents.AGENTS_OUTPUT_ROOTS`) — `.claude/agents/<slug>.md` for
  the Claude family and `.github/agents/<slug>.agent.md` for GitHub Copilot, the
  second added in `basicly-8sxf` (2026-07-31) after `basicly-ajq`'s single root
  was reopened with measured facts: the Copilot **cloud** agent reads only its own
  root, the Copilot CLI's discovery of `.claude/agents` is real but
  **undocumented**, and Copilot custom agents support a `tools` allowlist so the
  read-only posture check survives the crossing. The double-load worry does not
  materialise — GitHub documents the config file name minus `.md`/`.agent.md` as
  the deduplication key, so the two files collapse to one agent. There is
  deliberately no root-selection flag (contrast `skills-build
  --all-default-roots`): a root only some commands write is how a second root
  drifts unnoticed. Only the Claude root receives the `claude:` passthrough; no
  root receives a `model` line. Rendered files carry the generated marker inside
  the `protect-generated` hook's scan window, so tool-time edits are blocked in
  both roots. **The remaining native subagent root is declined, not overlooked**:
  `.codex/agents/*.toml` was decided against in `basicly-crkl` (2026-07-31):
  its documented field set (`name`, `description`, `developer_instructions`)
  has no `tools` equivalent, so a codex copy would silently drop the mandatory
  allowlist the lint checks against a `Read-only` posture — a lost guarantee,
  not a format cost — while forking the renderer, the drift check and the
  generated marker. The roster that grows this tier is deliberately
  catalog-source prompts rather than agent-native files
  ([plan](../plan/implementation-plan.md) Phase 5), so codex receives the same
  guidance through `AGENTS.md` and `.agents/skills`. No root costs always-on
  budget, and the saving is structural rather than a matter of size: only an
  agent's **name and description** load at session start, the **body never enters
  the parent's context**, and **only the final message returns** — a subagent runs
  in an **isolated context window**, so a dispatch's working set is never charged
  to the session that spawned it. (**Verified 2026-08-09 against Claude Code
  2.1.226**, upgrading this from the first-party-guidance claim it was recorded as
  on 2026-07-26. All four hold. It is the fact that refutes any claim that
  concurrent subagents must share one window — see `factory-loop.md` D24.)
- **Agent and hook definitions hot-reload; they are not read once at process
  start** [M, 2026-08-09, claude 2.1.226]. Both `~/.claude/agents/` and
  `.claude/agents/` are watched and an added or edited file is picked up within
  seconds, as is hook config in a settings file. **One exception, and it is exactly
  the case a first install creates**: the first file in an `agents/` directory that
  did not exist at session start. Clearing the conversation reloads neither and is
  the wrong lever — it is what a consumer reaches for first, so the tier kit's
  notice says so.
- **Skill scope precedence is the inverse of agent scope precedence**, and for a
  distribution tool that asymmetry is load-bearing [M, 2026-08-09]. Agents resolve
  managed > `--agents` > **project** > user > plugin; skills resolve enterprise >
  **personal** > **project**. `basicly install` writes a consumer's _project_
  `.claude/skills/`, which is the **lowest-priority writable scope** — so a
  developer's `~/.claude/skills/<same-name>` silently overrides a skill we shipped
  them, while an identically named agent would not. Nothing we ship makes that
  visible to the consumer, and the surface freeze cannot promise stability for a
  scope it can lose (`plan` §7).
- **Tool names** are _not_ translated. GitHub's published alias table accepts
  Claude's PascalCase names as first-class and matches case-insensitively, so the
  names a source declares resolve on both families. The table is pinned as
  reviewed data in `agents.COPILOT_TOOL_ALIASES` (reviewed 2026-07-31) for two
  reasons: it drives the read-only posture check (every alias of Copilot's `edit`
  primary fails it), and it lets lint refuse a name that resolves to nothing,
  because Copilot drops an unrecognised entry with no error where Claude Code
  refuses to launch and names it. An unrecognised entry fails **safe** (no
  grant-all fallback), so the residual risk is a useless agent, not a lost
  guarantee. What the allowlist does not control on Copilot is recorded beside the
  table: `skill` and `sql` are granted unconditionally, `Bash` expands to four
  tools, and `NotebookEdit` alone resolves to both `create` and `edit`.
- **Lint** (`catalog lint`): schema validation for both source kinds, plus
  composition rules — block refs must resolve, every declared tool must resolve
  through the pinned Copilot alias table, a `Read-only` posture may not grant
  write tools, and the composed body must stay under 30,000 characters (the
  strictest reader's prompt ceiling).

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

1. **Size discipline**: a **shared soft cap of 9,000 chars** for the `claude` and
   `copilot` targets, and **12,000 for `codex`** (`max_size_warning` per
   `targets/*.yaml`; raised from 8,000 in `e70e4db`). The cap counts **characters**,
   not bytes — `cli.py` compares `len(content)` on the decoded string, so a `wc -c`
   reading overstates a UTF-8 baseline by the multi-byte characters in it. The
   shared-baseline reasoning still holds — all three always-on
   files project from the same `applies_to: [all]` fragment set and differ only by a
   small per-target defaults fragment — but the codex projection legitimately carries
   more: scoped fragments are inlined there for glob fidelity (detail 4), so its cap
   gets a documented allowance rather than a pretense of identical content. The numbers
   are a deliberate discipline choice, not platform limits: Claude Code's own
   degradation warning is ~40 KB, GitHub removed its former 4,000-char hard limit on
   `copilot-instructions.md` (it now only advises shortening past ~4,000 chars), and
   Codex reads AGENTS.md up to `project_doc_max_bytes` (32 KiB default, configurable;
   verified 2026-07-31). A cap warning means split into a scoped rule, not shrink the prose.
   (Refs: GitHub removed the hard limit — github/docs#42761; Claude ~40 KB — Claude
   Code memory docs; Codex 32 KiB — learn.chatgpt.com/docs/agent-configuration/agents-md.)

   Measured from the projected files themselves and regenerated by
   `.scripts/docs_claims.py`, which gates this block on every commit:

   <!-- docs-claims:begin always-on-sizes -->

   | Surface | chars | cap | headroom |
   | --- | --- | --- | --- |
   | `.claude/CLAUDE.md` (claude) | 8895 | 9000 | 105 |
   | `AGENTS.md` (codex) | 14343 | 16000 | 1657 |
   | `.github/copilot-instructions.md` (copilot) | 8994 | 9000 | 6 |

   <!-- docs-claims:end always-on-sizes -->

   So **`copilot-instructions.md` is the tightest always-on surface** and binds for an
   always-on fragment, while **`AGENTS.md` binds for the path-scoped tier** — a scoped
   fragment costs `AGENTS.md` 900-1600 chars and costs the other two nothing. **Four
   fragments are scoped, not three** [M 2026-08-14]: `platform-hermetic-tests`,
   `external-review`, `code-is-authoritative` and `model-tier-routing`.

   **The codex cap moved from 12,000 to 16,000, and the reason is a finding rather than
   an allowance** (`basicly-a3ab.1`, `codex.yaml:9`). The audit run on the 13,135-character
   overrun found the excess **is** the scoped tier: claude and copilot receive those four
   fragments as separate `paths:`-carrying rules files, codex has no glob-based instruction
   scoping and inlines them, so the gap is structural to one target. Evicting always-on
   lines would have charged all three families to fix one and left the cause standing. What
   the move trades away is stated where it was made: the old cap also stood proxy for the
   vendor's claim that adherence degrades with length, which this repo has never measured
   (`basicly-agzx.1`).
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
   scoped fragments are still inlined there, for two reasons (re-verified 2026-07-31
   against OpenAI's docs). First, a framing correction: Codex is **not** short of
   separate steering files — it supports nested `AGENTS.md` (root→leaf concatenation,
   nearest file wins), `AGENTS.override.md` precedence,
   `project_doc_fallback_filenames`, repo-checked-in Agent Skills (SKILL.md open
   standard, discovered from `.agents/skills` at repo root/cwd with progressive
   disclosure — basicly's skill projection already targets this), project subagents at
   `.codex/agents/*.toml`, and sandbox policy at `.codex/rules/`. What it has is a
   **type mismatch and a loading limit**:
   1. **No glob- or pattern-based instruction scoping exists at all** — no `applyTo`, no
      `paths:` frontmatter, no `globs` field, anywhere in `AGENTS.md` discovery, the
      config reference, or `SKILL.md` frontmatter. Directory placement is the only
      scoping axis, while basicly scoped fragments are **glob-based** (`**/*.py`), so a
      per-directory offload cannot faithfully express a glob scope.
   2. **A nested `AGENTS.md` below the cwd is never loaded.** Codex walks from the
      project root down to the current directory and _stops there_. Run from the repo
      root — the normal case — a file at `src/foo/AGENTS.md` contributes **nothing**.
      So per-directory offload would not merely lose glob fidelity; it would usually
      not load. (Exception: Codex code review on GitHub does walk the changed files, so
      that surface behaves differently from the CLI.)

   Inlining therefore remains the correctness-preserving choice. This is why
   `AGENTS.md` runs larger than the other two baselines and why the codex cap carries
   an allowance (detail 1). Offloading via nested `AGENTS.md`/skills for
   directory-shaped scopes is **[Deferred]** (§11), and reason 2 is what makes that
   reject stronger than a fidelity trade-off.

   Two naming traps on the codex surface, both of which have misled a reader here:
   Codex's own **"Rules"** feature (`.codex/rules/`, Starlark `prefix_rule(pattern=…)`)
   is a **sandbox command-execution policy**, unrelated to per-file instructions —
   reading that page and concluding Codex has no instruction rules is the exact wrong
   turn; and file-based **custom prompts** (`~/.codex/prompts`) are **deprecated** in
   favour of skills and are user-scope only, so they can never ship in a repo — worth
   remembering if a codex command tier is ever proposed. (Refs:
   learn.chatgpt.com/docs/build-skills;
   learn.chatgpt.com/docs/agent-configuration/agents-md; agentskills.io. The former
   `developers.openai.com/codex/*` paths 308-redirect to `learn.chatgpt.com/docs/*`.)
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
(`worktree`, `verify`, `commit`, `policy`, `decompose`, `loop`, `runner`, `rubric`). The authoring
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
| `basicly brief <issue-id>` | Prints the brief the loop would dispatch for one issue, without dispatching it. Shares `dispatch_brief.dispatch_prompt` rather than re-rendering it — a preview that differs from the dispatch is worse than none. Cross-lane records and answered decisions are not folded in: those are assembled at dispatch time against the session's live bead set (§12.8, `basicly-a4q3.5`) |

**Catalog**:

| Command | Behavior |
| --- | --- |
| `basicly build [--target NAME] [--verify]` | Renders enabled targets (or one), writes only changed bytes, updates the manifest, warns on size-cap overrun; `--verify` runs `catalog verify` first and writes nothing on failure |
| `basicly check` | Byte-for-byte staleness check of generated files + manifest; exit `1` on mismatch, no auto-fix |
| `basicly skills-build [--root ...\|--all-default-roots]` / `skills-check` | Same build/check contract, applied to the skill catalog |
| `basicly agents-build` / `agents-check` | Same build/check contract for the agent catalog: composes slot blocks into `.claude/agents/<slug>.md` and `.github/agents/<slug>.agent.md`, always both roots and with no root-selection flag (§5 agent composition model) |
| `basicly hooks-build [--no-install]` / `hooks-check` | Materializes catalog hook scripts, merges a managed `repo: local` block into `.pre-commit-config.yaml` (foreign hooks preserved, idempotent), and then runs `pre-commit install` for every managed stage so the gates are actually active (`--no-install` skips activation; graceful when pre-commit is absent). `hooks-check` reports projection drift and warns (non-fatal) when the git hooks are not installed. It skips the script content comparison when the installed catalog and the target are the same working-tree-relative path in the same git repository — basicly installed editable from its own checkout, where a difference is uncommitted or branch-local work rather than drift, so a hook-script change could not otherwise pass its own landing verify — and falls back to comparing whenever git cannot answer; path equality is required as well as repository identity, so a consumer with an in-repo `.venv` keeps the gate |
| `basicly permissions-build` / `permissions-check` | Projects the catalog agent-permissions deny-list (`.basicly/core/permissions/permissions.yaml`) into the co-owned `.claude/settings.json` `permissions.deny`, the way hooks are managed: ensure-present (managed patterns merged in, consumer-added entries preserved, nothing pruned — an extra deny is fail-safe and a flat deny string has no per-entry marker), with a semantic subset-match drift check. Claude-only: Copilot CLI has no config-file deny (session-scoped `--deny-tool` flag only) and Codex forbids project-scope override of `sandbox_mode`/`approval_policy`, so those guardrails are invocation-only — the copilot runner injects the deny-list as `--deny-tool` flags at dispatch (`basicly-lqz5`), while Codex sandbox/approval defaults remain to wire (`basicly-t0kt`) |
| `basicly usage report` | Reports the tool/skill counts recorded by the `tool-usage` agent hook (token-free telemetry in `.basicly/usage/`) and names never-used catalog skills — the culling input (§4.3) |
| `basicly usage forecast` | Reports the forecast error per dispatch — actual spend over forecast working set, per bead/class/model, with a median — from the run records and the committed `[harness-run]` markers. Refuses to compute an error for a record missing either half and reports those as unpaired counts, so an empty report explains itself (§12.8.1, `basicly-jr0l.34`) |
| `basicly usage tuning` | Advises every governed factory parameter from the recorded dispatches, over the local run records **and** the committed `[harness-run]` markers, naming which corpus each sample came from. Per parameter it prints the value in force for the dispatches it summarises (a session override puts its dispatches in their own cohort), the outcome distribution under that value, and a recommendation with its sample size labelled `measured` or `seeded` — below `[policy.sizing] calibration_min_samples` the declared prior stands and the row names the in-force value it would displace. A parameter the ledger records nothing about still prints, with a sample size of zero, no recommendation and the reason it has none. **Advisory only: it writes nothing** (§12.8.3, `basicly-3ifz.1`) |
| `basicly usage lane-split` | Splits each persisted lane transcript into a **context acquisition** share and an **implementation** share, so `basicly-ejdm`'s causal claim — that a lane's floor is bought by the dispatch instruction rather than by the work — has an instrument behind it before a remedy is judged by it (§12.8.4, `basicly-ejdm.2`) |
| `basicly usage outcomes` | Reports how every recorded dispatch ended — the `handoff`/`executed`/`failed`/`unstarted` distribution from `run_record.outcome_of` over the local run records — with the failure share as an explicit rate. The kill rate is the point: a harness whose lanes mostly return no-go is working correctly and looks, per lane, like a run of failures. Prints its own boundary, because these are dispatch outcomes and no record here says whether a lane reached a result (§12.8, `basicly-a4q3.5`) |
| `basicly usage tracker [--promote] [--refresh-surface] [--as-json]` | Reports the measured `br`/`bv` surface Phase 6 freezes its replacement scope from. `--promote` folds the spool into the committed ledger before reporting, `--refresh-surface` re-probes `br`/`bv` `--help` and rewrites the committed surface inventory (needs `br` on PATH), `--as-json` emits the whole report for the freeze |
| `basicly catalog list [fragment\|skill\|agent]` | Table of catalog sources of the given kind (default `fragment`); the authoring/inspection verbs live under the `catalog` group |
| `basicly catalog new <fragment\|skill\|agent> NAME [--category C] [--description D]` | Scaffold a new `<id>.fragment.yaml` / `skill.yaml` / `agent.yaml` source (§4.2 source format); `--category` sets a fragment's category, `--description` seeds the one-line summary |
| `basicly catalog lint` | Source-format gate: schema validation, no `.md`-named sources, single `.yaml` extension; wired as a pre-commit hook and CI step |
| `basicly catalog verify` | Deterministic content checks beyond the load-path validation: duplicate bodies, contradictions, ambiguity, scope overlaps (§6); named `catalog verify` because `basicly verify` is the loop check runner |
| `basicly catalog review [--runner NAME] [--dry-run]` | Advisory agent-assisted semantic review: renders the always-on files, dispatches a review prompt via the agent-agnostic runner (handoff when no CLI is on PATH), always exits 0. `--dry-run` prints the prompt without invoking an agent (§6) |
| `basicly rubric eval <issue> [--runner NAME] [--dry-run]` | Evaluates the issue's work-type behavioral rubric (`.basicly/core/rubrics/*.rubric.yaml`): deterministic checks run via the verify runner (exit code = yes/no), judged checks dispatch one agent prompt via the runner (handoff when no CLI). Reports an advisory `rubric` gate (`br gate report`) — non-required by default (a judged verdict never fails the gate; deterministic-first), promotable by adding `rubric` to `[policy] required_gates`. `--dry-run` prints the judged prompt (basicly-0122) |

**Harness** (§12):

| Command                                     | Behavior                                                                                                                                     |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `basicly worktree ...`                      | Sibling git-worktree lifecycle: `create` + provision (deps, hooks), `list`, `cleanup`; `merge` lands one finished worktree on its base (rebase, re-verify, `--no-ff`) and `merge-queue` lands several serially in the given topological order; `bg-isolation` sets Claude's `worktree.bgIsolation=none` so the harness isolates itself (§12.5–12.6) |
| `basicly verify [--gate] [--fix]`           | Runs the consumer's `[[verify.checks]]` per mode and optionally records a `br` gate; `--fix` applies mechanical repairs first (§12.3–12.4)   |
| `basicly policy ...`                        | `dor`/`gate`/`rework`/`checkpoint`/`scaffold` checks, plus `grant` to show, issue or revoke a session autonomy grant (L1–L3; `--token-budget` required for L2+); `policy checkpoint --approve` needs an interactive TTY or a one-time `--confirm` code (§12.2) |
| `basicly decompose`                         | Turns a feature into child `br` issues + a computed dependency graph (§12.2)                                                                 |
| `basicly loop status\|advance\|run <issue>` | Drives one issue through the harness loop; a blocked step exits non-zero and names the input it needs. The multi-lane path is `preflight` (read-only: clean base, live worktrees, runner, grant, budget, per-lane band table and forecast spend) and then `supervise`, which dispatches ready lanes, routes their outcomes and lands green work until it is done or blocked on a human. A second session observes a live run with `session` and `watch`, and clears what a lane is blocked on with `decisions`, `answer` (record a human answer) and `decide` (invoke the decider agent, corpus-bounded). `kill` is the one gate verb that is not an answer — it closes a lane won't-do-this-way with a recorded reason and tears its worktree down, always behind a one-time confirm code no grant or TTY substitutes for (§12.2). `improve` is the other loop shape and takes no issue: it runs the repo's improvement controller (`.scripts/improvement_controller.py`), which measures one declared property of the tree, selects one target deterministically and files at most one lane — bounded to one unlanded lane at a time, `--dry-run` to select without filing |
| `basicly commit <description>`              | Assembles the conventional-commit envelope from engine state — type from the bead's work class (refined by an all-docs/all-test/all-ci diff), scope from the staged paths weighted by churn, trailing bead id from the branch's worktree binding — and commits the staged change with it. Only the description (and an optional `--body`) is authored; a description the charset rules reject names the offending character before any commit is attempted. `--type`/`--scope`/`--issue` override a derived part, `--dry-run` prints the message only. The `commit-msg`/`beads-commit-msg` hooks stay the gate (design D10, `basicly-kjc5.42`) |
| `basicly runner list\|dry-run\|run`         | Agent-agnostic headless runner adapters (claude/codex/copilot + `manual` handoff); the loop build phase auto-dispatches through them (§12.8) |
| `basicly tracker shadow`                    | Step 2 of the work-tracker cutover ([`work-tracker`](../requirements/work-tracker.md) §5): folds the owned event log under `.basicly/ledger/` and compares its answers to phase derivation, the ready set and gate status against the **live** `br`, record by record. The reference is a `br list`/`br show`/`br gate list` read and never the JSONL export — an upsert-only import cannot express a deletion, so two derivatives of one snapshot agree with each other and prove nothing (§5.1), and the kit audits the reference by perturbing the ledger and refusing a source whose answers move with it. Writes to neither store, and reports `clean` and `conclusive` as two verdicts: a query every record answered identically discriminated nothing, so exit `0` needs both |
| `basicly release <version> --issue ID [--date D] [--dry-run] [--autonomous --root ID]` | Component 9 release automation (`release.py`): bumps the single-sourced `__version__`, regenerates the version-stamped projections in a **fresh interpreter with the target repo forced onto `PYTHONPATH`** (`cli` binds `__version__` at import, and a same-process or installed-copy rebuild stamps the previous version), rewrites the `@vX.Y.Z` install pins in `README.md` + `site/index.html`, assembles the per-lane `changelog.d/<bead-id>.<category>.md` fragments into the `## [Unreleased]` body (grouped by Keep a Changelog heading, ordered by category then filename, deleted in the same commit — one file per lane so two lanes can never collide on one changelog anchor, `basicly-4746`) and upserts the dated `CHANGELOG.md` section from it via `.scripts/generate_release_changelog.py`, commits, and creates the annotated tag. **Never pushes** — publishing is irreversible and stays a human step. Refuses on a dirty tree, a version that does not move forward, an existing tag, or a fragment it cannot place (empty, misnamed, or no `[Unreleased]` heading to fold into), reporting every reason from one run; `--dry-run` runs the same checks and writes nothing. `--autonomous` requires an **L3** grant (not L1/L2) with green lights-out preconditions on `--root` (D3) |

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
  [`.beads/config.yaml`](../../.beads/config.yaml) and the
  [`tool-br` skill](../../.basicly/core/skills/tool-br/skill.yaml) — not restated here, per
  §3.1.
- Enforcement: [`commit-msg.py`](../../.basicly/core/hooks/commit-msg.py)
  (conventional-commit format, permits a trailing issue-id parenthetical) and
  [`beads-commit-msg.py`](../../.basicly/core/hooks/beads-commit-msg.py)
  (requires the referenced id to exist in `.beads/issues.jsonl`) both run at the
  `commit-msg` git stage, wired independently in
  [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml).
- These hooks are both this repo's own dev-process tooling **and** the literal
  catalog source (§4.2) — dogfooding is direct, not a copy.
- Practical implication for planning work as beads issues: use `epic` for large
  initiatives (e.g. "make basicly uvx-installable"), `feature`/`task` for new
  work (including the deferred items in §11), `bug` for regressions, and
  `--parent` to link a `task` under a `feature`/`epic` instead of inventing a
  "story"/"sub-task" type.

---

## 11) Not yet implemented

Everything described in §§0–13 exists in code today. The items below are the only
known exceptions — each is **[Deferred]**: consciously not built until a real
consumer need appears. None is tracked as an open issue (the former tracking beads
were closed as won't-do, 2026-07-16); file a fresh task if demand appears.

This section is **not** the roadmap. A deferred item here is one nobody has asked
for; the work the architecture is actively heading toward is §14, and the combined
status view of both — what is built, building, designed, researched or deferred — is
§15.

1. **A native per-path instruction renderer for codex**: Codex reads
   the shared `AGENTS.md` baseline today, with path-scoped fragments inlined for
   glob fidelity (§7 detail 4). A native scoped-rules projection would add
   per-path parity once a real Codex consumer needs it — but there is currently no
   Codex mechanism to project _to_: it has no glob-based instruction scoping at all.
   (Not to be confused with `.codex/rules/`, which is Codex's _sandbox
   command-execution_ policy, a different feature that happens to share the name
   "Rules" — see §7 detail 4.)
2. **Cursor as a target**: no renderer, no templates.
3. **Offloading directory-shaped scopes** via nested `AGENTS.md`/skills for the
   codex target (§7 detail 4). Rejected more firmly than a fidelity trade-off would
   warrant: Codex loads nested `AGENTS.md` only from the project root **down to the
   cwd** and stops there, so a file at `src/foo/AGENTS.md` is not loaded at all when
   Codex runs from the repo root — the offload would usually contribute nothing.
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
**Verify** (deterministic, blocking) → **Validate** → _[human
checkpoint]_ → **Ship** (teardown folded into the same advance). A failed node enters a bounded
**rework loop (n=2)** then escalates to a human; any track can **escalate a tier** (carry work
forward, re-hit only the Decomposition checkpoint) without restarting. Default is
task-by-task; one-shot mode collapses the middle checkpoint. The concurrency cap is
`[worktree].concurrency`, whose default is `config.DEFAULT_WORKTREE_CONCURRENCY` and is
deliberately not restated here — a document that repeats it goes stale silently
(`basicly-tcmy.9`). Each _[human checkpoint]_
approval (`policy checkpoint <issue> <name> --approve`) is gated on an interactive terminal:
off a TTY — as any tool-invoked Bash runs — the command refuses and issues a one-time
confirm code that a human must echo back with `--confirm`, so a subagent driving the loop
cannot self-approve ship autonomously. When an autonomy grant _was_ consulted and declined
— an uncovered checkpoint, an issue outside the grant's session tree, a spent token budget,
a ceiling that cannot be metered (below), or a ship whose lights-out preconditions do not
hold — the reason rides on the challenge's
`detail` and is threaded through `loop advance` and the supervisor's decision queue, so an
operator can tell _no grant_ from _a covering grant that refused_; a bare confirmation
request made the two indistinguishable. No decision logic changed with it. This mitigates
the shared-identity gap (a fork and its
human share one OS/git identity); it does not defeat a process deliberately re-running with
the code.

The DoR's required section set is derivable from the work type, so it is **emitted rather than
discovered**: `basicly policy scaffold --type <t>` prints a body with every required heading
present and a `TODO` under each, and both refusal paths (`policy dor` and the classify gate in
`loop._on_classify`) name that command typed for the bead instead of only listing what is
missing. `policy.compose_body` is the single source — `decompose._child_body` composes engine-created bead
bodies through it, so a `bug`-typed child carries `## Steps to Reproduce` too. `br`'s per-type templates are compiled
into its binary and no read-only `br` command reports them, so `policy._TYPE_SECTIONS` states
the set and `tests/test_integration_dor_scaffold.py` pins it against the installed `br`
(basicly-kjc5.44).

**12.2.1 VALIDATE is a rung, not a lint** (`basicly-u2hl.54`). `config.LOOP_PHASES` reads
`intake · classify · decompose · build · verify · validate · ship`, and `loop_state.PHASES`
adds the terminal `done`, which has no transition out. The two tuples differ by exactly that
one element **on purpose** — `config.py:369` records the reason and `tests/test_loop.py` pins
`loop._HANDLERS` to `LOOP_PHASES` so a handler cannot drift from the set that validates it.
Do not reconcile them.

The phase is gated at the recorded L3 level: it refuses its advance on a failed or missing
consumer gate, dispatches the `validator` role, and prices that dispatch as a **read** rather
than a write, so a judge never enters the sample a lane's cost is calibrated from (§12.8.2).
`reviewer` fans out beside it, once per entry in `roles.REVIEW_LENSES` — `correctness` and
`security`, and the vocabulary is pinned by a literal tripwire rather than by a length check.
Both are advisory: a reviewer records findings under a `[harness-review] lens=<lens>` marker
and the validator owns the gate, so §6's no-rerank rule holds by construction rather than by
instruction. Maintainability is deliberately not a lens — ruff, pyright, vulture,
`lint-imports`, `module-size`, `comment-density` and `noqa-debt` ratchet that axis
mechanically, and a lens restating a green check is a paid dispatch on every L3 unit.

**12.2.2 Four gate verbs, all four of which now write.** Go and Recycle (bounded rework in the
lane's own worktree) were the loop as first built. **Hold** and **Kill** were words an escalation
offered that no answer carried out (`basicly-u2hl.3`): an operator who answered `park` changed no
status, and the next supervised pass dispatched the lane again. Both are writes today — an
answered `park` defers the lane and records the reason (`cli._carry_out_rework_hold` →
`policy.hold_lane`), and `basicly loop kill` tears the worktree down and closes the bead behind a
one-time confirm code that no grant and no TTY can substitute for (`policy.authorize_kill` →
`policy.kill_lane`). Kill requires a human at **every** integrity level, because it is the only
verb that removes a requirement rather than routing work.

**12.3 Components — build vs reuse.** The engine we build is thin: worktree lifecycle; merge
orchestrator + serial merge queue + conflict-resolver; a **verify runner** (runs the
consumer's configured checks — adapted from beads-blueprint's `validate.py`, made
config-driven rather than Python-specific); the loop state machine + checkpoints; the
classifier; the concurrency cap. Everything else is delegated to `br`: **gate ledger**
(`br gate report`/`br gate list`, with required-gate status), **dependency graph + readiness**
(`br dep`/`br ready`/`br blocked`), **Definition-of-Ready** (`br lint`), and
**swarm/stale-claim diagnosis** (`br coordination`).

**Two of those surfaces have already left the list, and the delegation claim above is
narrower than it reads** [M 2026-08-14]. **Ranking is owned**: `kit/tracker/scheduler.py`
sits behind `br.read_ranking` and emits `schema: basicly.scheduler.v1` — unblocked only, then
priority, then descending count of still-live blocking dependents, then id, every term a pure
function of the graph, with `created_at` dropped because age-based ordering makes dispatch
order clock-dependent for an unchanged graph. **Harness markers are native**
(`basicly-s5li`): 89% of the live tracker's comments are `[harness-*]` markers using a beads
comment purely as transport, and in `owned` mode those twelve families are written and read as
`comment` events with no `br` spawned at all. What is left is **32 `run_br` call sites across
12 modules** behind the one seam in `br.py` — `supervise` 8, `decompose` 6, `loop` 4,
`policy` 4, `merge` 3, then one each in `classify`, `loop_state`, `cli`, `rubrics`, `worktree`,
`verify` and `validate_gate`. The repo still runs `[tracker] mode = "external"`, so none of
the owned path is authoritative here yet; §14.5 and `work-tracker.md` §5 carry the cutover.

**12.4 Gates — deterministic blocks, semantic advises.** Deterministic checks (tests, lint,
type, build; the existing commit-msg/identity/beads hooks) report a **required** gate via
`br gate report --status pass|fail`; a failed required gate blocks loop advancement.
AI-semantic verification reports a **non-required** gate — advisory, never blocking (§3.3
deterministic-first, semantic-second, applied to the loop). The block-vs-advise policy and
the n=2 rework rule live in the harness engine; `br gate` only stores the verdicts. Because
`br gate report` authenticates nothing and a dispatched lane agent shares the real tracker
through the worktree `.beads` redirect, a **required** gate counts only results carrying the
engine's own provider (`basicly-verify`, `basicly-rubric` — `config.ENGINE_GATE_PROVIDERS`);
a foreign result on a required gate is surfaced as _disregarded_ rather than counted, so
invariant 3 of §1 is enforced rather than left to agent good behaviour. Advisory gates still
accept any provider. Forging one of those provider strings is still possible: that is the
same acknowledged class as grant and checkpoint marker forgery, and authenticated gate
results are the only real fix. A check
whose repair is purely mechanical and lossless also declares a **`fix_command`** (a
formatter's write mode): the pre-commit hook applies it to the staged files and re-stages
them, so the commit carries the fixed bytes and no agent cycle is ever spent re-running a
repair a script can make. The check itself is unchanged, so unformatted input from outside
the harness still fails in CI, and a non-mechanical failure (lint, type, test) still blocks.

**12.4.1 Declared evidence artifacts.** A gate records a status, not an artifact, so a lane
could reach ship having recorded a passing verify with nothing on disk to point at — and when
a landing was later questioned, the evidence was whatever happened to be committed. A phase
may therefore **declare** a file the engine asserts is present before that phase may report
success (`basicly-m4zv.13`, adapted from Archon's `evidence_policy.required`):

```toml
[policy.evidence]
verify = ".basicly/evidence/verify.log"
```

**Opt-in, blocking where declared.** Nothing is declared by default, so the mechanism is
inert until a consumer writes the table, and deleting the line removes the requirement.
Blocking every phase was rejected as too strict, record-only as toothless.

**Presence only** — the engine stats the artifact and never opens it. Anything more would put
a parser, a schema and a verdict about content on the deterministic side of §12.4's contract.
The corollary, stated rather than hidden: an `echo` satisfies this, exactly as a forged
provider string satisfies a required gate (same acknowledged class). What it buys is that
"verified" can no longer be claimed with an empty disk behind it. Archon's own completion gate
is `signalDetected || bashComplete`, which lets a model's self-emitted DONE short-circuit the
deterministic half; that disjunction is rejected, only the evidence requirement adopted.

The check is a precondition on **leaving** a phase, so it is decided before the phase handler
runs and a refusal has spent nothing — the work type is not recorded, the merge is not
attempted. `build` is the exception in placement only: a lane's sub-task steps stay inside
`build` and are what produce a build artifact, so checking them would deadlock the lane on its
own evidence; its check sits at the single build→verify funnel (`loop._verify_and_land`, before
the merge) and resolves the path against the **lane's worktree**, since that is where the
build's evidence is produced. Everything fails closed: an empty declaration, a path escaping
the checkout, a directory, and a misspelled phase name all refuse rather than degrade to "no
requirement" — a gate the operator believes is on and that never fires is the exact failure
this removes, so a typo refuses _every_ phase and names the key to fix. A satisfied path is
recorded on the bead as a `[harness-policy] evidence` marker before the transition runs, so it
travels with the tracker export (`br` comments) rather than landing after ship's own commit.
`verify` streams its output to the terminal and captures nothing today, so a producer for it is
tracked separately (`basicly-m0s4`); this is the mechanism, not its first user.

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

The declared scope those disjointness claims rest on is **verified at the landing, not trusted
from the plan** (`basicly-jr0l.44`). `decompose` reads a child's `## Scope` globs to group and
size the plan and then never looks again, so a wrong or stale declaration used to surface only
later and indirectly — as a merge-queue conflict, after two lanes had already done work that
fights. The build→verify funnel (`loop._scope_block`, beside the evidence check and likewise
before the merge, so a refusal spends nothing) diffs the lane against its merge base
(`merge.branch_changed_paths`, three-dot, so a base that moved on is not counted as the lane's
work) and holds the result against the declaration. Two outcomes, and only one refuses:

- **Every** out-of-scope path is recorded on the bead as a `[harness-policy] scope-violation`
  marker — evidence about the _plan_, travelling with the tracker export like the rest (§12.4),
  and written whatever the policy then decides.
- A path that also falls inside **another live lane's** declared scope is the case that
  actually produces the conflict, and `[policy] scope_collision` decides it deterministically:
  `block` (default) refuses and names the lane that declared that ground, `warn` lands on the
  finding. Blocking the non-collision case too would turn every legitimately incomplete
  agent-authored plan into a rework cycle, which costs more than the finding is worth.

"Live" is the worktree session records on disk, not the tracker export: the `worktree:` binding
is written with `br update --external-ref` and is not flushed to `issues.jsonl` until the next
tracker commit, so a freshly provisioned lane — the one most likely to be mid-edit — would be
invisible there. Engine-owned paths (`.beads/`) are never out of scope, for the reason
`merge.coupled_lanes` excludes them: the harness rewrites the tracker on every landing. A bead
with no readable `## Scope` — anything not created by `decompose` — is not checked at all,
because it contradicts no plan.

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

**12.6.2 Owned vs shared scope.** Grouping is the transitive closure of scope overlap, so a
single path several children declare made every one of them overlap every other and collapsed a
wholly parallel plan into one serial chain — worst for the most honest plan, because a careful
author is _more_ likely to declare the manifest they will touch (basicly-jr0l.45). A child may
therefore list part of its `scope` as `shared`: paths it touches but does not own, and overlap
through a path **both** sides declared shared does not serialize them (ccpm's designated-owner
rule, read from the other side — one child _owning_ the path still blocks everyone who touches
it). The escape hatch is deliberately narrow so no agent-authored plan can use it to hide a real
collision: an entry must appear verbatim in `scope` (the recorded `## Scope` stays the whole truth
for read-cost sizing and merge attribution) and must be one literal path, never a glob, so no
subtree can be exempted behind a wildcard. Independently of the declaration, every decompose
surface **names the load-bearing path**: `decompose.collapsing_paths` reports each declared glob
whose removal would leave the plan in more groups, marking the ones a `shared` declaration already
defused — the original failure was silent, and a serial chain with no stated reason is the reason
nobody made the one-line fix.

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
the binary; no model leaves the argv unchanged. Preferred over a pinned id is a portable
**`tier`** (`low`/`medium`/`high`/`maximum`), resolved at dispatch through the committed map
(§4.2) into the one spelling the target surface accepts — `claude-haiku-4-5` on the Anthropic
surface, `claude-haiku-4.5` on Copilot's — with `[runner] default_tier` applied to any spec
declaring none. Resolution is most-specific-first (`model` pin → `tier` → default) and it
**refuses before spawning** when the tier resolves to nothing, naming the agent and the config
key, because silently running on another tier's model is the failure the map's keyless
`unavailable` cells exist to prevent. A tier aimed at a family that cannot pin one at all — the
`manual` handoff — is recorded as _not honoured_ rather than as satisfied. The run record keeps
the provenance, not just the id: the tier, which input decided it, and the model the adapter
reported it **actually** used, which is measured per family rather than assumed (claude names it
three ways and keys `modelUsage` by the dated build while carrying the short `canonicalModel`;
copilot names it in its session store's `modelMetrics` keys and one dispatch may list several;
codex 0.146.0 names it nowhere, so codex is recorded as _unobserved_ instead of assumed to
match). This is model/agent-property _awareness at the invocation seam_, not a token-level
inference client — per-track model choice stays out of scope. There is no cross-agent CLI invocation standard, so an unknown agent's command is
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
appends the adapter's usage-report flags (claude `--output-format stream-json --verbose`, codex `--json`;
opt-in per call site, because they wrap stdout in an envelope — a consumer that parses the
agent's answer reads it back through `runner.result_text`, which inverts the envelope, and the
two CLI passthroughs — `basicly review` and `basicly runner run` — stay unflagged because they
print the reply for a human rather than metering it) and `runner.extract_usage` parses reported
tokens/cost from the captured output;
when the output does not parse, it falls back to a chars/4 transcript estimate flagged
`estimated` so calibration can down-weight it. Copilot is metered **out of band**
(`basicly-2rn9`): it reports nothing usable on stdout, but each dispatch's per-model token
split and AI-credit spend land on the terminating `session.shutdown` event of its own session
store (probed 1.0.75), so a metered dispatch supplies the new session's UUID with
`--session-id` and the reader joins on it — which measures real tokens _and_ leaves stdout
plain text, so this is the one arm an answer-parsing consumer needs no inversion on. An
absent or unreadable store
takes the same flagged estimate, so a measurement that could not be made is never reported as
one that was. `[runner] copilot_session_store` moves the base directory (default
`~/.copilot/session-state`); it lives in `[runner]` rather than `[paths]` because only the
harness sections read the gitignored `basicly.local.toml`, which is where a machine-specific
store path belongs. Token counts are recorded both as the summed `tokens` total every consumer
already reads and, where an adapter reports the breakdown, as a provider-neutral
input/output/cache-read/cache-write/reasoning split; AI credits get their own `credits` field
rather than `cost`, which stays USD. A `[[runner.agents]]` override sets `usage_format`
(`claude-stream-json`/`claude-json`/`codex-jsonl`/`copilot-session-store`) to keep exact
extraction on a custom command. The claude default is the **streaming** envelope because it is the only one carrying
per-turn usage: `runner.context_occupancy` reads the last assistant turn for the D8
context-ceiling meter, while the stream's terminating result event still supplies the
cumulative cost view (`basicly-kjc5.14`). A consumer pinning `claude-json` keeps exact cost
telemetry and an inert ceiling. Only metadata is persisted — the command is stored with the prompt argument
elided, never the prompt body or captured output. This is the correlation foundation for
agent attribution, model provenance, and the cross-repo fleet rollup.

An estimated sample is good enough to calibrate against and **not** good enough to meter a
grant with, so `policy.session_spend` keeps the two apart (`basicly-jr0l.35`). The chars/4
fallback counts the captured output only — never the prompt, the system prompt, the tool
definitions or cache writes, which is where nearly all of an agentic dispatch's tokens are —
so it is a floor far below reality rather than the conservative over-count a ceiling needs:
measured on a live copilot probe, 5514 bytes of stdout estimated 1378 tokens against 24210
real input tokens, 17.6x under, and with plain-text output the captured answer was two
characters. Counted at face value it therefore _bought_ budget. There is no honest multiplier
to inflate it by either, so the ceiling errs the only way a ceiling may: a session that took a
dispatch its adapter could not meter is **halted** with the reason surfaced (`spend_status`
detail, its own decision-queue question, and the `loop preflight` verdict), and
`remaining_tokens` reads 0 because what is left is unknown rather than free. The count of
unmeterable dispatches is baselined on the grant marker exactly as spend is, so re-granting —
the human seeing the reason and accepting it — clears the halt, and any adapter with no usage
format inherits the refusal rather than a silent under-count.

**12.8.1 The forecast lands on the record its actual lands on** (`basicly-jr0l.34`). A dispatch
records its **working-set forecast, task class and forecast source** alongside the scope
read-cost it already froze, so one record carries the estimate and the outcome it produced.
Before this they were written to disjoint classes of record — the governor froze an estimate on
a _feature_ at decompose while tokens landed on a run record whose `forecast_tokens` field had
no writer at all (measured non-null on **zero** of 149 records) — so the forecast error, which
is the entire learning signal the spend forecast (`basicly-jr0l.21`) calibrates against, had
never once been computable. `decompose.dispatch_sizing` resolves it: the estimate frozen for
this content wins where one exists (marked `frozen`, evidence of prediction skill), otherwise
the same formula is applied at dispatch (marked `dispatch`) — a distinction recorded rather than
averaged away. A bead with no readable `## Scope` gets **no** forecast, because a forecast
against an unknown scope is an invented number.

`basicly usage forecast` reads the pair back, over local records **and** the committed
`[harness-run]` markers, so a fresh clone computes the same error a teammate measured. It
**refuses to compute an error for a record missing either half** — a forecast with no actual is
a handoff or a killed run, an actual with no forecast is an un-sized helper dispatch — and
reports those as unpaired counts instead, so an empty report says _why_ it is empty rather than
looking like a passing calibration. Two things the report states, because misreading either is
expensive: the ratio is **actual spend over forecast working set**, and since an agentic loop
re-sends its context every turn it carries the turn multiplier (which nothing models yet) as
well as any estimator error; and the summary is a **median**, because the measured misses span
160x-420x and one such sample would drag a mean somewhere no dispatch has ever been.

The pair the ratio above is computed from spans **two quantities**, and a dispatch now records
the same-unit one beside it (`basicly-tcmy.34`). `forecast_spend_tokens` is what
`decompose.forecast_spend` predicts and what `supervise.admit_pass_spend` already refuses a pass
on — whole-lane spend, which is what `tokens` meters — so the forecast a completed lane can be
held to no longer has to be the one denominated in context. Both halves stay, because each has an
actual of its own (`forecast_tokens` against `context_tokens`, `forecast_spend_tokens` against
`tokens`) and the turn multiplier is measured from the cross-unit ratio. The assumed bound an
unsizeable lane is gated at (§12.8) records on the spend field for the same reason: it is a
quantile of measured lane actuals, and in the working-set slot it paired at ~1x — a forecast that
looked perfect while predicting the wrong quantity.

`decompose.spend_accuracy` holds every _bead_ to its spend forecast, and `basicly usage forecast`
reports the verdict under the table: **one order of magnitude either way**, because
under-forecasting spends money no grant admitted and over-forecasting refuses a pass that would
have fitted. The unit is the bead because `forecast_spend_tokens` is derived from the bead's
scope, so every dispatch of one bead records the identical number and each attempt after the
first would otherwise be scored against a forecast covering work an earlier attempt already did —
`basicly-u2hl.14`'s third dispatch read as 0.057x against a lane that came in at 1.31x. A bead's
attempts are therefore summed and the count reported, which is also the unit a grant is minted
in. It binds on the history that already exists rather than only on records written from now on —
an older record's working set is converted by today's calibration through the one converter
`forecast_spend` uses — and on this repo's committed ledger the 60 comparable lanes come in at
0.19x-6.37x (median 0.97x) where the cross-unit comparison of the same records reads 64x-793x. A record whose recorded working set the band itself would refuse
cannot be converted and is **named** rather than skipped: one exists, carrying a factor of ~193
from the spend-derived calibration `basicly-z2wi` deleted, and a population quietly shrunk by a
filter is how `basicly-ipx2` committed a false claim.

**12.8.2 One named phase set, and a factor that says it was declared** (`basicly-tcmy.5`).
Every dispatch records a **phase**, and the write phases — the interactive `build`
(`loop._run_agent`) and the supervised `lane` (`supervise._dispatch_lane`) — are one named set
(`run_record.WRITE_PHASES`, tested through `run_record.is_write_phase`). They are the same kind
of work, so both consumers of the phase read the same definition: the unsizeable-lane bound
(§12.8) counts a write dispatch from either path, and the spend calibration samples only write
dispatches, so a rubric judge (`validate`) or the decider (`decide`) can never contribute a
helper's spend to a lane's ratio. The two filtered oppositely before this — the bound required
`lane` alone, so on this repo's own history it measured 24 of 32 metered write dispatches and
bounded a lane at 15245717 tokens where the whole population gives 15830484, while the
calibration filtered on no phase at all. A record whose phase was never written is excluded from
both: unknown provenance fails closed.

The same record now also carries **where its build factor came from** (`seed` from
`DEFAULT_BUILD_FACTOR_SEEDS`, or `configured` from `[policy.sizing.build_factor]`). Nothing
measures a working-set factor — the calibration that appeared to was measuring whole-lane spend,
a different quantity, and was removed (`basicly-z2wi`) — so every forecast is a declared constant
times a scope read-cost, and the record says so on the same rule its siblings already follow
(`forecast_source`, `SpendCalibration`, the bound's `measured`/`seed`). `basicly loop preflight`
reports both: the per-class paired-sample counts against `calibration_min_samples` with the
verdict they add up to, and whether any build factor is anything but a seed — so an operator
minting a budget from the forecast learns it rests on a prior before the money is granted, rather
than by reading source.

**12.8.3 The parameters in force, held against the outcomes they produced** (`basicly-3ifz.1`).
Almost every number governing the factory is set by judgment and then never revisited.
`basicly usage tuning` (`tuning.py`) is the readable half of the feedback loop the exceptions
already have: it reads the dispatch ledger from **both** corpora — the self-ignored local run
records and the committed `[harness-run]` markers, deduplicated on (bead, timestamp) so a
dispatch recorded in both is one sample labelled `both` — and reports, per governed parameter,
the value in force for the dispatches it summarises, the outcome distribution under that value,
and a recommendation with its sample size.

Three rules keep it from becoming another declared number:

- **It writes nothing.** Deterministic checks block, judged checks advise, the engine disposes;
  a tuner proposes a `basicly.toml` change and a human or a gate applies it. Nothing in the read
  path opens a file for writing.
- **A seed never reads as a measurement.** At or above `[policy.sizing] calibration_min_samples`
  the recommendation is the statistic over the newest `calibration_window` observations, labelled
  `measured`. Below it the **declared prior** stands, labelled `seeded`, and the row names the
  in-force value it would displace — deliberately not a number fitted to three samples, which
  would still be read as a measurement whatever the label said. The prior is read from the config
  loader's own dataclass fallback rather than copied, so it cannot drift from the value actually
  in force. Same discipline as `run_record.SpendCalibration`, one layer down.
- **A parameter nothing measures still prints**, with a sample size of zero, no recommendation
  and the reason it has none. `stall_after`, `quiet_after`, `max_agent_processes`,
  `[worktree] concurrency`, `max_subtasks_per_lane`, `decider_max_decisions` and the two
  calibration bounds are all in that state today: a bound nothing records is a bound nobody can
  tighten, and omitting the row makes "no evidence exists" look exactly like "this is fine".

The statistics split by what being wrong costs. A **backstop** (`runner_timeout`,
`context_ceiling`) fires on work already in progress and destroys it, so it is read from the
worst observed run with headroom rather than from a quantile — calibrating `runner_timeout`
against the work distribution is what had it killing working lanes (`basicly-lpsf`). A **band**
(`working_set_min`/`working_set_max`) refuses a package, and both refusals are recoverable — merge
with a sibling, or split into more top-level packages — so it is read at the quantiles of what
really happened. `max_rework` is write dispatches per bead at the ceiling quantile less the first
attempt, which is not rework. The per-class **build factors** are fitted to measured
`context_tokens` over declared `scope_tokens` — the working set the factor predicts — and never to
spend, which is working set times a turn count nothing models (§12.8.2, `basicly-z2wi`).

A session override is the one per-dispatch record of a parameter's value (`config_overrides`), so
dispatches run under one form their own **cohort** with their own outcome distribution; pooling
them would report outcomes under a value that never governed half of them.

**12.8.4 A lane's spend split into acquisition and implementation** (`basicly-ejdm.2`).
`basicly-ejdm` claims a lane's multi-million-token floor is bought by the dispatch
instruction rather than by the work. That claim had **no instrument behind it**, so its
remedy could not have been judged — which is why the ordering is `.1` record the tools,
`.2` derive the split, `.3` brief the lane, `.4` measure, and only `.4` is a claim.
`basicly usage lane-split` (`lane_split.py`) is `.2`.

**The pairing rule is the whole arithmetic, and two naive versions measure the wrong
thing.** A `tool_use` turn's usage is the cost of _emitting_ the call; the tool's result
lands in the **next turn that carries usage**. So summing tokens on the turns that called
the tools counts the request and misses the answer — and pairing against the immediately
preceding _line_ fails too, because a real transcript forwards the tool result as a `user`
event carrying no usage, which sits between the call and its answer. That second version
was written first here and attributed a real captured lane **100% to unattributed**, a
confident figure measuring nothing; the demonstration caught it, not the unit tests. A
turn's tokens are attributed to the last tools emitted before it, and a turn with none is
unattributed rather than guessed at.

**Three things it refuses to guess.** A tool that is neither read nor write is
`unclassified`, not bucketed — `Bash` runs `git status` and `mv` alike, and a majority
rule over a mixed turn would put a guess inside the number the remedy is judged by. A
transcript written before `basicly-ejdm.1` added the tool field is **unclassifiable**
rather than fully implementation, because absent is unknown and `[]` is "called nothing".
And a lane with no transcript is reported as missing rather than as a zero split.

**Shares lead, tokens follow, and the report says why.** Per-turn stream usage
over-reports against the run record by **1.46x-1.79x** [M 2026-08-13, four lanes], so a
stream-derived absolute is in a different denomination from the grant it would be
compared against — a mixture that has already cost this repo a lane. The report also
states that it is **claude-only**: no other family emits the per-tool event it reads,
which matches a ledger holding only claude and manual dispatches.

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
agent-agnostic re-founding of the same goal as an earlier private harness (a lefthook/pinned-pack,
tracker-abstracted first attempt): borrow that battle-tested
worktree/merge know-how (copy-mode deps, `git merge-tree` pre-flight probe, mode-aware
cleanup) as a reference, while keeping the **`br`-wrapping engine + agent-agnostic projection +
installable composable distribution** as the differentiators. From beads-blueprint, adapt
the `validate.py` gate-runner structure into the verify runner. `bv` (beads-viewer) is an
**optional human viewer only** — redundant with `br scheduler` at runtime, never a harness
dependency. Measured over 1,568 recorded invocations, **0 of `bv`'s 141 surfaces** have ever
been called programmatically, which is what makes it a viewer rather than a dependency.

**12.11 The role map — a phase resolves to a named agent.** For two days this section could
have read "the projection works and nothing consumes it": twelve agent sources were authored,
rendered into both agent roots and vendored to consumers, and every dispatch ended at a bare
`claude -p <prompt>`. That is closed. `roles.resolve_role` maps a phase to a role by **table
lookup** and the runner puts `--agent <role>` on the argv, verified against claude 2.1.226 and
copilot 1.0.78 rather than recalled.

Two tables, because the state table gives VALIDATE two roles. `ROLE_BY_PHASE` names the role
that **drives** a phase — the one whose reply the engine acts on — and `LENS_ROLE_BY_PHASE`
the one it fans out beside it (§12.2.1). Three properties are decisions rather than
implementation detail:

- **The map is data, not judgment.** The choice is not gameable, costs no tokens and cannot
  drift between lanes.
- **A role that is not projected resolves to nothing**, and the dispatch falls back to the
  default runner rather than failing. The check is against the **projected** file, not the
  catalog source, because that is what the host reads — so a consumer on an older install gets
  an unspecialised loop instead of a stopped one. Resolution also fails to `None` for a phase
  with no persona (VERIFY, by decision) and for a family that cannot select one (codex ships
  no subagent root).
- **Repair is the implementer's second state, not a role.** A persona is admitted only when it
  differs in tier, tools or artifact; repair differs in none of them, only in prompt. So REPAIR
  maps to `implementer` too and the mode travels in the brief, carrying the gate evidence that
  rejected the work.

Three `resolve_role` call sites reach an argv: `loop.py:818` (`_run_agent` — validate, build,
repair, sub-task build), `loop.py:1062` (`_run_proposer` — classify, decompose) and
`supervise.py:2679` (lane build). **Six of the seven loop roles are reachable today** —
`decider`, `decomposer`, `implementer`, `validator`, `reviewer`, and `retrospector` once its
trigger landed (§12.13). `curator` is the one that is not, and its cause is one wiring rather
than a design gap: it maps to `ship`, which is a live phase with a live handler that never
calls `_run_agent`.

**The ledger could not falsify any of this until the record learned to copy the argv**
(`basicly-jn1x`). Measured 2026-08-14: **0 of 357 dispatch records carry `--agent`**, against a
positive control of 163 carrying `-p`. The record _re-derived_ its command from the spec rather
than copying what ran, so it was wrong in both directions at once — it omitted the role flag the
lane passes, and it appended usage flags the decider's argv never had. A record that can be
wrong both ways is not evidence, and neither error is visible from the record itself.
`record_dispatch` now copies `result.command` with the dispatched prompt elided by equality,
recording no argv at all when the prompt is unknown rather than publishing one.

**That builds the instrument; it does not supply the reading.** The 357 historical records are
unchanged and still name no role, so a before/after measurement of role injection begins with the
next supervised pass. Until one runs, read §12.11 as a statement about reachable wiring, never
about observed dispatch.

Every role declares a **tier**, and `catalog lint` refuses a source that does not
(`basicly-plhx`). No projected agent file carries a provider model id — a projected `model:`
line would _disable_ tier injection rather than implement it — so the tier is declared, gated
and, until `basicly-a3yi` lands the injection, **inert at spawn** (§14.2). A role's declared
`skills:` do reach the agent dispatched for it (`basicly-ey58`), measured at ~0.03% of a lane
and reaching all three families rather than the one the vendor mechanism serves.

**12.12 Handoff artifacts — a schema at each state boundary.** Eight artifact kinds are named
and **seven carry a JSON schema** under `.basicly/core/schemas/`: `classification`,
`change-shape`, `implementation-plan`, `change-summary`, `verification-evidence`,
`validation-transcript` and `release-record`. `solution-design` is the one without, because it
is specified as **markdown with six machine-checked sections** rather than a JSON payload —
problem in the requester's terms, success as an observable, a consumer transcript, out of
scope, constraints, and open questions — so whether it belongs to the same family is an open
question (`basicly-32qz`) rather than an omission.

Two mechanisms carry them, and the second is the one a reader gets wrong. The schemas are
**catalog sources**, so a repo that has not installed them runs _neither_ end of the contract;
both producer and consumer resolve the schema first, which is what keeps a skipped write from
becoming a refusal downstream. And the artifacts travel as `[harness-artifact]` comment
markers through `br.add_comment`/`br.read_comments` — **not** by appending to
`.basicly/ledger/` directly. A direct ledger append would refuse the landing it precedes: the
advance sweeps base-checkout dirt only under `.beads/`, and anything else blocks the merge, so
an artifact written into the committed ledger on the way into BUILD would wedge the very
landing it gates. The marker seam writes on every rung and _becomes_ a ledger `comment` event
at the flip.

Schemas written is **not** roles reachable. Five of the seven roles carry a contract that
cannot be exercised until its artifact has run in anger, and that is a debt this section names
rather than a gap it hides.

**12.13 RETROSPECTIVE fires on a special cause, and is deliberately not a phase**
(`basicly-xmhc`). `retrospective.py` reads the gate-failure ledger and fires only on a
**computed** signal — a point beyond three sigma, or a non-random run or trend within the
limits. A single failure inside the limits is common cause and fires nothing: acting on it is
tampering, which "invariably increases variation in the results of a stable process". This is
the first mechanism in the harness that decides to **suppress** work, and it is a correction to
this repo's own practice, which filed beads off single occurrences.

`LOOP_PHASES` and `loop._HANDLERS` are untouched by it. A state exists to hold an entry
predicate, an exit gate and a persona; a conditional process over a ledger needs none of the
three, and adding a rung that never blocks anything would be ceremony around a function call.
The dispatch is recorded under a `retrospective` phase for role resolution and cost
attribution only (`retrospective.PHASE`), outside `WRITE_PHASES`.

One arithmetic trap is fixed in the module and is worth stating because the naive form looks
right: a c-chart's control limit falls **below 1** at low mean failure counts, so raw
arithmetic flags every isolated failure — at roughly 36× the rate a three-sigma tail admits.
The limit is floored at 2.

The output contract is **not** the why-chain. Three things: a named control that would have
refused the defect, its tier (control / warning / documentation), and the class of defects it
covers — plus the branch of the analysis not taken, because iterated-why yields one causal path
chosen by the asker and is not reproducible between analysts. A documentation-tier outcome is
recorded as a downgrade with the reason no stronger control was available. A retrospective's
output is a **diff against catalog YAML**, never prose advice, and no autonomy grant disposes
it: an agent that can amend the catalog under a grant widens its own constraints, and the next
session inherits the widening as ground truth.

**12.14 The second loop shape — an improvement controller** (`basicly-u2hl.27`). Everything
above drives a _requirement_ to a landed change. `basicly loop improve` drives a **property of
the codebase** toward a set point: one sensor reading, one lane. It is the actuator behind the
ratchets, which bound a file and cannot themselves repair one.

Three properties keep it inside the engine-disposes rule. The controller is a **repo-declared
script** at a fixed path, run with this process's own interpreter and `shell=False`; a repo
that declares none is **refused by name**, because an absent script is the one state otherwise
indistinguishable from a run that measured everything and found nothing to do. Its exit code
passes straight through, so a schedule can branch on it. And it holds a **one-lane bound**: it
files one bead and does not file another until that one lands.

It has run live and filed its first real bead. What it does not have is a caller —
`basicly-e2mz.6` gives it one, `workflow_dispatch` only, which is what makes the wiring
non-circular.

## 13) References

### 13.1 The documentation layer

**Summary**: this file is the _reference_ quadrant and nothing else. A reference
answers "what is it and how is it specified"; it cannot also be the page that
gets a new consumer from `basicly install` to a first shipped unit, and trying to
make it both is what left that path missing. The four quadrants are separate
files with separate jobs:

| Quadrant | Where | Job | Written for |
| --- | --- | --- | --- |
| Tutorial | [`docs/tutorial/first-loop.md`](../tutorial/first-loop.md) | one guaranteed-success path, install → shipped bead, no options offered | a consumer on day one |
| How-to | [`docs/how-to/`](../how-to/) | the recurring operations, one page per task | a consumer with a job to do |
| Reference | this file, plus [`CONTRIBUTING.md`](../../CONTRIBUTING.md) | the system as specified | anyone implementing or debugging |
| Explanation | [`docs/requirements/`](../requirements/), [`docs/research/`](../research/) | why one question was settled the way it was | anyone changing a decision |

Three rules keep the layer from rotting into a second, competing account of the
system:

1. **A tutorial command is executed before it is written.** Every command and
   every quoted output in the tutorial was run against a fresh repo. A
   walkthrough is the one surface where an untested step costs the reader the
   whole session, because they have no model yet to notice it is wrong.
2. **A how-to states the operation and its failure text, not the design.** Where
   it needs a reason, it links to the section here that owns it. Duplicated
   rationale is what goes stale first.
3. **Where they disagree with this file, this file wins** — same rule as §15's
   rendered copies. The tutorial and the how-tos are consumer-facing renderings
   of §§4, 6, 9 and 12, not independent sources.

The current pages: the tutorial, and how-tos for customizing the catalog,
wiring the verify gate, unblocking a refused commit, upgrading and drift,
running parallel lanes, and resuming a track.

### 13.2 External references

- pre-commit: <https://pre-commit.com/>
- Trunk Code Quality: <https://docs.trunk.io/code-quality/overview>
- MegaLinter: <https://github.com/oxsecurity/megalinter>
- Claude Agent SDK: <https://code.claude.com/docs/en/agent-sdk/overview>
- OpenAI SDKs and CLI: <https://developers.openai.com/api/docs/libraries>
- Cursor SDK: <https://cursor.com/blog/typescript-sdk>
- Pydantic AI: <https://pydantic.dev/docs/ai/overview/>
- Fowler series (context priming, design-first, context anchoring): <https://martinfowler.com/articles/reduce-friction-ai/>

---

## 14) Target state — where the architecture is going

**Summary**: the four pillars of §0 are at very different maturities. The loop and
the factory are built and dogfooded; the judgment layer is designed and unbuilt;
the evidence layer barely exists and is the largest gap; the tracker is a dependency
we intend to own. This section is the map, not the detail — each row points at the
design document that owns it, and **none of it is running code**.

**The order it gets built in is [`docs/plan/implementation-plan.md`](../plan/implementation-plan.md)**,
which sequences these rows into phases with dependencies and exit criteria. That file
is deliberately external to the tracker, because the tracker is itself one of the
things being replaced (§14.5). **Which of these rows is actively being built rather
than merely agreed is §15**, the roadmap view.

Everything here is grounded in a 2026-07-26 review of eleven comparable projects
read at pinned revisions ([`research/`](../research/2026-07-26-sota-review.md)),
which is also where the competitive framing comes from: the field has converged on
a name for what this repo is — **harness engineering**, the claim that the
deterministic scaffolding around a model matters more than the model choice. The
open question is therefore not whether the harness approach is right; it is whether
this harness is measurably better than the others that also believe it.

### Details

**14.1 Pillar maturity.**

| Pillar | Today [M 2026-08-14] | Target | Owning design doc |
| --- | --- | --- | --- |
| Catalog (guidance) | projected + structurally gated; the path-scoped tier in use on **four** fragments; routing measured (tiers 1 and 2 ship as `catalog lint` rules) | behaviour measured per entry — tier 3 | §14.4 |
| Gates (enforcement) | deterministic, per-site behaviour, typed by `policy.GATE_TYPE_BY_GATE` for the five gates the engine names; 25 `[[verify.checks]]` including four ratchets | every gate classified by type, with a severity contract on judged output | [`factory-loop-requirements` §5.1](../requirements/factory-loop.md) |
| Loop / factory (SDLC) | parallel lanes, autonomy grants, merge queue, **VALIDATE as a rung, six of seven roles reachable, seven of eight artifact schemas** — dogfooded | the seventh role wired; the judged half hardened; release automation reachable under a grant | §14.3 |
| Tracker (state) | external `br` binary in the critical path, **32 call sites behind one seam**; ranking and harness markers already owned; repo runs `mode = "external"` | owned, in-process, append-only event log | [`work-tracker`](../requirements/work-tracker.md) |

**14.2 The factory — built, and its remaining honesty gaps.** The supervisor,
autonomy grant ledger, decision queue, lane mini-loop, and merge queue v2 have
landed and have been exercised on this repo's own development, including a
supervised multi-lane run. The tier-to-model mapping is published, drift-checked
(§4.2) and now **bound**: a declared tier resolves to a concrete model at dispatch and
an unresolvable one refuses rather than silently running on another tier's model
(§12.8). **The clause that stood here was false when it was written and is now false twice
over** [M 2026-08-14]. It said no role or catalog entry declares a tier, so every dispatch
is unpinned. Nine roles already declared one at the time; today all twelve do, `tier` is a
required property of `agent.schema.json`, and `catalog lint` refuses a source without it
(`basicly-plhx`). What remains true is narrower and worth stating exactly: **a declared tier
reaches no spawn.** No projected agent file carries a model id, by decision D30, and the
injection that would resolve one at spawn is `basicly-a3yi`, still open. So the tier is
declared, gated and inert. One recorded gap remains, and it matters
because a reader would otherwise believe the design is enforced: coupling attribution
still depends on intra-pass landing order, which the determinism rule forbids.
**The pointer that stood here is dead**: `factory-design.md` lost tiebreaker authority
and was deleted. Authority now runs **measured evidence in this repo, then the two
requirements documents, then nothing else**.

**14.3 The judgment layer — built at the routing seam, unbuilt at the judged half.**
**This section read "designed, unbuilt" and that is now false** [M 2026-08-14]. The
factory no longer dispatches one generic prompt shape for every lane: a phase
resolves to a named role by table lookup, the role reaches the argv, and six of the
seven loop roles are reachable (§12.11). What is described below as the target is
therefore split — the **routing** is running code in §§12.11–12.12, and what remains
open is the seventh wiring plus the judged-output contract this section closes on.
Three decisions constrain the design, and all three are deliberately conservative:

- **An agent may spawn only a role the engine authored.** The supervisor is code and
  stays unnamed precisely so nobody treats the thing that enforces the rules as
  something that can be persuaded. Reviewers are read-only; a separate actor fixes.
  **Amended 2026-08-09**: the original form was "no agent spawns agents", which both
  installed runtimes contradict by construction — a blanket ban is unenforceable
  prose. The amended form is stronger, because a host hook can intercept a subagent
  finishing _before_ its results return to the parent, so the boundary is a runtime
  gate rather than a process boundary we hope holds. Personas still never invent
  unmetered helpers, and any output is a proposal the engine validates.
- **Admission is a test, not a preference.** A role becomes a persona only if the
  work is genuine judgment, has a checkable success criterion, _and_ needs a
  materially different tool policy or model tier than its neighbours. Otherwise it
  is a prompt section or a deterministic engine step.
- **Tier is chosen by reliability, priced per landed package** — total tokens, wall
  clock, and human interventions per landed, correct unit, never the price of one
  dispatch. A cheap dispatch that buys a rework cycle and a human interruption is
  the expensive one. The refinement worth recording is that the safe predicate is
  **specification completeness**, not the work's nominal category: a brief
  containing the literal code is transcription, which is mechanically checkable.

The judged half needs hardening the current prompts do not have: an explicit
adversarial stance plus a **role-specific list of how that role goes soft**, derived
from observed failures rather than invented. Reviewer conflict-avoidance —
downgrading a blocker to a warning to avoid disagreeing with the producer — is a
predictable failure mode and is named rather than hoped away.

**What is still unbuilt here is deterministic engine code, not a persona**, which is
why it survives the routing landing above. The reviewer must be **structurally
incapable** of receiving the producer's conclusion — bundles are assembled by code, so
the assembler can guarantee it rather than a rule requesting it. The review base must
be **recorded before the producer is dispatched**, never derived as `HEAD~1`, which
silently truncates a multi-commit unit and reviews its last commit while reporting on
the whole. A re-review is **scoped to the fix range**, verdicting each open finding
addressed or not, so an out-of-scope observation becomes a deferred minor with a named
consumer instead of extending the loop. Late rework rounds **escalate a tier** with the
prior attempt's record, rather than bouncing to the same tier with the same framing and
spending the cap without changing a variable — which yields a measurable signal, since
if late-round bumps routinely succeed the initial tier was wrong. And two degenerate
reviewers are currently invisible and both are computable from data already recorded:
the rubber stamp, whose advisory green is worthless, and the noise generator, whose
findings are nearly all adjudicated contestable.

**14.4 The evidence layer — the largest gap.** Roughly thirty catalog entries ship
today and there is behavioural evidence about **one** of them, from a single-task
pilot whose own write-up notes the result was partly circular. Two failure modes
follow and both are silent: a skill whose description contains no word a user would
actually say never fires yet costs its context load forever, and an always-on rule
that has drifted past the point where the model attends to it still passes every
gate. The existing gates verify that an entry is _well-formed and projected_, never
that it _changes behaviour_ — excellent checks on the wrong axis.

The target is three tiers: structural (have it), **routing** (deterministic,
lexical, free, runs in CI — the highest-value single deliverable and the thing
nobody else in the field has), and behavioural (judged, on demand, with control arms
so a result means something). Two disciplines carry over from the strongest
measurement work reviewed: separate _mechanism confirmed_ from _outcome improved_
and never report one as the other, and keep a safety tier that **executes** the
produced code against hostile input, so "less code" or "more decisive" can never be
bought by dropping validation.

**Tier state today, measured 2026-08-08.** **Tier 1 and Tier 2 both ship**, as rules 8 and 9 of
`catalog lint`, on every commit. The routing gate asserts positive top-k, pairwise negatives — the
declared owner must _outrank_ the entry, because a bare "must not rank first" passes vacuously on a
prompt that matches nothing — and a description-collision ceiling; it reports a **rank-1 rate**,
measured at 83/90 = 92.2% against a `[catalog] rank1_floor` of 0.85 that ratchets and cannot be
lowered. **Tier 3 is paper**: no arms, hidden checks or safety tier exist in code. The one Tier-1
rule still unbuilt is making a _missing_ eval case file a failure (`basicly-m4zv.3`) — all 30
model-invoked skills carry one by convention today, and the 19 fragments carry none.

The single highest-leverage unknown sat here, and half of it has now been measured.
The always-on baseline is a few thousand characters per family — the live measurement
is the generated table in §7 detail 1; it was 7167 / 7299 / 8434 for Claude / Copilot /
Codex at the recall test below — on the order of 1100–1600 words of dense rules,
against a consistent practitioner
finding that adherence to dense rules degrades well below that. **The "cliff already
crossed" reading is refuted**: measured 2026-07-26, both families reproduce 93–98% of
their baseline's rules when asked, against a 6–17% no-guidance control
(§14.4). The content is
not invisible at this size.

What that result does **not** settle is the operational question, so the entry above
still stands as written: nothing measures which baseline rules _bind_ while an agent
works. Recall under a direct cue is an upper bound and confirms mechanism only — by
§14.4's own rule it may not be cited as evidence of quality. So the cap
policy is now asymmetric: **lowering it is ordinary housekeeping; raising it still has
no evidence behind it** and would prejudge exactly the adherence question that remains
open.

Relatedly, and cheaper than the design documents assume: the **path-scoped tier is
already built** — targets declare a `scoped_rules` output and the planner routes
fragments carrying a `scope` — and **four fragments now declare one** [M 2026-08-14]:
`external-review`, `platform-hermetic-tests`, `code-is-authoritative` and
`model-tier-routing`. Moving conditional guidance out of the always-on baseline is
therefore authoring work, not engine work.

**A skill takes the same glob, and that refuted a plan this section was queued
behind.** A skill's frontmatter accepts a `paths:` glob that both limits _and triggers_
automatic activation, so scoping `python-guidelines` cost **zero** always-on characters
and did not need a fragment at all. The key is not in the portable Agent Skills subset,
so it is declared under a per-target vendor fence and emitted only into the roots that
understand it — `.agents/` gets the portable six, `.claude/` gets the six plus its
fenced keys. **The gap that survives is codex**, which has no glob-based instruction
scoping and never loads a nested `AGENTS.md` below the cwd (§7 detail 4), so a fragment
remains the only mechanism there.

**A skill is not free, and the cost is in the listing rather than the body.** Every
skill's `description` plus `when_to_use` is capped per entry and the whole listing is
budgeted at 1% of the context window; on overflow the host drops descriptions
**starting with the least-invoked skills**. That is a feedback loop rather than a cost:
a rarely-invoked skill is the first to be truncated, which makes it harder to invoke.
Both caps are gated. The exercised-count that sized this — 8 of 34 skills ever invoked —
is **now unsound and has no successor figure**: since a role injects its skills into the
dispatch, the never-used report cannot tell an uninvoked skill from an injected one
(`basicly-4grf`).

Its cost effect is **asymmetric across families, not a blanket improvement** (§7
detail 1): scoping removes a fragment from the Claude and Copilot baselines and
**adds** it to Codex's, which has no glob scoping at all and therefore inlines scoped
fragments into `AGENTS.md` — measured 869, 1462 and 1614 characters for the three that exist,
against the `AGENTS.md` headroom in the §7 detail 1 table, which the **next** scoped
fragment can already exhaust. Whether scoping also improves **adherence** is a hypothesis
this repo cannot yet assert: the only measurement is recall under a direct cue, which
§14.4 bars from standing as evidence of quality, and at 98% / 93%
there is no headroom left to improve — which is why Phase 4's exit criterion asks only
that recall be **not degraded**.

**14.5 Owning the tracker.** The tracker is not a peripheral integration — it _is_
the harness's state, so every guarantee above is downstream of it, and it is
currently an unowned external binary in the critical path. The target is pure
Python inside this package, with an **append-only event log as the truth** and every
other file derived and disposable; a record's state is a fold over its events, so
history lives in the data rather than depending on git history surviving a squash or
a shallow clone. The motivation is **ownership, not speed**: re-measured 2026-08-07
against the live 642-record ledger, a single-record in-process read is ~15× cheaper
than the median external CLI call and a full fold ~1.9× — real but modest, and the
fold ratio narrows as the ledger grows. An earlier "~175×" here compared a p95 CLI
call against a single-record read of a much smaller ledger; `work-tracker.md` §10
carries the corrected table (`basicly-rxc1`).

Two constraints are recorded because they are easy to lose: a **clean-room
boundary** applies (the licence of the binary we currently depend on carries a rider
restricting a class of users, which is itself the strongest argument for owning the
component), and the alternative of adopting a versioned database is rejected because
it reintroduces exactly the unowned-binary upgrade surface being removed.

**The migration is five steps and they did not run in order** [M 2026-08-14]. Step 1,
the import, **ran once by hand** — 643 records as 3,775 events, every one carrying
provenance — but `migrate.import_snapshot` has no caller, no `main()` and no CLI, so it
is a one-shot that cannot be repeated and **nothing a fresh consumer runs can build the
ledger at all** (`basicly-vkh0.23`, P0). Step 5, native harness markers, **landed
before steps 2–4** (§12.3). Steps 2 (shadow), 3 (dual-write) and 4 (flip) are unrun,
and the repo is `mode = "external"`. That order costs one binding constraint rather
than a defect today: the shadow differential's comment comparison diverges **by
construction** at `owned`, because the marker families no longer reach the external
tracker there — so it must be run on `dual`, and a run that finds comment divergence at
`owned` is measuring the ordering, not a bug.

**Five operations have no owned equivalent at all**, and each is a design question
rather than a port: `lint` (which means owning the validation rules), `dep cycles`,
`list --label`, id minting (`ids.mint_root_id` exists and only tests call it), and
`gate list`. **The kit is also outside the scope of any architectural audit until its
own promotion runs**: eight kit modules landed against reasoning in `work-tracker.md`
that was never promoted to a design, so there is no frozen surface, no declared schema
and no cache decision to judge them against — and an audit needs a specification. That
gate was written as prose, nothing read it, and the condition was discharged by a bead
closing somewhere else. **A gate written as prose is not a gate.**

**14.6 Asserted, not yet earned.** Recorded explicitly so it is not mistaken for
established fact. The structural leads are real: enforcement is code and hooks
rather than prose; state is a tracker with a dependency graph rather than markdown
plan files; one catalog is projected to three agent families and the projection is
gated. But three headline claims are unmeasured — that the roster's tiers and lenses
pay for themselves, that the always-on baseline is effective at its current size,
and that individual catalog entries change behaviour. The cost-per-landed-package
baseline is the instrument that makes the first falsifiable, and it gates several
downstream decisions.

**14.7 Explicitly rejected, so it is not re-proposed.** Each refusal has a reason
stronger than taste, and several were reached independently by other projects: an
LLM orchestrator in control of the tracker; personas spawning personas; an
agent-writable catalog (a bad implementation bounces off a gate, a bad fragment is
_absorbed_ and silently degrades every later lane); `--no-verify` to dodge parallel
commit contention, which the merge queue already solves without defeating a gate;
lossy compaction of the ledger; a maintained TUI; an external database or daemon; a
compression proxy in the critical path; a cheap-tier model pre-reader whose
characteristic error is an undetectable omission; and agent-to-agent messaging,
which is a real capability declined because it costs reproducible scheduling and
resumability.

---

## 15) Roadmap — status per capability

**Summary**: one status view of every capability, so a reader can tell running code
from a decision on paper without cross-referencing three sections. It is **derived,
not a new source of truth**: a `shipped` row is the system described in §§0–13, a
`deferred` row is §11, and every other row is a §14 target with the state it is
currently in. There are **no dates** — the project does not run to a schedule, so
status is the only honest axis.

The **order** the non-shipped rows get built in is
[`docs/plan/implementation-plan.md`](../plan/implementation-plan.md), which sequences
them into phases with dependencies and exit criteria. This section carries status;
that file carries order and reasons.

This table is the copy the [README roadmap](../../README.md#roadmap) table and the
[landing page](https://niksavis.github.io/basicly/#roadmap) render for a wider
audience. Where the three disagree, this one wins and the other two are stale — with
one **deliberate** difference: neither rendered copy carries the `deferred` rows,
because a prospective consumer is choosing between what exists and what is coming, and
a list of things nobody has asked for is noise there. That omission is editorial, not
drift, and this table plus §11 remain the complete record of what was consciously left
unbuilt.

### Details

**15.1 Status vocabulary.** Five states, each defined by the evidence it requires, so
a row cannot be promoted by optimism:

| Status | Means | Evidence required to claim it |
| --- | --- | --- |
| `shipped` | Running code in the current release | Exercised on this repo's own development, and described in §§0–13 |
| `building` | Sequenced into a phase being worked now — plan Phases 0–4 | An open work package with written exit criteria |
| `designed` | Settled in a design document but sequenced behind a later phase — plan Phases 5–6 — and **nothing is built** | A design document and a §14 row. **Not** evidence that anything enforces it |
| `researching` | The deliverable is a number rather than a capability, so this label wins over the phase band | A specified measurement whose result is allowed to cancel the work, written into the design document that owns it |
| `deferred` | Deliberately not built | Nobody has asked for it yet (§11) |

The `building`/`designed` line is drawn at the plan's phase sequence rather than at
enthusiasm, because both states have an open tracked record and only the sequence says
which one is actually in flight.

**15.2 The map.** Grouped by the four pillars of §0, because that is the axis the
whole document is organised on.

Pillar 01 — **guidance**:

| Capability | Status | Where it is specified |
| --- | --- | --- |
| One catalog projected to Claude, Codex and Copilot — instructions, skills, subagents, permissions | `shipped` | §§4–7, §9 |
| Drift gate (`basicly check`) run by CI | `shipped` | §6 |
| Path-scoped rules tier, so conditional guidance loads on a matching file instead of always | `shipped` | §7 detail 4 — engine built, **four** fragments use it today, plus a `paths:` glob on a skill at zero always-on cost; cost falls for claude and copilot and rises for codex (§14.4) |
| Invocation axis per entry: model-invoked pays context load, user-invoked does not | `shipped` | §4.2 (skills) — declared on skill sources today, not yet on fragments |
| Deterministic lexical routing evals — rank-1 rate in CI, no embeddings | `shipped` | §14.4 — `catalog lint` rules 8 and 9 on every commit, with a ratcheting rank-1 floor that cannot be lowered |
| An eval case file per catalog entry, enforced as a structural failure | `building` | §14.4 |
| Relieve the always-on baseline by scoping what is conditional | `building` | §7, §14.4 |
| Tutorial and how-to layer, so a new consumer has a path from install to first shipped unit | `shipped` | §13.1 — the tutorial was executed end to end on a fresh repo before it was written |
| Whether an individual entry changes behaviour, and which baseline rules bind while an agent works | `researching` | §14.4 — recall measured 2026-07-26; adherence still open |
| Cursor as a target; a native Codex scoped-rules renderer | `deferred` | §11 |

Pillar 02 — **gates**:

| Capability | Status | Where it is specified |
| --- | --- | --- |
| Git hook floor across pre-commit, commit-msg and pre-push | `shipped` | §4.2 |
| Agent hooks for Claude Code and Copilot | `shipped` | §4.2 |
| Verify pipeline with `fast`, `full` and `staged` modes | `shipped` | §6, §12 |
| Every gate classified by type, and a pre-flight gate that writes nothing | `building` | [`factory-loop-requirements` §5.1](../requirements/factory-loop.md) — the five gates the engine names are typed; the unnamed ones are classified in prose because they have nothing to key on |
| Severity required on judged output, plus a lint refusing a pre-judging reviewer bundle | `shipped` | §14.3 — severity as a required field, the no-pre-judging lint, the composite rubric gate and the convergence detector all landed; the deterministic half listed in §14.3 did not |
| Rework convergence detection from the open-finding set rather than the count | `shipped` | §14.3 |
| Agent-context ratchets: module size, comment density, suppression debt, tree growth | `shipped` | §12 — four `[[verify.checks]]` entries over all `.py`, frozen per file; tree growth reports rather than blocks because it has no firing history yet |
| Enforcement at the tool-call boundary, not only at the commit boundary | `designed` | [`factory-loop`](../requirements/factory-loop.md) §11 item 8 — every gate here judges an artifact _after_ it exists; `claude_settings.py` maps 2 of 31 documented host hook events, so this is engine work before it is catalog work |
| `basicly install` reporting the capability tier it actually delivered | `building` | Plan Phase 3 — enforcement is plugin-tier; on an instruction-tier host the harness degrades to advice, and we currently say so nowhere |

Pillar 03 — **the loop**:

| Capability | Status | Where it is specified |
| --- | --- | --- |
| Single-track loop, intake to retro, driven identically by any supported agent | `shipped` | §12 |
| Worktree isolation per unit of work | `shipped` | §12 |
| Parallel lanes: supervisor, lane mini-loop, serial merge queue | `shipped` | §12, §14.2 |
| Autonomy grants L0–L3 with a spend ceiling, decision queue, confined decider | `shipped` | §12 |
| Release automation up to the annotated tag | `shipped` | §12 |
| Scope sized by the material a lane actually reads, so a small change to a large module is dispatchable | `shipped` | §12 — per-file read cap measured over 185 (lane, file) pairs; a file under ~4,000 tokens is read whole and above that a lane takes out ~1,500 however large it is |
| Measured context occupancy recorded beside the forecast on every dispatch | `shipped` | §12 — `RunRecord.context_tokens`, the first measurement of the quantity the band gates on |
| Per-model spend and wall-clock forecast, enforced when a supervisor pass is admitted | `building` | Plan Phase 1 — the current forecast models working set, not turn count, and that is now **measured** rather than suspected: declared scope predicts occupancy at R² = 0.095 against 0.863 for turn count |
| A supervised multi-lane run with zero human interventions caused by a harness defect | `building` | Plan Phase 0 exit criterion |
| VALIDATE as a rung with its own gate, dispatching a validator plus a reviewer per lens | `shipped` | §12.2.1 |
| Hold and Kill as writes an operator's answer actually carries out | `shipped` | §12.2.2 |
| A named role per judgment step, each with its own instructions, tool policy, tier and output contract | `shipped` | §12.11 — six of seven roles reachable in code; `curator` is one wiring behind a live `ship` handler, a declared tier is inert at spawn until `basicly-a3yi`, and the ledger only became able to falsify it when the record started copying the real argv (`basicly-jn1x`) — no supervised pass has yet written one |
| A schema-validated handoff artifact at each state boundary | `building` | §12.12 — seven of eight kinds carry a schema; `solution-design` is markdown sections and its family membership is open |
| RETROSPECTIVE on a computed special cause, never on a single failure | `shipped` | §12.13 |
| An improvement controller that drives a codebase property to a set point | `shipped` | §12.14 — it has run live and filed a bead; it has no scheduled caller yet |
| Cost per landed package — the instrument the tier claims rest on | `researching` | §14.6 |
| Whether deterministic AST localisation cuts an implementer's pre-first-edit cost | `researching` | Plan Phase 1c |

Pillar 04 — **the work graph**:

| Capability | Status | Where it is specified |
| --- | --- | --- |
| Issues, dependencies, gate results, checkpoints and evidence in a tracked graph | `shipped` | §12.1 |
| Phase derived from tracker state, so resume is a read rather than a replay | `shipped` | §12.1 |
| Atomic publish of the shared tracker export, and a store error charged to the store rather than to the lane's rework budget | `shipped` | §12.1, [`work-tracker`](../requirements/work-tracker.md) R7 — gated by four reader processes against a live writer |
| The scheduler score and rank recorded behind each dispatch | `shipped` | [`work-tracker`](../requirements/work-tracker.md) §9.2 — every `[harness-run]` marker carries the rank, fallback rank, score and the schema version that makes the score interpretable, plus the dispatch order actually used, since a provisioned lane is claimed and the external scheduler has no opinion on it |
| A pure, age-free ranking function owned in-process | `shipped` | §12.3 — `kit/tracker/scheduler.py` behind `br.read_ranking`, emitting `basicly.scheduler.v1` |
| Owned in-process append-only event log, removing the external binary from the critical path | `building` | §14.5, `work-tracker` — eight kit modules exist and the import ran once; steps 2–4 of the cutover are unrun and the repo is `mode = "external"`, so none of it is authoritative |
| A repeatable ledger import a fresh consumer can run | `building` | §14.5 — `basicly-vkh0.23`, P0: the import is a one-shot with no entry point |
| Provenance on every edge — extracted, inferred, ambiguous | `designed` | §14.5 |
| `fsck` and `rebuild`, so "the log is the truth" is a claim someone can check | `designed` | §14.5 |
| Cross-repo work offers as self-writes in each repo's own ledger, read-only across the boundary | `deferred` | `work-tracker` |

**15.3 Not planned.** So a reader does not read absence as an oversight, the refusals
in §14.7 are permanent rather than unscheduled: an LLM orchestrator in control of the
tracker, personas spawning personas, an agent-writable catalog, a maintained TUI, an
external database or daemon, and agent-to-agent messaging. Each has a reason stronger
than taste recorded there.

**15.4 How this stays current.** A row changes state in the change that lands the
behaviour, not in a later cleanup pass, and the same change updates the two rendered
copies (README and the landing page). Nothing gates that today — it is a convention,
and the honest consequence is that a stale row here is possible; §§0–13 remain the
place a `shipped` claim has to be true.
