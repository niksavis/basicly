# basicly Implementation Plan

The route from the current release to `v1.0.0`. Rewritten 2026-08-03 against `main` @ `cbbd47a`
to be short, current and actionable: what is true now, what ships next, in what order, and why
that order. Historical narrative was removed rather than kept — the tracker holds the incidents
and `git log` holds the sequence.

This file plus [`architecture.md`](../architecture/architecture.md) are the whole picture.
Architecture is the human-readable account of concepts, structure and settled decisions; this
file is the ladder. Every other document under `docs/` is temporary and is deleted when its
design becomes code plus architecture prose (§11).

## 1. How to work from this file

The contract for whoever — human or agent — picks up work here.

1. **The code is the authority.** This file sequences; it does not define behaviour. Where the
   two disagree, the code is right and this file is stale. Where this file and architecture
   disagree about *what the system is*, architecture wins.
2. **A bead's claim is a claim.** Every defect row below cites a `file:line` that was re-checked
   at the named commit. Re-check it again before you fix it: the code may have moved, and a bead
   written weeks ago may describe a defect someone else already closed.
3. **A closed bead proves code exists, not that a gate binds.** Before trusting any gate, call
   its own functions on real inputs. A fail-open gate is indistinguishable from a passing one.
4. **Ask the tracker for counts, never this file.** `br` is always right about status. Use
   `.beads/issues.jsonl` for whole-tracker questions — `br list --json` caps its result and drops
   closed rows. Structural figures that *are* in this file come from a generated block (§3).
5. **Measure before you dispatch.** Take the baseline in base, record it on the bead, diff against
   it at review. A lane that rewords a number instead of measuring it produces a regression under
   an acceptance criterion claiming improvement.
6. **One unit of work per bead; order and reasons live here.** A graph of 50 beads does not make
   a sequence legible, and the tracker is itself scheduled for replacement (§6).

## 2. Destination

`basicly` is a harness for coding agents that ships its own development process. Four pillars
(architecture §0), each done only when it is true, enforced *and* measured:

| Pillar | Done means |
| --- | --- |
| Guidance | Every entry's routing and behavioural effect is measured; nothing ships on assertion. Conditional guidance is path-scoped, not always-on. The delivered guarantee per agent family is stated rather than implied. |
| Gates | Every gate is classified by type, so "what happens when this fails" is answered by the type and not per call site. Judged output carries a required severity and cannot pass a required gate. Rework detects non-convergence instead of burning its cap. |
| The loop | Every deterministic step is one command. Every judgment step is routed to a named role at a tier chosen by measured reliability. A supervised multi-lane run completes with no human intervention caused by a harness defect. |
| The work graph | Owned in-process: an append-only event log we control, with provenance on every edge, no external binary and no bootstrap step in the critical path. |

Three invariants constrain *how* any of it may be built:

1. **The engine disposes, agents propose.** No model holds authority over the tracker, the
   schedule, or a required gate — at any autonomy level.
2. **Determinism where the answer is derivable.** A model is paid only where it is not, at the
   tier that can be relied on, priced per landed package.
3. **Evidence over assertion.** An unmeasured behavioural claim costs context every turn and
   confers confidence nobody earned.

**`v1.0.0` means three things**, all required (owner, 2026-07-30):

1. Every agreed design is implemented — architecture §14's target state is running code, and each
   design document is absorbed and deleted (§11).
2. The consumer criterion is *demonstrated* (`basicly-ctdz`): a fresh repo with only git and a
   uv-provisioned Python interpreter installs basicly, runs every gate and drives the loop end to
   end, with no external `br` binary.
3. A full semver contract: the CLI surface, `basicly.toml`, the catalog source schemas, the
   generated-file contract and the owned ledger format are declared stable. Every one of those
   broke within the last two minors, so the promise needs a stabilization release (§9), not a
   version-number ceremony.

**Non-goals**, so the plan cannot quietly grow: an LLM orchestrator; personas spawning personas;
an agent-writable catalog; a general-purpose issue tracker; a maintained TUI; an external database
or daemon; agent-to-agent messaging. Reasons are in architecture §14.7.

## 3. Current state

Structural figures are generated from the tree by `.scripts/docs_claims.py` and gated on every
commit, because every hand-written copy of them was stale within days:

<!-- docs-claims:begin plan-current-state -->

| Measure | Value |
| --- | --- |
| Engine modules (`src/basicly/*.py`) | 45 |
| Test files | 84 |
| `[[verify.checks]]` declared | 19 |
| …of which run in `--mode fast` | 14 |
| …of which run in `--mode full` | 18 |
| …of which run in `--mode staged` | 3 |

<!-- docs-claims:end plan-current-state -->

Two kinds of figure are deliberately absent. **Tracker counts** move several times per session, so
generating them would rewrite this document during unrelated lanes and dirty the base checkout a
landing refuses on. **Always-on character sizes** are already a generated block in
`architecture.md`; a second copy here is the duplication this exercise removes.

**Shipped and dogfooded** (`v0.6.0` plus the unreleased work): catalog and projection with drift
gates; the git and agent hook floor; the single-track loop; worktree isolation; the concurrent
supervisor with lanes and a serial merge queue; autonomy grants L0–L3 with a spend ceiling; the
decision queue and corpus-bounded decider; release automation to the annotated tag; the sizing
band and its governor; read-capped scope sizing; per-model spend forecasting; tier→model
resolution with recorded provenance; `basicly loop preflight`; atomic publication of the shared
tracker export.

**Not built** (verified at `cbbd47a`): no role registry and no persona routing — the two `src/`
hits for "persona" are prose; no lexical ranker; no `severity` field anywhere in `src/`,
`.basicly/core/rubrics/` or `schemas/` — the one `supervise.py` hit is a comment; no `evals/`
directory. The tracker is still the external `br` binary, reached through two `subprocess.run`
sites in `br.py` behind the call sites `basicly-tcmy.14` exists to unify.

**Three facts that are easy to get wrong**, so they are stated rather than left to be re-derived:

| Claim | Measured reality |
| --- | --- |
| "We lack a path-scoped guidance tier" | Built and **in use**. 2 of 21 fragments declare `paths:` (`platform-hermetic-tests`, `external-review`), each projected to `.claude/rules/{id}.md`. The remaining work is authoring plus the empty-glob check (§8). |
| "Scoping a fragment shrinks the always-on baseline" | Only for Claude and Copilot. **Codex pays**, ~1500 chars per scoped fragment (1462 and 1614 measured), because it has no glob scoping and inlines them. `AGENTS.md` has ~1225 chars of headroom against a 12000 cap, so the *next* scoped fragment overflows it. Any claim that the baseline shrank must name the family. |
| "The baseline is past the recall cliff" | **Refuted**, 2026-07-26 (`basicly-agzx.1`): claude recalls 98% of its 53 always-on rules, copilot 93% of 54, against 17% / 6% no-guidance controls. Read it narrowly — recall under a direct cue is an upper bound and may **not** be cited as evidence of quality. Codex is unmeasured, its CLI being absent. |

## 4. The release ladder

A **release** is a shippable cut; the phase labels in the tracker (`phase-0` … `phase-7`) are the
dependency clusters a cut draws from (§14). Rows are in shipping order.

| Release | Content and reason | Size |
| --- | --- | --- |
| ~~**`v0.7.0`**~~ | **SHIPPED 2026-08-06.** Trustworthy factory. 19 beads closed over two sessions. **Exit criterion 5 was not met** — see §5.1. `basicly-yc0x`. | shipped |
| **`v0.7.1`** | **End the shared-anchor collision class**, and carry the unattended-run proof `v0.7.0` could not. `4746` (a changelog fragment per lane, so collision is impossible by construction), `bdd4` (dispatch a bounced lane to resolve its conflict rather than replaying the rebase), `3f76` (design docs stop carrying bead-id lists), `m4zv.5` (a stalled rework round escalates without spending the cap). A patch, not a minor, **only if** a curated `[Unreleased]` body keeps working alongside fragments. | 1–2 sessions |
| **`v0.8.0`** | **Own the work graph — the store, not yet the floor.** The owned event log exists and is checkable: provenance, ids, snapshot with rotation, `fsck`/`rebuild`, import with tombstones, a shadow differential that refuses a self-agreeing comparison, and a `[tracker] mode` rung that flips the one record-read seam. **It does not remove `br` from the consumer floor**, and the row said it did until 2026-08-07 — measured at `MODE_OWNED` with no `br` on PATH, `gate list` and `lint` still raise "the harness requires the beads tracker", because `.19` flips `read_record` alone and 44 further spawn sites remain, 26 of them `comments`. That claim moves to `v1.0.0`, where the fresh-consumer acceptance test makes it falsifiable instead of asserted (`basicly-vkh0.22`). Plus streaming telemetry (`wctc`, `jr0l.66`). `basicly-vkh0`. | 5–8 sessions |
| **`v0.9.0`** | **Evidence, gates, docs.** Cost per landed package (`7bur`), AST localisation (`agzx.2`), the Phase 2 deterministic gates (`m4zv.2`–`.6`) built against the owned tracker, the D4 amendment, the tutorial layer (`imnu.2`), the install capability tier (`imnu.3`), the ceremony threshold (`imnu.5`), parameter learning (`3ifz`). | 6–9 sessions |
| **`v0.10.0`** | **The judgment layer and always-on relief.** The roster (`s2xf`), gated on `7bur`'s numbers by construction; the Phase 4 authoring pass and the empty-glob check (`a3ab.1`–`.3`). | 5–8 sessions |
| **`v1.0.0`** | **Stabilize and declare.** Surface audit and semver freeze, the breaking-marker gate, the fresh-consumer acceptance test. `1.0` is a promise, so the last release proves the promise instead of adding capability. | 3–5 sessions |

Sizes are sizing signals for decomposition, not commitments.

**Why `v0.7.0` precedes `v0.8.0`, and why the two were split** (owner, 2026-08-03): the ladder
previously wrote one release for both. `u6jq.1`'s whole value is that it *measures*, and it cannot
complete today because `basicly-gczc` halts the grant on any delegated decision — so a
measurement taken now measures the meter. Meanwhile Phase 6's build is undecomposed. Shipping the
proof of the factory and the replacement of the factory's state store in one cut serves neither.

**The ladder's invariant**: a row must name every open bead its own entries are blocked on, so a
reader who starts at the top of a row is never sent to a blocked bead. Check it against the
tracker's edges rather than by eye — `br dep tree <id>`, not judgement.

## 5. `v0.7.0` — trustworthy factory (next)

Tracked by `basicly-yc0x`. Track A's evidence was re-verified at `cbbd47a`, line by line; Track B's
and Track D's rows carry their beads' own evidence and should be re-checked before they are worked.

**Status after the 2026-08-04 session.** Six beads shipped — `23ep`, `7kxq`, `uexy`, `irrm`,
`qorx`, `toj6` — at a measured **78.5M tokens / $59.65**, a mean of **13.1M and $9.94 per lane**
across six samples. That supersedes the single-sample ~17M/$12 figure recorded on 2026-08-03: the
spread is wide (5.6M to 25.1M) and the mean is roughly half the top of it, so **size a pass on the
mean and treat any single lane as a poor estimator of the next one**. Every dispatch metered
`estimated: False`, so `gczc`'s meter fix holds across all seven.

**The 2026-08-03 rows below are corrected, not just extended.** `tcmy.5`, `tcmy.6` and `tcmy.22`
were recorded as SHIPPED; each in fact crossed the context ceiling and **finalized early**, leaving
`tcmy.39`, `tcmy.38` and `tcmy.37` to carry the remainder. They are partial landings. The cause was
not their size — see `23ep` below.

**The finding that reorders everything (`23ep`, closed).** `runner.py` declared the claude context
window as **200000** while the dispatched model (Claude Opus 5) has **1000000**, so the finalize
trigger sat at 120000 instead of ~600000 and **truncated healthy lanes for months** — twelve
`(context-ceiling overrun)` follow-up beads, eight still open at the time. The repo's own telemetry
had refuted the constant all along: a recorded occupancy of 223221 cannot fit a 200000 window. The
fix declares the window per agent in `basicly.toml`, records its provenance, and ships
`runner.window_violations` so the declaration is falsifiable against the ledger. **Validated on this
session's own six lanes**: occupancies of 403051, 208904, 200996, 173228, 128113 and 123312 — every
one over the old trigger, none near the corrected one. Six of six would have been spuriously
truncated. Zero new overrun follow-ups were spun.

**Its sibling (`7kxq`, closed).** Probing `23ep`'s gate showed the finalize protocol had exactly one
caller, in `supervise.py` — so the **single-track `loop run` path recorded occupancy and never acted
on it**. The two write paths disagreed about a bead's fate for reasons unrelated to the bead. Both
now share one `meter_context_ceiling`. This was Phase S's shape exactly, an instrument built and
never connected, and `uexy`'s gate would have caught it.

**Deferred out of the release, deliberately:** `o486` (P2) carries the working-set band's own
calibration — the band gates on a proxy running 3.16x–12.72x low against measured occupancy across
nine pairs, and `config.py`'s maximum was derived by re-applying the estimator to its own output. It
is blocked on `23ep` because calibrating against a trigger that was wrong by 5x would fit the error.

Three findings from the 2026-08-03 session still stand, and one is the remaining P0:

- **`tcmy.34` (P0, blocks `u6jq.1`)** — the dispatch forecast is **269× low at the median** over
  14 paired records (range 1×–591×). The narrowing that makes it tractable: the engine already
  computes a realistic whole-lane figure for pass admission (18.6M/lane, against measured 17M,
  15.8M, 10.7M) while recording a *working-set* figure in the field named `forecast_tokens`. Two
  estimators ~280× apart, and `jr0l.34`'s pairing compares one against the other. **Fix the unit
  before fitting anything** — a calibration on the current field is a correction factor on a units
  error, which is how `z2wi` reached 216.65 against a seed of 3.0.
- **`tcmy.35` (P1, blocks `7bur`)** — nothing in this repo declares a model tier, so every dispatch
  runs unpinned. `models.py:69` refuses only when a tier *was* declared, so the refusal is
  unreachable; `tier_honoured` is null while `observed_models` shows two models served one lane.
- **`qorx` gained a blast radius.** `tcmy.5` widened its own scope mid-flight from the 8 globs it
  was admitted on to 16, completed at 130,780, and its finishing record failed the tracker-wide
  ceiling gate — **for its two siblings as well**, because a pass shares one `.beads` through the
  redirect. Each was charged rework for a defect in neither diff. Resolved for now by deriving the
  ceiling to 132,000 (`b6c5685`); the ratchet is untouched.

**Two gates proved they bind rather than merely existing**, which is the distinction §1 calls the
sharpest recurring lesson: pass admission refused a 1.7% forecast overrun instead of dispatching,
and the grant halted `gczc`'s ship when its budget was spent. `4tjt` also fired correctly — an
answered `retry` granted one further attempt instead of instantly re-escalating.

### Track A — lights-out blockers, and they gate the proof run

Each carries a `blocks` edge into `basicly-u6jq.1`, so the tracker refuses to hand out the proof run
first. **`jr0l.65` is the only one still open.**

One structural note earned the hard way on 2026-08-04: `supervise` fans out over a root's
`parent-child` dependents, so a release epic that declares its blockers as `blocks` edges has **no
children and cannot be a pass root at all**. `yc0x` was filed that way and `preflight` reported
`0 open child(ren)`. Membership is now expressed as `parent-child`, which costs nothing — an epic
with open children does not close either — and the four beads adopted had no parent of their own, so
§14's rule that phase membership is a label rather than a re-parenting is intact. Attempting both at
once is refused as a cycle.

| Bead | Evidence at `cbbd47a` | Fix shape |
| --- | --- | --- |
| ~~**`gczc`** P0~~ **SHIPPED 2026-08-03** | `decisions.py:655` calls `runner.run` with no `capture_usage`. `policy.py:1305-1319`: one `estimated=True` record that is not `unstarted` sets `halted=True`. So one delegated decision ends the grant. | **Not a one-liner.** With `capture_usage=True`, claude's stdout becomes a JSON envelope and `decisions.parse_verdict` (`:562`) takes first-`{`→last-`}`, so it would parse the envelope and fail closed to abstain. `rubrics.py:281` omits the flag for exactly that reason. Root fix: a `result_text(spec, stdout)` unwrapper in `runner` (claude-json → `.result`, stream-json → last result event, codex-jsonl → last message; copilot's `--session-id` never touches stdout) with both call sites routed through it. Fallback: treat a corpus-bounded decider floor like `unstarted` — cheaper, but it under-meters a real agent run, so record that. |
| ~~**`tcmy.5`** P1~~ **SHIPPED 2026-08-03** | `loop.py:627` records `phase="build"`; `supervise.py:2120,2226` record `phase="lane"`. `decompose.unsized_lane_tokens` reads only `lane`; `calibrated_build_factors` filters on no phase at all, so decider and rubric dispatches are build-factor samples. | One named write-phase set read by both consumers, and a seeded factor recorded as seeded rather than measured. Same family as `z2wi`: a number compared against a number denominated in a different quantity. |
| ~~**`tcmy.6`** P1~~ **SHIPPED 2026-08-03** | `policy.gate_from_unreliable_escalation` (`policy.py:560`) has zero production callers — only `tests/test_loop.py:2171` and `tests/test_policy.py:1689`. `cli.py:3421` calls `gate_from_rework_escalation`, whose regex does not match the unreliable question. | Answering "land anyway" implements nothing, so the flake re-trips and the identical question re-enqueues under the next generation. Carry out the override once, or stop offering it. |
| **`jr0l.65`** P1 — **the only Track A item left** | `_live_session_violations` counts needs-input markers by text; only `_issue_is_closed` discounts them. **Line refs re-verified 2026-08-04 and corrected on the bead**: the two functions are at `policy.py:1438` and `policy.py:375`, not the `1394`/`1430` this row cited. | Discount an *answered* marker exactly as a closed bead's is — a third case of the rule both docstrings already state, that a marker on closed work is history rather than live state. `decisions.answer` writes a second marker with the same id, so resolution is readable and no schema growth is needed. Smallest item in the track. |
| ~~**`toj6`** P1~~ **SHIPPED 2026-08-04** | `supervise.py:321` defined open children as `status != "closed"`, so a `deferred` bead was sized into the band, counted in `children_open`, and funded. | Excluded `deferred`. The unsized-child question stays with `jr0l.61`. |
| ~~**`qorx`** P1~~ **SHIPPED 2026-08-04, re-scoped first** | The row below described the *ratchet*, and that half moved to `o486`: `23ep` replaced the derivation, which removed the self-declared input. | What actually shipped is the half the bead never carried, though `config.py`'s comment claimed it did — **the cross-lane blast radius**. A pass shares one `.beads`, so `tcmy.5`'s failing record charged rework to two siblings for a defect in neither diff. Now attributed to the lane whose declaration invalidated the gate. |
| ~~**`23ep`** P0~~ **SHIPPED 2026-08-04** | Filed this session. `runner.py` declared claude's window at 200000 against a dispatched 1000000, so the finalize trigger sat at 120000 and truncated healthy lanes; the ledger already refuted it at 223221 occupancy. | Window declared per agent in `basicly.toml` with recorded provenance, plus `runner.window_violations` to keep the declaration falsifiable against the ledger. **Do not re-fix by writing a bigger constant** — that is the same unchecked declaration one generation on. |
| ~~**`7kxq`** P1~~ **SHIPPED 2026-08-04** | Filed this session, found by probing `23ep`'s gate. The finalize protocol had one caller, so the single-track `loop run` path measured occupancy and never acted on it. | One shared `meter_context_ceiling` called from both write paths, replacing the supervised path's inline copy — a duplicated ceiling is how the two came to disagree. |

### Track B — close Phase S: the gates that never existed

Phase S was inserted because every defect found on 2026-08-02 had one shape: **an instrument
built and never connected.** `permissions-check` shipped wired to no gate; the import contract
forbade modules that cannot exist and reported `1 kept, 0 broken` forever; `.scripts/recall_eval.py`
was built, run once and wired to nothing. Its sizing half is closed (`z2wi`, `3w44`, `ipx2`,
`fcls`, `8ry8`, `vkh0.10`, `jr0l.64`, `vaal`); its two gates were rows in a document with no bead
until 2026-08-03.

| Bead | Work |
| --- | --- |
| ~~**`uexy`** P1~~ **SHIPPED 2026-08-04** | **Wired-or-deleted.** `vulture` was declared at `pyproject.toml:37` and called from nowhere; it now runs as a declared verify check and the gate fails on a symbol referenced only inside its own module or under `tests/`. `tcmy.21` remains the deletion half. |
| ~~**`irrm`** P1~~ **SHIPPED 2026-08-04** | **Exercised-or-unproven.** No release tag while a shipped capability has zero executions in the ledger — the deterministic form of the rule that a consumer-facing capability claim must be exercised before it is published. **Its inventory surfaced a live false claim**: 221 dispatch records hold only `claude` and `manual`, so `codex` and `copilot` have never run while the README advertises all three; 8 of 34 skills and none of the 8 shipped tool skills are exercised. Recorded as its own finding, not silently absorbed. |
| ~~**`tcmy.22`** P1~~ **SHIPPED 2026-08-03** | **Fix the suite the release rests on.** The git stub returns `_Proc(0)` for any unstubbed subcommand across 35 instantiations in `test_merge.py` alone; `work_repo` copytrees 331 MB including the live tracker DB and the gitignored local overlay; `conftest.py` resets neither `runner._BUDGET` nor `session._OVERRIDES`. |
| **`m4zv.14`** P1 | **Signature-forgiveness half only.** The machine-global `~/.beads/.write.lock` makes the pytest gate flaky and each flake spends a rework attempt against a cap of 2. The root fix is the `v0.8.0` flip, so one more release pays the flake — but a recognised signature must stop it charging rework. |

### Track C — the release event

**`u6jq.1`** — re-run the dogfood shape as a supervised multi-lane run. **Two open blockers as of
2026-08-04**: `tcmy.34` (the 269× forecast miss — a proof run measured through that forecast measures
the forecast) and `jr0l.65`. Everything else that gated it has closed.

Size it on the six-lane mean of **13.1M tokens / $9.94**, so a three-lane proof needs ~40M — not the
10M this row once cited, and not the ~55M implied by the single 25.1M outlier. It doubles as the
telemetry run for the tracker surface. Run it behind `basicly loop preflight` and the forecast gate:
the first attempt cost $34.16 for 46.0M tokens, 13.7× the 3.36M baseline, and failed its criterion.
**Cost is bounded by sizing the work, never by interrupting a working agent.**

**What the 2026-08-04 session already demonstrated, short of the criterion.** A four-lane supervised
pass ran `uexy`, `irrm`, `qorx` and `toj6` concurrently through the serial merge queue to `done: yes`
with no human editing code, and a preceding two-lane sequence landed `23ep` and `7kxq` — six beads,
zero new overrun follow-ups, every dispatch metered. That is not `u6jq.1`: the pass needed **three
human approvals** (the L3 grant, its top-up, and the epic's own `decompose` checkpoint, which a
covering grant cannot serve itself), and the criterion is zero interventions attributable to a
*harness defect*. Those three are gates working as designed, so the honest reading is that the
remaining distance is `tcmy.34` and `jr0l.65`, not the fan-out mechanics.

### Track D — bug fillers, opportunistic

Take where a lane has remainder; drop without renegotiation: `tcmy.19` (the beads redirect
resolved in two places), `tcmy.25` (scope read cost reads binaries as text), `ky5z`, `1pcl`,
`y2uh`, `kjc5.57`.

### Exit criteria

1. Every Track A bead closed, each with a regression test **proven red first** (`kjc5.49`).
2. A pass that delegates a decision does not halt its grant — proven by calling
   `policy.session_spend` and `spend_status` on the real record set, not by reading the diff.
3. `vulture` runs as a declared verify check and the wired-or-deleted gate fails on a planted
   unreferenced symbol; the exercised-or-unproven gate fails on a planted zero-execution
   capability.
4. An unstubbed git subcommand fails a test loudly.
5. `u6jq.1` completes with zero human interventions attributable to a harness defect.
6. `[Unreleased]` in `CHANGELOG.md` is curated **before** `basicly release` runs — the command
   promotes that body into the dated section and the workflow reads the tagged commit, so curating
   afterwards never publishes.

**Explicitly out**: the tracker replacement, `7bur`, `agzx.2`, `m4zv.2`–`.6`, the roster, the
Phase 4 authoring pass, and everything in §9.

### 5.1 `v0.7.0` shipped with exit criterion 5 unmet — the record

Tagged 2026-08-06. Criteria 1–4 and 6 were met and verified by exercise. **Criterion 5 —
`u6jq.1`, a supervised multi-lane run completing with zero human interventions — was not**,
and the release documents that rather than claiming it.

Four attempts over two sessions. Each failed on a *different* file, and three of the four on
one shape: **two lanes editing the same anchor in a file no bead declares.**

| Attempt | Blocked on | Outcome |
| --- | --- | --- |
| 1 | `CHANGELOG.md`, three lanes at one `### Fixed` anchor | 2 of 3 landed; `o8p0` filed and fixed |
| 2 | `pytest` red on `main` — the spend gate compared a failed dispatch and an `assumed:` placeholder against whole-lane forecasts | fixed at the root; gate now 0 violations, median 0.96x |
| 3 | `.basicly/generated-manifest.json` | 2 of 3 landed; `lyro` filed and fixed |
| 4 | `docs/design/tier-injection-kit.md` §7, which lists two of the running beads by name | 2 of 3 landed |

**The proof run did its job.** It found three real defects that four prior supervised passes
never surfaced, and all three shipped. What it could not do is pass, because the remaining
cause is a **convention, not a bug**: while lanes edit shared prose at one anchor, no pass
completes unattended, and the enumerate-the-paths approach can never finish — nobody predicted
a design document's open-items list.

Two structural facts make retrying pointless, and both are now filed:

1. `o8p0`'s remedy is **advisory** — it warns and recommends a build order; it cannot prevent
   a collision, and it only knows paths someone declared.
2. **A rebase bounce cannot converge under rework.** Attempt two replays the identical rebase
   against the identical moved anchor. Observed three times in one session; it is `m4zv.5`'s
   thesis, and it means every prose collision costs the full cap before escalating.

`v0.7.1` carries the fix, and `4746`'s acceptance criterion — three concurrent lanes each
adding a changelog entry, none conflicting — *is* the evidence `u6jq.1` needs. So the proof
lands with the release that makes it possible, rather than being re-attempted against a cause
we already understand.

## 6. `v0.8.0` — own the work graph

`basicly-vkh0`, P0. The tracker *is* the harness's state, so every guarantee in §2 is downstream of
it, and it is currently an unowned external binary in the critical path whose licence carries a
rider restricting a class of users. Twelve distinct defects on the epic have already been paid for
in diagnosis time; the clock defect alone consumed two tracks of workaround. `br.run_br` raises
when the binary is absent and `basicly install` does not install it, so a `1.0.0` declared before
that changes would freeze a contract the roadmap already voids.

**That condition is still standing after `v0.8.0`, and this section used to imply otherwise.**
`vkh0.19` flips `br.read_record` and nothing else, deliberately — the other subcommands are each
read at their own call site with their own payload shape, and rewriting callers was the one thing
that bead was required not to do. Measured 2026-08-07 at `MODE_OWNED` with `br.which` returning
None, so an empty ledger cannot be the cause: `policy.gate_status` and `policy.definition_of_ready`
both raise `br is not on PATH; the harness requires the beads tracker`. 44 typed spawn sites remain
— `comments` 26, `dep` 5, `update` 3, `sync` 2, and one each of `where`, `lint`, `init`, `gate`,
`close`, `blocked` — and `comments` is the carrier for every checkpoint, gate marker, grant and
rework record, so it is the load-bearing half rather than the tail. `basicly-vkh0.22` holds the
measurement and the decision; the claim is carried to `v1.0.0`'s acceptance test (§9), which
exercises a consumer with no `br` rather than asserting the binary is gone.

**Decompose first** — the build has no beads yet. Order:

1. **The event log.** Append-only is the truth; the record snapshot and any index are derived and
   disposable. A record's state is a fold over its events, so history survives a squash or a
   shallow clone. Sequence numbers from the single writer give total order. **A wall-clock
   timestamp is evidence and nothing branches on it** — that is the defect class behind the flake
   in §5's Track B.
2. **Provenance on every edge.** `EXTRACTED` (asserted by a human or mechanically derived from a
   repo fact) may gate a landing; `INFERRED` (proposed by an agent, or deduced from a bounce) is
   usable but visible as a proposal; `AMBIGUOUS` routes a decision and never silently gates. The
   label belongs to the *event*, so a human confirming an inferred edge is a new promoting event
   rather than a mutation.
3. **An explicit collision budget** for ids, sized from the birthday paradox against a declared
   maximum probability, with adaptive length — safe because existing ids never change.
4. **`fsck` and `rebuild`.** Without them, "the log is the truth" is a claim nobody can check.
5. **Import → shadow → dual-write → flip.** Three known risks: the JSONL format is second-class
   upstream and will drift; `import` is upsert-only so **a snapshot cannot express deletion** and
   tombstones are a first-class concern; and the shadow differential must therefore compare
   against the **live tracker**, never a re-import of its own export — two derivatives of one
   lossy snapshot agree with each other and prove nothing.

**Constraints.** The surface is frozen (`vkh0.2`, closed): a surface nobody exercised does not
exist in the replacement, and no schema may be designed from memory of our own usage. A clean-room
boundary applies and was signed off on `basicly-qk6y` — not derived from `beads_rust` source;
sanctioned inputs are our own ledger's observable data, `br`'s documented CLI contract and the
genuine-MIT upstream original. Wire `qk6y` as a blocker of the event-log beads to record the
sign-off.

**Also here**: `vkh0.9` (absorb the measured journal mechanisms into the requirements register) is
the one open prerequisite and is not surface-dependent. When we own the ranking, the scheduler
score must become **pure** and drop `created_at`: age-based ordering makes dispatch order
clock-dependent for an unchanged graph. `vkh0.4` (cross-repo offer exchange) stays deferred —
nothing consumes it. Requirements carried forward from paid-for `br` defects live on `vkh0.6`
(closed) as committed regression tests, including the WAL corruption R7 found under our own
five-lane fan-out.

## 7. `v0.9.0` — evidence, gates, docs

**Status, 2026-08-07.** The deterministic-gate row is **shipped**: `m4zv.2`, `.4` and `.6` landed
this release and `.5` landed in `v0.7.1`, so four of the five are closed and only `m4zv.3` remains
(blocked behind `v0vt`). The D4 prerequisite named below is **already satisfied** — `imnu.1` is
closed. Ready and unblocked: `imnu.2`, `imnu.3`, `imnu.5`, `3ifz`.

**The numbers are blocked, so do not start here.** `7bur` cannot begin: its one open blocker is
the `u6jq` epic, whose remaining child `u6jq.1` is itself blocked on `69az`. The chain is
`69az → u6jq.1 → u6jq → 7bur → agzx.2, 4t9z, s2xf → kjc5`, so **`69az` is the unblocking
action for this whole section**, not `7bur`. `agzx.2` sits behind `7bur` and inherits the wait.
(Note `kjc5` is `7bur`'s *parent*, not a blocker — a child does not wait on its epic.)

**What `7bur` is for, once reachable.** It is the hub: it gates `4t9z` by an existing
edge and four design decisions defer to it — the roster's tier table, eval scale, the localisation
question, and prefix-stable dispatch bundles. Its hard constraint: **the eval must not cost more
than the thing it measures** — cheap models on the arms, the strong model only for judging. Label
dispatches by *specification completeness*, not work category: the predicate for a cheap tier being
safe is whether the brief already contains the code and the tests. `agzx.2` then answers whether an
AST-derived localisation artifact (tree-sitter, no model, no tokens) cuts an implementer's
pre-first-edit token share; it must land before the roster, because it changes what a decomposer's
scope declaration has to carry.

**The deterministic gates** (`m4zv.2`–`.6`), highest value-per-cost in the plan — all run in CI at
zero token cost:

- **`m4zv.2` routing evals.** Stemmed TF-IDF over descriptions, pure Python, no new dependency.
  Three assertions: a positive prompt ranks its owner top-k; a negative prompt declares an `owner`
  and the assertion is that the owner **outranks** this entry (a bare "must not rank first" passes
  vacuously); no two descriptions exceed a pairwise similarity ceiling (error 75%, warn 50%). The
  CI metric is rank-1 rate, floor set below a measured baseline and **never lowered to make a
  regression pass** — lowering it is deleting the test while looking like maintenance. Refuse
  embeddings: non-deterministic, network-dependent, unownable.
- **`m4zv.3` an eval case per catalog entry**, enforced as a Tier-1 failure, colocated with the
  source and scaffolded from `catalog new`. Stage by *adding* entries to the enforced set, never by
  lowering a threshold. This raises the cost of adding an entry, which is the intended brake on
  accretion. **The entry set is now counted** (`v0vt`, 2026-08-07): **35 skill sources** — 29
  model-invoked, 6 user-invoked — project to **30 skills in each root** (27 model, 3 user). The
  five that project nowhere each declare a `technologies` gate naming their own environment
  (`tool-starship`, `tool-tmux`, `tool-wezterm`, `tool-zsh`, `wsl`) against this repo's
  `["python", "node"]`; confirmed by adding `tmux` and watching `tool-tmux` project, then prune on
  restore. Nothing is silently dropped. **So decide which set `m4zv.3` enforces**: the routing eval
  gates **29 model-invoked *sources***, of which only 27 are projected here — its near-miss list
  names `wsl` and `tool-tmux`, neither of which exists in this repo's `.claude/skills/`. Enforcing
  over sources is defensible for a distributed catalog, but it must be stated, because "an eval case
  per catalog entry" reads as the projected set and is 3 entries smaller.
- **`m4zv.4` severity as a required field** on judged output — `BLOCKER` / `IMPORTANT` / `MINOR` —
  rejected as a schema violation rather than complained about, plus the **no-pre-judging lint**:
  refuse to emit a reviewer bundle containing a finding-suppressing directive.
- **`m4zv.5` rework convergence.** Compare the **open-finding set** between iterations, not the
  count. One stalled round warns; two consecutive escalate immediately without consuming the cap; a
  diverging round escalates on first occurrence. The subsystem it hardens has **never fired** — 286
  gate results recorded, 0 failed, re-counted 2026-08-03 — so treat its existing behaviour as
  unvalidated rather than proven.
- **`m4zv.6` gate taxonomy.** Classify every gate as pre-flight / revision / escalation / abort and
  enforce that **a pre-flight gate writes nothing**; our two worst recorded incidents were both
  checks that recorded state where they should have blocked entry.

**Prerequisite for `m4zv.4`/`.5`: the D4 amendment — satisfied, nothing to do first.** `imnu.1`
closed, so architecture already records validate as a **composite**: a deterministic pre-flight
component that can fail the lane plus a judged escalation component that enqueues a decision. This
keeps "no persona passes a required gate" intact while giving the required gate teeth. Build
against the shipped shape.

**The docs debt.** `imnu.2` (a tutorial and how-to layer — `docs/` has no "your first loop"
walkthrough, which is an adoption blocker for a distribution meant to be installed by other
repos), `imnu.3` (declare and report the capability tier `basicly install` actually delivered:
instruction-tier / skill-tier / plugin-tier — our central claim is *enforcement*, which is
plugin-tier, and on an instruction-tier host the harness degrades to advice and we say so nowhere),
`imnu.5` (the ceremony threshold and the named lightweight path below it), `kjc5.13` (absorb the
factory design, then delete it per §11).

**Also here**: `3ifz` — learn `concurrency`, the per-lane budget, the sizing band and `max_rework`
from recorded outcomes instead of judgment; it needs the forecast/actual pairs `vz78` created.
`jr0l.43`'s successor `jr0l.56` (close or declare the gap where no test drives a real agent CLI
through the loop).

## 8. `v0.10.0` — the judgment layer and always-on relief

**The roster** (`s2xf`, design agreed on `eqp6`, nothing built). Today the factory dispatches one
generic prompt shape for every lane; this replaces it with named roles carrying their own
instructions, tool policy, model tier and output contract.

- **Engine**: a role registry (role id → prompt source, tier, tool policy, output schema); a
  `[runner.roles]` config section with defaults, so a consumer with no overlay gets a working
  roster; role-aware dispatch; tool-policy overlays at invocation, generalising the existing
  decider confinement to every read-only role; per-role attribution, so per-role telemetry and
  cost-per-landed-package fall out for free.
- **Prompts as catalog sources**, not agent-native subagent files — the factory is agent-agnostic.
  Each judged role carries an explicit adversarial stance and a role-specific list of how *that*
  role goes soft (reviewer conflict-avoidance — downgrading a blocker to avoid disagreeing with the
  producer — named as a predicted failure). **Derive those lists from the recorded verdict, rework
  and adjudication history, never invent them**; a generic rigour instruction is a no-op that costs
  tokens.
- **Contracts**: lens output reported per lens, never merged into one ranked list, because a change
  can pass one axis and fail another and merging lets one mask the other. The implementer hands
  over a **report file** and returns only status, commits, a one-line test summary and concerns —
  pasted history stays resident in the dispatcher's context and is re-read every later turn. Four
  statuses with different correct responses: `DONE` / `DONE_WITH_CONCERNS` / `NEEDS_CONTEXT` /
  `BLOCKED`, and the rule that gives it teeth: never force the same model to retry unchanged.
- **Two pieces that do not fall out of the registry shape**: the R4 disposition path (a judged NO
  enqueues a decision carrying the failing criterion and its evidence, and the lane *holds* rather
  than landing or bouncing), and a decision class **no grant level auto-disposes** — an exception
  to the L0–L3 ladder, so an autonomy grant can never hand the catalog to the decider.
- **Capability escalation on late rework rounds** (resume the same agent early, fresh dispatch one
  tier up late), which produces a readable signal: if late bumps routinely succeed, the initial tier
  was wrong.

**Always-on relief** (`a3ab.1`–`.3`), routine tidying rather than surgery because §3 found the
baseline recalled rather than lost:

1. `a3ab.1` — audit every baseline line against three questions: is this really a hook? can I write
   a glob for it? **does it change behaviour versus the default at all?** The third is the no-op
   test and it is the most common defect in an always-on layer.
2. `a3ab.2` — declare scopes on the fragments that earn them. Authoring work, not engine work. Per
   fragment, weigh the guarantee change: a fragment that must bind in PR review stays unscoped,
   because scoping removes it from the github.com Copilot surface. Mind Codex's headroom (§3) — the
   next scoped fragment overflows `AGENTS.md` unless something leaves it first.
3. `a3ab.3` — the one check the existing gates cannot see: a scope whose globs match nothing. The
   fragment is well-formed, it projects, `check` is green, and the rule never loads. Warn at
   projection time; error only where the technology is selected, so a docs-only consumer is not
   punished for having no Python.

Then `a3ab.4` (project the constraints as an explicit always-do / ask-first / never-do block — a
retrieval problem wants a structural fix, not more prose) and `a3ab.5`.

**Exit criteria.** Every judgment step routed to a named role; no dispatch with an unresolved tier;
a judged NO holds its lane and produces a disposable decision; the roster's cost claims visible in
`7bur`'s instrument rather than asserted; the baseline measurably smaller on two of three families
with recall **not degraded** (at 98% / 93% there is no headroom to improve, so demanding
improvement would be unsatisfiable); and no declared scope matching nothing.

## 9. `v1.0.0` — stabilize and declare

No epic yet; file one at decomposition. Nothing here is speculative — every item traces to a defect
the consumer review found.

| Work | Why |
| --- | --- |
| Surface audit and semver freeze | Enumerate and freeze five surfaces: CLI commands and flags, `basicly.toml` plus the `basicly.local.toml` overlay contract, the four catalog source schemas, the generated-file/manifest contract, and the owned ledger format. Each broke within the last two minors, so the audit means reading each surface against its consumers; the freeze is a written compatibility policy with a deprecation path. |
| Breaking-marker discipline as a gate | The `v0.6.0` audit existed because zero of 535 commits carried a `!` marker. After the freeze, a commit changing a frozen surface without the marker must fail a deterministic check — or `2.0`'s audit repeats `0.6`'s. |
| Forward-version CI job | The floor claim is "3.14+" and CI tests exactly 3.14. Add the next Python so the "+" is tested. The floor itself stays (owner-confirmed). |
| Error-path polish | Two soft spots at the consumer trust boundary: `basicly check` in a never-installed repo points at `build` instead of `install`, and the CLI's blanket exception handler leaves no `--debug` escape hatch (`tcmy.24`). |
| The acceptance test | A fresh consumer repo — git plus a uv-provisioned interpreter, no `br` — installs basicly, runs every gate, and drives one unit of work through the loop to a landed commit. Exercise it as it will really be used, and publish nothing that was not exercised. |
| Final absorption | Every remaining design document folded into architecture and deleted (§11); §3 of this file refreshed one last time. |

**Exit criteria.** The acceptance test passes on a machine that has never seen this repo; the
compatibility policy is published; a surface-breaking commit without a marker fails CI; `v1.0.0` is
tagged by `basicly release` with both pushes explicitly owner-approved.

## 10. Standing constraints

Rules any release must honour. Each exists because breaking it cost a session or more.

- **Do not grow the schema of a component about to be replaced.** Land evidence as `[harness-*]`
  comment markers — a format we own, which migrates with us — not as tracker fields. Applies to
  `kjc5.47`, `.48`, `.50`, `.51` and `jr0l.19`, `.20`.
- **Free deterministic gates before judged ones.** A CI check at zero token cost that catches a
  silent failure forever outranks a judged check that costs tokens per run.
- **Prefer the root cause, but ship the workaround first when the root cause is a release away** —
  then stop charging rework for it and carry the defect forward as a *requirement* on the
  replacement.
- **Never lower a CI floor to make a regression pass**, and never defeat a gate to force success.
  Fix the failing gate.
- **Recall is an upper bound.** It confirms mechanism, never outcome, and may not be cited as
  evidence of quality.
- **A filter on an optional field hides a population.** Every failed lane records no
  `scope_tokens`, so a filtered query silently excluded the whole failure set and a false rationale
  reached `config.py` (`ipx2`).
- **Assert a platform difference by injection, not by racing.** Make the platform difference test
  data; a passing local `pyright --pythonplatform Windows` is false comfort.
- **A wall-clock timestamp is evidence; nothing branches on it.**
- **A constant describing an external capability must be falsifiable against our own ledger.** It is
  the one class of value that is *correct when written* and rots silently as the vendor ships, so no
  gate catches it and no review re-reads it. `claude`'s context window sat at 200000 against a
  dispatched 1000000 for months while the run records held occupancies above 200000 — a contradiction
  that was mechanically detectable the whole time — and it truncated healthy lanes into twelve
  follow-up beads (`23ep`). Where a field measures the same quantity a constant declares, wire the
  comparison as a check; and fix such a constant by *declaring* it with recorded provenance, never by
  pasting in a fresher number.
- **A recurring follow-up shape is a symptom, not a workload.** Twelve beads named the truncation and
  none asked why the trigger fired. Before working a queue of look-alike items, suspect the mechanism
  that spawns them.

## 11. Document disposal register

Owner rule: **the code is the authority, `architecture.md` is the human-readable summary, and every
other document under `docs/` is temporary** — deleted once its design is code and its surviving
rules are in architecture. The one exception is the consumer-facing layer (`tutorial/`, `how-to/`):
it documents shipped behaviour for someone who does not read this repo's code, so it is corrected
against the code rather than deleted. This register is what makes the rule enforceable: if a
document is not listed here it should not exist.

`Live` means it specifies work not yet built, so deleting it loses a requirement. `Deletable` means
its design is implemented and only the listed precondition stands between it and deletion.

| Document | Status | Precondition for deletion |
| --- | --- | --- |
| `architecture/architecture.md` | **Authoritative** | Never deleted. Corrected against the code whenever the two disagree. |
| `plan/implementation-plan.md` | **Authoritative** | This file. Deleted when `v1.0.0` ships and the ladder is spent. |
| `tutorial/first-loop.md` | **Consumer-facing** | Never deleted while `basicly install` ships. Re-executed against a fresh repo whenever a command or its output changes (`imnu.2`). |
| `how-to/customize-the-catalog.md`, `how-to/wire-up-the-verify-gate.md`, `how-to/unblock-a-commit.md`, `how-to/upgrade-and-check-drift.md`, `how-to/run-parallel-lanes.md`, `how-to/resume-a-track.md` | **Consumer-facing** | One page per recurring operation; a page goes when its operation does. Rationale stays in architecture — a how-to that starts explaining *why* is drifting into the reference (`imnu.2`). |
| `design/work-tracker.md` | **Live** | `v0.8.0` ships. Five inbound references from `br.py`, `cli.py`, `tracker_surface.py`; it is the only record of what the replacement must be, including the requirements register. |
| `design/agent-roster-design.md` | **Live** | `v0.10.0` ships the roster. Referenced from `.basicly/core/models/README.md`. |
| `design/factory-design.md` | Deletable after `kjc5.13` | Absorb D1–D10 into architecture, then delete. `commit.py` names it; remove that reference first. |
| `design/factory-loop-requirements.md` | **Live** | The target state of the loop and the measured delta from it, with 16 decisions and their sources. Drives `basicly-u2hl`. Absorbed into architecture and deleted when that epic closes. |
| `design/gates-and-rework-design.md` | **Deletable now** — `uhiq.2` | Cited by path from `architecture.md:1692` only. The bounded-rework subsystem is built but has never fired, so what survives is the *unvalidated* status, which belongs on `m4zv.5`. |
| `design/steering-surfaces-design.md` | **Deletable now** — `uhiq.2` | Zero inbound references; architecture `:1553-1585` already carries the recall result. |
| `design/catalog-efficacy-design.md` | **Deletable now** — `uhiq.2` | Architecture `:1563` carries the upper-bound rule, but `:1583` and `:1692` still cite the file by path — inline those citations or they dangle. |
| `design/harness-eval.md` | **Deletable now** — `uhiq.2` | Zero inbound references; superseded by the shipped `rubric` command and `.scripts/recall_eval.py`. |
| `design/tier-injection-kit.md` | Deletable after reference removal | The kit ships at `.basicly/core/kit/`; referenced from `CHANGELOG.md` and the kit's own README. A changelog entry is history and may keep its link, so decide that explicitly. |
| `architecture/hook-runner-decision.md` | **Deletable now** — `uhiq.2` | Zero inbound references anywhere. |
| `archive/foundry-spike.md` | **Deletable now** — `uhiq.2` | Zero inbound references. The `docs/archive/` directory goes with it — an archive is the thing this rule forbids. |
| `research/2026-07-26-sota-review.md` | Dated evidence | A review of the field on one date, with its conclusions already absorbed. Delete when nothing cites it; not design, so it never becomes code. |
| `research/references.md` | Dated evidence | Goes with the review above. |

**Deleting a document means removing its references from the code first.** Under the owner rule the
code must read well enough not to need them, so a prose pointer at a design document is deleted
rather than repointed.

## 12. Owner decisions still owed

Each blocks something and none can be derived from the code.

1. **The ceremony threshold's written form** (`imnu.5`, `v0.9.0`). The loop is mandated for
   "non-trivial work", which is the agent's judgment call, so the rule is unenforceable. Needs a
   written threshold **and** a named lightweight path below it that skips ceremony but never hooks.

Resolved and recorded so they are not reopened: **Tier-2's rank-1 floor** (`m4zv.2`, discharged
2026-08-07) — `[catalog] rank1_floor = 0.85` against a measured baseline of 80/87 = 92.0%, with
`rank1_floor_high_water` starting equal so a later lowering is a visible act; the rationale is in
`basicly.toml` beside the value; `v1.0.0`'s meaning (§2); the clean-room boundary
(`qk6y`, discharged); declarative YAML phases are **rejected**, not deferred — the
`verified`/landed invariant cannot move into data without leaving the type checker, the test suite
and code review behind, and no consumer has asked; the github.com Copilot surface question is a
per-fragment rule at §8 step 2; the machine-local retro lane (`jr0l.28`) stays deferred past
`1.0.0` on bypass-by-accretion risk and zero recorded demand; the Python 3.14 floor stays.

## 13. Risks and how each is detected

| Risk | Detection |
| --- | --- |
| A measurement is uninterpretable because an arm was contaminated | The eval harness asserts its own isolation: read back what guidance is live in the cell and fail if it does not match the arm's declaration. Never rely on someone noticing an implausible number. |
| Eval-case coverage stalls and the gate is quietly relaxed | Tier-1 failure from the start; stage by adding entries to the enforced set, never by lowering a threshold. |
| `v0.8.0` grows into a general-purpose tracker | The frozen surface list is the scope contract, and the non-goals are recorded. Anything not in the measured surface is out. |
| The roster is built on a guessed tier table | `s2xf` is gated on `7bur` by an edge. If `7bur` slips, the roster waits rather than proceeding on assumption. |
| A gate is believed to bind because its bead is closed | Probe the gate's own functions on real inputs. `jr0l.22` was shipped and inert for 67% of the tracker, which cost a 36% grant overrun. |
| This file goes stale | §3 is generated and gated; everything else is refreshed at the start of each release, and a row that cites a `file:line` is re-verified before it is worked. |

## 14. Phase labels to epics

Phase membership is a tracker **label**, not a re-parenting, so a bead's parent stays its epic of
origin and `kjc5` children appear across several phases. Query it rather than reading a list here:

```sh
br list --label phase-2          # membership
basicly loop status <issue>      # where one bead actually is
br scheduler                     # what to pick up next
br dep tree <issue>              # what blocks it
```

| Phase | Epic | Release |
| --- | --- | --- |
| S — make what exists true | `basicly-vaal`, gates on `uexy` / `irrm` | `v0.7.0` |
| 0 — unattended run | `basicly-u6jq` | `v0.7.0` |
| 1 — buy the numbers | `basicly-agzx` | `v0.9.0` |
| 2 — free deterministic gates | `basicly-m4zv` | `v0.9.0` |
| 3 — absorb designs, pay docs debt | `basicly-imnu` | `v0.9.0` |
| 4 — always-on relief | `basicly-a3ab` | `v0.10.0` |
| 5 — judgment layer | `basicly-s2xf` | `v0.10.0` |
| 6 — own the work graph | `basicly-vkh0` | `v0.8.0` |
| 7 — factory hardening | `basicly-jr0l` | interleaved as capacity fillers |
| multi — parallel factory | `basicly-kjc5` | children spread across releases |
| — | `basicly-tcmy` (2026-08-01 review), `basicly-ze8z` (externalize operator knowledge), `basicly-uhiq` (document disposal), `basicly-ctdz` (own our state) | cross-cutting; children placed by release |

Each row names **one epic**, which is the index *into* the tracker rather than a copy of its
contents: the epic is stable, its children are not. So no row says which children remain, are
done, or slipped — that is what the queries above answer, and a per-bead status written here
would be stale by the next close and would put two lanes closing two items in one phase onto
this single anchor (`basicly-3f76`).

A release epic (`basicly-yc0x` for `v0.7.0`) carries `phase-meta` rather than a phase label,
because a release is a cut across phases.
