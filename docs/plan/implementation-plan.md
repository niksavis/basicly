# basicly Implementation Plan

Authored 2026-07-26 against `main` @ `b02b527`. Reviewed: every document in
[`docs/design/`](../design/) and [`docs/research/`](../research/), the 39 engine modules under
`src/basicly/`, and all 51 non-closed tracker records.

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

## 2. Current state, measured

Everything in this section was checked against the tree at `b02b527` rather than read from a
design document, because two figures the design documents carry turned out to be stale.

**Shipped and dogfooded.** Catalog and projection with drift gates; the git and agent hook floor;
the single-track loop; worktree isolation; the concurrent supervisor with lanes and a serial merge
queue; autonomy grants L0–L3 with a spend ceiling; the decision queue and corpus-bounded decider;
release automation to the annotated tag. 39 engine modules, 55 test files, 1247 tests, an 8-check
`verify --mode full`. The 2026-07-26 dogfood landed three concurrent lanes with no human editing
code, at 3.36M tokens against an earlier run's 25.25M — a 7.5× reduction from right-sizing alone.

**Not started.** Grep confirms zero modules mention a role registry or persona routing, no
lexical ranker (`tf-idf`/stemmer), no `severity` field, and no `evals/` directory anywhere. The
tracker is still the external `br` binary.

**Partially true, and the gap matters:**

| Claim | Measured reality |
| --- | --- |
| "Rework has a cap but no stall detector" | Correct. `policy.py` has no convergence check. `runner.StallWatchdog` exists but is dispatch-level (no file activity for _n_ seconds) — a different mechanism entirely. |
| "We lack a path-scoped guidance tier" | **Wrong, and it was corrected this session.** The tier is fully built: `claude.yaml` declares a `scoped_rules` output at `.claude/rules/{fragment_id}.md`, the planner routes on `has_scope`/`exclude_scoped`, `rule_md.j2` renders it. **Zero fragments declare a scope.** The remaining work is authoring plus one check. |
| "The always-on baseline is ~9000 chars with ~1000 chars of headroom" | Stale. Actual: 7014 / 7209 / 7343 chars for `AGENTS.md` / `CLAUDE.md` / `copilot-instructions.md` — roughly 1070 words each, against a 9000 soft cap, so ~1700–2000 chars of headroom. **The cliff concern stands** (1070 words is well past the ~500-word threshold the review cites); the headroom figure does not. |
| "Roughly thirty catalog entries" | 19 fragments + 29 projected skills + 7 subagents + 4 rubrics. The eval-coverage lift is **48 guidance entries**, not 30. |

**Tracker state.** 42 open, 5 deferred, 4 tombstones, 314 closed. Three epics open: `basicly-kjc5`
(parallel factory, 7 open children), `basicly-jr0l` (factory hardening — D9/D10/D11 and field
usability, 22 open children), `basicly-vkh0` (own the tracker, deferred, 5 children). Only two
gating `blocks` edges exist among open work: `basicly-7bur` blocks `basicly-4t9z`, and
`basicly-vkh0.1` blocks `basicly-vkh0.2`. **Almost all sequencing in this plan is therefore not
yet expressed in the graph** — which is the main thing the decomposition step must fix.

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
   `br` bug; the root fix is Phase 6, which is months of work. Stop charging rework for it now, and
   carry the defect forward as a **requirement** on the replacement.

## 4. The phases

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

### Phase 1 — Buy the numbers that decide the expensive phases

**Priority: P0. Depends on: Phase 0 (for 1b/1c). Size: 3–4 sessions.**

Three assumptions currently carry large downstream work. Each has a cheap test, and at least one
could cancel a whole phase.

**1a. The always-on recall test.** _Independent — can run first or in parallel._ Open a fresh
session per agent family and ask the agent to summarise the rules in the always-on file; anything
it cannot recall is not doing work. Formalised as a behavioural recall case with the no-guidance
control being the same session with the baseline absent. **This decides whether Phase 4 is urgent
surgery or routine tidying**, and no change to the always-on cap in either direction is legitimate
until it runs.

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
tier table plus the localisation question decided by data rather than argument.

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

**Exit criteria.** `docs/design/factory-design.md` is archived, the architecture reference carries
its content, a new consumer can follow a tutorial from install to first shipped bead, and
`basicly install` states the guarantee it actually delivered.

### Phase 4 — Relieve the always-on layer

**Priority: P1 if 1a shows a cliff, P2 if it shows a slope. Depends on: 1a (urgency), 2a (adjacent). Size: 2–3 sessions.**

Cheaper than the design documents assume, because the mechanism is already built (§2).

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

**Watch the asymmetry** established this session: scoping removes a fragment from the Claude and
Copilot baselines but **not** from Codex's, which inlines scoped fragments because our scopes are
globs and nested `AGENTS.md` is directory-based. So a claim of "we cut the baseline" must name the
family, and scoping is a deliberate _removal_ from the github.com Copilot surface — a guarantee
change per fragment, not a refactor.

**Exit criteria.** Baseline measurably smaller on two of three families, 1a's recall test re-run
shows improved recall, and no declared scope matches nothing.

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

**Priority: P2, highest effort. Depends on: `vkh0.1` → `vkh0.2`; schema stability from Phase 5. Size: 5–8 sessions.**

The tracker _is_ the harness's state, so every guarantee in §1 is downstream of it, and it is
currently an unowned external binary in the critical path whose licence carries a rider
restricting a class of users.

**Hard constraint: a clean-room boundary applies.** The replacement must not be derived from
`beads_rust` source. Sanctioned inputs are our own ledger's observable data, `br`'s documented CLI
contract, and the genuine-MIT upstream original. Confirm the boundary with someone qualified
before implementation proceeds; the conservative line costs nothing because the MIT original is
available.

**Order:**

1. **`basicly-vkh0.1`** — record `br`/`bv` usage at subcommand and flag level into a _committed_
   ledger, distinguishing engine call sites from interactive human ones. The engine's set is the
   hard requirement; the human's may be thinner at first. _Do not design the schema from memory of
   our own usage._
2. **`basicly-vkh0.2`** — report the surface actually exercised, then **freeze the surface list**.
   Surfaces nobody uses simply do not exist in the replacement.
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

**Carry Phase 0's defects forward as requirements.** The clock error that makes a gate flaky, the
dependency-field spelling inconsistency, the unconfigurable lint templates, the single-line
acceptance-criteria field, the ids whose internal hyphens break our own commit gate, and the
absolute-path leak are all _requirements input_ for the replacement. Each should become a
committed regression test, so the replacement cannot reintroduce a defect we already paid for.

**Also in this phase:** `basicly-vkh0.3` (record the scheduler score and rank behind each
dispatch) is cheap and independently useful now — it makes a pass's dispatch order reconstructible
without replacing anything, so it can land early. When we own the ranking it must become **pure**
and drop `created_at`: age-based ordering makes dispatch order clock-dependent for an unchanged
graph, which the determinism rule forbids.

### Phase 7 — Factory hardening, interleaved

**Priority: P2–P3. Mostly independent. Size: ongoing.**

The `basicly-jr0l` epic's 22 open children are largely small and self-contained, which makes them
good capacity fillers between the phases above rather than a phase of their own. Four groups, with
one placement warning.

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

## 5. Dependency graph

Gating edges only. `→` means "must precede".

```text
Phase 0 ─┬─ vkh0.5 (leak)      ─────────────────────────────────────┐
         ├─ 4tjt + 55yh + jr0l.14 + 8veb ──→ unattended run ──┐    │
         └─ f7li                                              │    │
                                                             │    │
Phase 1 ─┬─ 1a recall test  (independent, no prerequisite) ───┼────┤
         ├─ kjc5.29 (pin the model) ──→ 7bur (1b) ────────────┤    │
         └─ 7bur ──→ 1c localisation ──→ Dana's contract      │    │
                 └──→ 4t9z (existing edge)                    │    │
                                                             ▼    ▼
Phase 3 ─── D4 amendment ──→ Phase 2d/2e                  (all later phases
         └─ kjc5.13 absorb ──→ archive factory-design       need both)

Phase 2 ─── 2a invocation axis ──→ 2b Tier-2 routing ──→ 2c eval case files
         └─ 2d severity + no-pre-judging lint ──┐
         └─ 2e stall detector + gate taxonomy ──┴──→ Phase 5

Phase 4 ─── 1a ──→ baseline audit ──→ declare scopes ──→ empty-glob check

Phase 5 ─── 1b + 1c + 2d + D4 ──→ role registry ──→ role-aware dispatch
         └─ kjc5.32 (coupling attribution) should precede

Phase 6 ─── vkh0.1 ──→ vkh0.2 ──→ freeze surface ──→ event log ──→ import
         └─ shadow (vs LIVE tracker) ──→ dual-write ──→ flip
```

**Critical path to a credible product claim**: `Phase 0 → kjc5.29 → 7bur → Phase 2b → Phase 5`.
That is the chain that turns "our harness is better" from an assertion into an instrumented,
gated, role-routed system. Phase 6 is the largest effort but is **not** on this path — it is a
strategic dependency-removal that can proceed in parallel once its telemetry lands.

**Longest pole**: Phase 6. Start `vkh0.1` early (it is only telemetry) so the measurement window
is accumulating while other phases run.

## 6. What to cut if capacity is short

- **Must**: Phase 0 in full, `kjc5.29`, `7bur`, `2a`+`2b`, the D4 amendment, `kjc5.13`.
  Without these the project cannot make an evidence-backed claim about itself, and it cannot run
  unattended.
- **Should**: `1a` (one session, disproportionate leverage), `2d`, `2e`, Phase 3's tutorial layer,
  Phase 4 steps 1–3.
- **Opportunistic**: Phase 7's catalog-content beads, `kjc5.47`/`.48`, the capability-tier
  declaration.
- **Defer deliberately, and say so**: Phase 5 until 1b has numbers — building the roster first
  means guessing the tier table, which is the specific error R5 was written to prevent. Phase 6
  until `vkh0.2` has a measured surface, for the same reason.

## 7. Decisions still owed by the owner

Each blocks something; none can be derived from the code.

1. **The ceremony threshold** (`steering-surfaces` §6.2). A policy choice about how much ceremony
   this repo wants, not a technical question. Currently the loop is mandated for "non-trivial
   work", which is the agent's judgment call, so the rule is unenforceable. Needs a written
   threshold **and** a named lightweight path below it that skips ceremony but never the hooks.
2. **Whether losing the github.com Copilot surface is acceptable, per fragment**, before Phase 4
   moves anything (§4, Phase 4).
3. **The clean-room boundary sign-off** before Phase 6 writes code.
4. **Whether the machine-local, expiring retro lane is wanted** — a rung between dropping a retro
   proposal and asking a human to amend the shared catalog. The risk to weigh is bypass by
   accretion: guidance that shapes a machine's sessions while never being reviewed.
5. **Tier-2's rank-1 floor**, after the baseline is measured — deliberately not guessable.

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

## 9. Appendix A — every non-closed bead, mapped

| Phase | Beads |
| --- | --- |
| 0 | `vkh0.5`, `4tjt`, `55yh`, `jr0l.14`, `8veb`, `f7li` |
| 1 | `kjc5.29`, `7bur`; new: recall test, localisation experiment |
| 2 | new: invocation axis, Tier-2 routing, eval case files; `4t9z` (after `7bur`) |
| 2/7 | `kjc5.46` (run through Phase 2's micro-test harness) |
| 3 | `kjc5.13`; new: D4 amendment, tutorial/how-to layer, capability tiers |
| 4 | new: baseline audit, scope declarations, empty-glob check, boundary triad |
| 5 | new: role registry, role-aware dispatch, soft-lists, R4 path, R9 class, tier escalation |
| 6 | `vkh0` epic: `vkh0.1`, `vkh0.2`, `vkh0.3`, `vkh0.4` |
| 7 | `jr0l` epic: `jr0l.1`, `.4`, `.8`, `.9`, `.10`, `.12`, `.13`; `kjc5.32`, `.33`, `.37`, `.38`, `.45`, `.47`, `.48`, `.49`, `.50`, `.51`, `.52`; `kvx5`, `5xcj`, `sco6`, `g7os`, `z25w`, `l7zo`, `k5tr`, `7h1z` |
| bookkeeping | `q5pk` (record the review — largely satisfied by the 2026-07-26 doc work; verify and close), `2rn9`, `0jiq` (deferred, external CLI) |

Epics `kjc5`, `jr0l`, `vkh0` close when their children do.

## 10. Appendix B — tracker hygiene found while planning

Small, worth fixing during decomposition rather than filing separately:

- **4 tombstone records** remain from probe beads (`2ra`, `qij`, `yci`,
  `dor-accept-ac-field-ayb1`). Prune with a hard delete.
- **`basicly-jr0l.9`** has all three children closed, no acceptance criteria, and is still open.
  Either close it or give it an AC — the criteria-on-every-bead rule is supposed to make this
  impossible, so it is also a small gap in that gate.
- **`basicly-q5pk`** (P1) is very likely satisfied by the 2026-07-26 documentation work. Verify
  against its description and close, rather than leaving a P1 that looks like open work.
- **Almost no sequencing is in the graph.** Only two gating edges exist among 42 open beads. The
  decomposition step should add the `blocks` edges this plan implies, otherwise `br ready` will
  keep offering Phase 5 work while Phase 0 is unfinished.
- **`basicly-vkh0` is `deferred`** while its child `vkh0.1` is `open` and its child `vkh0.5` is a
  live leak. Reopen the epic or promote the two children, so the scheduler stops hiding them.
