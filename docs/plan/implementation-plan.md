# basicly Implementation Plan

The route from the current release to `v1.0.0`. **Rewritten 2026-08-08** against `main` @ `28257e9`,
after a session that measured the factory running and found the *plumbing* — not the loop's
states — to be what fails. The nine design documents this file used to index are gone: six were
absorbed into three authoritative documents and deleted, three carried nothing live (§9).

Shipped history is deliberately absent. `git log` holds the sequence and the tracker holds the
incidents; this file is the ladder and nothing else.

This file plus [`architecture.md`](../architecture/architecture.md),
[`requirements/factory-loop.md`](../requirements/factory-loop.md) and
[`requirements/work-tracker.md`](../requirements/work-tracker.md) are the whole picture.
Architecture is the **specification** and wins over all three of the others; the two requirements
documents are the arguments behind the decisions it records; this file is the order.

## 1. How to work from this file

1. **The code is the authority.** This file sequences; it does not define behaviour. Where the two
   disagree, the code is right and this file is stale. Where this file and architecture disagree
   about *what the system is*, architecture wins.
2. **A bead's claim is a claim.** Re-check a cited `file:line` before fixing it — the code may have
   moved, and a bead written weeks ago may describe a defect someone else already closed.
3. **A closed bead proves code exists, not that a gate binds.** Call the gate's own functions on
   real inputs. A fail-open gate is indistinguishable from a passing one. Measured 2026-08-08:
   three separate claims in this file were false against the tree.
4. **Ask the tracker for counts, never this file.** `br` is always right about status. Use
   the committed ledger for whole-tracker questions — a paged query caps its result and drops
   closed rows. Structural figures that *are* here come from a generated block (§3).
5. **Measure before you dispatch.** Take the baseline in base, record it on the bead, diff against
   it at review. A lane that rewords a number instead of measuring it produces a regression under
   an acceptance criterion claiming improvement.
6. **A document that describes shipped code is noise.** If it is in the code, it does not belong in
   a document (§9). This rule deleted nine files on 2026-08-08.

## 2. Destination

`basicly` is a harness for coding agents that ships its own development process. Four pillars
(architecture — *System overview*), each done only when it is true, enforced *and* measured:

| Pillar | Done means |
| --- | --- |
| Guidance | Every entry's routing and behavioural effect is measured; nothing ships on assertion. Conditional guidance is path-scoped, not always-on. The delivered guarantee per agent family is stated rather than implied. |
| Gates | Every gate is classified by type, so "what happens when this fails" is answered by the type and not per call site. Judged output carries a required severity and cannot pass a required gate. Rework detects non-convergence instead of burning its cap. |
| The loop | Every deterministic step is one command. Every judgment step is routed to a named role at a tier chosen by measured reliability. A supervised multi-lane run completes with no human intervention caused by a harness defect. |
| The work graph | Owned in-process: an append-only event log we control, with provenance on every edge, no external binary and no bootstrap step in the critical path. |

The invariants that constrain *how* any of it may be built are in architecture — *Core
invariants*.

**`v1.0.0` means three things**, all required (owner, 2026-07-30):

1. Every agreed design is implemented — every row of architecture's status table reads *shipped*, and every
   requirements document is absorbed and deleted (§9).
2. The consumer criterion is *demonstrated* (`basicly-ctdz`): a fresh repo with only git and a
   uv-provisioned Python interpreter installs basicly, runs every gate and drives the loop end to
   end, with no external `br` binary.
3. A full semver contract: the CLI surface, `basicly.toml`, the catalog source schemas, the
   generated-file contract and the owned ledger format are declared stable. Every one of those
   broke within the last two minors, so the promise needs a stabilization release (§7), not a
   version-number ceremony.

**Non-goals bound the plan** so it cannot quietly grow. The list and the reason for each are
in architecture — *Non-goals*.

## 3. Current state

Structural figures are generated from the tree and gated on every commit, because every
hand-written copy of them was stale within days:

<!-- docs-claims:begin plan-current-state -->

| Measure | Value |
| --- | --- |
| Engine modules (`src/basicly/*.py`) | 111 |
| Test files | 221 |
| `[[verify.checks]]` declared | 35 |
| …of which run in `--mode fast` | 30 |
| …of which run in `--mode full` | 34 |
| …of which run in `--mode staged` | 3 |

<!-- docs-claims:end plan-current-state -->

**Not built**, re-verified against `src/` rather than by counting closed beads: **no EARS
validation** anywhere, and `policy.rework_recorded` reports a cross-gate total that nothing
enforces. REPAIR and RETROSPECTIVE were on this list until the architecture settled them the
other way: neither is a phase **by decision** — repair is the implementer's second mode and a
retrospective is a conditional process over a ledger — and both are dispatched. A decided
non-phase is not a gap.

**All seven loop roles are reachable** [M 2026-08-16 against `roles.py` and `loop.py`].
`curator` was the last: `loop._on_ship` was a live handler that closed the unit without
dispatching anything, and `loop._dispatch_curation` now runs it, priced and bounded exactly as
the validator's judges are. This paragraph named the wrong roles for the wrong reasons three
times before that — `reviewer` resolves through `roles.LENS_ROLE_BY_PHASE` rather than
`ROLE_BY_PHASE`, and `retrospector` is dispatched from `loop._retrospective` even though
RETROSPECTIVE is deliberately not a phase. **A role's reachability is a fact about the dispatch
sites, not about the phase ladder**, and `rg -n 'resolve_role\(|_run_agent\(' src/basicly/`
re-establishes it in one command.

**One item on this list was never a defect, and it sat here for two days.**
`loop_state.PHASES` and `config.LOOP_PHASES` were recorded as disagreeing tuples. They
differ by exactly one element — the terminal `done`, absent from `LOOP_PHASES` because it
has no transition out. The reason is documented at `config.py:369` and `test_loop.py:2811`
pins `loop._HANDLERS` to `LOOP_PHASES` so a handler cannot drift from the set that
validates it. Checked 2026-08-09 *before* acting on it. Do not "reconcile" them.

**Three claims this section carried on 2026-08-08 are now false**, and the work that
falsified them landed the same day it was measured:

| Was written | Now [M 2026-08-09] |
| --- | --- |
| zero of the seven personas | **all seven authored**, role-named, projected to both families |
| four of the five designed skills are absent | **5 of 5 exist** |
| one default runner serves every phase | a phase resolves to a role and reaches the argv (`roles.resolve_role`) |

**That third row carried a correction of its own, and it outlived the bead that fixed it by five
days.** As written on 2026-08-09 it said `resolve_role` was called from one site inside
`_run_agent`, so `implementer` was the only role ever to reach an argv — true then, and
`basicly-4xmu` **closed** against it. Re-measured 2026-08-14: three call sites in `src/`, and the
two the row said never ask now do.

```text
roles.resolve_role      loop.py            _run_agent           → validate · reviews · build ·
                                                                  repair · sub-task · retrospective
                        loop.py            _run_proposer        → classify · decompose
                        supervise.py:2707  _dispatch_lane       → lane build
_run_agent call sites   loop.py:530        _dispatch_validation
                        loop.py            _dispatch_reviews    (once per lens)
                        loop.py            _dispatch_runner     (build)
                        loop.py:1699       _repair_in_place
                        loop.py:1828       _run_subtask
                        loop.py:2250       _retrospective
```

Every line above cites the *defining* line of the symbol named beside it, not the call inside it,
and `docs-citations` checks that the two still agree — the third re-measurement of this block in a
week (2026-08-15) found **all nine** of its previous line numbers drifted, one onto a blank line.

The lesson is the one below it, not the wiring: a paragraph naming a **closed** bead reads as live
work to the next reader, and this section is where a decider looks first. Re-check a `file:line`
and a bead status together, or the correction becomes the stale claim.

The per-surface inventory — how many agents, skills, hooks and always-on characters ship,
and how far each reaches — is in architecture. This file no longer carries a second copy;
one went stale inside a week.

**Tree growth is the finding this section had no instrument for until 2026-08-14.** A ratchet
bounds a file. Nothing bounded the tree, so 138 modules arrived in a week under six green
gates. `basicly-5p49` measures it now, and it reports rather than blocks because it has no
firing history yet.

A competing harness with strictly worse definitions — hand-written, no schema, no projection,
no vendoring — beats this repo on agent wiring, skill exercise, hook coverage and role
observation, because its definitions are **wired** (`factory-loop.md` §11.7). That is not an
argument for its design; its loop is the declarative-phase pattern this repo already
rejected, and §11.7 carries the receipts. It is the argument that the next unit of work on
this axis is **wiring, not authoring**.

**Four figures this file previously got wrong**, corrected against the tree:

| Was written | Measured 2026-08-08 |
| --- | --- |
| "Step 1 has not been run… `.basicly/ledger/` holds no `events-*.jsonl`" | the import **ran** (`b97a653`): 3,775 events over 643 records |
| External tracker spawn sites 43 / 44 | **0** [M 2026-08-17, `rg 'run_br\(' src/basicly` — the functions are deleted]. Was 32 across 12 modules on 2026-08-14; the 43/44 count had included import lines and docstrings |
| 20 verify checks | **24** — and this row is why §3's figures moved into a generated block |
| `basicly-tcmy.34` "the remaining P0" | **closed** 2026-08-05 |

**And one that matters more than the rest**: `basicly-u6jq.1` — `v0.7.0`'s unmet exit criterion 5,
the unattended multi-lane proof — is **unblocked and scheduler-ranked first**, and has been. All 22
of its `blocks` edges point at closed beads, and `br` computes readiness from target status rather
than edge presence, so nothing was ever holding it. It was held on a stated precondition, and that
precondition is now met: see §5.4.

## 4. The release ladder

Rows are in shipping order. A **release** is a shippable cut; the phase labels in the tracker are
the dependency clusters a cut draws from (§12).

| Release | Content and reason | Size |
| --- | --- | --- |
| ~~`v0.7.0`~~ | **SHIPPED 2026-08-06.** Trustworthy factory. Exit criterion 5 was not met, and the release documented that rather than claiming it. | shipped |
| ~~`v0.8.0`~~ | **SHIPPED 2026-08-07.** Own the work graph — the store, not the floor. `br` is still in the runtime path (§6). | shipped |
| ~~`v0.9.0`~~ | **SHIPPED 2026-08-14.** The plumbing, then the loop. Every P0 §5.1's ordering argument rested on landed — transcripts persist, the rebase loses no work, the scope field is split, the decider's corpus is honest, the landing anchors are collision-proof. Plus VALIDATE as a real state, seven of eight handoff schemas, and the roster wired. The remaining §5.2 rows moved to `v0.9.1` because they are a different theme. | shipped |
| **`v0.9.1`** | **The architectural backlog, then the dispatch mechanism.** §5A is the first half and it is the priority: 34 open items under `basicly-e2mz`, from a five-lane survey at the maximum tier plus a re-verification of the 2026-08-01 review. The second half is what §5.2 has left — `ejdm`, `xjd2`, `0p8n`, `66ix` — which is one theme, the dispatch mechanism, and reads better as its own row than as leftovers. The measured evidence layer (`7bur`, `agzx.2`, `m4zv.3`, `3ifz`) rides here too. | 8–12 sessions |
| **`v0.10.0`** | **The judgment layer and always-on relief.** The roster's routing (`s2xf`) once `7bur` has numbers; the Phase 4 authoring pass and the empty-glob check (`a3ab.2`–`.3`). **This row had a gating item and no longer does** [M 2026-08-14]: `a3ab.1`, `.10` and `.12` are all closed, and `AGENTS.md` is **14,428 characters against a 16,000 cap** — 1,572 under, not 1,135 over. The audit `a3ab.1` ran found the overrun was the *scoped tier* Codex cannot receive as separate `paths:` rules files, so evicting always-on lines would have cost all three families to fix one; `codex.yaml:9` records the cap moving 12,000→16,000 instead. Three documents outlived that by five days — see §3's note on correcting a `file:line` and a bead status together. **The local board (`basicly-rn0o`) is placed here by owner decision 2026-08-17**, because it was on no row at all: its consumer half is built and its producer half is not, which is the shape a ladder exists to catch. Move it behind `v1.0.0` if the judgment layer needs the whole row — `v1.0.0` adds no capability, so post-1.0 is the only other honest home. | 5–8 sessions |
| **`v1.0.0`** | **Stabilize and declare.** §7. Surface audit and semver freeze, the breaking-marker gate, the fresh-consumer acceptance test, `br` out of the runtime path. `1.0` is a promise, so the last release proves it instead of adding capability. | 3–5 sessions |

Sizes are decomposition signals, not commitments.

**These four rows were re-confirmed by the owner on 2026-08-09 against a conflicting claim**, and
the conflict is recorded because the next reader will hit the same one. A session handover asserted
that `v0.10.0` had been re-agreed as the *tracker* cut, moving `br` out of the runtime path there
instead of in `v1.0.0`. Nothing supports it: the ladder above was written 2026-08-08 (`47a7275`),
`vkh0`'s own 2026-08-07 comment records `br`-out-of-runtime as **carried to `v1.0.0`'s
fresh-consumer acceptance test** (`vkh0.22`), and a whole-tracker probe finds **zero** mentions of
`v0.10.0` or `v0.9.1` in any comment — against a positive control that finds `v0.9.0`, `v0.8.0` and
`v1.0.0`. A handover is inherited claims, not evidence; this file and the tracker are.

## 5A. `v0.9.1` first half — the architectural backlog

Tracked by `basicly-e2mz`.

**The "start here until the epic closes" instruction is withdrawn, by owner decision 2026-08-14, and
the reason is the epic's own argument.** §5A.1 states that a read-only pass emitting a prioritised
list is something this repo can already produce, and that what it cannot do is *drain* the list — which
is why `basicly-u2hl.27`, the controller and the actuator, "is not an item in the backlog but the thing
that decides whether the backlog is worth having".

**`u2hl.27` landed on 2026-08-14 and has filed its first real bead.** The precondition is met, so the
ordering that followed from it no longer does. The remaining backlog is mostly P2/P3, and hand-picking
those is doing by hand what the controller was built to select.

The order is now:

1. **`basicly-e2mz.6`** — give the improvement loop a caller. `workflow_dispatch` only, no cron yet
   (owner, 2026-08-14): it makes the wiring non-circular, and the actuator has run live exactly once.
2. **`basicly-u2hl` and `basicly-vkh0`** — where `v1.0.0` actually lives. Three consumer-facing P0s sit
   there: `89hm` ships a context-window defect to every consumer, `vkh0.23` means nothing a fresh
   consumer runs can build the ledger at all, and `ejdm`→`xjd2` is §5.1's measured 254x.
3. **The tail of `e2mz`, `tcmy` and `jr0l`** — roughly 70 items — drains through the controller rather
   than through a human's queue.

**Two of item 2's three P0s could not be started when this was written, and this list said
nothing about it** [M 2026-08-14, `blocks` edges over `.beads/issues.jsonl`, `parent-child`
excluded]. Naming a bead as the place to start is a readiness claim, and a readiness claim is
checkable — so it is checked here rather than discovered by whoever picks it up:

| Named P0 | Was | Now [M 2026-08-15] |
| --- | --- | --- |
| `89hm` | BLOCKED by `u2hl.30` | both **closed** — the context ceiling is observability |
| `vkh0.23` | BLOCKED by `c357` | both **closed**; `u4xu` ran and the repo is on `dual` (§6) |
| `ejdm` | READY | READY; `xjd2` is blocked behind it, as §5.2 already says |

`c357` was itself a P0 and appeared nowhere in §6's five-step list, which was the second half of
the same defect: §6 describes the cutover's *steps* and the tracker holds its *entry point*, and
only one of the two was read. §6 now carries the cutover's measured state instead of its plan.

**So item 2's remaining P0 is `ejdm`, and item 1's `e2mz.6` is still the head of the order.**
Two new P0/P1 defects entered the backlog on 2026-08-15 from using the dual write rather than
from a survey — `e2mz.23` and `e2mz.24`. Both are fixed in the tree [checked 2026-08-16:
`owned_store.tracker_mode` raises `TrackerModeUnknownError` instead of defaulting, and
`mirror` runs its translatability precheck before the spawn]. Architecture records the
decide-then-spawn-then-mirror ordering as the shipped design. The lesson they produced is in
§8.

**And one ready P0 is named by no ordering in this file.** `basicly-jn1x` — **0 of 357 dispatch
records carry `--agent`**, against a positive control of 163 carrying `-p`. The role wiring exists
in code (`supervise.py:2749`), so the two readings are "the lane path does not apply the role" and
"it does and the record does not capture it", and the ledger cannot tell them apart. That makes it
a prerequisite of any measurement that cites a role — `ejdm.4`'s before/after among them — rather
than a defect competing with them for a slot.

The general rule, because this file will name a bead as a starting point again: **an ordering is a
claim about the graph, so read the graph, not the epic.** Both of the misses above are visible in
one query and neither was in the argument that produced the order.

What does **not** change: the four live defects §5A.3 names are all closed, and the mechanism half of
§5A.2's pass 2 is complete. This is a change of *who dispatches* the remainder, not a decision to stop
draining it.

### 5A.1 Why a backlog and not a second audit

**This analysis has been run before.** `basicly-tcmy` is the 2026-08-01 architecture and code
review: three read-only passes over the engine, the projector and the catalog. Its own baseline
reads *43 modules in a six-tier DAG*. On 2026-08-14 the tree was **91 modules** and **13 of its
children were still open**.

So a read-only pass that emits a prioritised list is a thing this repo can already produce. What it
cannot do is **drain the list against feature pressure**. That is why `basicly-u2hl.27` — the
controller and the actuator — is not an item in the backlog but the thing that decides whether the
backlog is worth having.

### 5A.2 What ran, and in what order

Three passes, all complete as of 2026-08-14.

**Pass 0 re-verified `tcmy`'s 13 open findings against the tree. Zero were done.** Four had got
measurably worse, one was superseded by a different bead than assumed, two had a half that closed,
and **three carried a claim that re-verification refuted** — each of which would have sent an
implementer at the wrong thing. The verdicts are comments on the 13 beads.

**Pass 1 was five surveys at the maximum tier**: the loop core, supervision and landing, the
command-line and distribution surface, the measurement layer, and one cross-cutting lane for
duplicated concepts and suppression lists. 28 items after deduplication, 14 dependency edges, no
cycle. Two findings arrived from more than one lane independently, and both amended an inherited
bead rather than opening a new one.

**What the surveys judged *clean* is a comment on `basicly-e2mz` and is not in any bead.** It records
the modules a lane read and declined to file against, with the reason, plus what no survey covered.
Without it the next audit re-derives all of it. Read it before commissioning anything.

**Pass 2 is the mechanism half**, and four of its six items have landed: the dead-code gate reads
schema keys rather than prose (`r343`), every role must declare a tier (`plhx`), `reviewer` is
reachable once per lens (`feje`), and tree growth is measured (`5p49`). What remains is
`basicly-xmhc`, RETROSPECTIVE as a state fired by a special-cause signal, and `basicly-u2hl.27`.

### 5A.3 The four live defects the survey found

These are bugs, not smells, and each was verified by hand rather than taken from a report.

| Bead | Defect |
| --- | --- |
| `xab3` | A unit parked in `validate` counts against the WIP bound and no pass advances it. Two modules define that population and disagree, while `wip.py:15` states they are the same on purpose. |
| `9rv0` | Two predicates answer whether a bead declares acceptance criteria. One tests a substring, one tests a whole line, so a heading quoted mid-sentence passes the readiness gate and yields the plan parser nothing. |
| `izpi` | The unsized lane bound seeds 4,000,000 against a measured median of 7,694,941 and a 0.9 quantile of 20,594,047. The comment above the constant calls it deliberately high and names erring low as the dangerous direction. |
| `n6uu` | Fragment provenance reads any absolute path component named `user` as an overlay, so a checkout under such a home directory lets a fragment silently drop a core rule from every agent. |

`xab3` is one day old at filing. VALIDATE became a phase on 2026-08-13 and its bead carried five
acceptance criteria, none of which named the supervisor's parked-lane driver. The close was correct
against the criteria and the work was incomplete against the system. **That is what the epic is for.**

### 5A.4 How to work it

The worktree mechanism and the reason it sits outside the repository are in architecture —
*Work isolation and merging*.

**`basicly-3w51` landed first, and it was not one line of configuration.** This paragraph said it
was, and so did the bead's own summary; the bead body refuted both under a heading naming two
reasons. The file is only *partly* generated, and the merge mechanism discards both sides of a
declared path, so declaring the whole path would have destroyed the hand-authored ladder prose
around the block. And `basicly build` could not rebuild it: `.scripts/docs_claims.py` is named from
`basicly.toml` and from nowhere under `src/basicly/`, so the one repo-wide `regenerate_command` was
a no-op for that block. What shipped is a keyed `[worktree.regenerate_commands]` table plus a
conflict-marker guard that refuses to stage a path whose rebuild left a marker behind — the marker
being the proof that the conflict sat in the half no rebuild owns. **A sizing claim inherited from a
summary line is a claim.**

## 5. `v0.9.0` — shipped 2026-08-14

The ordering argument below is kept because it is the reason the release was cut where it was, and
because §5.2's remaining rows carry it forward.

### 5.1 Why the plumbing precedes the features

Measured 2026-08-08 over the whole of `.basicly/usage/run-records.json`:

```text
phase    n    mean tokens   mean $   mean s   failed
lane    77      8,124,617    6.28      942    16/93 = 17.2%
build   28     11,564,149    8.70     1163     4/153 =  2.6%
decide  24         31,991    0.23       14     5/29  = 17.2%
```

`decide` is a dispatch **handed its corpus**; `lane` is a dispatch **told to go and read**. Same
model, same repo: **254x the tokens and 27x the cost**. `dispatch_brief.dispatch_prompt` (`dispatch_brief.py:121`) is
about ninety words and passes only the issue id — no requirement, no scope, no plan, no prior
finding — so the floor every lane pays before its first edit is bought by the prompt, not by the
work.

Per unit of output that floor dominates small work: `basicly-u2hl.16` cost **$0.077 per changed
line**, `basicly-u2hl.14` **$0.010**. The band has a floor verdict for exactly this and **the floor
never refuses**; only the ceiling does.

The failures are not incidental. In one pass: three of five lanes bounced on shared anchors, two
were wedged for an hour by a decider reasoning from a refuted claim in a bead, and **two lanes
silently lost committed work to the landing rebase** — on one of them the test suite stayed green,
because the feature and its tests were dropped together.

**Each of these corrupts the evidence a feature would be judged by.** A loop state built on a
factory that loses work, cannot be audited, and moves its own ratchets is a state whose acceptance
criteria cannot be trusted. That is the ordering argument, and it is the whole of it.

### 5.2 The plumbing track, in order

**Five of the eleven rows are closed, and with them every P0 §5.1's ordering argument rested on**
[M 2026-08-14, against `.beads/issues.jsonl`]. They are recorded as one line rather than five rows,
under §1.6: a row describing shipped code is noise, and a row written in the present tense of a
live defect is worse than noise — it sends the next reader to re-do it.

```text
landed   rrah  lane transcripts persist        efw2  the scope field split in two
         5vu4  the rebase loses no work        b9ef  the decider's corpus kept honest
         ef7t  landing anchors collision-proof  3w51  each generated path rebuilds
                                                      with its own command
         4kdm  the specialist agents and the dispatch that reaches them
         ca42  chars/4 kept, evidence recorded on basicly-y8el
```

What is open, in the order the dependencies allow:

| # | Bead | P | What it fixes |
| --- | --- | --- | --- |
| 6 | `89hm` | P0 | **The context-window fix never reached consumers.** `runner.py:142` ships `claude: 200_000`; `basicly.toml` overrides to one million *for this repo only*. Every consumer inherits the defect that produced eighteen overrun beads here. **Premise corrected 2026-08-09**: the binary reports its own window on the stream as `modelUsage.<id>.contextWindow`, so the fix is to *read* it, not to ship a second hand-maintained constant that goes stale the same way (`factory-loop.md` §15.5). |
| 7 | `ejdm` | P0 | **Hand a dispatched agent the context the session already holds** — §5.1's 254x. **The mechanism is now measured, and it is `--resume --fork-session`**: four real dispatches of one seeded session on claude 2.1.226 gave `cache_create 28 / cache_read 21,620` at **$0.0115** against a cold seed's **$0.2165** — **19x on a cache hit**, context confirmed by token recall, with a fresh session id per fork so lanes do not collide. Seed one session with the corpus, fork per lane. **The per-dispatch floor is a cache miss, not tokens**, which is a different fix from the longer prompt this row originally implied. **Size it against the corrected figures, not the headline** [M 2026-08-13, claude 2.1.231, `basicly-w20y`]: the 19x denominator is the ~21,800-token host floor rather than a repo corpus, so corpus reuse is nearer **10x**; and the cross-directory penalty is **one-time per working directory, not per fork** — a first fork into a fresh worktree reads 74–87% (`$0.0376`–`$0.0643`), every later fork into that same directory reads 100% (`$0.0113`). A worktree-per-lane design therefore pays it once per worktree, so the cost is a function of dispatches-per-worktree. `--agent` composes with the fork; the `--exclude-dynamic-system-prompt-sections` interaction is **unestablished** — that probe was confounded by arm ordering. See `factory-loop.md` §15.5. |
| 8 | `xjd2` | P0 | **Dispatch through the host agent runtime instead of spawning a headless CLI.** Blocked on `ejdm`. **Its open question is answered once, not twice, and the second answer was a misreading of our own document** [corrected 2026-08-15]. `--fork-session` settles it for our own path, and that half stands. The half withdrawn: this row claimed a competing harness "runs exactly this split in production — host-runtime dispatch for the same vendor". §11.7 records the opposite. That harness's loop is **prompt assembly** — its verbs "do NOT call any LLM", they emit a prompt to stdout, and the loop is prose in `SKILL.md` the model executes. That is an existence proof for the **re-scope** branch of `xjd2` (make the loop something a host session drives), not for engine-driven host-runtime dispatch, so it argues about which branch to take rather than for taking this one. A citation is not evidence until the cited section is read. |
| 9 | `esxp` · `o40x` | P1 | Bind the band floor; give a healthy supervisor a stop that does not kill live lanes. |
| 10 | `0p8n` · `66ix` | P1 | The harness gates carried into the coding agent's own hooks (`0p8n`), and Copilot hook parity behind it (`66ix` — a Copilot consumer gets the telemetry hook and **not** the `protect-generated` guard). `66ix` was blocked on `4kdm`, which is closed. **Re-ranked in argument, not in order, 2026-08-09**: `0p8n` is enforcement at the *tool-call* boundary, which our gates do not reach at all — every one of them judges an artifact after it exists (`factory-loop.md` §11 item 8). A working shape exists to aim at: one policy kernel plus N host codecs, with a golden-file `--check` gate proving the projection converges (§11.7). Note `claude_settings.py:51` maps **2 of 31** documented hook events, so this row is engine work before it is catalog work. |

### 5.2.1 Landed 2026-08-14, after the release

Recorded as one block rather than as rows, under §1.6: a row written in the present tense of a
fixed defect sends the next reader to re-do it.

```text
jn1x     the recorded argv, so role injection is falsifiable at all
c357     the flip boundary the shadow differential is judged on
vkh0.23  `basicly tracker import`, re-runnable, refusing a post-flip ledger
ejdm.2   the acquisition/implementation split over a lane transcript
e2mz.6   the improvement controller's independent call site
48d1     architecture reconciled against the tree; 1xz1 the same for §5A
```

**Three of those changed what a later reader may believe, so they are stated rather than listed.**
`jn1x` measured **0 of 357** dispatch records carrying `--agent` against a positive control of 163
carrying `-p`: the record *re-derived* its command instead of copying what ran, so it omitted flags
the lane passes and asserted flags the decider never carried. The instrument exists now; **the
reading does not**, because the 357 historical records are unchanged. `c357` settles that step 2
proves the **dual write** agrees rather than that history agrees, and makes an empty in-scope
population **inconclusive** so it cannot license `u4xu` over zero records. `vkh0.23` gives the
import an entry point but is **deliberately not run here** — closing the gap is `u4xu`'s call, and
the sequence that makes both consistent is recorded on that bead.

**What the measurement bought, which is the reason §5.3's ordering said not to shortcut it.**
`ejdm.2`'s first pairing rule passed nine unit tests and reported a real captured lane as **100%
unattributed**. Only the demonstration — run against a transcript built by driving the real writer
with a live captured stream — falsified it. A hand-built fixture encodes the format its author
believes in (`basicly-zqgg`).

### 5.3 What remains of the loop

Everything that landed between 2026-08-08 and 2026-08-14 is specified in architecture and is
not re-listed here. This file's own §1.6 rule says a document that describes shipped code is
noise.

Open, in the order the dependencies allow: `u2hl.6` skill descriptions · `u2hl.21` diff size
reported at plan time · `u2hl.22` `change-shape` derived from an AST · D10's criterion-derived
checks · EARS. **`REPAIR as a state` left this list on a decision, not a landing**: architecture
records repair as the implementer's second mode and a dispatch label rather than a phase, so
building the state would undo a decision rather than close a gap.

**A bead that outlives its landing is the same defect as a document that does**, one layer down,
and the tracker is the layer a scheduler reads. Three rows sat on this list after their work had
landed, `u2hl.17`, `u2hl.40` and `u2hl.54` among them; all are closed now. Re-check a bead's
status and its cited code together, or the correction becomes the stale claim.

**The roster's blocker moved three times, and each move was smaller than it looked.** Artifacts
held four roles unreachable and that is fixed; the remaining failures were each one wiring, and
each has been done — `reviewer` through `LENS_ROLE_BY_PHASE`, `retrospector` through
`loop._retrospective` without making RETROSPECTIVE a phase, and `curator` through
`loop._dispatch_curation`. All seven resolve and all seven have a dispatch site.

**`reviewer` was the one that read smallest and was not.** The bead said map a role to a phase.
`ROLE_BY_PHASE` is one-to-one, and the design gives VALIDATE two roles with one dispatched once per
lens, so the lane had to give a phase a driving role and a fan-out role. **Do not size these from
the bead title.**

**`ejdm` is decomposed and its first child has landed** (`ejdm.1`). The ordering is deliberate and
should not be shortcut: the bead's causal claim — that a lane's multi-million-token floor is bought
by the dispatch instruction — had **no instrument behind it**, so the remedy could not have been
judged. `ejdm.1` records which tool each turn called; `.2` derives the acquisition-vs-implementation
split; `.3` hands the lane a durable brief assembled from artifacts the engine already holds, which
does *not* overturn D6's fresh context priming; `.4` measures before against after. Only `.4` is a
claim, and only after `.2` exists.

**`u2hl.17` shipped on D35's mechanism, and the always-on overrun it was queued behind was never
what it looked like** [closed; audit `a3ab.1` closed 2026-08-14]. The original plan — promote
`python-guidelines` to an always-on fragment — rested on a premise the mechanics research refuted:
a skill takes a `paths:` glob that both limits and triggers activation, so the scoping cost zero
always-on characters. The audit then found the `AGENTS.md` overrun was **structural to Codex**:
the extra characters are the scoped tier that claude and copilot receive as separate `paths:`-carrying
rules files and Codex cannot, so evicting baseline lines would have charged all three families to fix
one and left the cause. `codex.yaml:9` records the cap moving 12,000→16,000 instead, and `a3ab.10`
put the check on `basicly check` rather than only on `build`.

**And `u2hl.6` is larger than "descriptions".** A skill's `description` plus `when_to_use` is capped
at 1,536 characters per entry, the whole listing is budgeted at **1% of the context window**, and on
overflow the host drops descriptions **starting with the least-invoked skills** [M 2026-08-09]. That
is a feedback loop rather than a cost: the skills nobody invokes are the first to become uninvokable.
`u2hl.45` gates both caps and `a3ab.12` brought the listing under budget. **The exercised-count that
sized this row is now unsound** — `basicly-4grf`: since `ey58` injects a role's skills into the
dispatch prompt, the never-used report cannot tell an uninvoked skill from an injected one, so
"8 of 34" has no successor figure until that is fixed.

Nine `u2hl` children are **band-refused** on read cost rather than on size — the same
over-declaration `efw2` describes. Narrowing their scopes is about thirty minutes, not nine splits.

### 5.4 The hold on `u6jq.1` is discharged

**It was held on a stated precondition, and that precondition is met** [M 2026-08-14]. `u6jq.1` is
the proof that a supervised pass completes with zero interventions attributable to a harness
defect. This section held it because running it against a 17.2% lane failure rate and two known
silent data losses would have measured §5.2's defects rather than the factory, and it named the
release condition exactly: *"it becomes the right move after `rrah` and `5vu4`."* **Both closed.**

So the hold has no argument left behind it. That is not the same as "run it next" — it means the
next reader owes a *new* reason or a dispatch, not a repeat of this one. A hold whose stated
condition has been met and which is still written as a hold is indistinguishable from a decision
nobody re-examined, which is the shape §3's roster paragraph took for five days.

## 6. What is left of the `br` cut

Tracked by `basicly-vkh0`; specified by
[`requirements/work-tracker.md`](../requirements/work-tracker.md).

The five cutover steps, their states and the five unported operations are in architecture —
*Dual write, and where it leaks*, and *The shadow differential*.

**`basicly tracker shadow` is the instrument; read it rather than a number written here.**
The verdict shape that matters is that the run is `conclusive` and not yet `clean`, and that
what it finds in scope is **write surfaces that bypassed the seam** — records `br` holds
because a human ran it directly, which `basicly tracker write` and `basicly-vkh0.24` exist to
close. The disagreements it prints against pre-cutover records are excused as import history
and are not the gap. The flip's remaining distance is therefore those bypass routes plus
post-cutover records, not a defect hunt.

**The flip is also the largest single dead-code event left on the ladder**, which is why it
bounds the scope of any architectural audit run before it: every one of those call sites, the
`br.py` seam and its parsers all leave the tree at the flip. Auditing them is auditing a
scheduled deletion.

Rework state lives in `br` comments, so the rework cap — one of only two controls with a recorded
correct firing — depends on the tracker being removed.

## 7. `v1.0.0` — stabilize and declare

| Work | Why |
| --- | --- |
| Surface audit and semver freeze | Enumerate and freeze five surfaces: CLI commands and flags, `basicly.toml` plus the overlay contract, the catalog source schemas, the generated-file contract, and the owned ledger format. Each broke within the last two minors. |
| Breaking-marker discipline as a gate | The `v0.6.0` audit existed because zero of 535 commits carried a `!` marker. After the freeze, a commit changing a frozen surface without it must fail deterministically. |
| Forward-version CI job | The floor claim is "3.14+" and CI tests exactly 3.14. Add the next Python so the "+" is tested. |
| Error-path polish | `basicly check` in a never-installed repo points at `build` instead of `install`; the CLI's blanket handler leaves no `--debug` escape hatch (`tcmy.24`). |
| The acceptance test | A fresh consumer repo — git plus a uv-provisioned interpreter, no `br` — installs basicly, runs every gate, and drives one unit of work to a landed commit. |
| The consumer's own scopes are part of the surface | Added 2026-08-09 [M]. `basicly install` writes a consumer's **project** `.claude/skills/`, and skill scope precedence is enterprise > **personal > project** — the *inverse* of agents, which resolve project over user. So a developer's `~/.claude/skills/<same-name>` silently overrides a skill we shipped them, while an identically-named agent would not. A distribution tool cannot freeze a surface it does not know it loses. `u2hl.46` records it; the acceptance test above is where it is exercised. |
| Final absorption | Both requirements documents folded into architecture and deleted (§9); §3 refreshed one last time. |

**Exit criteria.** The acceptance test passes on a machine that has never seen this repo; the
compatibility policy is published; a surface-breaking commit without a marker fails CI; `v1.0.0` is
tagged by `basicly release` with both pushes explicitly owner-approved.

## 8. Standing constraints

Rules any release must honour. Each exists because breaking it cost a session or more.

- **Do not grow the schema of a component about to be replaced.** Land evidence as `[harness-*]`
  comment markers — a format we own — not as tracker fields.
- **Free deterministic gates before judged ones.** A CI check at zero token cost that catches a
  silent failure forever outranks a judged check that costs tokens per run.
- **Never lower a CI floor to make a regression pass**, and never defeat a gate to force success.
- **A ratchet moved by an artifact is not evidence.** `DEFAULT_WORKING_SET_MAX` has been retuned
  seven times, twice inside one landing, each time chasing the last dispatch. Read every derivation
  in `config.py` as measuring *declaration completeness*, not working set, until `efw2` lands.
- **A sizing control with no recorded correct firing becomes observability** (D23). The spend
  ceiling, with five correct firings, and the rework cap, with 78, keep their teeth; the runner
  timeout, the band ceiling and the context ceiling have zero between them.
- **A constant describing an external capability must be falsifiable against our own ledger.** It is
  the one class of value that is correct when written and rots silently. And a fix that reaches only
  this repo's config is not a fix (`89hm`).
- **Recall is an upper bound.** It confirms mechanism, never outcome.
- **A filter on an optional field hides a population.** Prove a zero-finding against a positive
  control.
- **Assert a platform difference by injection, not by racing.**
- **A wall-clock timestamp is evidence; nothing branches on it.**
- **A recurring follow-up shape is a symptom, not a workload.** Twelve beads named one truncation and
  none asked why the trigger fired; five survivors were killed on 2026-08-08 after probing that each
  original had in fact delivered.
- **A hand-picked set of checks is not the gate, and looks exactly like it.** Five green checker
  scripts and a green suite were reported as "all gates green" on 2026-08-14; the commit was then
  refused on two checks nobody had run. Only a runner that prints its own count — `20/20` — makes a
  subset visible (`basicly-lkzq`).
- **Measure both ratchets before writing prose, never one.** Module size is the trap this file
  records most, which is why it is the one that gets measured and density is the one that refuses.
  Three changes in one session paid for that, one of them by having its placement redesigned
  (`basicly-co64`).
- **A hand-built fixture encodes the format its author believes in.** Where a live capture exists
  in the tree, drive it: nine unit tests passed against a parser that was wrong on every real
  transcript, and the demonstration is what caught it (`basicly-zqgg`).
- **A bulk find-replace needs a line-by-line audit.** One on 2026-08-08 rewrote references to
  *deleted* files into paths that never existed; the diff caught it and nothing else would have.
- **A demonstration line is not a check until it selects something.** Five beads closed or
  worked on 2026-08-15 named a `pytest ... -k <expr>` that selects **zero** tests, against
  positive controls collecting 210, 142, 87 and 23 in the named files; every real regression
  existed under another name. D18's gate refuses a field that is absent or names nothing
  runnable, and a backticked span that matches nothing passes it. Run the criterion's own
  command before closing, and read the collected count, not the exit code.
- **A guard ships with the input that makes it fail.** Three defects on 2026-08-15 were one
  shape — a guard that fails open or fails late: an unregistered mode reader defaulting to
  `external` and skipping the dual write (`e2mz.23`), a mirror translator refusing *after* the
  write it mirrors has landed (`e2mz.24`), and the demonstration lint above. The two gates that
  caught real defects that day — `test_repo_isolation.py` and the identity scan — are the two
  carrying a positive control.

## 9. Document register

The owner rule: **the code is the authority, `architecture.md` is the human-readable status quo,
and every other document under `docs/` is temporary.** Nine files were deleted on 2026-08-08 under
it — six absorbed first, three carrying nothing live. If a document is not listed here it should not
exist.

| Document | Status | Precondition for deletion |
| --- | --- | --- |
| `architecture/architecture.md` | **Authoritative** | Never deleted. Corrected against the code whenever the two disagree. |
| `architecture/status.md` | **Live** | The capability status view, extracted from the architecture reference so the reference holds specification only. Deleted when a status view is generated from the tracker rather than written by hand. |
| `architecture/backlog.md` | **Live** | Plan-gate-shaped items the architecture review emitted. Deleted when the last item is filed as a tracked issue. |
| `architecture/conventions.md` | **Live** | How the architecture reference is produced — the diagram renderer, the types used and declined, the reading order and the authority order. Extracted so the reference describes the system and never itself. Deleted only if the reference stops carrying diagrams. |
| `requirements/factory-loop.md` | **Live** | The target loop and the measured delta from it, with 26 decisions. Drives `basicly-u2hl`. Absorbed into architecture and deleted when that epic closes. |
| `requirements/work-tracker.md` | **Live** | Survives until `br` leaves the runtime path (§6). The only record of what the replacement must be, including nine requirements carried forward from `br` defects already paid for. |
| `requirements/harness-board.md` | **Live** | The `harness-board` design, moved off the `harness/basicly-rn0o` branch on 2026-08-19 (`basicly-rn0o.12`) so the gates read it. Deleted when `basicly-rn0o` closes and the board's specification is absorbed into architecture. |
| `plan/implementation-plan.md` | **Authoritative** | This file. Deleted when `v1.0.0` ships and the ladder is spent. |
| `tutorial/first-loop.md` | **Consumer-facing** | Never deleted while `basicly install` ships. Re-executed against a fresh repo whenever a command or its output changes (`imnu.2`). |
| `how-to/customize-the-catalog.md`, `how-to/wire-up-the-verify-gate.md`, `how-to/unblock-a-commit.md`, `how-to/upgrade-and-check-drift.md`, `how-to/run-parallel-lanes.md`, `how-to/resume-a-track.md` | **Consumer-facing** | One page per recurring operation; a page goes when its operation does. Rationale stays in architecture — a how-to that starts explaining *why* is drifting into the reference. |
| `research/2026-07-26-sota-review.md` | **Dated evidence** | A review of the field on one date, plus Appendix A — the licence and provenance register the tracker work's clean-room boundary rests on. Delete when nothing cites it. |
| `research/2026-08-17-deepseek-harness.md` | **Dated evidence** | What the DeepSeek harness is and how its concepts line up against ours, read from the paper and the cloned source on one date. Input to a critique and an architecture revision, neither of which it performs. Delete when nothing cites it. |
| `research/2026-08-17-archify-visualization.md` | **Dated evidence** | Whether archify serves the interactive factory dashboard. Verdict: rejected for work state — its node schema is closed and carries no status field — with a narrow architecture-illustration case left open. Delete when nothing cites it. |
| `research/2026-08-19-documentation-routes.md` | **Dated evidence** | The probe behind `interface-facts`' route table: what `llms.txt`, Sphinx `_sources` and the GitHub Docs APIs answered for each declared dependency on one date, the absence controls, and the undocumented `client_name` the Search API requires. Delete when the skill's table stops resting on it. |

**Deleting a document means removing its references from the code first.** Under the owner rule the
code must read well enough not to need them, so a prose pointer at a design document is deleted
rather than repointed.

**The previous register was wrong on six of nine rows**, every one undercounting inbound references
— it called `gates-and-rework-design.md` "cited from `architecture.md` only" when seven modules
cited it. Do not trust a register row; run the grep.

## 10. Owner decisions still owed

**Both rows below are now owned, so this section is empty of unowned decisions.** It is kept because
the owed-then-taken pair is the useful record: row 1 was decided on 2026-07-26 and sat here for six
weeks reading as pending, which is the shape §3's roster paragraph took for five days.

1. ~~**The ceremony threshold's written form**~~ — **decided** 2026-07-26, and `imnu.5` implements it:
   the threshold is the touched path. The loop is required for any change touching `src/**` or catalog
   sources under `.basicly/**`; a docs-only, tracker-only or config-only change may take the
   lightweight path. Chosen because it is the only form a hook can check.
2. ~~**Tier 3 of the catalog eval**~~ — **taken 2026-08-14: design the arms now**, against a
   recommendation to defer it past `v1.0.0`. `basicly-imnu.13` owns it. Two things are owed: the four
   arms, each declaring its guidance configuration and what a difference from its neighbour would
   establish; and whether the safety tier gates or reports. D23 supplies the rule for the second — a
   control with no recorded correct firing becomes observability — so the real question is whether
   safety is the exception to it. **The isolation assertion is the instrument**: §11 already requires
   the harness to read back what guidance is live in a cell and fail if it does not match the arm's
   declaration, and without that every number the eval produces is unfalsifiable. Note the design must
   also state what it depends on for a sound exercised-count, since `basicly-4grf` made the 8-of-34
   figure unsound.

**Decided 2026-08-13, recorded here so they are not re-asked:**

- **Losing the github.com Copilot surface for a scoped fragment is accepted.** Not a new
  trade-off — `.basicly/core/targets/copilot.yaml` has carried it inline since the
  `.github/instructions/` twin was retired on 2026-07-16 (architecture — *Targets*): VS Code loads
  `.claude/rules/` and `.github/instructions/` with no dedup, so a twin double-loads. The
  planner already supports a scoped output (`has_scope`, which `claude.yaml` declares and
  `copilot.yaml` does not), so this is a choice rather than a vendor limit. **Four** fragments
  are scoped, not three: `platform-hermetic-tests`, `external-review`, `code-is-authoritative`,
  `model-tier-routing`. What a github.com Copilot user does not get is those four.
- **`xjd2`'s dispatch shape is hybrid** — host runtime same-vendor, subprocess cross-vendor
  (§11.7's existence proof). Watch the named failure mode: the vendor-specific path carries the
  features, so cross-vendor parity rots quietly.
- **`ca42` is closed and `harness/basicly-ca42` deleted** (tip `aa07505`). `chars/4` stays; the
  evidence lives on `basicly-y8el`, which carries both measurements.
- **A 100M L3 grant is live on `basicly-u2hl`**, covering 118 beads.

**Settled and recorded so they are not re-litigated:** the language stays **Python** (the
TypeScript prevalence in this field is a Linguist artifact, and every committer already needs
`uv` and Python 3.14 for the projected hooks); `factory-design.md` **lost tiebreaker authority**
(D24, and the file is now deleted) — authority runs *measured evidence, then the requirements
documents, then nothing else*; sizing controls become observability (D23); agent-authored guidance
never reaches the catalog without a human at any grant level (D25); roles route to the cheapest tier
that can be *relied on*, priced per landed package (D26); the tokenizer stays `chars/4` because a
real one needs a network call; declarative YAML phases are **rejected**, not deferred; the Python
3.14 floor stays.

## 11. Risks and how each is detected

| Risk | Detection |
| --- | --- |
| A gate is believed to bind because its bead is closed | Probe the gate's own functions on real inputs. Three claims in this file were false against the tree on 2026-08-08. |
| A measurement is uninterpretable because an arm was contaminated | The eval harness asserts its own isolation: read back what guidance is live in the cell and fail if it does not match the arm's declaration. |
| Eval-case coverage stalls and the gate is quietly relaxed | Tier-1 failure from the start; stage by adding entries to the enforced set, never by lowering a threshold. |
| The tracker replacement grows into a general-purpose tracker | The frozen surface list is the scope contract, and the non-goals are recorded. |
| The roster is built on a guessed tier table | `s2xf` is gated on `7bur` by an edge. If `7bur` slips, the roster waits rather than proceeding on assumption. |
| A lane silently loses committed work | `5vu4`. Until it lands, diff a landing against its pre-rebase tip — the suite will not catch it. |
| This file goes stale | §3 is generated and gated; everything else is refreshed at the start of each release, and a row citing a `file:line` is re-verified before it is worked. |

## 12. Phase labels to epics

Phase membership is a tracker **label**, not a re-parenting, so a bead's parent stays its epic of
origin. Query it rather than reading a list here:

```sh
basicly tracker list --status open   # the set
basicly loop status <issue>      # where one bead actually is
basicly tracker ready                # what to pick up next
basicly tracker blocked              # what is held, and by what
```

| Phase | Epic | Release |
| --- | --- | --- |
| 1 — buy the numbers | `basicly-agzx` | `v0.9.1` |
| 2 — free deterministic gates | `basicly-m4zv` | `v0.9.1` |
| 3 — absorb designs, pay docs debt | `basicly-imnu` | `v0.9.0` |
| 4 — always-on relief | `basicly-a3ab` | `v0.10.0` |
| 5 — judgment layer | `basicly-s2xf` | `v0.10.0` |
| 6 — own the work graph | `basicly-vkh0` | `v1.0.0` |
| 7 — factory hardening | `basicly-jr0l` | interleaved as capacity fillers |
| multi — parallel factory | `basicly-kjc5` | children spread across releases |
| — (no phase label) | `basicly-rn0o` (the local board) | `v0.10.0` |
| — | `basicly-u2hl` (the loop and its plumbing), `basicly-tcmy`, `basicly-ze8z`, `basicly-uhiq`, `basicly-ctdz` | cross-cutting; children placed by release |

Each row names **one epic**, which is the index *into* the tracker rather than a copy of its
contents. No row says which children remain — that is what the queries above answer, and a per-bead
status written here would be stale by the next close.
