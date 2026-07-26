# Parallel Factory Design — Orchestrated Lanes on the Harness Loop

Status: **agreed design, largely implemented — see §9 for what has landed.** Decisions D1–D8
were settled on 2026-07-22; D9 and the D4 amendment on 2026-07-25. This document is the
implementation reference; `docs/architecture/architecture.md` remains authoritative for everything already
built and absorbs the relevant sections as the pieces land (`basicly-kjc5.13`).

A decision recorded here is not evidence that it is enforced in code. §9 is the reconciliation,
and every gap named there has an open bead — treat a decision without a landed bead as a
statement of intent.

## 1. Vision

Extend the harness loop from a single-track driver into a parallel software factory:

- A **supervisor** decomposes intake into work packages sized for parallelism, maintains the
  dependency graph, and spawns parallel **lanes** — one isolated worktree per package.
- Each lane runs a simple engineering mini-loop: plan → decompose → per-task
  build/verify/validate → integrate → signal merge-ready.
- A **merge queue** lands finished lanes serially back on base, in dependency order.
- The whole factory is agent-agnostic (Claude/Codex/Copilot via runner adapters) and
  consumer-customizable through the existing overlay/override machinery.
- Sessions run **interactive** (human at checkpoints) or **autonomous** (a decider agent
  resolves delegable decisions under an explicit, auditable grant).

At the time of writing roughly 70% of this existed (architecture §12): the phase engine,
`br`-native dependency graph with deterministic scope-overlap serialization, worktree fan-out
with runner dispatch, the serial merge function, gates/checkpoints/bounded rework, and the
needs-input protocol. This design added the concurrent supervisor, the autonomy model, the lane
mini-loop, a standing merge queue, and context-budget sizing — all of which have since landed
(§9). What remains is the client surface, process budgeting, release automation, and the
evaluation instruments.

**Dual-use constraint (load-bearing):** the factory is a distributed product consumed by
other repos via `basicly install`, *and* the process basicly uses to develop itself. Every
component below therefore ships through the existing distribution model — engine behavior
as versioned CLI code in the `basicly` package, guidance as catalog YAML fragments/skills
projected per agent, enforcement as managed hooks — and none of it may hardcode
basicly-repo specifics (trackers, paths, branch names, model choices are config).
Defaults must be sane in a fresh consumer repo with no overlay; all knobs live in the
overridable `[worktree]`/`[verify]`/`[policy]`/`[runner]` sections or overlay fragments so
consumer changes survive updates. Dogfooding is the acceptance test: each component is
exercised on basicly's own development before it is considered shipped.

## 2. Decisions

### D1 — Topology: singleton supervisor, sessions are clients

One deterministic `basicly loop supervise` process per repo, lock-guarded. It alone owns
runner dispatch, the concurrency cap, and the merge queue. Interactive agent sessions
(any CLI) are clients: they enqueue work via `br`, observe status, and answer decisions.
The first session that needs a supervisor starts it.

Rationale: with multiple sessions open on one repo, the tracker is not the true bottleneck —
`br` claims are atomic and the loop already reconstructs state from `br` on every call. The
real singleton resources are the **base checkout** (git allows one checkout of base; two
concurrent merges corrupt each other), the **machine-level concurrency/rate budget**, and
the single-writer **usage files**. A singleton process owns all three; peer supervisors
would turn cap accounting and merge ordering into a distributed protocol for no gain on one
machine, and an "uber-orchestrator" LLM adds a nondeterministic layer that cannot outlive
its own context window.

### D2 — Orchestration principle: the engine disposes, agents propose

The supervisor is a deterministic event loop. Agents are invoked as **pure functions**:
input is a context bundle assembled deterministically from `br` state; output is a
structured proposal (decomposition plan, decision, committed branch) that the engine
validates against policy before it becomes state. This generalizes the existing
`_dispatch_runner` pattern (engine builds prompt → agent works headless → engine verifies
and lands) to N concurrent invocations plus a decision queue.

An LLM never holds authority over the tracker or scheduling. The interactive session the
user talks to is a client acting through supervisor commands; it cannot bypass the engine.

### D3 — Autonomy: session-scoped grant ledger, built from existing primitives

Autonomy is a grant recorded as `[harness-policy]` comment markers on the session's root
issue (feature or epic; see the session definition in §7.2) — the same mechanism
checkpoints use today. **Grants can only be issued interactively**
(TTY or the existing confirm-code path), so an agent can never self-escalate; the
anti-autopilot tripwire (basicly-shgo, foundry incident) is reused as the grant-issuance
gate rather than replaced.

Levels:

- **L0 — task-by-task** (default, unchanged): every checkpoint human.
- **L1 — one-shot**: decompose checkpoint pre-approved at intake (formalizes today's
  operator discipline).
- **L2 — autonomous build**: classify + decompose delegable to a decider agent; every
  delegated decision logged as an attributed `br` comment (agent/model/run-id — plumb the
  existing `br create --agent-name/--harness/--model` attribution flags). Ship stays human.
- **L3 — lights-out**: ship delegable **only** when deterministic preconditions hold — every
  required gate green **on the node being shipped**, zero rework escalations and zero
  needs-input events **anywhere in the session**. Any wrinkle drops ship back to human. The
  gate check is scoped to the shipped node rather than the session root because a root that is
  still open cannot have its own gates green (owner decision 2026-07-25, `basicly-kjc5.39`);
  the two session-wide preconditions carry the "any wrinkle" stance.

Enforcement stays in `policy.approve_checkpoint_guarded`, extended to accept a valid grant
in lieu of a TTY when the level covers the checkpoint and preconditions hold. Grants expire
at session end; revocation is a comment. The supervisor enforces the grant's
`token_budget`: once run-record spend for the session reaches it, no new dispatches or
delegated decisions occur — the session drops to human-only until re-granted.

> **Enforced at admission since `basicly-kjc5.23`.** `policy.spend_status` is the single halt
> predicate, consulted at all three places a grant can spend: delegated checkpoint approval
> (`policy._grant_approval`), lane dispatch admission (`supervise.dispatch_lanes`), and decider
> delegation (`decisions.invoke_decider`, so the human's `loop decide` and the supervisor's
> autonomous pass are bound alike). A halted pass starts nothing new, lets in-flight lanes land,
> and enqueues an `escalation` item on the session root so the halt is not read as an idle pass.
> No grant, or an L1 grant with no budget, means no ceiling — not a halt.
>
> **L3 reaches a child's ship since `basicly-kjc5.39`.** `lights_out_violations` takes the node
> being shipped and checks *its* required gates; the root-scoped check it replaced could never
> hold mid-session, so it refused every child ship unconditionally. Still open: R9 (roster) needs
> a decision class this ladder cannot express: one
> that **no** level auto-disposes, an exception to the L0–L3 progression rather than a rung in
> it, so an autonomy grant can never hand the catalog to the decider.

### D4 — Verify vs. validate

- **Verify** = deterministic gates (tests/lint/build), the existing `verify` gate with
  `fast|full|staged` modes.
- **Validate** = acceptance-criteria satisfaction, the existing `rubric` gate promoted from
  advisory to required at package (lane) and session level; advisory at sub-task level.
- Gate mode is chosen **deterministically by change class** (leaf sub-task → `fast`; lane
  integration → `full`; session ship → `full` + validate), configured in `[verify]`/
  `[policy]`. Skipping is never an agent's judgment call. Supervisor-level verify/validate
  are never skippable; sub-task-level runs in `fast` mode rather than being skipped.

**Amended 2026-07-25 — what a judged NO does.** Promoting `rubric` to required is only as
strong as the checks the rubric carries, and a *judged* check cannot fail a gate (D9: no agent
output records a required gate). Two consequences, which together make the roster's R4
operative rather than aspirational:

- **Every shipped rubric must carry at least one deterministic check**, so a required validate
  gate can fail on evidence. A rubric with only judged checks passes having proved nothing,
  which is worse than an advisory gate because it reads as green.
- **A judged NO routes a decision-queue item**, carrying the failing criterion and the
  validator's evidence. The lane holds — it neither lands nor bounces — until a human, or the
  decider under an L2+ grant, disposes of it. An unsatisfied acceptance criterion is a
  decision, not a test failure: failing the lane on a judged verdict would hand a model
  blocking power and spend a rework attempt on a possible false NO.

### D5 — Merge queue: standing consumer, conflicts bounce back to the lane

The one-shot `merge_queue` function becomes a standing consumer inside the supervisor:
lands lanes in dependency (topo) order as they turn ready, re-verifying after each landing
(current behavior preserved).

On conflict: **no merge-time AI resolution.** A conflict means the decomposition's scope
declarations missed a coupling — the graph was wrong, not the merge. The owning lane
rebases on the new base and its own agent re-applies its intent with full context, bounded
by the existing rework cap, then escalates. The missed coupling is recorded as a dependency
edge so the graph learns. Optional later refinement: deterministic auto-resolution for
mechanical conflict classes (lockfiles, generated files) — never semantic ones.

### D6 — Cross-lane freshness: fresh at boundaries, never mutated mid-flight

Never inject information into a running lane's context (unreproducible, can invalidate
in-progress work). Instead:

- Every dispatch prompt is a pure function of `br` state **at dispatch time**; within a
  lane, each sub-task's fresh dispatch naturally sees the updated graph and landed work.
- Discoveries propagate through `br`: a lane that learns something cross-cutting writes a
  structured "found-info" record (generalizing the needs-input sentinel); the supervisor
  folds it into **future** dispatch bundles and adds dependency edges where it implies a
  missed coupling.
- Merge-time rebase remains the code-level reconciliation.
- If a landed lane invalidates a running lane's assumptions, the supervisor **cancels and
  re-dispatches** that lane rather than messaging it.

### D7 — Lane structure: depth-1 write parallelism, sequential sub-task beads

- Write-lanes exist **only at depth 1**: only the supervisor provisions worktrees and
  dispatches lane runners. A lane never creates a worktree or spawns write-agents.
- A lane's package is decomposed into child beads via the same `basicly decompose` engine
  and worked **strictly in sequence** in the lane's single worktree — one fresh runner
  dispatch per sub-task, `fast` verify per sub-task, then lane-level integrate + `full`
  verify + validate before signaling merge-ready. One tracker, supervisor-visible progress,
  mid-package resume after a crash.
- Read-only helper agents (explore/search/review) inside a lane are allowed and counted
  against one global `max_agent_processes` budget — a single number, not multiplicative
  per-level caps.

Rationale: parallel-safety derives from disjoint file scopes computed globally at
decompose time — a package splittable into disjoint sub-scopes should have been split into
top-level lanes; same-lane sub-tasks overlap by construction and would serialize anyway.
Depth-2 write-parallelism would require a merge queue per lane (multiplying the most
failure-prone component) while the multi-turn literature (D8) favors sequential fresh
dispatches for quality. When a package is too big, **flatten the tree, don't deepen it**:
the sizing governor forces a re-proposal as more top-level lanes or more sequential
sub-tasks.

### D8 — Work sizing: absolute working-set budget + fractional ceiling

The literature (see §5) refutes sizing by fraction of the context window; degradation is
driven by **absolute tokens of material the model must reason over** and by **turn-count
unreliability**, with one fractional behavioral effect near the perceived limit. Three
mechanisms, all deterministic:

- **Estimate (at decompose):** a task's context cost = fixed instruction overhead (known
  per repo) + tokenized size of files matching its declared scope globs + a per-task-class
  build factor calibrated from run-record telemetry. Every completed run is a calibration
  sample. Token counting defaults to the deterministic chars/4 estimate (no new
  dependency); a real tokenizer is a later calibration upgrade and is confirmation-gated
  as a dependency change.
- **Govern (at decompose):** a DoR rule — the estimate must land inside the per-class
  **working-set budget** (govern band 8–64K tokens; §6 is authoritative; packages should
  target the upper half of the band, regardless of window size). Too big → engine
  refuses, agent must split. Below the floor → merge with a sibling in the same scope
  group (under-cutting wastes per-lane overhead and parallel slots, though never model
  quality).
- **Meter (at run):** runner adapters capture actual token usage; crossing the **context
  ceiling** (default 60% of the model's window — an anxiety guard, not a fill target)
  triggers the finalize protocol: commit what's done, mark remaining acceptance criteria,
  spin the remainder into a follow-up bead.

The orchestrator's own planning bundle obeys the same working-set budget (digests, not
full files); a session plan that exceeds it is staged into an epic of features. Per-model
`context_window` lives in runner config with per-adapter defaults; budgets and ceiling are
`[policy]` config — consumer-tunable through the existing override machinery. The band is
a synthesis of moderately-supported evidence and ships as a calibratable default, refined
by the factory's own telemetry. Bootstrap note: the governor (component 2) does not exist
when this plan itself is first decomposed — the first decomposition applies the band
manually.

### D9 — Determinism is a property of the engine and of dispatch inputs, never of agent output

Settled 2026-07-25. The loop must be **deterministic in the engine and reproducible in its
inputs**, and it must leave the agent's *method* free. Three clauses:

- **The engine is deterministic.** Every engine step is a pure, idempotent function of `br`
  state plus pinned configuration, with a total order over every set it iterates, and no
  dependence on wall-clock, randomness, or completion order for any outcome that outlives the
  pass. Re-entering a step either blocks or produces a new `br` signal — never a different
  answer to the same question.
- **Dispatch inputs are reproducible; dispatch outputs are recorded.** Every dispatch persists
  the resolved runner, the resolved model, the adapter version, a hash of the exact prompt, and
  the ids of the found-info records folded into it. Two runs of the same node are then
  *diffable*: when attempt 2 behaves differently from attempt 1, the record says whether the
  input changed.
- **Agent output is never required to be repeatable.** It is required to be schema-valid,
  fail-closed, non-authoritative, timed out, and metered. An unparseable output is an
  abstention, not a pass (§7.1 already does this for the decider). Where the *repeatability* of
  a judgment actually matters, the judgment moves into code — a deterministic rubric check, a
  deterministic lens skip — not into a more tightly worded prompt.

**Latitude is granted by the rejector, not by the prompt.** An agent gets freedom of method
exactly where the answer is not derivable from recorded state **and** a deterministic rejector
catches a wrong exploration cheaply. Implementation has the strongest rejectors in the system
(commit-presence probe, `fast`/`full` verify, rebase + re-verify + merge-tree, bounded rework),
so implementation gets the widest latitude: a wrong exploration there costs one bounded cycle
and nothing else. Where no such rejector exists, the correct move is to **build one, or route
the item to a human** — never to constrain the agent's method and hope. This is the same
economics as D8 and R5: what buys reliability is a clear, right-sized, reproducible *input*,
not a narrower method.

Rejected alternatives:

- **Pin temperature and seed and call the loop deterministic.** No shipped coding CLI exposes a
  seed; tool use over a live tree and vendor-side model updates defeat it regardless, so the
  claim would be false in the very run-record that is supposed to be evidence.
- **Majority or consensus voting across N dispatches per judgment.** Triples spend on exactly
  the judgments D8/R5 route to a reliable tier instead, and buys agreement rather than
  correctness. It is also the blocking-model shape R4 rejects.
- **Forbid free-form prose output.** A reviewer's `path:line` findings and a retro's catalog
  diff *are* the product; schematizing them into enums destroys the evidence they exist to
  carry. Structure the *envelope*, not the finding.
- **"Reduce agent experimentation."** Refused as a goal. What is reduced is **unrecorded,
  unbounded, and authoritative** agent judgment — not exploration. Constraining a capable
  model's method raises cost per landed package (D8, §5).

### D10 — Zero-token operation: deterministic work is a command, not a procedure

Settled 2026-07-25. **Anything fully deterministic must be executable as one command that an
agent triggers and waits on.** If an agent has to perform a *sequence* of deterministic steps,
the engine is missing a command — and the tokens, the latency, and the chance of getting a
mechanical step wrong are all pure waste.

This is not a restatement of R3.1 ("if code can decide it, code decides it") or of §6's "never
dispatch where code decides". Those govern the *decision* to spend a model at all, and the
engine already obeys them: merge, verify, sizing, mode selection, and scheduling are all code.
D10 governs the **operator interface**, which did not obey them. The evidence is the
2026-07-25 dogfood run: driving one leaf bead end to end took about six agent-issued commands,
all deterministic, and the driving agent wrote a throwaway shell wrapper mid-session to survive
the repetition. A harness whose own operation needs a hand-rolled wrapper has misplaced the
boundary.

The test to apply to any step: *could a script do this without judgment?* If yes, an agent
performing it is a defect, whether or not the step is "cheap". Three consequences:

- **Ceremony collapses into phase-boundary commands.** One command takes a track from intake to
  the point where agent work is required; one takes committed work to shipped
  (`basicly-kjc5.41`).
- **Mechanical repair is applied, not reported.** A check whose fix is lossless and derivable
  (formatting) is applied by the engine, not handed back as a failure for an agent to repair
  (`basicly-kjc5.43`).
- **Structure is emitted, never guessed.** The template sections a work type requires, and the
  envelope of a commit message, are derived from state; only the judgment inside them is
  authored (`basicly-kjc5.42`, `basicly-kjc5.44`).

Rejected alternatives:

- **"Teach the agent the sequence in a skill."** Prose cannot make a deterministic procedure
  reliable — it re-derives the same steps every session at full token cost and fails silently
  when an agent misreads it. Skills should carry judgment, not procedure a command could own.
- **"It is only a few commands."** The measured cost is not the keystrokes: it is the context
  spent re-deriving the sequence, the round trips lost to mechanical rejections, and the
  divergence between what each agent does. Cost per landed package (§6) charges all three.
- **Scripting it outside the engine.** A wrapper in a scratch directory is not distributable and
  does not survive `basicly install`; the dual-use constraint requires it to be engine code
  every consumer gets.

### D11 — The bead carries evidence; the usage files carry only what may be lost

Settled 2026-07-25. A bead has two distinct roles, and conflating them is what makes trackers
rot:

- **State** — authoritative, minimal, and *branched on* by the engine: type, status, gates,
  checkpoints, dependencies, scope, acceptance criteria, the worktree binding. A stale state
  field is a correctness bug, so this set stays as small as it can be.
- **Evidence** — append-only, engine-written, and *branched on by nothing*: what a track cost,
  how long it waited, which environment produced a gate result. Evidence can be rich precisely
  because no decision depends on it; the worst outcome of a missing field is a thinner audit
  trail.

**Cost history is evidence, and it must live on the bead.** `.basicly/usage/` is self-ignored,
so run-records — the actuals ledger D8's calibration reads — never leave the machine that
produced them. A fresh clone or a new team member therefore forecasts from the seed build
factors, and two machines size the same plan differently. Since `.beads/issues.jsonl` is
committed, the bead is the only carrier that survives a clone: the engine writes a cost
projection at ship (`basicly-kjc5.50`), and the local usage files keep only the per-dispatch
detail that is cheap to lose.

Three rules for anything added:

1. **Derived, never authored.** A field the engine writes and an agent may also edit is a second
   authority; that is why sizing stays in tokens from the governor and never accepts a
   story point (`basicly-kjc5.48`).
2. **Keep the forecast next to the actual.** Actuals say what happened; the *pair* says how
   biased the estimator is, per class, which is the only thing that improves a forecast. The
   actual must aggregate failed attempts too, or it flatters exactly the packages whose cheap
   dispatch produced an expensive result (R5, §6).
3. **Measure the wait, not just the work.** A factory's wall-clock is dominated by waiting on
   humans at checkpoints and decisions, which no current field records
   (`basicly-kjc5.51`). A forecast built from dispatch duration alone predicts the compute and
   misses the bottleneck — and the value of an autonomy grant *is* the wait it removes.

## 3. Components to build

Ordered roughly by dependency; each builds on named existing modules.

1. **Run-record token telemetry** — populate the reserved token/cost fields from runner
   adapter output (all three headless CLIs report usage). Prerequisite for D8 calibration
   and the meter.
2. **Sizing estimator + DoR governor** — deterministic scope tokenization + per-class
   budgets; new DoR rule in the decompose path (`decompose.py`, `policy.py`).
3. **Autonomy grants** — grant markers + issuance CLI (interactive-only) + extension of
   `policy.approve_checkpoint_guarded`; plumb `br` attribution flags through dispatch.
4. **Decision queue** — one queue for needs-input facts, escalations, and checkpoint
   requests (generalizing `needs_input.py` and the escalation block). Interactive mode
   surfaces to the human; autonomous mode invokes the decider agent per decision with the
   grant checked per item.
5. **`basicly loop supervise <issue>`** — lock-guarded singleton event loop composing
   existing pieces: ready-set from `br scheduler`, worktree fan-out
   (`_ensure_child_worktrees`), **concurrent** async runner dispatch (`_dispatch_runner`),
   outcome collection (green → merge-ready; block → decision queue), standing merge queue
   (`merge.merge_queue` behaviorally preserved), loop until done/blocked. Client commands
   for attach/status/answer. Single-track `loop status/advance/run` remain supported
   unchanged — the supervisor composes `advance`, it does not replace it. Expected to
   decompose into ~3–4 packages (lock/lifecycle, concurrent dispatch, outcome routing +
   merge integration, client commands).
6. **Lane mini-loop** — lane-level decompose into sub-task beads; sequential per-sub-task
   fresh dispatch in the lane worktree; lane integrate + `full` verify + validate gate
   before merge-ready.
7. **Merge queue v2** — consume-as-ready ordering, bounce-back conflict path with
   dependency-edge recording (D5).
8. **Global process budget** — `max_agent_processes` accounting across lane runners and
   read-only helpers.
9. **Ship/release automation** — automate the `release-process` skill behind the ship
   checkpoint; only reachable under an L3 grant with preconditions (D3).
10. **Factory A/B evaluation driver** (existing bead `basicly-7bur`) — the repeatable
    harness-vs-bare eval, upgraded into the factory's acceptance instrument (§8). Lands
    after component 1 so it inherits real per-arm token/cost accounting.
11. **Skill evals** (existing bead `basicly-4t9z`) — trigger-accuracy and behavioral-lift
    evals for distributed skills, extended to cover the factory's new prose surfaces
    (§8). Independent of the engine order; can start any time.

Customization surface: all new knobs live in the existing overridable sections
(`[worktree]`, `[verify]`, `[policy]`, `[runner]`) plus overlay fragments for prose —
consumer changes survive `basicly install` via the current three-tree ownership model.

## 4. Explicitly rejected

- **LLM orchestrator in control of the tracker** — inverts agent-proposes/engine-disposes;
  loses reproducible scheduling, `br`-based resume, enforcement-by-construction, and
  agent-agnosticism.
- **Merge-time conflict-resolver agent** — resolves with neither lane's context at the
  point of weakest verification; the graph, not the merge, is what was wrong.
- **Depth-2 write-parallelism (lanes spawning sub-lanes)** — re-derives splits the global
  scope math already makes, multiplies merge queues, and peak-concurrency math
  (1 + 4 + 4×2 = 13 agents) blows the machine/rate budget for parallelism that mostly
  serializes anyway.
- **Sizing as "fill to 50–70% of the window"** — unsupported by primary literature (§5);
  replaced by absolute working-set budgets + a fractional ceiling as a behavioral guard.
- **Mid-flight context injection into running lanes** — unreproducible; superseded by
  fresh-at-boundaries (D6).
- **Agent-discretion gate skipping** — replaced by deterministic mode selection per change
  class (D4).
- **Pinned sampling parameters, consensus voting, and prose-free agent output** — three ways to
  chase determinism in the wrong layer; see D9's rejected alternatives.
- **Teaching a deterministic procedure to an agent in prose** — re-derived every session at full
  token cost and silently fragile; it belongs in a command (D10).

## 5. Literature grounding for D8

Verified against primary sources, 2026-07:

- **Degradation is absolute-token- and task-difficulty-driven, not window-fraction-driven.**
  NoLiMa (Adobe, ICML 2025, arXiv:2502.05167): GPT-4.1 (1M window) effective ≈16K on
  non-lexical retrieval; GPT-4o (128K) ≈8K — same absolute band across an 8× window gap.
  RULER (NVIDIA, COLM 2024, arXiv:2404.06654): claimed-vs-effective ratios range 25% to
  >100% across models — no constant fraction exists. Chroma "Context Rot" (2025): decay is
  gradual from thousands of tokens on Claude 4 / GPT-4.1 / Gemini 2.5.
- **Task type moves the threshold ~10×.** Lexical retrieval holds ~85% at 128K on 2026
  frontier models; semantic/multi-hop reasoning shows measurable loss beyond ~16–32K of
  relevant material.
- **Agentic coding's dominant failure is multi-turn unreliability, not retrieval.**
  Laban et al. (arXiv:2505.06120): average −39% single-turn→multi-turn, frontier models
  equally affected; mechanism is premature commitment to early wrong outputs. Motivates
  fresh dispatch per sub-task (D7) over long-running lane sessions.
- **The one fractional effect is behavioral ("context anxiety").** Models aware of their
  limit cut corners near the *perceived* limit (Cognition SWE-1.7; Anthropic context
  awareness in Sonnet 4.5+); hence the D8 ceiling is a guard below the real window, not a
  fill target.
- **Smaller relevant context is strictly better for quality.** Chroma LongMemEval: a
  ~300-token focused prompt beat the same question over a ~113K history. The only cost of
  cutting small is per-lane overhead — hence the lower bound on package size is economic,
  not model-quality.
- **The "50–70% of window" folk rule appears in no primary source** (checked: the above
  plus Lost in the Middle arXiv:2307.03172, Anthropic's context-engineering post — which
  deliberately names no threshold).

## 6. Configuration defaults

Existing knobs unchanged: `[worktree] concurrency = 4`, `base_branch = "main"`;
`[policy] max_rework = 2` (reused for merge bounce-backs); `[runner] default = "auto"`.
New parameters (all in the overridable sections per the dual-use constraint):

| Section           | Parameter                 | Default            | Rationale                                                              |
| ----------------- | ------------------------- | ------------------ | ---------------------------------------------------------------------- |
| `[runner]`        | `max_agent_processes`     | `8`                | Rule `2 × concurrency`: one avg helper per lane; API/RAM-bound, not CPU                  |
| `[runner]`        | `runner_timeout`          | `3600` s           | Hard kill per dispatch → decision queue                                 |
| `[runner]`        | `stall_after`             | `900` s            | No commit/file activity → flagged possibly-stuck to the decision queue; a flag, never a kill |
| `[runner]`        | `decider`                 | session default    | Runner/agent used for decider invocations (§7.1)                        |
| `[[runner.agents]]` | `context_window`        | per adapter        | claude 200K (1M where known), codex 400K, copilot 128K, unknown 128K    |
| `[[runner.agents]]` | `model`               | **unset**          | No built-in spec pins one, so R5's tier economics rest on an unpinned mapping and `BR_MODEL` attribution is usually blank. **`basicly-kjc5.29` makes it required** |
| `[policy]`        | `autonomy`                | `"L0"`             | Factory autonomy is opt-in; default preserves today's behavior          |
| `[policy]`        | `decider_max_decisions`   | `50` / session     | Runaway-loop guard for the decider agent                                |
| `[policy]`        | `notify_command`          | none (disabled)    | Consumer-supplied command fired per new human-required decision (§7.3)  |
| `[policy]`        | grant `token_budget`      | none — required    | L2+ grants must state a spend ceiling; unbounded lights-out unreachable |
| `[policy.sizing]` | `working_set_min`         | `8_000` tokens     | Below → merge with a scope-group sibling (overhead amortization)        |
| `[policy.sizing]` | `working_set_max`         | `64_000` tokens    | Above → engine refuses, agent must split (see §5 evidence)              |
| `[policy.sizing]` | `context_ceiling`         | `0.6` of window    | Behavioral anxiety guard; finalize-protocol trigger, not a fill target  |
| `[policy.sizing]` | `build_factor` seeds      | task 3.0 / bug 2.0 / chore 1.5 | Multiplier on scope read-cost until telemetry calibrates    |
| `[policy.sizing]` | `calibration_min_samples` | `10` per class     | Measured factors override seeds only past this                          |
| `[policy.sizing]` | `calibration_window`      | `50` runs          | Rolling window per task class                                           |
| `[policy]`        | `max_subtasks_per_lane`   | `10`               | Sanity bound; sizing governor is the real limit                         |
| `[verify]`        | level→mode mapping        | sub-task `fast`; lane `full`+validate; ship `full`+validate; merge re-verify `full` | D4: deterministic by change class |

Stall detection (`basicly-kjc5.25`) is `runner.StallWatchdog`, started around each lane
dispatch: it samples `supervise.lane_activity` — a digest of the lane's HEAD plus its dirty
tree — and enqueues one `stall` decision item when that fingerprint holds still for
`stall_after`. It never kills; `runner_timeout` stays the only terminal action, so a
slow-but-working run is not cut short. Commits and file writes are the sampled signal rather
than agent stdout, because the runner drains its pipes only after the process is down
(`basicly-kjc5.15`) and so has nothing incremental to read.

Process-budget reservation classes (fixed semantics, not config): `concurrency` slots
reserved for lane runners, 1 slot reserved for the decider (prevents decision-queue
deadlock), remainder best-effort for read-only helpers. Built in `basicly-kjc5.11` as
`runner.ProcessBudget`, which every engine-initiated dispatch acquires a slot from: lane
runners and lane sub-task runners as `lane`, `invoke_decider` as `decider`, the validate
judge and `basicly review` as `helper`. A helper never blocks a lane, so queueing for a
helper slot cannot deadlock a pass. The decider's slot is carved out *before* the lane
reservation — a ceiling too small for both narrows the lanes rather than dropping the
reservation that exists to keep the queue workable — and a ceiling below "decider plus one
lane" is raised to that minimum instead of overcommitting the machine. `basicly runner
dry-run`/`run` are deliberately unbudgeted: a human running one command by hand is not the
factory allocating machine capacity. Instruction overhead for the
sizing estimator is computed by tokenizing the projected instructions, never configured.

## 7. Resolved mechanics

Formerly the open-items list; all resolved 2026-07-22.

### 7.1 Decider-agent contract

Invoked as a pure function per decision item. Context bundle (deterministically
assembled): the decision item, the session's intake corpus (requirements, plan, prior
attributed decisions), the relevant bead, and the policy constraints in force. Structured
output: `{decision, rationale, confidence, abstain}`; `abstain` routes the item to the
human. The intake corpus is a concrete, engine-readable artifact: the root issue's
description plus its `agent-context` attachment (`br update --agent-context`, inherited
by children) — "derivable from the corpus" means derivable from those fields, which
keeps the boundary checkable in decision review. **Authority is corpus-bounded:** at L2+ the decider may approve delegable
checkpoints, triage escalations (retry / re-dispatch / park), and answer needs-input
questions **only when the answer is derivable from the intake corpus** — a fact not in
the corpus goes to the human even in autonomous mode. This preserves block-don't-guess
while eliminating stops the human's own documents could answer. The decider runs as a
`[runner]`-configured agent (`decider` key, defaulting to the session's default runner).

> **Reached autonomously since `basicly-kjc5.40`.** `supervise.delegate_decisions` runs before
> dispatch in each pass, so an item the decider answers releases its lane in that same pass. It
> offers only `escalation` and `needs-input` (`DELEGABLE_KINDS`). Three kinds stay human-only:
> `checkpoint`, because that item exists *because* `policy._grant_approval` already refused and
> answering it would clear the lane's hold with the checkpoint still unapproved; `validate`,
> because a judged NO re-judged by an agent is the consensus-voting shape D9 rejects; and `stall`,
> which is a fact about a killed process rather than a corpus question. Delegation is serial (one
> reserved decider process), beats the lock between invocations, needs an L2+ grant, and stops at
> D3's spend ceiling. The corpus bound is tool-level as of `basicly-kjc5.16`.
>
> An answer now also *reaches* its lane: `build_bundle` folds the lane's own answered items into
> the dispatch prompt. Before that, both the human's answer and the decider's went nowhere — the
> lane re-dispatched with the prompt it already had and re-blocked on the same fact.

### 7.2 Supervisor lock and crash recovery

Lockfile `.basicly/usage/supervisor.lock`, created with `O_CREAT|O_EXCL` (atomic,
portable), containing PID + session id. Liveness by **heartbeat mtime** refreshed every
15 s; a lock older than 60 s is stale and taken over atomically — no PID probing (avoids
platform divergence and new dependencies). Crash recovery is derivation, not replay: the
supervisor keeps no side-state, so on start it rebuilds from `br` — re-adopts in-flight
worktrees via `external_ref` bindings and re-dispatches runs whose run-record shows no
completion.

**Lifetime: session-scoped, lazy-start.** The first client that needs a supervisor starts
it (foreground or `--detach`); it exits when the session's work is done or everything is
blocked on a human with nothing running. No repo daemon, no service management; engine
upgrades take effect next session.

**Session, defined:** one supervisor run bound to one root issue (feature or epic),
identified by the session id in the lock file. Grant expiry and `token_budget` accounting
(D3), L3 precondition counting, and supervisor lifetime all reference this definition.

### 7.3 Client attach protocol

Three layers, built in order:

1. **CLI primitives** (agent-agnostic base): `basicly loop decisions [--json]`,
   `basicly loop answer <decision-id> ...`, `basicly loop watch`, and
   `basicly loop session <root> [--json]` — the status read: lock holder plus heartbeat
   age, each in-flight lane and what it last ran, the queue depth, and grant spend
   against the D3 budget. It takes no lock and writes nothing, so any number of clients
   may attach at once, and attaching to an unsupervised root is a valid read.
2. **Notify hook**: a consumer-configured command (`[policy] notify_command`) fired on
   each new human-required decision — desktop toast, Slack webhook, anything; no default.
3. **Agent client skill**: the projected `harness-client` catalog skill, so an interactive
   session presents pending decisions conversationally and records answers via the CLI
   primitives — and knows what a client must *not* do (take the lock from a live holder,
   answer on the human's behalf, approve a checkpoint for the supervisor).

Queue items persist as `[harness-decision]` comment markers on the affected bead (same
durable, attributable pattern as `[harness-policy]` and `[harness-info]`), each with a
stable decision id; an answer is recorded in place with the answerer's attribution
(human, or decider agent/model/run-id). No side-state files; `loop decisions` is a pure
read over `br`.

### 7.4 Found-info record schema

A `[harness-info]` comment marker on the discovering bead (same durable, attributable
pattern as `[harness-policy]`), carrying JSON: `kind` (`coupling | constraint | decision
| fact`), `summary`, `detail`, `affects` (issue ids or scope globs); the engine stamps
run-id and timestamp. The supervisor folds matching records into future dispatch bundles
where scopes intersect `affects`; `kind=coupling` additionally proposes a dependency
edge. No new state store.

### 7.5 Token-usage extraction fallback

The design rule: when an adapter reports nothing, fall back to a chars/4 transcript estimate
flagged `estimated: true`, so calibration can down-weight estimated samples.

Probed live and pinned during components 1 and `basicly-kjc5.14`:

- **claude** — meters through `--output-format stream-json --verbose` (stream-json is refused
  under `-p` without `--verbose`). Cumulative tokens and cost come from the terminating `result`
  event; `context_occupancy` is the *last assistant message's* usage. The plain
  `-p --output-format json` usage block was rejected: it is session-**cumulative**, so
  `cache_read` re-counts the whole context every turn. The reader must skip unrecognized lines —
  the stream can open with a plain-text warning and carries `system` and `rate_limit_event`
  kinds, and a killed dispatch leaves a truncated tail.
- **copilot** — reports **no token counts at all** (probed 1.0.73; only `premiumRequests`), so
  copilot lanes always meter by estimate. Calibration and any cross-arm cost comparison must
  down-weight them.
- **codex** — extraction is fixture-based (`turn.completed`, with `cached_input_tokens` excluded
  as an input subset); the CLI is not on this machine's PATH, so it is unverified live
  (`basicly-0jiq`, deferred). A mismatch degrades to the estimate rather than failing.

Usage capture is opt-in per call site (`capture_usage`), and deliberately so: it switches some
adapters' stdout to JSON, which the rubric judge's plain-text line parser cannot read. The judge
and the catalog review therefore meter by estimate rather than setting the flag
(`basicly-kjc5.31`).

### 7.6 Finalize-protocol follow-up placement

Deterministic by overrun level: a **sub-task** overrun → the remainder bead becomes the
next sequential sub-task in the *same* lane worktree (every dispatch is fresh-context, so
nothing is lost). A **package-level** overrun → the lane integrates and lands what is
coherent; the remainder becomes a **new top-level package** with a fresh worktree after
the partial landing — preserving merge-queue semantics and the flatten-don't-deepen rule
(D7).

## 8. Evaluation plan

The dogfooding constraint (§1) makes measurement part of the design, not an afterthought.
Two pre-existing beads are integrated as factory components (§3 items 10–11) rather than
left as unrelated backlog:

- **Factory A/B evaluation (`basicly-7bur`).** The repeatable harness-vs-bare driver
  (fixed versioned task set with hidden objective checks; Arm H = harness with gates +
  rework ON vs Arm B = bare; cheap-model arms, strong-model judge, ≥2 agent families)
  becomes the factory's acceptance instrument. The factory enhances it for free:
  run-record token telemetry (component 1) supplies per-arm cost accounting, and a lane
  is the natural Arm H unit — the same task dispatched through a factory lane versus a
  bare headless run. Beyond per-criterion lift, it reports **cost per landed package**,
  the factory's own success metric. Per its bead, current API references
  (`rubrics.evaluate`, `policy.rework_attempts`, `RunnerSpec.model`) are reconciled with
  whatever the factory work has refactored by pickup time.
- **Skill evals (`basicly-4t9z`).** Trigger-accuracy and behavioral-lift evals per
  distributed skill. The factory adds new prose surfaces whose quality directly shapes
  every lane — the lane runbook, the agent-client skill (§7.3), decider guidance — and
  skill evals are the regression gate for that layer: a degraded skill edit is caught in
  eval, not discovered as a fleet-wide behavior change.

Sequencing: neither blocks the engine build order; both run as parallel tracks (and are
themselves good early dogfooding lanes). The A/B driver waits only for component 1;
skill evals have no prerequisite.

## 9. Implementation reconciliation

Reviewed against the code 2026-07-25. This section is the honest answer to "is the design
real?" — a decision above without a landed bead here is intent, not behavior.

**Landed.** Components 1–7 of §3, plus the hardening beads that followed:

| Component | Bead(s) | Note |
| --- | --- | --- |
| 1 Run-record token telemetry | `kjc5.1`, `kjc5.14` | claude meters via stream-json; copilot has no counts (§7.5) |
| 2 Sizing estimator + DoR governor | `kjc5.2` | estimate still drifts across invocations (`kjc5.30`) |
| 3 Autonomy grant ledger | `kjc5.3` | ledger exists; enforcement gaps below |
| 4 Decision queue | `kjc5.4`, `kjc5.17` | writes serialized; decider not yet auto-invoked (`kjc5.40`) |
| 5 Supervisor core / dispatch / routing | `kjc5.5`, `.6`, `.7`, `.18`, `.20` | client commands still open (`kjc5.8`) |
| 6 Lane mini-loop | `kjc5.9` | |
| 7 Merge queue v2 | `kjc5.10` | attribution is landing-order dependent (`kjc5.32`) |
| D4 amendment (validate has teeth) | `kjc5.19`, `kjc5.35` | every rubric now carries a deterministic check, enforced at load; a judged NO enqueues a decision and holds the lane |
| Portable worktree provisioning | `kjc5.27` | `repo_root` threaded; was resolving from process cwd |
| Process-tree kill on timeout | `kjc5.15` | |
| Judge + decider bounded and metered | `kjc5.31` | |
| Acceptance criteria required on every bead | `kjc5.36` | `br lint` never asks a chore, so the rule lives in the harness gate |

**Open, and named where the decision claims otherwise.** §3 components 8–11 remain
(`kjc5.11` process budget, `kjc5.12` release automation, `7bur` A/B eval, `4t9z` skill evals),
plus the integration test (`kjc5.21`), the dogfood run (`kjc5.22`), and the architecture
absorption (`kjc5.13`). The client surface (`kjc5.8`) is built: `loop session` observes and the
`harness-client` skill drives it.

The gaps that matter most, because a reader would otherwise believe the decision is enforced:

- **No model is pinned on any adapter**, so R5's tier economics rest on an unpinned mapping
  and dispatch is not reproducible in its inputs as D9 requires (`kjc5.29`, `kjc5.28`).
- **Coupling attribution depends on intra-pass landing order** (`kjc5.32`), which D9 forbids.

**Dogfooding status.** Every landed component was built through the single-track loop on
basicly itself, which satisfies §1's constraint for those components — but the *supervisor* path
(concurrent lanes, autonomy, the standing queue) has never run on real work. `kjc5.22` is that
test, and it is the honest gate on calling the factory done. Note also that `[policy] autonomy`
is `L0` in this repo, so the grant ladder has never been exercised here at all.
