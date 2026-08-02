# basicly Implementation Plan

Authored 2026-07-26 against `main` @ `b02b527`. Reviewed: every document in
[`docs/design/`](../design/) and [`docs/research/`](../research/), the engine modules under
`src/basicly/`, and every non-closed tracker record. **Reshaped 2026-07-30 against `main` @
`31d441d`** to terminate at a defensible v1.0.0 (owner decisions in §1.1 and §7.2), after a
second full review of the designs and the engine — six previously filed defect claims
confirmed at HEAD, four new defects filed with evidence as `basicly-jr0l.49`–`.52`.

**§2 last refreshed 2026-07-30 against `main` @ `31d441d`** (§8 makes this a standing
obligation at the start of each phase; §2 states the commit it was measured at, and that
stamp — not this header's — is the one to trust).

## 0. What this document is, and why it is a file

**This plan is externalized on purpose.** The work tracker is itself scheduled for replacement
(Phase 6), so a roadmap that lived only in `br` would be lost or silently wrong the moment its
storage changed. Beads carry _one unit of work each_; this file carries the _order and the
reasons_, which no single bead can hold and which a graph of 50 beads does not make legible.

Three things this document is **not**:

- **Not a bead decomposition.** Turning phases into beads with dependency edges is the next task
  and is deliberately out of scope here. Where a bead already exists it is named, so the
  decomposition is a mapping exercise rather than a re-derivation.
- **Not authoritative over architecture.** [`docs/architecture/architecture.md`](../architecture/architecture.md)
  remains the single authoritative reference. This plan sequences the §14 target state; if the two
  disagree, architecture wins and this file is stale.
- **Not a schedule.** Session counts are sizing signals for decomposition, not commitments. A
  "session" here means one focused working context, roughly what fits before compaction.

Status convention, matching the architecture reference: a claim about **current state** (§2) is
about running code and cites how it was measured. Everything in §4 onward is **intent**.

## 1. The destination

`basicly` is a harness for coding agents that ships its own development process. Four pillars
(architecture §0): **guidance**, **gates**, **the loop**, **the work graph**. The destination is
each pillar being true, enforced, and _measured_:

| Pillar | Done means |
| --- | --- |
| Guidance | Every entry's routing and behavioural effect is measured; nothing ships on assertion. Conditional guidance is path-scoped, not always-on. The delivered guarantee per agent family is stated rather than implied. |
| Gates | Every gate is classified by type, so "what happens when this fails" is answered by the type and not per call site. Judged output carries a required severity and cannot pass a required gate. Rework detects non-convergence instead of burning its cap. |
| The loop | Every deterministic step is one command. Every judgment step is routed to a named role at a tier chosen by measured reliability. A supervised multi-lane run completes with no human intervention caused by a harness defect. |
| The work graph | Owned in-process: an append-only event log we control, with provenance on every edge, no external binary and no bootstrap step in the critical path. |

And three invariants that constrain _how_ any of it may be built:

1. **The engine disposes, agents propose.** No model holds authority over the tracker, the
   schedule, or a required gate — at any autonomy level.
2. **Determinism where the answer is derivable.** A model is paid only where it is not, and then
   at the tier that can be relied on, priced per landed package.
3. **Evidence over assertion.** An unmeasured behavioural claim is a liability: it costs context
   every turn and confers confidence nobody earned.

**Explicit non-goals**, so the plan cannot quietly grow: an LLM orchestrator; personas spawning
personas; an agent-writable catalog; a general-purpose issue tracker; a maintained TUI; an
external database or daemon; agent-to-agent messaging. Reasons are in architecture §14.7.

### 1.1 What v1.0.0 means — decided by the owner, 2026-07-30

v1.0.0 is the first stable, production-ready release: no longer a pre-release, usable in a
production setting. Three conditions, all required:

1. **Every agreed design is implemented.** The architecture reference's §14 target state — the
   owned tracker, the judgment layer, the evidence tiers, the gate taxonomy — is running code,
   and each design document is absorbed and archived (§3.5).
2. **The consumer criterion is demonstrated, not asserted** (`basicly-ctdz`): a fresh repo with
   only git and a uv-provisioned Python interpreter installs basicly, runs every gate, and
   drives the loop end to end — with no external `br` binary. This is what makes Phase 6 the
   load-bearing 1.0.0 gate rather than a cost optimisation (§4, Phase 6).
3. **A full semver contract.** The CLI surface, `basicly.toml`, the catalog source schemas, the
   generated-file contract, and the owned ledger format are declared stable; breaking changes
   after 1.0.0 land only at 2.0 behind a deprecation policy. This is the strongest of the three
   candidate readings and was chosen deliberately: every one of those surfaces broke within the
   last two minor versions, so the promise needs a real stabilization phase (Phase 8), not a
   version-number ceremony.

### 1.1 Every document, and whether its design is built

Owner rule, 2026-08-02: **the code is the source of truth. A document that has served its
purpose is deleted, not archived, and no document is authoritative except
[`architecture.md`](../architecture/architecture.md).** This plan is the index that makes that
rule enforceable — if a document is not listed here it should not exist, and if its design is
built and described in the architecture then its purpose is served.

`Live` means the document specifies work not yet built, so deleting it would lose a
requirement. `Fulfilled` means its design is implemented and the architecture describes the
implementation, so it is a deletion candidate under the rule above.

| Document | Status | What it still carries |
| --- | --- | --- |
| [`architecture/architecture.md`](../architecture/architecture.md) | **Authoritative** | The exception to the rule. Never deleted; corrected against the code whenever the two disagree. |
| [`plan/implementation-plan.md`](implementation-plan.md) | **Authoritative** | This file: the ladder to 1.0.0 and the index above. |
| [`design/work-tracker.md`](../design/work-tracker.md) | **Live** | Requirements for the `br` replacement (Phase 6, `vkh0`). Nothing is built yet, so this is the only record of what must be. |
| [`design/agent-roster-design.md`](../design/agent-roster-design.md) | **Live** | The seven-persona roster (Phase 5, `s2xf`). Design agreed, not implemented. |
| [`design/factory-design.md`](../design/factory-design.md) | Mostly fulfilled | Decisions D1–D10 are cited by name from the code and from this plan; the loop, queue and grants are built. Fold the still-live decisions into the architecture, then delete. |
| [`design/gates-and-rework-design.md`](../design/gates-and-rework-design.md) | Mostly fulfilled | Gates are built. The bounded-rework subsystem is built but has **never fired** — 0 gate failures in 277 recorded results — so its design is unvalidated rather than unbuilt. |
| [`design/steering-surfaces-design.md`](../design/steering-surfaces-design.md) | Fulfilled | Owns the recall result (claude 98% of 53 rules, copilot 93% of 54, against 17%/6% no-guidance controls). Fold that result into the architecture, then delete. |
| [`design/catalog-efficacy-design.md`](../design/catalog-efficacy-design.md) | Fulfilled | §4.1's rule survives and must move before deletion: recall is an upper bound and may **not** be cited as evidence of quality. |
| [`design/tier-injection-kit.md`](../design/tier-injection-kit.md) | Fulfilled | The kit ships at `.basicly/core/kit/`. Referenced from `CHANGELOG.md`. |
| [`design/harness-eval.md`](../design/harness-eval.md) | Fulfilled | Superseded by the shipped `rubric` command and the recall harness. |
| [`research/2026-07-26-sota-review.md`](../research/2026-07-26-sota-review.md) | Dated snapshot | A review of the field on one date. Its conclusions are already absorbed into the designs that cite it. |
| [`research/references.md`](../research/references.md) | Dated snapshot | Citation list for the above. |

Two documents are **already deletable** and carry zero inbound references from anywhere in the
repo: `docs/archive/foundry-spike.md` and `docs/architecture/hook-runner-decision.md`. The
`docs/archive/` directory goes with the first of them — an archive is the thing this rule
forbids.

Deleting a fulfilled design means **removing its references from the code first**. Eight
prose references point from `src/basicly/` at design documents; under the owner rule the code
must read well enough not to need them, so they are deleted rather than repointed.

## 2. Current state, measured

This section used to be checked against the tree by hand at a named commit, and re-checked, and
re-checked again. It was stale every time within days, and by 2026-08-02 two of its own
paragraphs contradicted each other — one said 88 open / 371 closed, the other 60 open / 332
closed, and neither matched the tree. That is `basicly-tcmy.26`, whose own recorded "actual"
figures were themselves stale one day after it was filed.

**So the structural figures are generated, not written** (`basicly-uhiq.1`). The block below is
rendered from the tree by `.scripts/docs_claims.py` and gated on every commit:

<!-- docs-claims:begin plan-current-state -->

| Measure | Value |
| --- | --- |
| Engine modules (`src/basicly/*.py`) | 43 |
| Test files | 69 |
| `[[verify.checks]]` declared | 15 |
| …of which run in `--mode fast` | 11 |
| …of which run in `--mode full` | 14 |
| …of which run in `--mode staged` | 3 |

<!-- docs-claims:end plan-current-state -->

Two kinds of figure are deliberately **absent** rather than generated. **Tracker counts** move
several times per session, so generating them would rewrite this document during unrelated
lanes and dirty the base checkout that a landing refuses on — ask `br`, which is always right.
**Always-on character sizes** are already a generated block in `architecture.md`; a second copy
here would be the duplication this exercise exists to remove.

The verify row is the one that shows why a table beats a sentence: the count is **per-mode**.
This section previously stated one fixed number of checks for `verify --mode full` and a
different one for what `basicly.toml` declares. Both were wrong, and no single sentence could
have been right, because the count differs per mode.

Two hygiene regressions: **14 non-closed beads carry no phase label** (was 4) — including the
release epic `basicly-m3od`, both of its original blockers, and the model-tier cluster
`kjc5.58`–`.61` — confirming §10's prediction that the continuation path keeps minting them;
and the `vkh0` surface measurement (17 of 87 surfaces over 1,568 invocations) predates the
`vkh0.8` spool fix, so it under-represents engine lane traffic and the freeze still waits on a
supervised multi-lane run. The 2026-07-30 engine review confirmed all six filed adoptable
claims at HEAD and found four new defects, filed with evidence: `basicly-jr0l.49` (an
out-of-order ship approval closes a never-provisioned leaf with zero work — the destructive
mechanism behind the trap `jr0l.39` renames), `jr0l.50` (a crash between merge and gate
recording strands the lane as not-ready and burns rework), `jr0l.51` (a required gate accepts
any provider, so a dispatched agent's forged `br gate report` passes it — the narrowest
hardening of the engine-disposes constraint), and `jr0l.52` (a stall decision outliving a
green run parks the lane on a moot question).

**Shipped and dogfooded.** Catalog and projection with drift gates; the git and agent hook floor;
the single-track loop; worktree isolation; the concurrent supervisor with lanes and a serial merge
queue; autonomy grants L0–L3 with a spend ceiling; the decision queue and corpus-bounded decider;
release automation to the annotated tag; the sizing band and its governor. Sizes are in the
generated block above, not restated here. The 2026-07-26 dogfood landed three concurrent lanes
with no human editing
code, at 3.36M tokens against an earlier run's 25.25M — a 7.5× reduction from right-sizing alone.

**Not started.** Grep confirms zero modules mention a role registry or persona routing (the one
`src/` hit for "persona" is the word _impersonating_ in `decisions.py` prose), no lexical ranker
(`tf-idf`/stemmer/Porter), no `severity` field anywhere in `src/`, `rubrics/` or `schemas/`, and
no `evals/` directory. The tracker is still the external `br` binary, reached through seven
subprocess sites in `br.py`.

**Partially true, and the gap matters:**

| Claim | Measured reality |
| --- | --- |
| "Rework has a cap but no stall detector" | Correct. `policy.py` has no convergence check. `runner.StallWatchdog` exists but is dispatch-level (no file activity for _n_ seconds) — a different mechanism entirely. |
| "We lack a path-scoped guidance tier" | **Wrong.** The tier is fully built: `claude.yaml` declares a `scoped_rules` output at `.claude/rules/{fragment_id}.md`, the planner routes on `has_scope`/`exclude_scoped`, `rule_md.j2` renders it. **Two fragments now declare a scope** — `platform-hermetic-tests` on `tests/**` (`62cabc8`) and `external-review` on `docs/research/**` + `docs/design/**` (`basicly-a3ab.6`), each projected to `.claude/rules/{id}.md`; at `b02b527` none did. So the tier is in use, the remaining work is authoring, and the one check is still missing — see the asymmetry below, which that first real fragment measured. |
| "The always-on baseline is ~9000 chars with ~1000 chars of headroom" | Stale as written, but the headroom figure has since become accidentally right. Re-measured 2026-07-31 in **characters** (`wc -m`, matching the `len(content)` the cap compares — the figures first recorded here were `wc -c` bytes and overstated every surface): 10775 / 7894 / 8026 for `AGENTS.md` / `CLAUDE.md` / `copilot-instructions.md` (8434 / 7167 / 7299 at `31d441d`, before the `jr0l.33`, `a3ab.6` and `a3ab.7` catalog edits). The 9000 soft cap applies to Claude and Copilot only, leaving **1106** and **974** chars; `codex.yaml` sets 12000, leaving `AGENTS.md` **1225** — no longer the roomy surface, because it inlines every scoped fragment. So the two surfaces bind for different things: **Copilot is tightest in absolute headroom**, and so binds first for an always-on fragment, while **`AGENTS.md` binds for the path-scoped tier** — the next scoped fragment alone (~1500 chars) overflows it. **The cliff concern is refuted, not merely unproven** — at 1086–1303 words per file, `agzx.1` measured 98% recall (claude, 53 rules) and 93% (copilot, 54), so the ~500-word threshold the review cites does not bind here; read that result narrowly, per Phase 1 1a. |
| "Roughly thirty catalog entries" | 21 fragments (18 core + 3 local, `basicly-a3ab.6` added one) + projected skills + **3** subagents + 4 rubrics. The eval-coverage lift is **~50 routable guidance entries** (fragments + skills), not 30. The skill half needs a recount before it is quoted again: 32 `skill.yaml` sources project to 28 files at the Claude root and 27 at `.agents/`, and which filter accounts for the gap is untraced. |

**A correction to the entry count, because it changes what 2c owes.** The earlier "7 subagents"
was a miscount. There are **three** subagents — `code-reviewer`, `security-auditor`, `test-runner`
— plus **four reusable prompt blocks** under `.basicly/core/agents/blocks/` (`context-priming`,
`escalation-honesty`, `evidence-discipline`, `read-only-discipline`). A block is composed into an
agent rather than routed to, so it is not an eval-coverage entry in the sense 2c means and cannot
carry a routing case of its own. The 48 figure was `19 + 29` — subagents and rubrics were listed
but never added. Separately, 32 skill _sources_ project to 29 skills; the other three are gated by
technology selection, so a consumer's entry count is a function of its stack.

**The scoped tier costs Codex, and this is now measured rather than predicted.** Landing the first
scoped fragment moved exactly one baseline — in characters (`wc -m`, re-measured 2026-07-31; the
byte figures first recorded here overstated each surface by its multi-byte characters):

| File | `b02b527` | `13a4647` | Δ |
| --- | --- | --- | --- |
| `AGENTS.md` (Codex) | 6972 | 8434 | **+1462 (+21%)** |
| `.claude/CLAUDE.md` | 7167 | 7167 | 0 |
| `.github/copilot-instructions.md` | 7299 | 7299 | 0 |

One scoped fragment removed **nothing** from the Claude and Copilot baselines and **added** 1462
chars to Codex's, because Codex inlines scoped fragments (our scopes are globs and Codex has no
glob-based scoping at all; its nested `AGENTS.md` is directory-based _and_ is not loaded below the
cwd). The second scoped fragment, `external-review`, cost 1614 — so **~1500 chars is the working
per-fragment figure**. There is now real gate pressure: Codex's cap is 12000 against 10775 today,
so the **next** scoped fragment overflows it. The direction is fixed
by construction, not by this fragment: **for Codex, scoping is strictly a cost increase, always.**
Phase 4 §4 is worded as though Codex merely fails to benefit; it pays. Its exit criterion
("measurably smaller on two of three families") is therefore the only achievable form rather than a
conservative target, and any future claim that the baseline shrank must name the family. Note also
that this fragment landed **before** the empty-glob check of Phase 4 step 3 exists, so nothing
verifies its `paths:` still match anything.

**Tracker state.** Counts are not written here — this paragraph and §2 used to carry two
different sets and both were wrong (`basicly-uhiq.1`). Run `br list --status open | wc -l`, or
read `.beads/issues.jsonl` directly for a whole-tracker question, since `br list --json` caps
its result and drops closed rows. What is structural, and therefore worth stating: the phase
epics `basicly-u6jq` / `agzx` / `m4zv` / `imnu` / `a3ab` / `s2xf` (labelled `phase-0`…`phase-5`),
plus `basicly-vkh0` (`phase-6`, now `open` — it was `deferred`), `basicly-jr0l` (`phase-7`) and
`basicly-kjc5` (`phase-multi`). **The sequencing gap this section used to report is closed**: 21
gating `blocks` edges now exist among open work, up from two, and phase membership is a label
rather than a re-parenting. The residue is four open beads carrying **no** phase label —
`basicly-jr0l.18` / `.19` / `.20`, which the loop created as context-ceiling continuations of the
closed `kjc5.32` / `.50` / `.51` without inheriting their `phase-7` label, and `basicly-jr0l.24`.
An unlabelled bead is invisible to every phase-scoped query, and the continuation path will keep
producing them.

## 3. Sequencing principles

The order below is not by priority label. Six principles produce it, each stated with the failure
it prevents.

1. **Fix what makes an unattended run impossible, first.** Everything downstream is measured by
   running the factory many times. A defect that forces a human intervention per run multiplies
   across every later phase, and it corrupts the measurements themselves — a lane escalated for a
   flake is indistinguishable in the data from a lane escalated on merit.
2. **Buy the cheap measurements before building on the assumptions they test.** Three large work
   items rest on numbers we do not have. A measurement that _cancels_ a phase is the highest
   return available, and one of them (the recall test) costs a single session.
3. **Free deterministic gates before judged ones.** A check that runs in CI at zero token cost and
   catches a silent failure forever outranks a judged check that costs tokens per run. This is the
   same economics as the invariant in §1.2, applied to our own quality work.
4. **Do not grow the schema of a component you are about to replace.** Four open beads add
   evidence fields. Landing them as `[harness-*]` comment markers — the format we already own —
   rather than as tracker schema keeps Phase 6's migration surface flat. This is a real
   constraint, and it is cheap to honour only if noticed in advance.
5. **Absorb the design layer as it lands.** A design document that stays after its content ships
   becomes a competing account of how the system works. Each phase ends by folding its result into
   the architecture reference and archiving what it supersedes.
6. **Prefer the root cause when the proximate fix is a workaround, but ship the workaround first
   when the root cause is a phase away.** The clock defect that makes a gate flaky is an upstream
   `br` bug; the root fix is Phase 6. The workaround shipped (`jr0l.41`, `jr0l.42` — a bounded
   retry plus a forgiveness register keyed on the dependency's own message), which is what bought
   the room to sequence the root fix deliberately rather than in a panic. Stop charging rework for
   it now, and carry the defect forward as a **requirement** on the replacement (`vkh0.6`).

## 4. The phases

### 4.0 The release ladder — how phases become releases

Decided 2026-07-30. A **phase** is a dependency cluster; a **release** is a shippable cut
across the phases. The ladder below is the order the cuts happen in and the reason each sits
where it does. The structural insight that produced it: **the `u6jq.1` unattended-run proof
and the `vkh0.2` surface-freeze telemetry are the same supervised multi-lane run** — Phase 0's
exit criterion and Phase 6's entry gate converge on one event, so everything that makes that
run trustworthy and affordable lands immediately after v0.6.0, and the tracker follows it in
the same release.

| Release | Draws from | Content and reason |
| --- | --- | --- |
| before `v0.6.0` | housekeeping | `jr0l.32` + `jr0l.33` (owner asked for these first), the `a3ab.6`/`a3ab.7` retro pair where they do not disturb the release, and labelling the 14 unlabelled beads. Small catalog and tracker work; the review is done, so projection regeneration no longer corrupts anything. |
| `v0.6.0` | release epic `m3od` | The narrow critical cut as scoped on `basicly-m3od`: `jr0l.38`, `jr0l.39`, **`jr0l.49`** (added 2026-07-30 — the destructive mechanism behind the trap `jr0l.39` renames), the breaking-change audit, the changelog curation. Unblocks 625 commits of accumulated value. |
| before `v0.7.0` | Phase S (new) | **Make what exists true.** No new capability: wire, validate, delete. Inserted 2026-08-02 after the sizing step — the core of basicly — was found broken in three ways (`z2wi`, `3w44`), all the same mistake, and after an evidence pass found the pattern behind it. See **Phase S** below. It precedes `v0.7.0` because `u6jq.1`'s proof run cannot measure a factory whose sizing is wrong: the measurement would measure the defect. |
| `v0.7.0` | Phases 0, 6, parts of 1 and 7 | **Trustworthy factory, owned tracker.** Hardening batch (`jr0l.46`/`.47`/`.50`/`.51`/`.52`, `m4zv.12`/`.13`) → forecast chain (`jr0l.21`/`.22`/`.34`/`.35` and `.35`'s blocker `2rn9`, model provenance `kjc5.58` → `kjc5.61` → `kjc5.59`, with `kjc5.60` in parallel once `.58` lands) → the proof run (`u6jq.1`, under the delegated 10M-token L1 ceiling, also gated on `jr0l.16`) → `vkh0.2` freeze → event log → import → shadow (vs the live tracker) → dual-write → flip. The flip removes `br` from the consumer floor — the single biggest 1.0.0 blocker. |
| `v0.8.0` | Phases 1, 2, 3 | **Evidence, gates, docs.** `7bur` cost per landed package, `agzx.2` localisation, the Phase 2 deterministic gates (`m4zv.2`–`.6`) built against the owned tracker (the `m4zv.14` write-lock flake dissolves with the flip), the D4 amendment, `kjc5.13` absorption, the tutorial layer (`imnu.2`), the delivered-install capability tier (`imnu.3`, which has no dependents and stays here — the model-tier bead `kjc5.58` it used to be paired with moved to `v0.7.0`), the ceremony threshold (`imnu.5`), the fake-agent-CLI e2e test (`jr0l.43`, decided 2026-07-30), and **`3ifz` parameter tuning from recorded outcomes** (owner, 2026-08-01: the per-lane budget is changeable, so it should be learned rather than set — and so should `concurrency`, the sizing band, `max_rework` and the rest; it sits here because it is Phase 1's "buy the numbers" work applied to our own configuration, and it needs `vz78`'s forecast/actual pairs to exist first). Measurements land before the phase they gate. |
| `v0.9.0` | Phases 5, 4 | **The judgment layer and always-on relief.** The roster (`s2xf`), gated on `7bur`'s numbers by construction (§6); Phase 4 authoring and the empty-glob check; `jr0l.44`/`.45`. |
| `v1.0.0` | Phase 8 | **Stabilize and declare.** The surface audit and semver freeze, the deprecation policy, the fresh-consumer acceptance test (Phase 8). 1.0 is a promise, so the last phase proves the promise instead of adding capability. |

Phase 7's small fillers interleave throughout. Rough sizing: v0.7 ≈ 8–12 sessions, v0.8 ≈ 6–9,
v0.9 ≈ 5–8, v1.0 ≈ 3–5 — sizing signals, not commitments (§0).

**The ladder's invariant**: a release row must name every open bead its own entries are blocked
on, so a reader who starts at the top of a row is never sent straight to a blocked bead. Check it
against the tracker's edges rather than by eye — `kjc5.58`'s placement broke it once
(`basicly-sy8c`), and the same check found `2rn9` and `jr0l.16` unlisted. A parent epic is exempt:
it closes when its children do, so `7bur`'s edge to the `u6jq` epic is satisfied by `u6jq.1`
sitting in `v0.7.0`.

### Phase S — Make what exists true

**Priority: P0. Depends on: nothing. Size: 3–5 sessions. Added 2026-08-02.**

Every defect found on 2026-08-02 is one pattern: **an instrument is built and never connected.**
`permissions-check` shipped as a command wired to no gate (`tcmy.23`). The only architectural
contract in the repo forbade modules that cannot exist, so it reported `1 kept, 0 broken`
forever (`tcmy.2`). `vulture>=2.16` is declared at `pyproject.toml:37` and called from nowhere.
`.scripts/recall_eval.py` was built, run once, and wired to nothing. 12 of 35 telemetry fields
have never once been non-null. There are **0 gate failures in 277 recorded gate results**, so
the triggering branch of the bounded-rework subsystem has never executed.

The cause is that nothing is ever removed: a **9.3% deletion rate and 5 `refactor:` commits in
1742**. Churn is 1.2×, so designs did not thrash — each instrument that did not work simply
stayed, and the next was built beside it. 1243 of 1742 commits are tracker bookkeeping; ~298
touch `src/` or `tests/`. The surface is not under-delivered, it is over-delivered against
roughly 300 product commits, and almost none of it is proven.

**This phase adds nothing.** It is the precondition for believing any later measurement.

| Work | Bead | Note |
| --- | --- | --- |
| Build factor sized a working set from whole-lane spend | `basicly-z2wi` | **Closed.** The task factor calibrated to 216.65 against a seed of 3.0, capping dispatchable scope at ~295 tokens and refusing every task-typed child. Ten successful dispatches crossed the sample threshold, so _using_ the engine is what disabled it. Removed the calibration; −161 lines. |
| The ceiling refused sizes that had already succeeded | `basicly-3w44` (a) | **Closed.** 64000 against a largest-completed estimate of 105318. Now 112000, derived from outcomes and gated by a test that fails when evidence outruns the constant. |
| Correct the ceiling's recorded rationale and bind the gate both ways | `basicly-ipx2` | **Closed.** The comment said "zero lanes have failed at any size". **False**: 4 dispatches failed with `returncode 143`, excluded from the analysis because failed lanes record no `scope_tokens`. Survivorship bias. The gate now binds both directions and names the value that reconciles it. The separating-boundary rationale that replaced it was then narrowed by `basicly-fcls`: `kjc5.42` and `kjc5.44` declare identical class and identical scope, and one completed while the other was SIGTERMed — so no function of (class, scope) separates that pair, and no ceiling can be credited with refusing it. |
| `scope_read_cost` measures whole files | `basicly-fcls` | **Closed — the chunking unlock.** Re-measured over 185 (lane, file) pairs from 24 headless transcripts: 78% of `Read` calls are ranged, a file under ~4000 tokens is read whole and above that the material taken out is flat at ~1500 tokens however large the file. So the model is a per-file cap, `SCOPE_FILE_READ_CAP = 4000`. `cli.py` alone went from 139448 tokens **refused** to 14780 within band; `supervise.py`+`merge.py` from 145691 **refused** to 26780. The ceiling came down with it, 112000 → 56000 → **72000**: the lane derived 56000 from `tcmy.31` while still running, then its own finishing record at 72000 contradicted it and its own gate caught it — `z2wi`'s shape, where using the engine is what breaks it. The derivation is a ratchet on a lane's self-declared scope, tracked as `basicly-qorx`. |
| Record `context_tokens` on `RunRecord` | `basicly-fcls` | **Closed.** One additive field, gates nothing, written by `record_dispatch` from `runner.context_occupancy`. It matters more than the cap: a lane's real context occupancy correlates with its declared scope at **R² = 0.095** over those 24 lanes (0.863 for turn count), and six lanes declaring **no scope at all** still occupied 106k–209k tokens. The term the formula is missing is a large ambient one, and nothing could fit it because nothing measured it. Build the field before the formula — a formula fitted to the wrong quantity is exactly how `z2wi` happened. |
| Wired-or-deleted gate, and the deletions it forces | — | Nothing merges without a reference outside its own module and outside `tests/`. Fails today on 11 commands, 19 config keys, 12 record fields, 16 never-varied parameters. Wire `vulture` here. |
| Exercised-or-unproven gate | — | No release tag while a shipped capability has zero ledger executions. Would have caught `permissions-check`, the import contract, `vulture` and `recall_eval` years earlier. |
| The subcommand dispatch guard, derived from the parser | — | `tcmy.4` fixed **1 of 7** sites. The other six are verbatim `return handler(args) if handler else 0` at `cli.py:2191, 2240, 2273, 2791, 3461, 3564`, and a seventh (`cmd_usage`, `cli.py:1758`) carries it in a different shape. Proven behaviourally: an injected orphan sub-parser returns exit 0 with no output at all seven, and exit 2 at the top level. Latent, not live — every sub-parser is `required=True` — but the existing test asserts `len(actions) == 1` against the root parser and so can never recurse. |
| The multi-lane blockers | `jr0l.64`, `jr0l.65`, `vkh0.10` | A 182-token failed dispatch fail-closed a 60M grant with 43.4M unspent; an _answered_ needs-input still blocks every ship until its bead closes; the tracker corrupts its WAL under the engine's own five-lane fan-out. |

**Exit criteria.** Every gate in the repo has been shown to fail on a real defect, not merely to
pass. No shipped capability has zero recorded executions. A five-child markdown-only epic
decomposes with no child refused for a reason the files do not justify.

**Why not later.** `v0.7.0` ends in `u6jq.1`, a proof run whose whole value is that it measures.
A measurement taken through a mis-calibrated sizer measures the sizer.

**What is explicitly _not_ here.** The instruction catalog. It was measured — claude recalls 98%
of 53 always-on rules and copilot 93% of 54, against 17%/6% no-guidance controls — so the
instructions are not the problem and rewriting them would trade a measured result for an
unmeasured one. Every item above is a script or a gate, which is the correct disposal for a
deterministic fact.

### Phase 0 — Make an unattended run possible, and stop a live leak

**Priority: P0. Depends on: nothing. Size: 2–3 sessions.**

The 2026-07-26 dogfood met its acceptance criterion but needed one human intervention, and the
cause is two defects that interact. Plus one committed data leak that should not wait behind
anything.

| Work | Bead | Note |
| --- | --- | --- |
| Stop committing machine-specific absolute paths in the tracker export | `basicly-vkh0.5` | **Do this first.** `source_repo_path` on 328 of 332 records publishes two users' home directory layouts to every consumer clone. It violates the repo's own hard constraint and it is already published. |
| An answered `retry` escalation must be executable | `basicly-4tjt` | `policy.record_rework` only increments; nothing clears or decrements. So the operator answers "retry", and the lane immediately re-escalates. The only current levers are loosening the cap repo-wide or landing by hand — the second is exactly what the dogfood's criterion forbids. |
| A flaky gate must not spend a lane's rework budget | `basicly-55yh` | A lane whose diff was three docs files was charged rework for an unrelated test failing on the `br` clock defect. An infrastructure failure and a merit failure are currently scored identically. |
| Pre-warm hook environments at worktree provisioning | `basicly-jr0l.14` | The first commit in a fresh worktree stalls **past ten minutes** while pre-commit builds environments. Every lane pays it, inside the dispatch timeout, and it is indistinguishable from a wedged lane to the `StallWatchdog` (HEAD and the dirty tree both hold still). Provisioning already runs `uv sync` and `npm install`; warming hooks belongs there. |
| Surface the supervisor's own progress when stdout is a pipe | `basicly-8veb` | Python block-buffers a non-TTY stdout, so a long supervised run shows nothing but subprocess noise. Needed to observe the runs every later phase depends on. |
| Ship advance must surface a skipped tracker-state commit | `basicly-f7li` | Silent skip; an operator learns about it later as unexplained dirt. |
| Prune 4 tombstone records; give `basicly-jr0l.9` an AC or close it | — | See Appendix B. |

**Exit criteria.** A supervised multi-lane run on real work completes with **zero human
interventions attributable to a harness defect**, and the committed tracker export contains no
absolute path. Re-run the dogfood shape to prove it rather than reasoning about it.

**Why not later.** Phase 1 measures cost per landed package. If a lane can be charged rework for a
flake and cannot be released once escalated, the measurement measures the defects.

**Status 2026-07-30, and where the proof now sits.** All six work items above are closed; the
phase's residue is the proof itself (`u6jq.1`), blocked on the forecast chain after the first
attempt failed its criterion at $34.16 for 46.0M tokens (§5). Owner decision 2026-07-30: **the
proof runs in `v0.7.0`**, after the forecast chain and the hardening batch land, under the
already-delegated 10M-token L1 ceiling — and it doubles as the telemetry run `vkh0.2`'s
surface freeze is gated on, because the `vkh0.8` spool fix means only a run from now on
records engine lane traffic at all (§2).

### Phase 1 — Buy the numbers that decide the expensive phases

**Priority: P0. Depends on: Phase 0 (for 1b/1c). Size: 3–4 sessions.**

Three assumptions currently carry large downstream work. Each has a cheap test, and at least one
could cancel a whole phase.

**1a. The always-on recall test — done, `basicly-agzx.1`, 2026-07-26.** _Was independent of every
other item, which is why it ran first._ A fresh session per agent family is asked to summarise the
rules in the always-on file; anything it cannot recall is not doing work. The no-guidance control is
the same session with the baseline absent. The harness is committed and re-runnable:
`.scripts/recall_eval.py` + `.scripts/recall_rules.toml`.

**Result: claude recalls 98% of its 53 baseline rules, copilot 93% of 54**, against 17% / 6%
no-guidance controls — +81 / +87 percentage points. **Codex is unmeasured**, its CLI being absent,
and it is the arm that matters most: `AGENTS.md` is the largest baseline and the only one that
_grows_ when a fragment is scoped (§2).

**Read it narrowly.** This is `mechanism confirmed`, never `outcome improved` — recall under a direct
cue is an upper bound, and it says nothing about whether a rule _binds_ while the agent works. Per
`catalog-efficacy-design` §4.1 it may not be cited as evidence of quality. It kills exactly one
claim: that the baseline is past the cliff, so the tokens are wasted.

**Two consequences, both applied below.** Phase 4 is routine tidying rather than urgent surgery. And
the cap policy is now **asymmetric**: lowering the cap is housekeeping, raising it still has no
evidence behind it.

**1b. Cost per landed package.** Bead `basicly-7bur`. The hub of the whole plan: it gates
`basicly-4t9z` by an existing edge, and the design documents defer four further decisions to it —
the roster's tier table, Tier-3 eval scale, the localisation question in 1c, and prefix-stable
dispatch bundles. Two things must be true for its numbers to mean anything:

- **`basicly-kjc5.29` lands first** — refuse to dispatch a runner whose model is unresolved. No
  adapter pins a model today, so "which tier" is currently whatever the CLI defaulted to that day.
  Measuring tier economics against an unpinned mapping produces a number about nothing.
- Dispatches are **labelled by specification completeness**, per the review's reconciliation of
  the tier argument. The predicate for a cheap tier being safe is not the work's category but
  whether the brief already contains the code and the tests.

**1c. Deterministic localisation.** _New work, no bead._ Does an AST-derived localisation artifact
(tree-sitter, no model, no tokens) measurably reduce an implementer's pre-first-edit token share?
This **must run before Phase 5**, because it changes what the decomposer's scope declarations have
to carry: if the engine can derive reachable surface, the decomposer declares intent and
boundaries instead of enumerating files. Note the framing — the persona was cut for being a
_model_ whose omissions are undetectable; a parser's coverage is a checkable property.

**Exit criteria.** Three numbers written into the design documents that own them, and the roster's
tier table plus the localisation question decided by data rather than argument. **One of the three is
in** (1a). 1b and 1c are still owed, and 1b cannot start until `kjc5.29` pins a model.

**Risk.** 1b is a genuine build (task set, hidden objective checks, arm isolation), not a
measurement script — it is the largest item in this phase and the one most likely to slip. Its
hard constraint carries over unchanged: **the eval must not cost more than the thing it measures**
— cheap models on the arms, the strong model only for judging.

### Phase 2 — The free deterministic gates

**Priority: P1. Depends on: 2a → 2b → 2c chain; 2d/2e independent. Size: 3–4 sessions.**

The highest value-per-cost work in the plan. All of it runs in CI at zero token cost, and each
piece catches a failure that is currently silent.

**2a. Declare the invocation axis** in catalog sources: an entry is **model-invoked** (keeps a
description, agent-reachable, pays permanent context load) or **user-invoked** (no description,
zero context load, reachable only by a human). This is a prerequisite — "does it route correctly"
is not well-posed until an entry knows whether anything can route to it — _and_ an immediate win,
since every entry correctly reclassified as user-invoked stops paying context load forever.

**2b. Tier-2 routing evals.** Stemmed TF-IDF over descriptions, pure Python, no new dependency.
Three assertions: positive prompts rank their owner in the top-k; negative prompts declare an
`owner` and the assertion is that the owner **outranks** this entry (a bare "must not rank first"
passes vacuously when the prompt matches nothing); and no two descriptions exceed a pairwise
similarity ceiling — error at 75%, warn at 50%. The CI metric is **rank-1 rate**, with the floor
set below a measured baseline, raised as routing improves, and **never lowered to make a
regression pass** — lowering it is deleting the test while looking like maintenance. Refuse
embeddings: they would make this semantic and therefore non-deterministic, network-dependent and
unownable, and semantics are Tier 3's job.

**2c. An eval case file per entry, enforced as a Tier-1 failure.** Colocated with the catalog
source so a reviewer sees the entry and its evidence in one diff. **48 entries** — stage this
rather than attempting it in one pass, and scaffold it from `catalog new`. Accept the consequence
deliberately: this raises the cost of adding a catalog entry, which is the intended brake on
accretion.

**2d. Severity as a required field** on judged output, rejected as a schema violation rather than
complained about — `BLOCKER` / `IMPORTANT` / `MINOR`. Plus the **no-pre-judging lint**: because
dispatch prompts are assembled by code, refuse to emit a reviewer bundle containing a
finding-suppressing directive. A rule an observer can mechanically check beats ten rules of good
intent.

**2e. Rework convergence detection.** Compare the **open-finding set** between consecutive
iterations, not the count (a round that fixes one and introduces one is not progress). One stalled
round warns on the bead; two consecutive rounds escalate immediately without consuming the
remaining cap; a diverging round escalates on first occurrence. Also classify every existing gate
into the four types (pre-flight / revision / escalation / abort) and enforce the rule that **a
pre-flight gate writes nothing** — our two worst recorded incidents were both checks that recorded
state where they should have blocked entry.

**Prerequisite for 2d/2e:** the D4 amendment must land in the authoritative document first (see
Phase 3), because validate is currently specified as a required gate whose judged half cannot fail
it. Build against the amended shape, not the current one.

**Two `phase-2` beads are pulled forward into `v0.7.0`'s hardening batch** (§4.0): `m4zv.12`
(refuse a catalog path that escapes the repo root — the 2026-07-30 review confirmed the write
happens _before_ the check that would catch it, `cli.py:304` vs `:307`) and `m4zv.13`
(evidence-artifact presence). The proof run should not be attempted while a hostile overlay
can write outside the repo and a lane can claim done with nothing to point at. Their phase
label is unchanged; the release is just earlier.

**Exit criteria.** CI fails on: a description that cannot route to its own realistic prompt, a
colliding description pair, a catalog entry with no eval case, a judged verdict with no severity, a
reviewer bundle containing a suppression directive, and a rework loop that has stopped converging.

### Phase 3 — Absorb the design layer and pay the documentation debt

**Priority: P1. Partly a prerequisite for Phase 2. Size: 2–3 sessions.**

| Work | Bead | Note |
| --- | --- | --- |
| Land the D4 amendment | — (new, small) | Validate is a **composite**: a deterministic pre-flight component that _can_ fail the lane, plus a judged escalation component that enqueues a decision. This keeps "no persona passes a required gate" intact while giving the required gate real teeth. **Blocks Phase 2d/2e.** Do it early and small. |
| Absorb the factory design into architecture; archive the source | `basicly-kjc5.13` | Ready now — its three blockers are closed. |
| Tutorial and how-to layer (Diátaxis) | — (new) | `docs/` has no "your first loop" walkthrough and no task-focused how-to guides. For a distribution meant to be installed by other repos this is an adoption blocker independent of any capability in this plan. |
| Declare a capability tier per agent family | — (new) | instruction-tier / skill-tier / plugin-tier. Our central claim is _enforcement_, which is plugin-tier; on an instruction-tier host the harness degrades to advice and we currently say so nowhere. `basicly install` should report the tier it installed and what is unavailable at it. |

**One `phase-3` bead is pulled forward into `v0.7.0`'s forecast chain** (§4.0): `kjc5.58` (declare
a model tier on a catalog entry instead of a model id). It is the root the whole model-provenance
chain is blocked on — it establishes the tier vocabulary that `kjc5.61`'s generator resolves to a
per-family model id, so `kjc5.61` has nothing to resolve without it, and `kjc5.61` blocks
`kjc5.59` → `jr0l.21` → `jr0l.22`. The edges are correct; it was the ladder's placement that was
wrong. Its phase label is unchanged; the release is just earlier. Mind the name collision with the
table row above: `imnu.3` is the **install** capability tier, `kjc5.58` is the **model** tier.

**Exit criteria.** `docs/design/factory-design.md` is archived, the architecture reference carries
its content, a new consumer can follow a tutorial from install to first shipped bead, and
`basicly install` states the guarantee it actually delivered.

### Phase 4 — Relieve the always-on layer

**Priority: P2 — settled by 1a, which found no cliff. Depends on: 2a (adjacent). Size: 2–3 sessions.**

Cheaper than the design documents assume, because the mechanism is already built (§2), and less
urgent than they assume, because 1a measured the baseline being recalled rather than lost.

1. **Audit every baseline line against three questions**: _is this really a hook?_ · _can I write a
   glob for it?_ · _does it change behaviour versus the default at all?_ Expect all three to fire.
   The third is the **no-op** test and it is the single most common defect in an always-on layer.
2. **Declare scopes on the fragments that earn them** — subprocess discipline, test isolation,
   catalog authoring. This is authoring work, not engine work.
3. **Add the one missing check**: a scope whose globs match nothing. This is the only failure the
   existing gates cannot see — the fragment is well-formed, it projects, `check` is green, and the
   rule never loads. Warn at projection time, error only where the technology is selected, so a
   docs-only consumer is not punished for having no Python.
4. **Project the constraint content as an explicit three-tier block** — always do / ask first /
   never do. A retrieval problem wants a structural fix, not more prose.
5. **Move `## Commands` early** and prefer one real example over three paragraphs where a
   convention has a canonical form.

**Watch the asymmetry**, now measured on the first real scoped fragment (§2): scoping removes a
fragment from the Claude and Copilot baselines and **adds** it to Codex's, which inlines scoped
fragments because our scopes are globs and nested `AGENTS.md` is directory-based. It is not that
Codex fails to benefit — it pays, ~1500 chars per fragment (1462 and 1614 measured). And with 1225
chars of headroom left on `AGENTS.md`, the next one overflows the cap. So a claim of "we cut the baseline"
must name the family, the exit criterion below is the strongest form available rather than a
hedge, and scoping is a deliberate _removal_ from the github.com Copilot surface — a guarantee
change per fragment, not a refactor.

**Exit criteria.** Baseline measurably smaller on two of three families, 1a's recall test re-run
shows recall **not degraded** — at 98% / 93% there is no headroom left to improve, so demanding
improvement would be unsatisfiable — and no declared scope matches nothing.

### Phase 5 — The judgment layer

**Priority: P2. Depends on: 1b, 1c, 2d, Phase 3's D4 amendment. Size: 4–6 sessions.**

Today the factory dispatches one generic prompt shape for every lane. This replaces that with
named roles, each carrying its own instructions, tool policy, model tier and output contract.
Design is agreed (`basicly-eqp6`, closed); nothing is built.

**Engine.** A role registry (role id → prompt source, tier, tool policy, output schema); a
`[runner.roles]` config section with defaults so a consumer with no overlay gets a working roster;
role-aware dispatch replacing the generic prompt; tool-policy overlays at invocation, generalising
the existing decider confinement to every read-only role; per-role attribution into the tracker so
per-role telemetry and cost-per-landed-package fall out for free.

**Prompts, as catalog sources** (not agent-native subagent files — the factory is agent-agnostic).
Each judged role carries an explicit adversarial stance and a **role-specific list of how that
role goes soft**, including reviewer conflict-avoidance — downgrading a blocker to a warning to
avoid disagreeing with the producer — named as a predicted failure. **These lists must be derived
from the verdict, rework and adjudication history the loop already records, not invented**; a
generic rigour instruction is a no-op and costs tokens for nothing.

**Contracts.** Lens output reported per lens, never merged into one ranked list, because a change
can pass one axis and fail another and merging lets one mask the other. The implementer hands over
a **report file** and returns only status, commits, a one-line test summary and concerns — pasted
history stays resident in the dispatcher's context and is re-read every later turn. Four statuses,
each with a different correct response: `DONE` / `DONE_WITH_CONCERNS` / `NEEDS_CONTEXT` /
`BLOCKED`, with the rule that gives it teeth — never force the same model to retry unchanged.

**Two pieces that do not fall out of the registry shape and need their own work:** the R4
disposition path (a judged NO enqueues a decision carrying the failing criterion and its evidence,
and the lane _holds_ rather than landing or bouncing), and a decision class **no grant level
auto-disposes** — an exception to the L0–L3 ladder rather than a rung in it, so an autonomy grant
can never hand the catalog to the decider.

**Also here:** capability escalation on late rework rounds (resume the same agent early, fresh
dispatch one tier up late) — which produces a readable signal: if late-round bumps routinely
succeed, the initial tier was wrong. And `basicly-4t9z` (eval cases per role prompt) unblocks once
1b lands.

**Exit criteria.** Every judgment step in the loop is routed to a named role; no dispatch occurs
with an unresolved tier; a judged NO holds its lane and produces a disposable decision; and the
roster's cost claims are visible in 1b's instrument rather than asserted.

### Phase 6 — Own the work graph

**Priority: P0, highest effort. Targeted as the release after v0.6.0. Depends on: `vkh0.2`;
schema stability from Phase 5. Size: 5–8 sessions.**

**Resequenced by owner decision 2026-07-29, and the rationale is cost, not strategy.** `br` is
flaky and bills us for its own errors: twelve distinct defects listed on `basicly-vkh0` have
already been paid for in sessions spent diagnosing them, and the clock defect alone consumed two
tracks of workaround (§3.6). A dependency that costs time and money every session is not a
dependency to remove eventually. So this phase is no longer a parallel strategic track — it is the
next release after `v0.6.0` (`basicly-m3od`) unless something blocks it.

The tracker _is_ the harness's state, so every guarantee in §1 is downstream of it, and it is
currently an unowned external binary in the critical path whose licence carries a rider
restricting a class of users.

**The 2026-07-30 consumer review hardened the rationale from cost to necessity.** The loop
hard-requires the external `br` binary (`br.py:179-190` refuses without it), `basicly install`
does not install it, and the pin is fragile in both directions — 0.2.19 rejects the harness's
`gate report` call, and a 0.2.19 database has no supported downgrade path. A 1.0.0 declared
before this phase lands would freeze a contract the roadmap already voids, so Phase 6 is the
load-bearing 1.0.0 gate, not an optimisation (§1.1).

**Hard constraint: a clean-room boundary applies.** The replacement must not be derived from
`beads_rust` source. Sanctioned inputs are our own ledger's observable data, `br`'s documented CLI
contract, and the genuine-MIT upstream original. **The boundary was signed off on `basicly-qk6y`
(closed), so this no longer gates the build** — wire `qk6y` as the blocker of the event-log beads
at this phase's decomposition, as a record of the sign-off rather than a decision still pending.

**Order:**

1. ~~**`basicly-vkh0.1`**~~ — **DONE.** `br`/`bv` usage is recorded at subcommand and flag level
   into a _committed_ ledger, distinguishing engine call sites from interactive human ones. This is
   what removed the deferral: the measurement window has been accumulating since it landed.
2. **`basicly-vkh0.2`** — report the surface actually exercised, then **freeze the surface list**.
   Surfaces nobody uses simply do not exist in the replacement. **Unblocked and open at P1** now
   that `vkh0.1` has landed; it is the one hard gate left, because `vkh0`'s acceptance criterion
   asks for a frozen surface and no replacement code may be written before it. _Do not design the
   schema from memory of our own usage._
3. **Design and build the event log.** Append-only is the truth; the record snapshot and any index
   are derived and disposable. A record's state is a fold over its events, so history lives in the
   data and survives a squash or a shallow clone. Sequence numbers assigned by the single writer
   give total order; **a wall-clock timestamp is evidence and nothing branches on it** — which is
   exactly the class of defect that produced Phase 0's flake.
4. **Provenance on every edge**: `EXTRACTED` (asserted by a human or mechanically derived from a
   repo fact) may gate a landing; `INFERRED` (proposed by an agent, or deduced from a bounce) is
   usable but visible as a proposal; `AMBIGUOUS` routes a decision and never silently gates
   anything. The label belongs to the _event_ that created the edge, so a human confirming an
   inferred edge is a new promoting event rather than a mutation.
5. **An explicit collision budget** for ids, sized from the birthday paradox against a declared
   maximum probability, with adaptive length — safe because existing ids never change.
6. **`fsck` and `rebuild` commands.** Without them, "the log is the truth" is a claim nobody can
   check.
7. **Import, then shadow, then dual-write, then flip.** Three known risks: the JSONL format is
   second-class upstream and will drift; `import` is upsert-only so **a snapshot cannot express
   deletion** and tombstones are a first-class concern; and therefore the shadow-mode differential
   must compare against the **live tracker**, never against a re-import of its own export — two
   derivatives of one lossy snapshot agree with each other and prove nothing.

**Carry Phase 0's defects forward as requirements** (`basicly-vkh0.6`). The clock error that makes
a gate flaky, the dependency-field spelling inconsistency, the unconfigurable lint templates, the
single-line acceptance-criteria field, the ids whose internal hyphens break our own commit gate,
and the absolute-path leak are all _requirements input_ for the replacement. Each should become a
committed regression test, so the replacement cannot reintroduce a defect we already paid for.
**Not surface-dependent, so it does not wait on the `vkh0.2` freeze** — it can land early alongside
`vkh0.3`.

**Also in this phase:** `basicly-vkh0.3` (record the scheduler score and rank behind each
dispatch) is cheap and independently useful now — it makes a pass's dispatch order reconstructible
without replacing anything, so it can land early. When we own the ranking it must become **pure**
and drop `created_at`: age-based ordering makes dispatch order clock-dependent for an unchanged
graph, which the determinism rule forbids.

### Phase 7 — Factory hardening, interleaved

**Priority: P2–P3. Mostly independent. Size: ongoing.**

The `basicly-jr0l` epic's open children are largely small and self-contained, which makes them
good capacity fillers between the phases above rather than a phase of their own. Five groups, with
one placement warning.

- **Engine hardening from the 2026-07-30 review — not fillers; pulled forward.**
  `basicly-jr0l.46`/`.47` (previously filed) and `jr0l.50`/`.51`/`.52` (new) land in
  `v0.7.0`'s hardening batch (§4.0), because each is a defect an unattended run would hit or a
  hole that lets one lie; `jr0l.49` is a `v0.6.0` blocker (§7.2). `jr0l.43` is decided
  (2026-07-30): build the fake-agent-CLI e2e test through the real subprocess seam — the
  deciding evidence is `jr0l.38`, the codex adapter having never completed a dispatch, exactly
  the defect class the test catches; it lands in `v0.8.0`. `jr0l.48` (the `derive_phase`
  review) is narrowed by the same review: the ladder is not over-long; the scope is one
  redundant conjunct and the overloaded ship predicate whose destructive half is `jr0l.49`.
- **D9 determinism.** `basicly-kjc5.32` (attribute a missed coupling independently of landing
  order) is a live violation of a decision the architecture claims is enforced — treat it as P2 and
  land it before Phase 5 leans on coupling records. `basicly-kjc5.52` (fingerprint the environment
  behind a gate result).
- **D10 zero-token operation.** `basicly-jr0l.9` — all three children are closed; it needs an AC
  or closing. `basicly-jr0l.13` (commit envelope rules duplicated from the hook),
  `basicly-jr0l.8` (pin a runner and autonomy for one session).
- **D11 evidence.** `basicly-kjc5.50` (forecast and actual cost on the bead), `basicly-kjc5.51`
  (human wait time, which dominates a factory's wall clock and nothing currently records),
  `basicly-kjc5.47` (derived facets), `basicly-kjc5.48` (predict wall-clock from duration
  telemetry). **Placement warning per §3.4: land these as `[harness-*]` comment markers, not as
  tracker schema fields.** Markers are a format we own, so they migrate with us in Phase 6; new
  bead fields would grow the migration surface for no gain.
- **Catalog and prose.** `basicly-kjc5.45` (replace metaphor-domain terms with engineering
  equivalents) and `basicly-kjc5.46` (measure whether the precise wording changes behaviour) —
  **`.46` should run through Phase 2's micro-test harness rather than inventing its own**.
  `basicly-5xcj`, `basicly-kvx5`, `basicly-sco6`, `basicly-g7os`, `basicly-kjc5.37`,
  `basicly-z25w`, `basicly-l7zo` are catalog-content beads that can land any time.

### Phase 8 — Stabilize and declare 1.0.0

**Priority: P1 once v0.9.0 ships. Depends on: everything above. Size: 3–5 sessions. New
2026-07-30; no epic yet — file one at its decomposition.**

1.0 is a promise (§1.1), so this phase proves it rather than adding capability. Nothing here
is speculative; every item traces to a defect the 2026-07-30 consumer review found.

| Work | Why |
| --- | --- |
| Surface audit and semver freeze | Enumerate and freeze the five public surfaces: the CLI commands and flags, `basicly.toml` plus the `basicly.local.toml` overlay contract, the four catalog source schemas, the generated-file/manifest contract, and the owned ledger format. Every one broke within the last two minors, so the audit means reading each surface against its consumers; the freeze is a written compatibility policy with a deprecation path. |
| Breaking-marker discipline as a gate | The v0.6.0 audit exists because zero of 535 commits carried a `!` marker. After the freeze, a commit that changes a frozen surface without the marker must fail a deterministic check — or 2.0's audit repeats 0.6's. |
| Forward-version CI job | The floor claim is "3.14+" and CI tests exactly 3.14. Add the next Python to the matrix so the claim is tested, not asserted. The floor itself stays (owner-confirmed 2026-07-30; E1 wontfix stands) — uv provisioning dissolves the adoption cost. |
| Error-path polish | The two soft spots at the consumer trust boundary: `basicly check` in a never-installed repo points at `build` instead of `install`, and the CLI's blanket exception handler leaves no `--debug` escape hatch for diagnosing a genuine engine bug. |
| The acceptance test | A fresh consumer repo — git plus a uv-provisioned interpreter, no `br` — installs basicly, runs every gate, and drives one unit of work through the loop to a landed commit. Exercise it as it will really be used, and publish nothing that was not exercised (`jr0l.33`'s rule, applied to our own release notes). |
| Absorb and archive | Every design document folded into the architecture reference and archived (§3.5); the roadmap's rendered copies agree with architecture §15; §2 of this plan refreshed one final time. |

**Exit criteria.** The acceptance test passes on a machine that has never seen this repo; the
compatibility policy is published; a surface-breaking commit without a marker fails CI; and
v1.0.0 is tagged by `basicly release` with both pushes explicitly owner-approved.

## 5. Dependency graph

Gating edges only. `→` means "must precede".

```text
Phase 0 ─┬─ vkh0.5 (leak)      ─────────────────────────────────────┐
         ├─ 4tjt + 55yh + jr0l.14 + 8veb ──→ unattended run ──┐    │
         └─ f7li                                              │    │
                                                             │    │
Phase 1 ─┬─ 1a recall test  (DONE 2026-07-26, agzx.1) ────────┼────┤
         ├─ kjc5.29 (pin the model) ──→ 7bur (1b) ────────────┤    │
         └─ 7bur ──→ 1c localisation ──→ Dana's contract      │    │
                 └──→ 4t9z (existing edge)                    │    │
                                                             ▼    ▼
Phase 3 ─── D4 amendment ──→ Phase 2d/2e                  (all later phases
         └─ kjc5.13 absorb ──→ archive factory-design       need both)

Phase 2 ─── 2a invocation axis ──→ 2b Tier-2 routing ──→ 2c eval case files
         └─ 2d severity + no-pre-judging lint ──┐
         └─ 2e stall detector + gate taxonomy ──┴──→ Phase 5

Phase 4 ─── 1a (done) ──→ baseline audit ──→ declare scopes ──→ empty-glob check

Phase 5 ─── 1b + 1c + 2d + D4 ──→ role registry ──→ role-aware dispatch
         └─ kjc5.32 (coupling attribution) should precede

Phase 6 ─── vkh0.1 (DONE) ──→ vkh0.2 ──→ freeze surface ──→ event log ──→ import
         └─ shadow (vs LIVE tracker) ──→ dual-write ──→ flip
         └─ vkh0.3 + vkh0.6 land early (not surface-dependent)
```

**Critical path to a credible product claim**: `kjc5.29 → jr0l.21 → jr0l.22 → u6jq.1 (Phase 0's
exit) → 7bur → Phase 2b → Phase 5`. That is the chain that turns "our harness is better" from an
assertion into an instrumented, gated, role-routed system.

**Phase 6 is a second, parallel critical path — to a harness that does not bill us for a
dependency's defects**: `vkh0.2 → freeze surface → event log → import → shadow → dual-write →
flip`. It does not gate the product claim above, and the product claim does not gate it; they
compete for capacity, and the owner's 2026-07-29 decision resolves that competition in Phase 6's
favour for the release after `v0.6.0` (§4, Phase 6). The earlier framing — that Phase 6 was
strategic work to run in the background once its telemetry landed — is superseded: its telemetry
_has_ landed, and the twelve paid-for defects are a recurring cost, not a risk to hedge.

**The head of that path inverted after the 2026-07-26 proof run, and the ordering above is the
corrected one.** The plan originally put Phase 0 ahead of `kjc5.29`. Phase 0's six work items did
all land, but its proof run (`u6jq.1`) cost $34.16 for 46.0M tokens — 13.7× the 3.36M baseline —
and so failed its acceptance criterion. The diagnosis is that the forecast, not the admission gate,
is the defect: D8 forecasts _working set_, under-forecasts it 2.8–4.8×, and does not model turn
count at all, while an agentic loop re-sends its context every turn — so spend came out 160–420×
the forecast. Re-running today would be financially unsafe rather than merely inconclusive, which
is why `u6jq.1` is now blocked on a forecast that must first learn actual tokens and wall clock per
unit of work **per model**. `kjc5.29` (model provenance) therefore precedes Phase 0's completion
rather than following it. The constraint that falls out is worth stating once: **cost is bounded by
sizing the work, never by interrupting a working agent.**

**Longest pole**: Phase 6 — and it is now the _next_ pole, not a background one. `vkh0.1` landed,
so the measurement window has been accumulating; the sequencing question is no longer when to start
telemetry but when `vkh0.2` freezes the surface, because everything after it is blocked on that one
document.

**The path to 1.0.0, restated as releases** (2026-07-30, §4.0): `v0.6.0` (`jr0l.38` +
`jr0l.39` + `jr0l.49`) → `v0.7.0` (hardening → forecast chain → the `u6jq.1` proof run
doubling as `vkh0.2` telemetry → event log → flip) → `v0.8.0` (`7bur` + Phase 2 gates +
Phase 3 docs) → `v0.9.0` (roster + Phase 4) → `v1.0.0` (Phase 8 freeze + acceptance test).
The two critical paths above did not change; the ladder is how they interleave into shippable
cuts, and it puts the tracker path first per the owner's 2026-07-29 resequencing.

## 6. What to cut if capacity is short

- **Must**: Phase 0 in full, `kjc5.29`, `7bur`, `2a`+`2b`, the D4 amendment, `kjc5.13`, **and
  Phase 6 through the `vkh0.2` freeze**. Without the first group the project cannot make an
  evidence-backed claim about itself, and it cannot run unattended; without the last it keeps paying
  a dependency for its own defects, which is the one cost that recurs every session.
- **Should**: `2d`, `2e`, Phase 3's tutorial layer, Phase 4 steps 1–3. (`1a` was listed here and is
  **done** — one session, and it cancelled Phase 4's urgency, which is the leverage the entry
  predicted.)
- **Opportunistic**: Phase 7's catalog-content beads, `kjc5.47`/`.48`, the capability-tier
  declaration, `vkh0.3` and `vkh0.6` (both cheap, independently useful, and not gated on the
  freeze).
- **Defer deliberately, and say so**: Phase 5 until 1b has numbers — building the roster first
  means guessing the tier table, which is the specific error R5 was written to prevent. **Phase 6
  is no longer on this list**: it was deferred here pending a measured surface, `vkh0.1` supplied
  the measurement, and the owner then resequenced it (§4, Phase 6). Only `vkh0.4` (the cross-repo
  offer exchange) stays deferred, because nothing consumes it yet.
- **For 1.0.0 specifically, nothing in Phase 8 is cuttable**: a 1.0 without the freeze, the
  acceptance test, or the marker gate is a version number, not a promise (§1.1). If capacity
  is short, v1.0.0 moves later; its content does not shrink.

## 7. Decisions still owed by the owner

Each blocks something; none can be derived from the code.

1. **The ceremony threshold** (`steering-surfaces` §6.2). A policy choice about how much ceremony
   this repo wants, not a technical question. Currently the loop is mandated for "non-trivial
   work", which is the agent's judgment call, so the rule is unenforceable. Needs a written
   threshold **and** a named lightweight path below it that skips ceremony but never the hooks.
2. **Whether losing the github.com Copilot surface is acceptable, per fragment**, before Phase 4
   moves anything (§4, Phase 4).
3. ~~**The clean-room boundary sign-off** before Phase 6 writes code.~~ **DISCHARGED** on
   `basicly-qk6y` (closed). Recorded here because Phase 6 is now next and a reader checking what
   still blocks it would otherwise find a decision that was already made.
4. **Whether the machine-local, expiring retro lane is wanted** — a rung between dropping a retro
   proposal and asking a human to amend the shared catalog. The risk to weigh is bypass by
   accretion: guidance that shapes a machine's sessions while never being reviewed.
5. **Tier-2's rank-1 floor**, after the baseline is measured — deliberately not guessable.

### 7.1 Decided and closed: declarative phases are rejected

Recorded here rather than left out, because the question will be asked again every time a competitor
ships YAML-defined phases — and because a rejection with no reason attached is indistinguishable from
an oversight.

**Decision, 2026-07-30: the loop's phases stay engine code. Not deferred — rejected.** The reasoning
sits with the pillar list in `architecture.md` §3; the short form is that the `verified`/landed
invariant from incident `basicly-k35r` cannot move into data without leaving the type checker, the
test suite and code review behind; that no consumer has asked (zero beads, and the eight phases were
stable through the entire factory build); and that what a consumer would plausibly vary — required
gates, rework cap, verify checks, autonomy — is already configuration.

**Not on the roadmap, and nothing is filed to build it.** Two things were adopted independently of
the rejection and must not be discarded with it: `basicly-m4zv.13` (Archon's evidence-artifact
presence check, which needs no phase machinery at all) and `basicly-jr0l.48` (a simplification review
of the existing ladder — explicitly a typed refactor, not a move to data).

**The limit of the evidence, stated so a future reader does not overclaim it.** It is tempting to
argue that declarativeness _caused_ the determinism failures in the two projects that adopted it. It
did not. Archon's flaw is a disjunction in a completion gate; Symphony's is holding all real state in
RAM. Both are independent design errors in projects that happened also to be configurable. The
correlation is real; the causation is not established. The reason to reject is the typed-invariant
argument above — anyone revisiting this should attack that, not the correlation.

### 7.2 Decided 2026-07-30: the shape of 1.0.0, and six items off this list

Recorded here because §7 is where a reader checks what still blocks a phase.

1. **What 1.0.0 means** — decided; §1.1. Full semver contract, all designs implemented, the
   consumer criterion demonstrated on a fresh repo.
2. **`u6jq.1` is required, in `v0.7.0`** — under the delegated 10M-token L1 ceiling; it
   doubles as `vkh0.2`'s telemetry run (§4.0).
3. **`jr0l.43` builds the fake-agent-CLI e2e test** — the declare-only option was rejected on
   the `jr0l.38` evidence; recorded on the bead.
4. **`jr0l.49` joins the `v0.6.0` blockers** — rationale recorded on `basicly-m3od`: renaming
   the trap (`jr0l.39`) without closing the ladder hole leaves the incident possible.
5. **The Python 3.14 floor stays** (E1 wontfix confirmed); Phase 8 adds the forward-version CI
   job so the "+" in "3.14+" is a tested claim.
6. **`a3ab.6`'s home is the path-scoped rules tier** (`docs/research/**` + `docs/design/**`),
   recorded on the bead; the always-on baseline gains nothing, and Codex's inlining cost is
   measured per the bead's AC.

Two §7 items were resolved under the standing L3 grant, reversible on owner objection: the
github.com Copilot surface question (item 2 above the line) becomes a per-fragment rule at
Phase 4 — _a fragment that must bind in PR review stays unscoped_ — and the machine-local
retro lane (item 4, `jr0l.28`) stays deferred past 1.0.0, on bypass-by-accretion risk and zero
recorded demand. Still genuinely owed: the ceremony threshold's written form (`imnu.5`,
`v0.8.0`) and Tier-2's rank-1 floor (item 5, deliberately post-measurement).

## 8. Risks, and how each is detected

| Risk | Detection |
| --- | --- |
| A measurement phase produces an uninterpretable result because an arm was contaminated | The eval harness asserts its own isolation: read back what guidance is live in the cell and fail if it does not match the arm's declaration. Never rely on someone noticing an implausible number. |
| Eval-case coverage (2c) stalls at 20 of 48 entries and the gate is quietly relaxed | The gate is a Tier-1 failure from the start, so coverage cannot silently regress; stage by _adding_ entries to the enforced set rather than by lowering a threshold. |
| Phase 6 grows into a general-purpose tracker | The frozen surface list from `vkh0.2` is the scope contract, and the non-goals are recorded. Anything not in the measured surface is out. |
| D11 evidence fields make Phase 6's migration expensive | §3.4: land evidence as comment markers, not schema. Check this at decomposition time, when it is free. |
| The roster is built on a guessed tier table | Phase 5 is gated on 1b by construction. If 1b slips, Phase 5 waits rather than proceeding on assumption. |
| A design document drifts from shipped behaviour and misleads a future session | Each phase ends by absorbing into the architecture reference and archiving the source (§3.5). This session already found two such drifts — a tier claimed missing that was built, and a bead citation pointing at unrelated closed work. |
| The plan itself goes stale after the tracker is replaced | This file is the durable copy; refresh §2 against the tree at the start of each phase rather than trusting it. |
| A frozen surface turns out to need a breaking change during 1.x | The deprecation policy defines the escape (deprecate in 1.x, remove at 2.0), and Phase 8's marker gate makes an accidental break impossible to land silently. |

## 9. Appendix A — how a phase maps to the graph

This appendix used to enumerate every non-closed bead per phase. That list is deleted rather than
updated: the decomposition landed phase membership as a **label**, so the graph now answers the
question directly and any hand-maintained copy here would be stale within a session — exactly the
failure §8's last row warns about. Query it instead of reading it:

```sh
br list --label phase-2          # membership
basicly loop status <issue>      # where one bead actually is
br scheduler                     # what to pick up next
```

What the phase label alone does not make obvious, and so belongs here — which epic owns each
phase, and which two epics pre-date the scheme:

| Phase | Epic | Note |
| --- | --- | --- |
| 0 | `basicly-u6jq` | all six work items closed; only `u6jq.1` (the proof run) remains, blocked on `jr0l.22` |
| 1 | `basicly-agzx` | — |
| 2 | `basicly-m4zv` | — |
| 3 | `basicly-imnu` | — |
| 4 | `basicly-a3ab` | — |
| 5 | `basicly-s2xf` | — |
| 6 | `basicly-vkh0` | pre-dates the phase epics; labelled `phase-6` rather than renamed. **P0 — resequenced 2026-07-29 as the release after `v0.6.0`**; `vkh0.1` closed, `vkh0.2` is the gate, `vkh0.4` alone stays deferred |
| 7 | `basicly-jr0l` | pre-dates the phase epics; labelled `phase-7` rather than renamed |
| multi | `basicly-kjc5` | the original parallel-factory epic; labelled `phase-multi`, its children spread across phases |
| 8 | — | new 2026-07-30 (§4, Phase 8); file its epic at decomposition time, when v0.9.0 ships |

The release epic (`basicly-m3od` for `v0.6.0`, and its successors) sits outside the phase
scheme on purpose: a release is a cut across phases (§4.0), so it blocks on beads rather than
carrying a phase label.

Nothing was re-parented — a bead's parent is still its epic of origin, and its phase is the label.
So `kjc5` children appear in several phases, and the epics close when their children do.

## 10. Appendix B — tracker hygiene found while planning

**Discharged in full, verified at `13a4647`.** Kept as a record because each item was a gate gap
as well as a piece of dirt, and the gap is the durable part:

| Item | Outcome |
| --- | --- |
| 4 tombstone records from probe beads (`2ra`, `qij`, `yci`, `dor-accept-ac-field-ayb1`) | pruned; 0 remain |
| `basicly-jr0l.9` open with all children closed and no AC | closed. The gate gap stands: the criteria-on-every-bead rule was supposed to make this state impossible |
| `basicly-q5pk` (P1) likely already satisfied | verified and closed |
| Almost no sequencing in the graph (two edges) | 21 gating `blocks` edges among open work; phase membership is a label |
| `basicly-vkh0` `deferred` while its children were live | epic is `open` |

**One residue, and it is a defect rather than dirt.** Four open beads carry no phase label:
`basicly-jr0l.18` / `.19` / `.20` are context-ceiling continuations the loop created when
`kjc5.32` / `.50` / `.51` finalized early, and they did not inherit their parents' `phase-7` label;
`basicly-jr0l.24` never had one. Because membership is a label, an unlabelled bead drops out of
every phase-scoped query silently, and the early-finalize path will keep producing them. Labelling
these four is bookkeeping; fixing the inheritance is engine work.
