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
Architecture is the status quo; the two requirements documents are the target; this file is the
order.

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
   `.beads/issues.jsonl` for whole-tracker questions — `br list --json` caps its result and drops
   closed rows. Structural figures that *are* here come from a generated block (§3).
5. **Measure before you dispatch.** Take the baseline in base, record it on the bead, diff against
   it at review. A lane that rewords a number instead of measuring it produces a regression under
   an acceptance criterion claiming improvement.
6. **A document that describes shipped code is noise.** If it is in the code, it does not belong in
   a document (§9). This rule deleted nine files on 2026-08-08.

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

1. Every agreed design is implemented — architecture §14's target state is running code, and every
   requirements document is absorbed and deleted (§9).
2. The consumer criterion is *demonstrated* (`basicly-ctdz`): a fresh repo with only git and a
   uv-provisioned Python interpreter installs basicly, runs every gate and drives the loop end to
   end, with no external `br` binary.
3. A full semver contract: the CLI surface, `basicly.toml`, the catalog source schemas, the
   generated-file contract and the owned ledger format are declared stable. Every one of those
   broke within the last two minors, so the promise needs a stabilization release (§7), not a
   version-number ceremony.

**Non-goals**, so the plan cannot quietly grow: an LLM orchestrator; personas spawning personas;
an agent-writable catalog; a general-purpose issue tracker; a maintained TUI; an external database
or daemon; agent-to-agent messaging. Reasons are in architecture §14.7.

## 3. Current state

Structural figures are generated from the tree and gated on every commit, because every
hand-written copy of them was stale within days:

<!-- docs-claims:begin plan-current-state -->

| Measure | Value |
| --- | --- |
| Engine modules (`src/basicly/*.py`) | 89 |
| Test files | 165 |
| `[[verify.checks]]` declared | 25 |
| …of which run in `--mode fast` | 20 |
| …of which run in `--mode full` | 24 |
| …of which run in `--mode staged` | 3 |

<!-- docs-claims:end plan-current-state -->

**Not built**, re-verified against `src/` on 2026-08-13 rather than by counting closed beads:
**no EARS validation** anywhere; **REPAIR and RETROSPECTIVE are not phases** (VALIDATE now is,
`basicly-u2hl.54`); and `policy.rework_recorded` reports a cross-gate total that nothing
enforces.

**Two entries left this list on 2026-08-13 and the reason matters more than the fact.** VALIDATE
is a phase, gated at the recorded L3 level, with the validator dispatched from it and priced as a
read rather than a write. And the handoff schemas are written: seven of the eight named kinds now
have one, so the four unreachable roles are no longer blocked *by their contract*. `curator` and
`retrospector` remain unreachable for a different reason — `_on_ship` never dispatches, and
RETROSPECTIVE has no state — and `reviewer` has no `ROLE_BY_PHASE` entry at all. **Do not read
"schemas written" as "roles reachable".**

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
roles.resolve_role      loop.py:818   _run_agent      → validate · build · repair · sub-task build
                        loop.py:1062  _run_proposer   → classify (:1138) · decompose (:1173)
                        supervise.py:2679             → lane build
_run_agent call sites   loop.py:459 validate · :667 build · :1610 repair · :1739 sub-task
```

The lesson is the one below it, not the wiring: a paragraph naming a **closed** bead reads as live
work to the next reader, and this section is where a decider looks first. Re-check a `file:line`
and a bead status together, or the correction becomes the stale claim.

```text
agents   12 sources · 7 loop + 5 ad-hoc · projected to both families · vendored
         5 of 7 loop roles reachable   decider, decomposer, implementer, validator, reviewer
         2 unreachable — curator (ship never dispatches), retrospector
         (RETROSPECTIVE is not a phase)                     [M 2026-08-14]
         12 of 12 declare a tier; catalog lint refuses one that does not (plhx)
         a declared tier reaches no spawn — D30 keeps the id out, a3yi injects it
         5 of 5 roles declaring `skills:` now receive them  (basicly-ey58)
         the projected `tools:` allowlist binds on copilot too   [M 2026-08-11]
skills   40 sources · 35 projected · 5 of 5 loop skills exist
         ever exercised                                     report unsound (basicly-4grf)
         projected listing under a consumer's budget        UNDER (a3ab.12 closed)
hooks    31 documented host events · mapped by catalog  2   (u2hl.49, blocked)
always-on  AGENTS.md 14,428 chars / 242 lines vs 16,000 / 320   UNDER  [M 2026-08-14]
loop     7 phases; VALIDATE dispatches validator plus reviewer once per lens
tree     175 → 313 tracked modules and +279,788 tokens in the 7 days to
         2026-08-14, with every per-file gate green throughout   (5p49)
```

**The roster's remaining two fail for one reason each, and neither is the roster's.** `curator`
maps to `ship`, which is a live phase with a live handler that never calls `_run_agent`.
`retrospector` maps to `retrospective`, which is not in `loop._HANDLERS` at all. Both are one
wiring behind a state, and `basicly-xmhc` builds the state the second needs.

**The tree row is the finding this section did not have an instrument for until 2026-08-14.** A
ratchet bounds a file. Nothing bounded the tree, so 138 modules arrived in a week under six green
gates. `basicly-5p49` measures it now, and it reports rather than blocks because it has no firing
history yet.

A competing harness with strictly worse definitions — hand-written, no schema, no projection, no
vendoring — beats this repo on all four rows because its definitions are **wired**
(`factory-loop.md` §11.7). That is not an argument for its design; its loop is the declarative-phase
pattern this repo already rejected, and §11.7 carries the receipts. It is the argument that the next
unit of work on this axis is **wiring, not authoring**.

**Four figures this file previously got wrong**, corrected against the tree:

| Was written | Measured 2026-08-08 |
| --- | --- |
| "Step 1 has not been run… `.basicly/ledger/` holds no `events-*.jsonl`" | the import **ran** (`b97a653`): 3,775 events over 643 records |
| `br` spawn sites 43 / 44 | **32**, across 12 modules [M 2026-08-14, `rg 'run_br\(' src/basicly/*.py` less `br.py`] — the 43/44 count included `from .br import` lines and docstrings, and 31/11 was this row's own reading one week stale |
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
| **`v0.10.0`** | **The judgment layer and always-on relief.** The roster's routing (`s2xf`) once `7bur` has numbers; the Phase 4 authoring pass and the empty-glob check (`a3ab.2`–`.3`). **This row had a gating item and no longer does** [M 2026-08-14]: `a3ab.1`, `.10` and `.12` are all closed, and `AGENTS.md` is **14,428 characters against a 16,000 cap** — 1,572 under, not 1,135 over. The audit `a3ab.1` ran found the overrun was the *scoped tier* Codex cannot receive as separate `paths:` rules files, so evicting always-on lines would have cost all three families to fix one; `codex.yaml:9` records the cap moving 12,000→16,000 instead. Three documents outlived that by five days — see §3's note on correcting a `file:line` and a bead status together. | 5–8 sessions |
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

## 5A. `v0.9.1` first half — the architectural backlog. **Start here.**

Tracked by `basicly-e2mz`. **34 open children, 21 of them ready**, and this is the priority for the
next session and the ones after it until the epic closes.

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

Sibling worktrees, one lane each, through `basicly worktree create` and `worktree merge --bead`.
They land outside the repo at `basicly.worktrees/`, which is what keeps them clear of the trap where
an agent worktree sits inside the tree and stales every whole-tree baseline.

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
model, same repo: **254x the tokens and 27x the cost**. `loop.dispatch_prompt` (`loop.py:858`) is
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
| 8 | `xjd2` | P0 | **Dispatch through the host agent runtime instead of spawning a headless CLI.** Blocked on `ejdm`. **Its open question is answered twice over**: `--fork-session` settles it for our own path, and a competing harness runs exactly this split in production — host-runtime dispatch for the same vendor, subprocess only for cross-vendor delegation (`factory-loop.md` §11.7). It is an existence proof, not a design. |
| 9 | `esxp` · `o40x` | P1 | Bind the band floor; give a healthy supervisor a stop that does not kill live lanes. |
| 10 | `0p8n` · `66ix` | P1 | The harness gates carried into the coding agent's own hooks (`0p8n`), and Copilot hook parity behind it (`66ix` — a Copilot consumer gets the telemetry hook and **not** the `protect-generated` guard). `66ix` was blocked on `4kdm`, which is closed. **Re-ranked in argument, not in order, 2026-08-09**: `0p8n` is enforcement at the *tool-call* boundary, which our gates do not reach at all — every one of them judges an artifact after it exists (`factory-loop.md` §11 item 8). A working shape exists to aim at: one policy kernel plus N host codecs, with a golden-file `--check` gate proving the projection converges (§11.7). Note `claude_settings.py:51` maps **2 of 31** documented hook events, so this row is engine work before it is catalog work. |

### 5.3 What remains of the loop

Landed 2026-08-08 and unreleased: the plan gate on entry to BUILD, integrity levels from a path
rule with the diff-size downgrade, Hold and Kill as writes, repair in the lane's own worktree, the
module-size ratchet, the first two handoff artifacts (`implementation-plan`, `change-summary`), the
D18 demonstration field, the WIP bound, and the code-quality floor.

Landed 2026-08-13: **VALIDATE as a real state** (`u2hl.54`) — a phase gated at the recorded L3
level, refusing its advance on a failed or missing consumer gate, dispatching the `validator` and
pricing that dispatch as a read rather than a write. **Five more handoff schemas** (`r4jm`), so
seven of the eight named kinds have a contract. And a role's declared `skills:` now reach the
agent dispatched for it (`ey58`) — measured at ~0.03% of a lane, and reaching all three families
rather than the one the vendor mechanism serves.

Landed 2026-08-14, all through sibling worktrees: **`reviewer` is reachable** (`feje`) — a phase now
has a driving role and a fan-out role, and VALIDATE dispatches `reviewer` once per lens beside the
validator, at two lenses, priced as reads, on L3 units only. **Every role must declare a tier**
(`plhx`), refused by `catalog lint` on core and overlay alike. **The dead-code gate reads schema
keys rather than prose** (`r343`), which unmasked one real finding. **Tree growth is measured**
(`5p49`).

Open, in the order the dependencies allow: `u2hl.6` skill descriptions · `u2hl.21` diff size
reported at plan time · `u2hl.22` `change-shape` derived from an AST · D10's criterion-derived
checks · EARS · REPAIR as a state · **`xmhc` RETROSPECTIVE as a state fired by a special-cause
signal**, which is the one that makes `retrospector` reachable and is pass-2 work under §5A.

**Two rows left this list because the work landed and the bead did not close** [M 2026-08-14].
`u2hl.17` is closed — `python-guidelines` carries its `paths:` glob. `u2hl.40` is open and the
`root-cause` skill is authored and projected. `u2hl.54` is open with `.1`, `.2` and `.3` all
closed and VALIDATE live in `loop._HANDLERS`. A bead that outlives its landing is the same defect
as a document that does, one layer down, and the tracker is the layer a scheduler reads.

**The roster's blocker moved twice, and each move was smaller than it looked.** Artifacts held four
roles unreachable and that is fixed. Reachable went **1 → 4** on 2026-08-09, then **4 → 5** on
2026-08-14 when `reviewer` got a phase. Each remaining failure has its own cause and its own size:

```text
curator        maps to `ship`, a live phase with a live handler that never calls
               `_run_agent`.  One wiring.
retrospector   maps to `retrospective`, which is not in `loop._HANDLERS` at all.
               Blocked on the state — basicly-xmhc builds it.
```

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

The migration is five steps and they did not run in order:

```text
1 import          RAN once by hand (b97a653) - but migrate.import_snapshot has no caller,
                  no main() and no CLI, so it cannot be repeated  (basicly-vkh0.23, P0)
2 shadow          machinery ships; MUST run on `dual`, so it cannot run today
3 dual-write      NOT RUN - basicly.toml says mode = "external"
4 flip            blocked on 3
5 native markers  LANDED 2026-08-07, before steps 2-4
```

**32 spawn sites across 12 modules** behind the one seam in `br.py` [M 2026-08-14,
`rg 'run_br\(' src/basicly/*.py` less `br.py` itself: `supervise` 8, `decompose` 6, `loop` 4,
`policy` 4, `merge` 3, then one each in `classify`, `loop_state`, `cli`, `rubrics`, `worktree`,
`verify`, `validate_gate`]. Only `show`, `scheduler` and comments have owned equivalents. **Five
operations have none at all**, and each is a design question rather than a port: `lint` (which
means owning the validation rules — requirement R3), `dep cycles`, `list --label`, id minting
(`ids.mint_root_id` exists and only tests call it), and `gate list` (the owned side reads
`missing` on 331 of 643 records because only the dual write populates it).

**This is also the largest single dead-code event left on the ladder**, which is why it bounds the
scope of any architectural audit run before it: 32 call sites, the `br.py` seam and its parsers all
leave the tree at step 4. Auditing them is auditing a scheduled deletion.

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
- **A bulk find-replace needs a line-by-line audit.** One on 2026-08-08 rewrote references to
  *deleted* files into paths that never existed; the diff caught it and nothing else would have.

## 9. Document register

The owner rule: **the code is the authority, `architecture.md` is the human-readable status quo,
and every other document under `docs/` is temporary.** Nine files were deleted on 2026-08-08 under
it — six absorbed first, three carrying nothing live. If a document is not listed here it should not
exist.

| Document | Status | Precondition for deletion |
| --- | --- | --- |
| `architecture/architecture.md` | **Authoritative** | Never deleted. Corrected against the code whenever the two disagree. |
| `requirements/factory-loop.md` | **Live** | The target loop and the measured delta from it, with 26 decisions. Drives `basicly-u2hl`. Absorbed into architecture and deleted when that epic closes. |
| `requirements/work-tracker.md` | **Live** | Survives until `br` leaves the runtime path (§6). The only record of what the replacement must be, including nine requirements carried forward from `br` defects already paid for. |
| `plan/implementation-plan.md` | **Authoritative** | This file. Deleted when `v1.0.0` ships and the ladder is spent. |
| `tutorial/first-loop.md` | **Consumer-facing** | Never deleted while `basicly install` ships. Re-executed against a fresh repo whenever a command or its output changes (`imnu.2`). |
| `how-to/customize-the-catalog.md`, `how-to/wire-up-the-verify-gate.md`, `how-to/unblock-a-commit.md`, `how-to/upgrade-and-check-drift.md`, `how-to/run-parallel-lanes.md`, `how-to/resume-a-track.md` | **Consumer-facing** | One page per recurring operation; a page goes when its operation does. Rationale stays in architecture — a how-to that starts explaining *why* is drifting into the reference. |
| `research/2026-07-26-sota-review.md` | **Dated evidence** | A review of the field on one date, plus Appendix A — the licence and provenance register the tracker work's clean-room boundary rests on. Delete when nothing cites it. |

**Deleting a document means removing its references from the code first.** Under the owner rule the
code must read well enough not to need them, so a prose pointer at a design document is deleted
rather than repointed.

**The previous register was wrong on six of nine rows**, every one undercounting inbound references
— it called `gates-and-rework-design.md` "cited from `architecture.md` only" when seven modules
cited it. Do not trust a register row; run the grep.

## 10. Owner decisions still owed

1. **The ceremony threshold's written form** (`imnu.5`). The loop is mandated for "non-trivial
   work", which is the agent's judgment call, so the rule is unenforceable. Needs a written
   threshold **and** a named lightweight path below it that skips ceremony but never hooks.
2. **Tier 3 of the catalog eval** — the four arms, and the safety tier as a gate rather than a
   metric. Architecture §14.4 names the shape; the arms table is unbuilt and unowned.

**Decided 2026-08-13, recorded here so they are not re-asked:**

- **Losing the github.com Copilot surface for a scoped fragment is accepted.** Not a new
  trade-off — `.basicly/core/targets/copilot.yaml` has carried it inline since the
  `.github/instructions/` twin was retired on 2026-07-16 (architecture §858): VS Code loads
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
br list --label phase-2          # membership
basicly loop status <issue>      # where one bead actually is
br scheduler                     # what to pick up next
br dep tree <issue>              # what blocks it
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
| — | `basicly-u2hl` (the loop and its plumbing), `basicly-tcmy`, `basicly-ze8z`, `basicly-uhiq`, `basicly-ctdz` | cross-cutting; children placed by release |

Each row names **one epic**, which is the index *into* the tracker rather than a copy of its
contents. No row says which children remain — that is what the queries above answer, and a per-bead
status written here would be stale by the next close.
