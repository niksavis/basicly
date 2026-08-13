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
| Engine modules (`src/basicly/*.py`) | 84 |
| Test files | 152 |
| `[[verify.checks]]` declared | 24 |
| …of which run in `--mode fast` | 19 |
| …of which run in `--mode full` | 23 |
| …of which run in `--mode staged` | 3 |

<!-- docs-claims:end plan-current-state -->

**Not built**, re-verified against `src/` on 2026-08-09 rather than by counting closed beads:
**no EARS validation** anywhere; **VALIDATE, REPAIR and RETROSPECTIVE are not phases**;
`policy.rework_recorded` reports a cross-gate total that nothing enforces; and the four
remaining handoff schemas are unwritten, which is what blocks four of the seven roles.

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

**That third row is itself half wrong, and the correction is load-bearing** [M 2026-08-09,
re-verified 2026-08-11 at `e1a43ee`]. A phase does resolve, but only one phase ever asks:
`resolve_role` is called from `loop.py:745` alone, inside `_run_agent`, whose three call sites
(`loop.py:594`, `:1642`, `:1758`) are `build`, `repair` and `build`. `classify` and `decompose`
never call it, so **`implementer` is the only role that has ever reached an argv**. That is
`basicly-4xmu`, and it is why every decomposition to date was hand-dispatched from the driving
session.

```text
agents   11 sources · 7 loop + 4 ad-hoc · projected to both families · vendored
         1 of 7 loop roles reachable        implementer, via build AND repair
         6 unreachable — 4 have no state (u2hl.54), 2 are unrouted (4xmu)
         the projected `tools:` allowlist binds on copilot too   [M 2026-08-11]
skills   40 sources · 35 projected · 5 of 5 loop skills exist
         ever exercised                                     10 of 40
         projected listing 2342 tok vs a consumer's 2000    OVER (a3ab.12)
hooks    31 documented host events · mapped by catalog  2   (u2hl.49, blocked)
loop     loop_state.PHASES 7 incl. terminal `done` — correct, not a defect
```

Both halves of `basicly-4kdm` landed on 2026-08-09: the definitions and the dispatch that
reaches them. **What is missing is now the states, not the roster** — `validator`,
`reviewer`, `retrospector` and `curator` resolve correctly and can never be invoked. That
is `basicly-u2hl.54` and it is §5.3's work, not §6's or §7's.

A competing harness with strictly worse definitions — hand-written, no schema, no projection, no
vendoring — beats this repo on all four rows because its definitions are **wired**
(`factory-loop.md` §11.7). That is not an argument for its design; its loop is the declarative-phase
pattern this repo already rejected, and §11.7 carries the receipts. It is the argument that the next
unit of work on this axis is **wiring, not authoring**.

**Four figures this file previously got wrong**, corrected against the tree:

| Was written | Measured 2026-08-08 |
| --- | --- |
| "Step 1 has not been run… `.basicly/ledger/` holds no `events-*.jsonl`" | the import **ran** (`b97a653`): 3,775 events over 643 records |
| `br` spawn sites 43 / 44 | **31**, across 11 modules — the old count included `from .br import` lines and docstrings |
| 20 verify checks | **22** |
| `basicly-tcmy.34` "the remaining P0" | **closed** 2026-08-05 |

**And one that matters more than the rest**: `basicly-u6jq.1` — `v0.7.0`'s unmet exit criterion 5,
the unattended multi-lane proof — is **unblocked and scheduler-ranked first**, and has been. All 22
of its `blocks` edges point at closed beads, and `br` computes readiness from target status rather
than edge presence, so nothing was ever holding it. It is deliberately **not** run yet: see §5.4.

## 4. The release ladder

Rows are in shipping order. A **release** is a shippable cut; the phase labels in the tracker are
the dependency clusters a cut draws from (§12).

| Release | Content and reason | Size |
| --- | --- | --- |
| ~~`v0.7.0`~~ | **SHIPPED 2026-08-06.** Trustworthy factory. Exit criterion 5 was not met, and the release documented that rather than claiming it. | shipped |
| ~~`v0.8.0`~~ | **SHIPPED 2026-08-07.** Own the work graph — the store, not the floor. `br` is still in the runtime path (§6). | shipped |
| **`v0.9.0`** | **Make the factory's own plumbing trustworthy, then finish the loop.** §5. The quality floor and five loop features landed on 2026-08-08; the same session measured the plumbing under them failing. Fifteen ordered items, plus what remains of the loop. | 8–12 sessions |
| **`v0.9.1`** | **The measured evidence layer, when its chain unblocks.** Cost per landed package (`7bur`), AST localisation (`agzx.2`), the remaining Phase 2 gate (`m4zv.3`), parameter learning (`3ifz`). Split out because the whole chain sits behind `u6jq.1`, and holding the loop behind it would ship neither. | 3–5 sessions |
| **`v0.10.0`** | **The judgment layer and always-on relief.** The roster's routing (`s2xf`) once `7bur` has numbers; the Phase 4 authoring pass and the empty-glob check (`a3ab.1`–`.3`), now joined by `a3ab.10` — `AGENTS.md` is **1,135 characters over** its cap, which makes `a3ab.1`'s eviction audit the gating item of this row rather than a tidy-up. `a3ab.8` closed 2026-08-09 without adding to the always-on layer (D34), so nothing new is queued against that budget. | 5–8 sessions |
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

## 5. `v0.9.0` — the plumbing, then the loop

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

| # | Bead | P | What it fixes |
| --- | --- | --- | --- |
| 1 | `rrah` | P0 | **No lane transcript is persisted anywhere.** The stream is read into an in-memory sink and spent on token accounting; `.basicly/usage/` holds totals and nothing about what a lane did. Until this lands, no claim about a lane is checkable after it ends — including every claim in this section. |
| 2 | `5vu4` | P0 | **The landing rebase silently discards a lane's merge-commit conflict resolution.** `git rebase` skips merge commits and reports success. Twice on 2026-08-08; the suite is not a backstop. |
| 3 | `ef7t` · `3w51` | P0 · P1 | Three shared landing anchors — `basicly.toml` checks, `pyproject.toml` frozen lists, and the generated block in this file — bounced three of five lanes. `3w51` is the generated-block half. |
| 4 | `efw2` | P0 | **One `## Scope` field feeds two gates that want opposite things** — collision detection wants it complete, the band prices what it reads. Declaring honestly took one bead from 78,709 to 197,646 to 245,466 on an unchanged diff, and the ceiling moved twice inside one landing. |
| 5 | `b9ef` | P0 | **The decider's corpus is the epic's bead text**, which still asserted a claim its own requirements document had refuted. It quoted that claim, reasoned from it, and abstained — wedging two lanes. |
| 6 | `89hm` | P0 | **The context-window fix never reached consumers.** `runner.py:142` ships `claude: 200_000`; `basicly.toml` overrides to one million *for this repo only*. Every consumer inherits the defect that produced eighteen overrun beads here. **Premise corrected 2026-08-09**: the binary reports its own window on the stream as `modelUsage.<id>.contextWindow`, so the fix is to *read* it, not to ship a second hand-maintained constant that goes stale the same way (`factory-loop.md` §15.5). |
| 7 | `ejdm` | P0 | **Hand a dispatched agent the context the session already holds** — §5.1's 254x. **The mechanism is now measured, and it is `--resume --fork-session`**: four real dispatches of one seeded session on claude 2.1.226 gave `cache_create 28 / cache_read 21,620` at **$0.0115** against a cold seed's **$0.2165** — **19x on a cache hit**, context confirmed by token recall, with a fresh session id per fork so lanes do not collide. Seed one session with the corpus, fork per lane. **The per-dispatch floor is a cache miss, not tokens**, which is a different fix from the longer prompt this row originally implied. |
| 8 | `xjd2` | P0 | **Dispatch through the host agent runtime instead of spawning a headless CLI.** Blocked on `ejdm`. **Its open question is answered twice over**: `--fork-session` settles it for our own path, and a competing harness runs exactly this split in production — host-runtime dispatch for the same vendor, subprocess only for cross-vendor delegation (`factory-loop.md` §11.7). It is an existence proof, not a design. |
| 9 | `esxp` · `o40x` | P1 | Bind the band floor; give a healthy supervisor a stop that does not kill live lanes. |
| 10 | `4kdm` · `0p8n` · `66ix` | P1 | The specialist agents and skills the states already name (`4kdm`), the harness gates carried into the coding agent's own hooks (`0p8n`), and Copilot hook parity behind it (`66ix` — a Copilot consumer gets the telemetry hook and **not** the `protect-generated` guard). `66ix` is blocked on `4kdm` by an edge. **Re-ranked in argument, not in order, 2026-08-09**: `0p8n` is enforcement at the *tool-call* boundary, which our gates do not reach at all — every one of them judges an artifact after it exists (`factory-loop.md` §11 item 8). A working shape exists to aim at: one policy kernel plus N host codecs, with a golden-file `--check` gate proving the projection converges (§11.7). Note `claude_settings.py:51` maps **2 of 31** documented hook events, so this row is engine work before it is catalog work. |
| 11 | `ca42` | P0 | Rescoped: **keep `chars/4`**, record the evidence. `tiktoken` fetches a 3.5 MB vocabulary over HTTPS on first use, which a consumer's git hook cannot do. |

### 5.3 What remains of the loop

Landed 2026-08-08 and unreleased: the plan gate on entry to BUILD, integrity levels from a path
rule with the diff-size downgrade, Hold and Kill as writes, repair in the lane's own worktree, the
module-size ratchet, the first two handoff artifacts (`implementation-plan`, `change-summary`), the
D18 demonstration field, the WIP bound, and the code-quality floor.

Open, in the order the dependencies allow: `u2hl.6` skill descriptions · `u2hl.21` diff size
reported at plan time · the four remaining handoff schemas · **VALIDATE as a real state** · D10's criterion-derived checks · EARS ·
RETROSPECTIVE's special-cause signal · `u2hl.17` once `a3ab.1` evicts an always-on line.

**`u2hl.17` no longer waits on that eviction** [D35, 2026-08-09]. The plan behind it — promote
`python-guidelines` to an always-on fragment — rested on a premise the mechanics research refuted: a
skill takes a `paths:` glob and triggers on it. What the row should have said all along is that
`AGENTS.md` is **1,135 characters over** its cap, not 1,225 under, so `a3ab.1`'s audit is more urgent
than this row and less coupled to it. `a3ab.10` now makes the overrun visible from `basicly check`
rather than only from `build`, which is what let the figure stay wrong.

**And `u2hl.6` is larger than "descriptions".** A skill's `description` plus `when_to_use` is capped
at 1,536 characters per entry, the whole listing is budgeted at **1% of the context window**, and on
overflow the host drops descriptions **starting with the least-invoked skills** [M 2026-08-09]. With
8 of 34 ever exercised, that is a feedback loop rather than a cost: the skills nobody invokes are the
first to become uninvokable. `u2hl.45` gates both caps.

Nine `u2hl` children are **band-refused** on read cost rather than on size — the same
over-declaration `efw2` describes. Narrowing their scopes is about thirty minutes, not nine splits.

### 5.4 Do not start here

**`u6jq.1` is unblocked and ranked first, and is deliberately held.** It is the proof that a
supervised pass completes with zero interventions attributable to a harness defect. Running it
against a 17.2% lane failure rate and two known silent data losses would measure the defects in
§5.2 rather than the factory. It becomes the right move after `rrah` and `5vu4`.

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

**31 spawn sites** across 11 modules behind the one seam in `br.py`. Only `show`, `scheduler` and
comments have owned equivalents. **Five operations have none at all**, and each is a design
question rather than a port: `lint` (which means owning the validation rules — requirement R3),
`dep cycles`, `list --label`, id minting (`ids.mint_root_id` exists and nothing calls it), and
`gate list` (the owned side reads `missing` on 331 of 643 records because only the dual write
populates it).

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
2. **Whether losing the github.com Copilot surface is acceptable, per scoped fragment.** Scoping
   removes a fragment from that surface entirely — a guarantee change, not a refactor. Three
   fragments are already scoped and the call was never made for any of them.
3. **Tier 3 of the catalog eval** — the four arms, and the safety tier as a gate rather than a
   metric. Architecture §14.4 names the shape; the arms table is unbuilt and unowned.

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
