# Three-Nested-Loop Software Factory: Gap Analysis and Target Design

Research-first deliverable for basicly-assh.1 (epic basicly-assh). It maps the
**current** basicly loop against the target three-nested-loop architecture,
distinguishes verify from validate, checks the cross-cutting constraints, and
ends with a ranked decomposition into implementable work packages.

Evidence base: a read-only sweep of the loop engine (`loop.py`, `loop_state.py`,
`policy.py`, `merge.py`, `verify.py`, `rubrics.py`, `worktree.py`, `runner.py`,
`config.py`, `cli.py`) and the release workflow. Constraint honored: this is
design only — no loop-engine code was changed (that surface is under parallel
modification).

## The target (premise)

A deterministic software factory that turns any user input into production-grade,
releasable software on top of agent-agnostic coding agents, with three nested
control loops:

1. **Orchestrator loop** (one per session, owns the tracker): intake → decompose
   into work packages → maintain a live dependency graph → spawn/monitor/sync
   parallel lanes → handle escalations (autonomous or interactive) → when all
   lanes land, integrate → verify → validate → ship (cut release). Verify/validate
   here are never skippable.
2. **Lane loop** (one per work package, isolated worktree): plan → decompose into
   tasks → prioritize → per task build → verify → validate → finalize (verify/
   validate skippable when genuinely unnecessary) → integrate package → verify →
   validate → signal the merge queue.
3. **Merge queue** (one per session): receive ready signals, decide merge order,
   merge to base, resolve conflicts.

Cross-cutting: agent-agnostic, engine-enforced determinism where it matters, and
consumer customization that survives `basicly install`.

## Verdict up front

basicly today is a strong **single-track state machine plus a serial merge
queue** — effectively a mature Loop 2 (lane) and Loop 3 (merge queue), with the
cross-cutting constraints largely met. The **Loop 1 orchestrator does not exist
as an engine component**: there is no session owner that intakes a requirement,
decomposes it into work packages, drives many tracks together, or cuts a release.
That role is currently played by a human (or an agent like me) re-invoking
`basicly loop advance` per track. The largest gap, and the highest-value build,
is the orchestrator.

## Loop 1 — Orchestrator (session): MOSTLY MISSING

Current state (evidence):

- `basicly loop` is strictly **per single issue/track**. `advance(repo_root,
  issue_id, ...)` steps one issue (`loop.py:464`); `run_until_blocked(...,
  issue_id, max_steps=20)` chains steps on **the same one issue** (`loop.py:495`),
  not across a ready set. The module docstring is explicit: "this drives a single
  track" (`loop.py:25-29`). CLI exposes only `loop status/advance/run`, each on
  one `args.issue` (`cli.py:2073-2140`).
- The only requirement→packages step is **decompose** at `_on_classify`
  (`loop.py:151`), driven one parent-track at a time, and the child plan is
  agent-supplied (`Inputs.children`, `loop.py:67-72`). There is no intake of a
  free-form requirement above the track.
- Multi-issue awareness exists but is **read-only**: `fleet.py`/`fleet_report`
  and the status/health rollups (`cli.py:571,628`) report but never drive.
- **Release-cut is not in the loop**: `.github/workflows/release.yml` triggers on
  a **human-pushed `v*` tag** (`release.yml:4-7`); there is no `basicly release`
  command. The loop's ship closes a *track*, not a *release*.

Gap: the session-level intake → decompose-into-packages → drive-many-lanes →
integrate → verify → validate → release close-out. Monitoring/sync across lanes
and autonomous release cutting are absent.

## Loop 3 — Merge queue: PRESENT (serial, safe, stop-on-conflict)

Current state (evidence) — `merge.py`:

- `merge_queue(repo_root, items, ...)` iterates caller-ordered `(name, bead)` and
  lands each via `merge_worktree`, **stopping at the first node that does not
  cleanly merge** (`merge.py:270-300`).
- `merge_worktree` is a 4-step, base-checkout pipeline: rebase onto base
  (conflict → abort → `rebase-conflicts`); re-verify after rebase
  (`verify.run_verify`); a **non-destructive** conflict probe
  (`git merge-tree --write-tree --name-only` before touching any tree,
  `merge.py:53-69`); then `git merge --no-ff` (failure → abort, base left clean)
  (`merge.py:180-258`).
- Ready-signal intake = `_worktree_land_readiness` (`merge.py:85-110`):
  uncommitted tracked changes or zero commits ahead → `not-ready`, which blocks
  **without** burning a rework attempt (`merge.py:209-211`).
- Merge **order** is the caller's responsibility ("the queue lands the given
  order serially", `merge.py:11-13`).
- Conflicts are **reported/blocked, never auto-resolved**; on a real failure the
  queue records a rework attempt and flags `escalate` at `max_rework` (default 2)
  (`merge.py:296-299`).

Gaps vs target: (a) merge-**order decision** is delegated to the caller, not
decided by the queue; (b) conflict **resolution** is out of scope — conflicts
escalate rather than being resolved. Both are acceptable-but-manual today.

## Loop 2 — Lane (work package): PRESENT and mature

The single-track state machine *is* the lane loop. Phases
(`loop_state.PHASES`, `loop_state.py:35`): `intake → classify → decompose →
build → verify → ship → done`, with phase **derived** from the strongest `br`
signal (`derive_phase`, `loop_state.py:106-143`) rather than stored.

- **Isolation** (`worktree.py`): `create(name, base)` provisions the lane — a
  `harness/<name>` branch, sibling `<repo>.worktrees/<name>` path, a
  `.beads/redirect` to the base tracker (one shared DB, no divergent copy),
  standalone `.venv`/`node_modules`, and the repo hooks (`worktree.py:220-286`).
  `cleanup` refuses removal with pending real work unless forced
  (`worktree.py:338-422`). This is a clean, real lane.
- **Decompose → parallel-sizing is deterministic**: a plan supplies children with
  a mandatory `scope` (globs); `group_children` union-finds glob overlap and
  serializes overlapping scopes while leaving disjoint ones parallel
  (`decompose.py:145-198`). "Refusing to guess is the whole point"
  (`decompose.py:56-57`).
- **Per-task build/verify/finalize + bounded rework**: `_start_build_leaf`
  claims + provisions + dispatches the agent headless (`loop.py:244-302`);
  `_verify_and_land` rebases, re-verifies, probes, and `--no-ff` merges
  (`merge.py:180-258`); `_rework` records a marker and escalates at
  `max_rework` (default 2) (`loop.py:399-403`, `policy.py:163-171`).

Gaps vs target: (a) a lane does **not sub-decompose into small tasks
internally** — a "feature" fans out into separate *child tracks*, each its own
lane, rather than one lane running an internal task list; (b) the target's
per-task **validate** step is not part of the lane loop (see below); (c) the
target's "prioritize" is delegated to `br scheduler`, which is fine but not
lane-local.

## verify vs validate: the distinction exists in code but validate is dormant

The premise separates **verify** (does it work / gates pass) from **validate**
(does it meet the requirement). basicly already has both primitives — but only
verify is wired as a first-class, blocking, run-by-default step.

- **verify** — required, deterministic, **blocks**. `run_verify` runs the
  `[[verify.checks]]` for a mode and `report_gate` records `gate verify`
  (`verify.py:154-203`); `verify` is in `DEFAULT_REQUIRED_GATES`
  (`config.py:304`), so `policy.gate_status.can_advance` is false while it is
  missing/failing (`policy.py:108-130`).
- **validate** — semantic, advisory, **never blocks**, and **not run
  automatically** by the loop. `rubrics.evaluate` runs deterministic + judged
  checks and records `gate rubric` (`rubrics.py:209-271`), but `rubric` is not
  in `required_gates`, so it is classified `advisory` (`policy.py:123`;
  `rubrics.py:15-17`). Nothing in the loop invokes it per task; a consumer must
  run `basicly rubric eval` by hand and promote the gate to make it matter.

So the target's "verify → validate" per level maps onto `verify` (done) +
`rubric` (present but dormant). The **skippability** the premise wants (never at
orchestrator, sometimes at lane) has **no representation**: there is one
`required_gates` config, not a per-level policy.

## Cross-cutting constraints: all THREE hold today

- **Agent-agnostic — holds.** `RunnerSpec` + `BUILTIN_RUNNERS`
  (claude/codex/copilot/manual); `select_runner` honors an explicit runner, else
  `auto` walks `AUTO_ORDER` and falls back to a manual handoff — never guesses an
  unknown CLI (`runner.py:109-120, 299-334`). Model pinning via
  `RunnerSpec.model` (`runner.py:178-190`). The loop prompt points at the
  tracker, not an agent (`loop.py:327-339`).
- **Customization survives `basicly install` — holds.** The gitignored
  `.basicly-local` overlay shallow-merges over `basicly.toml`
  (`config.py:346-364`); managed-block hook rewrites strip only basicly-owned
  hooks by id and preserve foreign repos/comments/order via a round-trip
  (`hooks.py:241-341`); convergence compares semantically without clobbering
  (`hooks.py:354-380`).
- **Engine-enforced determinism — holds.** Blocking: required `verify` gate,
  Definition-of-Ready, the three checkpoints, bounded-rework escalation, the
  base-checkout guard, the ship landing guard. Advisory: `rubric` and any gate
  outside `required_gates`.

These three are the premise's hardest constraints, and they are already met —
no work package is needed for them beyond regression protection.

## Gap summary

| Target capability | Loop | Status | Note |
| --- | --- | --- | --- |
| Session intake of a free-form requirement | 1 | **missing** | no owner above a single track |
| Decompose requirement → work packages | 1 | partial | `decompose` exists per parent-track; no session-level requirement intake |
| Live dependency graph | 1 | present | delegated to `br scheduler`, re-read each step |
| Spawn / monitor / sync parallel lanes | 1 | partial | per-feature fan-out exists; monitoring is poll-by-re-read; no session driver iterating many tracks |
| Escalation autonomous vs interactive | 1 | present | checkpoints + confirm-code + needs-input + bounded rework |
| Integrate → verify → validate → ship (release cut) | 1 | partial | per-track ship exists; release-cut is manual/CI; validate not wired |
| Lane: plan → decompose → prioritize | 2 | partial | decompose + `br` prioritize; no lane-internal task list |
| Lane: per-task build / verify / validate / finalize | 2 | partial | build/verify/finalize present; validate advisory + dormant |
| Lane: integrate → verify → validate → signal queue | 2 | mostly present | `merge_worktree` re-verifies + signals; validate missing |
| Merge queue: ready-signal intake | 3 | present | `_worktree_land_readiness` |
| Merge queue: decide merge order | 3 | partial | caller supplies order |
| Merge queue: merge to base | 3 | present | rebase + probe + `--no-ff` |
| Merge queue: resolve conflicts | 3 | **missing** | conflicts escalate, never auto-resolved |
| Agent-agnostic | X | present | RunnerSpec / select_runner |
| Determinism engine-enforced | X | present | required gates + checkpoints |
| Customization survives install | X | present | overlay + managed blocks |

## Proposed decomposition (ranked work packages)

Each becomes a child of basicly-assh. Ranked by foundation-first value.
**Collision note:** WP1/WP3/WP7 touch `loop.py`/`loop_state.py`/`policy.py`,
which are under parallel modification — sequence them after coordinating.
WP2/WP4/WP5 land mostly in new files or `merge.py`/`cli.py` and are safer to
start in parallel.

| # | Work package | Loop | Depends on | Size | Rationale |
| --- | --- | --- | --- | --- | --- |
| WP1 | **Session orchestrator driver** — a `loop session`/`orchestrate` command that iterates the `br` ready set across many tracks, driving each via the existing `advance`, honoring concurrency | 1 | — | M | The single missing keystone; turns N per-issue invocations into one session owner. Reuses `advance` + `ready_ranked` |
| WP2 | **Requirement intake → work-package decomposition** — free-form requirement to an epic + parallel-sized children (assisted, scope-declaring) | 1 | WP1 | M | The premise's "any user input" entry point |
| WP3 | **Validate as a first-class step + per-level skippability policy** — run `rubric` in-loop; make verify/validate never-skip at orchestrator, skippable at lane | 1/2 | — | M | Activates the dormant validate primitive; encodes the premise's skippability rule |
| WP4 | **Autonomous release-cut** — `basicly release`: version bump + CHANGELOG section + tag, triggering the existing CI publish | 1 | WP1 | M | The orchestrator's "ship" close-out; today fully manual |
| WP5 | **Merge-queue self-ordering** — the queue computes topological/priority order from `br` deps instead of caller-supplied | 3 | — | S | Completes Loop 3; isolated to `merge.py` |
| WP6 | **Conflict-resolution assist** — a bounded agent pass to resolve rebase/merge conflicts before escalating | 3 | WP5 | L | Highest uncertainty; keep last |
| WP7 | **Lane-internal task decomposition** — a lane runs an internal task list (build/verify/validate/finalize per task) distinct from spawning child tracks | 2 | WP3 | L | Closes the Loop 2 gap; deepest engine change |
| WP8 | **Parallel-lane monitor/sync** — a live session view + cross-lane sync points | 1 | WP1 | M | Turns poll-by-re-read into real monitoring |

**Recommended first slice:** WP1 (keystone) + WP5 (independent, small, completes
the merge queue) + WP3 (activates validate). WP2 and WP4 follow to close the
orchestrator's intake and release ends. WP7/WP6 are the deep/uncertain tail.

These are proposed, not yet filed — filing and sequencing the children is the
epic's next move and must be coordinated with the in-flight loop-engine work.
