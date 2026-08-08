# Software factory loop — requirements

## Goal

**Turn basicly from a harness with one real gate into a software factory.**

A deterministic state machine where every state has an entry condition, an exit gate, an assigned
specialist agent, and a schema-validated artifact it hands to the next state — so many lanes run
unattended in parallel, and a defect is caught at the boundary that produced it instead of at the
end.

Done looks like one sentence: **a requirement goes in, and a verified, validated, shipped change
comes out, with zero human interventions attributable to a harness defect.**

Today that sentence is false in eight places. The loop has seven phase names and one enforcing
boundary; decompose cannot express a dependency; validation cannot fail anything; a failed lane is
re-dispatched blind instead of repaired; and none of the seven designed personas exist.

### The shape

```text
  requirements
       |
    INTAKE ----> CLASSIFY ----> DECOMPOSE ----> BUILD ----> VERIFY ----> VALIDATE ----> SHIP
       |            |               |            ^  |          |            |            |
  solution-     integrity-      plan gate        |  |      does it       did we      evidence
   design         level        (before the       |  |       work?      build the      bound
                 assigned       constraint)      |  |                  right thing?
                                                 |  v
                                              REPAIR  <-- Go / Kill / Hold / Recycle
                                          (same worktree, briefed
                                           with the real findings)

  RETROSPECTIVE fires only on a special-cause signal, never on a single failure.
```

Status: **draft for decomposition**. Written 2026-08-07 from four parallel research passes and a
line-by-line gap analysis of `src/basicly/`. This document is the input to its own loop: decompose
it, build it, and dogfood the design while implementing it.

Every claim is marked **[M]** measured in this repo, **[S]** sourced with a citation, or **[D]**
a design decision taken by the owner. Unmarked prose is connective tissue and carries no authority.

---

## 1. Why this document exists

The loop is specified in prose across `docs/design/factory-design.md`,
`docs/design/agent-roster-design.md` and the `harness-loop` skill. The engine implements a
different, smaller thing. This document states the target so the delta can be decomposed.

**The measured delta** [M], from a read of `loop.py`, `loop_state.py`, `decompose.py`,
`supervise.py`, `rubrics.py`, `verify.py`, `classify.py`, `config.py`:

| Intended | Implemented | Evidence |
| --- | --- | --- |
| States with entry/exit conditions | Phases re-derived from tracker evidence; no transition table | `loop_state.py:143-189` |
| INTAKE outputs a solution design | Records one enum value | `loop.py:234-262` |
| CLASSIFY outputs a technical design | Same enum plus a section lint | `classify.py:43-56` |
| DECOMPOSE emits a dependency graph | Plan schema has no dependency field; ordering derived from scope overlap only | `loop.py:923-925`, `decompose.py:370-376` |
| VERIFY and VALIDATE distinct | Validate runs on one path only, never for leaves; its sole deterministic check is re-running verify | `loop.py:1663` vs `:331`; `task.rubric.yaml:25-28` |
| Repair in place | Supervised rework dispatches a fresh agent | `supervise.py:3036` |
| Findings reach the repair | Dispatch prompt is fixed text every attempt | `loop.py:811-823` |
| Gates at every boundary | Gates at one boundary (build→verify) | `loop.py:257-262`, `:313-314` |
| Seven personas | Zero implemented; one default runner serves every phase | `loop.py:678` |
| Retrospective | Does not exist in the engine | `harness-loop/skill.yaml:337-346` |
| End-of-loop housekeeping | Per-track teardown plus a pre-run preflight | `loop.py:409-449` |

Read plainly: **the loop is a two-state machine wearing seven labels.** Real enforcement happens
at build→verify; everything else is checkpoints and lints.

---

## 2. Decisions taken

| # | Decision | Rationale |
| --- | --- | --- |
| D1 [D] | **VERIFY and VALIDATE are two states, run sequentially** — validate is gated on verify green (amended 2026-08-07; the original decision ran them in parallel) | Separate states: ISO/IEC/IEEE 12207 §6.4.9 / §6.4.11 define them as distinct technical processes [S]. Sequential: NASA's nominal flow gives validation a *verified* product, and parallel execution spends judged tokens validating builds verify will reject [S] |
| D8 [D] | **EARS for acceptance criteria**, ratcheted — required for new criteria, existing beads transform when touched | EARS distinguishes trigger / state / condition / feature-gated / ubiquitous; GWT collapses all five, and that distinction is what makes a check derivable (OQ-2). **Do not bulk-transform** the 600+ existing beads |
| D9 [D] | **Integrity level assigned by a deterministic rule over touched paths** | Scope globs are already declared and already gated. Not judgeable, therefore not gameable, and costs zero tokens |
| D2 [D] | **Three integrity levels, keyed on blast radius** | Observable at classify time. IEEE 1012's consequence×likelihood grid needs a likelihood axis we cannot measure |
| D3 [D] | **Four gate verbs: Go / Kill / Hold / Recycle** | Cooper's Stage-Gate [S]. As written, `park` was a word every escalation offered and no answer carried out; the original rationale mis-cited that as a status fail-open at `cli.py:3922` — corrected 2026-08-08, see §5 |
| D4 [D] | **A machine-checked handoff artifact at every state boundary** | ETVX (IBM Systems Journal 24(2), 1985) [S]: exit criteria are verifiable conditions *on work products*, which requires work products to have schemas |
| D5 [D] | **Repair is a mode of the implementer, not a new persona** | Roster R3 admits a persona only if it differs in tier, tools, or artifact. Repair differs in none — only in prompt |
| D6 [D] | **Light factory / dark factory as an explicit mode split** | Capacity, not preference: one shared context window cannot hold many lanes [S] |
| D7 [D] | **File size gated as a token ratchet with a per-file waiver**, over all `.py` | See §9.3. It is an agent-context gate, **not** a code-quality gate — the quality literature argues the other way |
| D10 [D] | **Every acceptance criterion names its own check at plan time.** The plan gate refuses a criterion with no named check; VERIFY runs checks and judges nothing | Moves judgement to the earliest, cheapest point and makes it gateable. Removes MAST's *incorrect verification* mode (9.1%) by leaving nothing to get wrong at verify time |
| D11 [D] | **Deterministic diff-size downgrade**: an L3 path with a small diff and no changed public signature drops to L2 | Keeps the ungameable property of D9 while reading the actual change rather than only where it lives |
| D12 [D] | **Rework allowance is per gate**, not per unit of work — verify and validate each get their own | Matches what the counters already record; `policy.record_rework` is already keyed by gate. Needs a total ceiling so a lane cannot grind |
| D13 [D] | **Handoff artifacts are typed events in the owned ledger** | `events.py` already declares an unused `KIND_DISPATCH`; the shape is anticipated. Artifacts inherit rotation, staleness headers and `fsck`. **Consequence: `s5li` and `u4xu` become prerequisites of §8, not parallel work** |
| D14 [D] | **File-size waiver: recorded reason at L1/L2, approval at L3** | Reuses the level already computed. Closes the self-granted-waiver-on-a-consumer-surface hole without ceremony everywhere else |
| D15 [D] | **Kill always requires a human**, at every integrity level | Kill is the only verb that removes a *requirement* rather than routing work. An agent that can kill what it finds hard has an exit from every difficulty |
| D16 [D] | **The plugin is a second distribution channel**, packaging the same projected output as `basicly install` | One source of truth, two delivery shapes. Betting the primary channel on a spec with seven areas still in FUTURE_CONSIDERATIONS would be premature |
| D17 [D] | **`solution-design` is markdown with six machine-checked sections** (amended 2026-08-08 from five): problem in the requester's terms, success as an observable, a **consumer transcript**, out of scope, constraints, and **open questions** | Structured markdown is the only shape that is both readable and checkable — JSON is unreadable and prose is unactionable. The pattern is already proven twice here: the `## Plan` section and `needs-input.json`. The transcript is this repo's translation of a UI mockup: our consumer surface is a CLI, so the artifact that settles a design dispute by *showing* the surface is the command as it will be typed and what it will print (§8.1) |
| D18 [D] | **Every planned child names how it is demonstrated end-to-end.** The plan gate refuses a child that cannot | Makes D10 satisfiable by construction. A child with no consumer-visible behaviour has no check to name, which is the horizontal-slice failure — and our decomposer slices horizontally *by construction* today, because scope-glob overlap is file adjacency (§8.2) |
| D21 [D] | **Context control is field selection, not encoding.** Project tracker payloads to the fields a phase needs; encode only what remains, and only where a bijective codec is safe | Measured 2026-08-08 (§14). Selection beats serialisation by ~500x on this repo's own data |
| D22 [D] | **Anything built against the tracker is written to our own record vocabulary, never to `br`'s payload shape** | `br` and `bv` are being removed (`work-tracker.md`). A field allowlist naming `br`'s JSON keys would have to be rewritten at the flip; one naming our own fields survives it, and only the adapter changes |
| D20 [D] | **`change-shape` — the shape of the whole change, derived not authored, emitted by CLASSIFY** | See §8.2. It is the structure `decompose` needs to cut end-to-end instead of by directory, and `basicly-agzx.2` already proposes deriving it from an AST at zero token cost. **Derived, so it is not a state**: states exist to hold a gate and a persona, and a derivation needs neither — DECOMPOSE's entry predicate gains it, nothing else moves |
| D19 [D] | **Diff size is a plan-time signal, not a review-time discovery** | The sizing governor already forecasts in tokens; a child whose forecast implies a diff far past reviewable is reported when splitting is still cheap. Deliberately **not** a human-review requirement — L1/L2 stay delegable (§4), and a 2,000-line lane is hard to review whether the reader is a human or the next agent |

### 2.1 Risk accepted on D4

D4 was taken against a recommendation to prove one schema first. Six schemas is six inventions
with no prior art — the research found output contracts are **the least standardised element in
the entire field** [S]. Mitigation, which does not change the decision: **sequence
`decompose→build` first** and let the other five be built to a shape that has already survived
contact.

**Scoped 2026-08-08** [D]. The first artifact track builds **`implementation-plan` and
`change-summary` only** — the `decompose→build` pair the mitigation names, and no more. The other
four are not designed until that pair has run in anger. `implementation-plan` is the cheapest of
the six to start from because the plan gate (§3.3, shipped) already validates most of what it must
carry, so the schema is a formalisation of a live contract rather than an invention.

---

## 3. The loop

### 3.1 States

| State | Entry predicate | Exit gate | Persona | Handoff artifact |
| --- | --- | --- | --- | --- |
| **INTAKE** | a requirements artifact exists (light: produced conversationally; dark: supplied as a document) | the five `solution-design` sections validate [D17] | human (light) / none (dark) | `solution-design` |
| **CLASSIFY** | `solution-design` valid | integrity level assigned; loop depth chosen; `change-shape` derives | Juno at L2+ | `classification`, `change-shape` [D20] |
| **DECOMPOSE** | `classification` **and** `change-shape` valid; depth = decompose | **plan gate** (§3.3) | Dana | `implementation-plan` |
| **BUILD** | plan gate green **and** downstream WIP below limit | self-check green; work committed on the branch | Kai | `change-summary` |
| **VERIFY** | `change-summary` valid | deterministic gates green **and** checks derived from this unit's acceptance criteria green | none (D4 of factory design) | `verification-evidence` |
| **VALIDATE** | `verification-evidence` **green** [D1 amended] | the change exercised **as a consumer would**, against the original requirements | Vera; Remo reviews by lens | `validation-transcript` |
| **REPAIR** | verify or validate failed | Go / Kill / Hold / Recycle | Kai, repair mode | updated `change-summary` |
| **SHIP** | verify and validate green | claims bound to evidence; post-ship action pre-declared | Tala curates | `release-record` |

VALIDATE is gated on VERIFY green [D1, amended] — sequential, not parallel. NASA's nominal flow
gives validation a verified product, and a validation transcript from a build verify rejected is

### 3.2 Retrospective is not a lane state

It is a conditional process over the gate-failure ledger, triggered by a **computed** signal:

- Special cause — a point beyond 3σ, or a non-random pattern (run/trend) within limits — fires
  RETROSPECTIVE [S] NIST/SEMATECH e-Handbook §pmc31.
- Common cause — a single failure inside the limits — **does not**. Acting on it is *tampering*
  and "invariably increases variation in the results of a stable process" [S] Deming, funnel
  experiment.

This is the first mechanism in the harness that decides to **suppress** work. It is also a
correction to current practice: beads were filed off single occurrences during the session that
produced this document.

Output contract: not the why-chain. Three things — **a named control that would have refused the
defect; its tier (control / warning / documentation); and the class of defects it covers.**
Persona: Lumi. A documentation-tier outcome is recorded as a downgrade with the reason no
stronger control was available.

### 3.3 The plan gate — the highest-value single addition

Placed on **entry to BUILD**, not exit. Three independent sources converge on placing inspection
before the expensive stage:

- Shingo ranks inspection placement **source > self-check > successive** [S].
- Theory of Constraints: inspect before the constraint so it never spends capacity on already
  defective items [S].
- BUILD is where nearly all tokens go [M].

The gate rejects a plan unless every task carries: acceptance criteria in a testable notation,
scope globs, declared dependencies, a token budget, and an integrity level; and the dependency
graph is acyclic; and scopes are disjoint or declared as shared.

**And one more, added 2026-08-08** [D18]: **every child names how it is demonstrated end-to-end** —
a command to run, a request to make, or a test that exercises it through the consumer surface. A
child that cannot name one is sliced horizontally, and a horizontal child is why D10 fails: there is
no consumer-visible behaviour yet, so there is no check to derive. This is the cheapest available
check on a property that is otherwise only discovered at verify, when the tokens are already spent.

**Also reported at plan time, not refused** [D19]: a child whose forecast implies a diff far past
reviewable size. The sizing governor already forecasts in tokens, so the signal is free. It is a
report rather than a refusal because a large diff is sometimes correct — a mechanical rename is one
— and because the remedy (split it) is the author's call while splitting is still cheap.

---

## 4. Integrity levels [D2]

| Level | Scope | Gates | Tier | Rework | Ship |
| --- | --- | --- | --- | --- | --- |
| **L1 routine** | docs, comments, test-only | fast | medium | 1 | delegable |
| **L2 internal** | engine code, no consumer surface | full | high | 2 | delegable |
| **L3 consumer** | CLI, `basicly.toml` schema, catalog source schemas, generated-file contract, ledger format | full + validate-as-consumer + evidence binding | high/maximum | 2 | human |

The L3 set is not invented here — it is the five surfaces the implementation plan §9 already names
for the semver freeze.

Integrity level is the economic gate as well as the quality gate. Anthropic measured multi-agent
at **15× chat tokens** versus 4× for single-agent, with token spend explaining ~80% of outcome
variance [S]. Level decides whether a unit of work earns the factory at all.

---

## 5. Gate verbs [D3]

| Verb | Meaning | State today |
| --- | --- | --- |
| **Go** | gate green, advance | exists |
| **Recycle** | bounded rework, same worktree | exists (`retry`) |
| **Hold** | park pending a dependency; lane **not** re-admitted to dispatch | **word exists, nothing carries it out** [M] — corrected below |
| **Kill** | close as won't-do-this-way with a recorded reason; worktree torn down | does not exist |

**Correction, 2026-08-08** [M]. This table and D3 first recorded Hold as a
fail-open — "the word exists and does the opposite", re-admitting a parked lane to
dispatch, cited at `cli.py:3922`. **That diagnosis was wrong, and it aimed the fix
at the wrong layer.** The status vocabulary was never the problem: `deferred` is
excluded from `DISPATCHABLE_STATUSES` (`loop_state.py:69`, with the exclusion
argued in the comment above it), `loop_state.is_dispatchable` refuses it,
`supervise.ready_lanes` declines to hand such a lane a runner
(`supervise.py:1823`), and `SessionState.open_children` drops it — so a held lane
was never re-admitted to anything.

The real gap was one layer up: **no answer carried the verb out.** Every escalation
`supervise._capped_dispatch` raises offers `park` as a route, and the answer path
matched `retry` and `land anyway` and nothing else — so an operator who answered
`park` changed no status, and the next supervised pass dispatched the lane again.
Hold was a missing *write*, not a wrong *status*. Kill was the same shape one verb
over: the vocabulary can close a bead, and what was absent was a surface, the human
gate D15 requires, and a teardown.

Both writes now exist (basicly-u2hl.3): an answered `park` defers the lane and
records the reason (`cli._carry_out_rework_hold` → `policy.hold_lane`), and
`basicly loop kill` tears the worktree down and closes the bead behind a one-time
confirm code no grant and no TTY can substitute for (`policy.authorize_kill` →
`policy.kill_lane`). The rows above are left as written, with this correction under
them, because the wrong claim is the more useful record: it is the one that shows a
gap analysis can name the right defect and the wrong cause in the same sentence.

Kill addresses the largest single documented failure mode in multi-agent systems: **step
repetition at 15.7%** [S] MAST, arXiv 2503.13657, 1,600+ annotated traces, κ=0.88. When the only
exits are pass and retry, a lane that should be abandoned burns its rework budget instead.

---

## 6. Personas

Seven are designed [S] `docs/design/agent-roster-design.md`. **Zero are implemented** [M] — one
default runner serves every phase, `loop.py:678`.

| Persona | Role | State | Status |
| --- | --- | --- | --- |
| Dana | Decomposer | DECOMPOSE | functional equivalent exists unnamed (`loop.py:1057-1094`) |
| Kai | Implementer (+ **repair mode** [D5]) | BUILD, REPAIR | equivalent exists (`loop.py:663-700`); repair mode does not |
| Vera | Validator | VALIDATE | equivalent exists (`rubrics._judge`) but never runs for leaves |
| Remo | Reviewer, by lens | VALIDATE | **paper only** |
| Juno | Decider | CLASSIFY, escalations | exists (`decisions.py`) |
| Lumi | Retrospector | RETROSPECTIVE | **paper only** |
| Tala | Curator | SHIP | **paper only** |

Deliberately not personas [S] roster §4: merge agent, tester/verifier, scout, shipper, conductor.

---

## 7. Skills

Discipline: **encode only what a second party can check, or what a gate cannot enforce.** The
research is explicit that the rest is prose, and this repo's root-cause analysis found accreted
prose is the underlying defect.

| Skill | Invoked in | What it carries | Replaces |
| --- | --- | --- | --- |
| `decompose-plan` | DECOMPOSE | testable criteria notation, dependency declaration, budget assignment | part of `harness-loop` |
| `validate-as-consumer` | VALIDATE | run it as a consumer would, in the operational environment — never a re-run of the gate suite | nothing (gap) |
| `repair-in-place` | REPAIR | same worktree, briefed with actual findings, no re-plan | nothing (gap) |
| `root-cause` | RETROSPECTIVE | iterated-why with every link citing an observation; output is a named control + tier + covered class | the `session-finish` retro section |
| `python-guidelines` | BUILD, REPAIR | §9(B) — the non-mechanical half | nothing (gap) |

**Shipped 2026-08-08** (`basicly-u2hl.13`), and **being promoted to a path-scoped fragment** [D]:
as a model-invoked skill it loads only when an agent thinks to ask, and the agent that most needs
it is the one that does not. The core rules move to a fragment declaring `paths: ["**/*.py"]`, so
they load whenever Python is touched. It is free on Claude and Copilot and costs ~1,500 characters
on `AGENTS.md`, which has ~1,225 characters of headroom — so **one always-on line must leave
first**, which is `basicly-a3ab.1`'s audit (the no-op test on every baseline line). The skill stays
for the long-form material; the fragment carries only what must always bind.

**Not encoded, deliberately** [S]: *genchi genbutsu* as a principle (its only checkable content is
"claims carry attached evidence", which the repo already has), "make policies explicit" as
exhortation, "quality at the source" as a slogan, vendor tollgate checklists, and RPN
multiplication — deprecated by AIAG-VDA 2019 in favour of an Action Priority lookup.

---

## 8. Handoff artifact schemas [D4]

Six schemas. Each is a validated artifact the producing state must emit and the consuming state
must accept. `needs-input.json` is the existing precedent for a schema-validated handoff.

| Artifact | Produced by | Must carry |
| --- | --- | --- |
| `solution-design` | INTAKE | six sections [D17]: `## Problem` (in the requester's terms), `## Success` (an observable, not a feeling), `## Consumer transcript`, `## Out of scope`, `## Constraints`, `## Open questions` (§8.1.1) |
| `classification` | CLASSIFY | integrity level; loop depth; the gate set, tier and budget the level selects |
| `change-shape` [D20] | CLASSIFY (derived) | the call tree of what calls what; the file-tree diff of what appears and what moves; the signatures of the new public functions |
| `implementation-plan` | DECOMPOSE | per task: testable acceptance criteria, scope globs, declared dependencies, budget, integrity level; plus the graph |
| `change-summary` | BUILD | what changed and why; self-check result; the commit |
| `verification-evidence` | VERIFY | per required gate: the check, the command, the result; per acceptance criterion: the derived check and its result |
| `validation-transcript` | VALIDATE | how the change was exercised as a consumer, and against which original requirement |

**Storage** is OQ-5. `[policy.evidence]` already exists as a per-phase artifact-path gate but is
presence-only — "the engine never opens it" [M] `verify.py:243-249` — and unconfigured here.

**Resolved to a mechanism 2026-08-08, building the first pair** (`basicly-u2hl.18`). D13's
"typed events in the owned ledger" is reached **through `br.add_comment`/`br.read_comments`**,
as a `[harness-artifact]` marker, not by appending to `.basicly/ledger/` directly. Two reasons,
the second decisive:

- A new `events.py` kind would have **no writer on this rung**: the repo runs
  `[tracker] mode = "external"`. The marker seam writes on every rung and *becomes* a ledger
  `comment` event at the flip, so `u4xu` and `vkh0.23` are no longer prerequisites of §8 —
  which retires D13's stated consequence.
- A direct ledger append **would refuse the landing it precedes** [M]: the advance sweeps
  base-checkout dirt only under `.beads/` (`merge.commit_tracker_state`), and anything else
  blocks the merge (`merge.foreign_dirt`). An artifact written into the committed ledger on
  the way into BUILD would wedge the very landing it gates.

The bound this carries, measured: below `owned` the marker is one argv element, and Windows
caps a command line at 32,767 characters. The largest real decomposition here — `basicly-u2hl`,
33 children — renders a **21,890-character** plan [M], failing loudly rather than silently if a
plan ever crosses. The ceiling is the transport's and it disappears at `owned`.

The schemas are catalog sources (`.basicly/core/schemas/`), so a repo that has not installed
them runs **neither end** of the contract. Both producer and consumer resolve the schema first,
which is what keeps a skipped write from becoming a refusal downstream.

### 8.1 The consumer transcript, and why it is not a mockup

A UI product settles a design dispute with a picture of the screen. **This product has no screens**
— its consumer surface is the CLI, `basicly.toml`, the catalog source schemas, the generated-file
contract and the ledger format (§4, L3). The artifact that does the same job here is the **command
as the consumer will type it, with the output it is intended to print**:

```text
## Consumer transcript

$ basicly tracker import --dry-run
ledger 643 records, export 667
would add 24 records, 0 tombstones
```

It earns its place three times over. A reader disputes the surface before it exists, which is the
whole function of a mockup. The agent receives the exact strings it must produce rather than
inferring them. And it is **falsifiable at SHIP by a rule this repo already has** — *"exercise the
change as it will really be used — run it and read the output"* (`quality-gate` fragment) — so the
design artifact and the shipping gate check the same thing from opposite ends.

### 8.1.1 `## Open questions`, and why the sixth section exists

Added to D17 on 2026-08-08. The harness already has a block-don't-guess protocol —
`needs-input.json` — and it fires **when a lane is already blocked mid-build**. The unknowns are
therefore surfaced at the most expensive moment available: after a worktree is provisioned, after a
dispatch has started, and after tokens have been spent reaching the wall.

This section moves the anticipable ones to the cheapest moment, before CLASSIFY. It carries what
was asked and answered to reach this design, and what is still unknown together with what would
resolve it. A design whose unknowns are written down can be **reviewed** for whether they matter;
one whose unknowns are discovered at build time can only be escalated.

`needs-input.json` is unchanged and stays: a fact nobody anticipated will still be reached
mid-build, and blocking is still the right answer then.

### 8.2 `change-shape` — the seventh artifact [D20]

**Decided 2026-08-08.** The six artifacts are one per *state*. Nothing among them carried the
**shape of the whole change**: the call tree of what calls what, the file-tree diff of what appears
and what moves, and the signatures of the new public functions.

**The name is ours and the pairing is the point.** `change-shape` is what BUILD is handed;
`change-summary` is what BUILD hands back. Same noun, two tenses — shape it, build it, summarise
it — so a reader who knows one knows where the other sits. The alternatives were rejected for
reasons worth recording: *program design* names a document rather than its content and is borrowed
expression from an unlicensed source; *structure* says nothing; *surface* already means the five
L3 consumer surfaces here; *projection* is what the catalog does to a source.

This is not a gap in presentation. It is the cause of a defect we can name [M]: **`decompose`
groups children by scope-glob overlap — that is, by file adjacency — and slicing by file *is*
horizontal slicing.** "The module", "the service", "the CLI" are file clusters, and each produces a
child with no consumer-visible behaviour, which is exactly the child D18 must refuse and D10 cannot
derive a check for. The decomposer slices horizontally because scope globs are the only structure
it can see.

`change-shape` is that missing structure, and it must exist **before** the slicing it informs — so
it is emitted by CLASSIFY and appears in DECOMPOSE's entry predicate. Its relationship to
`solution-design` is neither containment nor union: `solution-design` is one per *requirement*
entering the loop, a `change-shape` is one per *decompose event*, so a leaf has one and an epic has
one at each level of its tree.

**It is derived, never authored** [D20]. `basicly-agzx.2` already proposes exactly this artifact
from an AST — tree-sitter, no model, no tokens — and its own framing is that it lets "the decomposer
declare intent and boundaries instead of enumerating files". Deciding this artifact and deciding
`agzx.2` are the same decision, and they are now taken together.

**Derived is why it needs no state.** A state exists to hold an entry predicate, an exit gate and a
persona. A derivation has no persona and cannot fail a judgement — it either parses or reports that
it could not. Adding an eighth state for it would be ceremony around a function call, and §3.1's
table would grow a row that never blocks anything.

**Independent corroboration, and its limit** [S]. HumanLayer's shipping product runs a six-phase
workflow — Questions, Research, Design, **Structure**, Plan, Implement — placing a distinct phase
between design and planning, exactly where this artifact sits. That is the same team as the essay
in §14's licence flags, so it is one source expressed twice, not two sources agreeing. It raises
the prior; it does not settle it.

---

## 9. Code quality

The owner's stated pain is module bloat. **Nothing in the stack measures it** [M]: ruff has no
module-length rule, and `C90` is not enabled. `cli.py` is **5,097 lines**; `src/basicly/` totals
**36,641**.

### 9.1 Deterministic — gate it

| Guideline | Mechanism | Status |
| --- | --- | --- |
| Cyclomatic complexity | ruff `C90`, `max-complexity` | **enabled at 15** (`basicly-u2hl.5`). The 14-at-10 figure re-confirmed 2026-08-08; ratcheting to 10 is `cli.py` ×4, `supervise.py` ×2, then one each |
| File size | **no ruff rule exists.** A script under `.scripts/` wired as a `[[verify.checks]]` fast entry — see §9.3 | **the gap.** Nothing in the stack measures it |
| Blind `except Exception` | ruff `BLE001` | **4 violations measured** 2026-08-08; adopted in `basicly-u2hl.11`. Each is a judgement about a process boundary, not a mechanical narrowing |
| Exception hygiene, perf, builtins shadowing | ruff `TRY`, `PERF`, `FURB`, `A`, `RET`, `TC`, `TID`, `DTZ` | **adopted** in `basicly-u2hl.11`. Measured 2026-08-08: `RET` 1, `TID` 1, `DTZ` 1, `A` 2, `FURB` 4, `PERF` 16, `TRY` less `TRY003` 26, `TC001/002/006` 16. **`TRY003` (442) and `TC003` (111) are deliberately ignored** with the reason recorded in `.ruff.toml` — style at scale, not a defect class |
| Security lint over `src/` | ruff `S` | **adopted for `src/` only**, per-file-ignored elsewhere so bandit keeps the trees it already scans. 25 violations less `S101`. `S101` (6,931, every one an `assert` in `tests/`) mirrors the existing bandit `skips = ["B101"]` rather than inventing a second answer |
| Type completeness | pyright `basic` → `standard` | **done** (`basicly-u2hl.10`). Exactly one error, and it was a lying annotation rather than a defect: `tracker_usage._Timer.__exit__` was `-> bool`, which declares a context manager that *may suppress*, so every name bound in the `with` read as possibly-unbound. `-> Literal[False]` fixes it; restructuring the consumer only relocates the error. **Still open**: pyright's default exclude skips `.scripts/` and `.basicly/core/`, so ~35 modules are unchecked at either mode |
| Suppression-debt ratchet | count `# noqa` per code, fail on increase | **built** (`basicly-u2hl.12`): `.scripts/check_noqa_debt.py`, wired as the `noqa-debt` `[[verify.checks]]` fast entry, frozen per code in `[tool.noqa_debt]`. **The debt figure on this row was stale twice.** It read 30; re-measured 2026-08-08 it was 46 across 20 files; re-measured again once the gate could count it, after `basicly-u2hl.11` adopted `S`/`BLE`, it is **76 across 13 codes** — `PLR0913`×32, `S603`×15, `S607`×6, `PLC0415`×5, `E402`×4, `BLE001`×3, `A002`/`ARG001`/`PLR0911`/`S105`×2, `E731`/`S701`/`UP017`×1. Every one of those arrived through a green gate, which is the argument the row was making. **Seven carry no reason at all**, against a house form of `# noqa: CODE — reason`; `unreasoned_count` ratchets them in both directions. Counting is by `tokenize` comment and ruff's own directive grammar, not by substring: `src/basicly/br.py:70` reads as a suppression, suppresses nothing, and ruff warns about it invisibly on every run — **an open defect this gate deliberately does not fail on** |

Already enforced — **do not re-propose**: line length, format, naming, Google docstrings,
`PLR0911/12/13/15`, dead code, import layering, tri-platform pyright, commented-code ban, mutable
defaults, `finally` control flow.

**A finding this table did not predict** [M, 2026-08-08]. `src/` carries **21 `# nosec` comments
that no scanner reads**, because bandit is configured over `.scripts`, `.basicly/core/hooks` and
`.basicly/core/kit` and never `src/`. One of them is
`autoescape=False,  # nosec B701` — a real XSS-class annotation nobody checks. An inert suppression
is worse than none: it reads as "reviewed" and is not, and it is invisible to the very ratchet above.
Adopting ruff `S` over `src/` is what makes those 21 sites answerable, and 21 of the 25 findings land
on exactly them.

### 9.2 Non-deterministic — the `python-guidelines` skill

The skill exists to prevent rework: an agent that discovers a violation only when the hook fails
has already spent a round. No linter can check these:

1. **Where to split a module.** If you cannot name it without "and", it is two modules.
2. **Naming quality.** `N` checks case, not whether the name describes the domain effect.
3. **Docstring usefulness.** `D` checks shape; a docstring restating the signature passes and is
   worthless.
4. **Whether an abstraction earns its keep.**
5. **Fixing the metric versus gaming it.** Extracting `_part1()`/`_part2()` satisfies `C901` and
   makes the code worse. Extract along a nameable responsibility or do not extract.
6. **`noqa` legitimacy.** Every new suppression carries a reason naming the alternative rejected.
7. **Exception design** — what to catch, what context to attach, no internal detail in
   user-facing errors.
8. **3.14 idiom selection.** PEP 750 t-strings at injection boundaries; PEP 758 paren-free
   `except A, B:` — pick a house direction, no linter enforces either [S].
9. **Free-threading safety** (PEP 779): stop assuming GIL atomicity. Not mechanically checkable.

Test quality is **out of scope** — `test-discipline` already owns it.

### 9.3 The file-size ratchet [D7]

**Metric: tokens, not lines.** Lines drift with docstring density and comment ratio. Tokens are the
unit the sizing governor already runs in (`decompose._text_tokens`), so one constant serves both.

**Threshold: `SCOPE_FILE_READ_CAP = 4_000`** — an existing committed constant whose own comment
says it is "where the whole-file band ends", i.e. the point above which an agent stops reading a
file whole and starts reading selectively. Measured at this repo's median of 10.64 tokens/line,
that is ≈376 lines [M].

**Design:**

- **Ratchet, not hard cap.** No file may cross the threshold. A file already over may only shrink.
- **Top-level imports are not counted** [D, amended 2026-08-08]. As first shipped the ratchet
  counted them, and that charged for the one change it exists to force. Measured on the first lane
  after go-live: extracting `contention` out of `supervise.py` — a real split along a named
  responsibility, taking a frozen 48,020-token module under its baseline — forced one
  `from . import contention` line into `cli.py`, and that **four-token line failed `cli.py`'s own
  ratchet**. Splitting a large module therefore required shrinking every module that imports it, so
  the cheapest way to satisfy the gate was to split nothing. The exclusion is the narrowest fix: an
  import is one line and is not what makes a file too large to read whole, and code growth is still
  measured to the token. The 79 frozen baselines were recomputed once on the same measure — every
  one strictly dropped — so nothing is forgiven but the import block. The control assertion is
  `test_a_module_that_grew_by_code_still_fails_after_the_import_exclusion`; without it the
  amendment cannot be told apart from turning the gate off, which is exactly how it first *looked*
  to pass: run against import-inclusive baselines it reported a clean tree, because every module
  had silently gained an allowance the size of its own import block.
- **First touch brings it under, below 2× the cap** [D, amended 2026-08-08 — OQ-12 resolved].
  The first change to a frozen file **under 8,000 tokens** must bring it under the cap, not merely
  reduce it: one extraction reaches 4,000 from there, so the rule is payable by whoever touched it.
  **At or above 2× the cap the obligation is only not to grow.** Such a module comes down on a
  decomposition track of its own rather than as a toll on the next person to edit it — the failure
  mode OQ-12 named, and the one measured on 2026-08-08 when a repo-wide lint adoption put 18 modules
  over at once and the strict reading would have required decomposing `cli.py` (54,362), `runner.py`
  (32,295) and `supervise.py` (48,020) before a lint family could be enabled.
- **Per-file waiver** [D]. A module that is genuinely cohesive may exceed the cap deliberately,
  carrying a one-line reason in the file. Waivers are themselves ratcheted — the count may not
  grow silently — following the pattern already used for the vulture ignore list.
- **Scope: all `.py`** [D] — `src/`, `tests/`, `.scripts/`, `.basicly/core/`.

**Measured cost at go-live** [M]:

```text
src/basicly     50 files    409,323 tok   23 over cap   worst 53,095  (13x)
tests           95 files    596,347 tok   42 over cap   worst 60,235  (15x)
.scripts         8 files     32,009 tok    3 over cap
.basicly/core   26 files    100,916 tok   10 over cap
TOTAL          179 files  1,138,595 tok   78 frozen
```

Note tests are the larger half and hold the worst offender; they will **not** fall out of a `src`
refactor as a side effect.

**Justification — and what it must never claim.** This is an **agent working-set gate**. The
support is that LLM resolve rates degrade with context length even under perfect retrieval, and
that successful SWE-bench trajectories cluster well under the threshold [S]. Stated honestly: no
study isolates *file size* against edit success — that is plausible mechanism, not measurement.

The defect-density literature must **not** be cited in support, because it argues the other way:

- Hatton 1997 (IEEE Software 14(2)) found a U-shaped curve with **mid-size components best** and
  *very small* ones worse, faults migrating to the interfaces [S].
- Koru et al. (Empirical Software Engineering 2008/2010) found a monotonic power law where
  **smaller modules are proportionally more defect-prone**, with no upturn [S].
- Ousterhout's "classitis": many shallow modules accumulate interface complexity; depth beats
  smallness [S].
- No style guide prescribes a file length. Tool defaults span 300–2000 — a 6.7× spread, which is
  itself evidence nobody measured anything [S].

That our 4,000-token threshold lands inside Hatton's 200–400 LOC minimum is **coincidence**. It
must not be presented as corroboration.

### 9.4 Test file naming

One test file per source module, enforced by name:

- `test_<module>.py` — the default.
- `test_<module>_<aspect>.py` — when one module's tests justify a split.

Measured today [M]: 48 source modules, 84 test files — 41 exact matches, 43 following the derived
form, and every source module without an exact match is covered under a derived name. The
convention is already emergent; the gate makes it binding.

---

## 10. Specification conformance

| Spec | Verdict | Action |
| --- | --- | --- |
| **AGENTS.md** — MIT | **PASS** [M]. No fields, no schema, no validator; it is plain Markdown by design | None. Do not invent structure to conform to a spec that has none |
| **Agent Skills** — Apache-2.0 / CC-BY-4.0 | **3 failures** [M]: `tool-bat`, `tool-fzf`, `tool-git-delta` on the Claude root have no `description`, a required field | The `.agents` root already synthesises one for the same skills — apply the existing behaviour to both roots |
| **Agent Plugins 1.0.0** — CC-BY-4.0 / Apache-2.0 | Total gap by absence: no `plugin.json`, no `skills/` at plugin root | Emit a conforming plugin package |

### 10.1 The `metadata` field

A map of string keys to string values; **no size cap, no reserved keys, arbitrary custom keys
explicitly permitted**; one recommendation — keep key names unique [S]. `skills.py:106-148`
already validates and round-trips it [M], and **no skill uses it**. Free capacity for loop
automation:

```yaml
metadata:
  basicly-loop-state: verify
  basicly-integrity: L3
  basicly-persona: vera
  basicly-contract: contracts/verification-evidence.md
  basicly-source-version: "0.9.0"
```

**Abuse to avoid**: non-string values; instructions the agent must obey (hosts may never show
metadata to the model, so semantics placed there vanish silently); duplicating defined fields;
large serialized blobs.

### 10.2 Do not build these

`FUTURE_CONSIDERATIONS` lists seven items the plugin spec is considering but has **not**
standardised: permissions/approval, provenance/signing, secrets, enterprise controls, **audit
trail**, dependencies, **testing/validation** [S]. Our integrity levels and gate evidence overlap
four. Encode them in skill `metadata` or a plugin `extensions` namespace — never as invented
top-level manifest fields — so migration is a rename rather than a redesign.

---

## 11. Capabilities the state of the art has and we lack

From MAST's failure taxonomy and Anthropic's published engineering, ranked by documented failure
mass [S]:

1. **Acceptance-criteria-derived verification.** MAST attributes **23.5%** of failures to
   verification itself — 8.2% none/incomplete, **9.1% incorrect**; adding objective-level
   verification gained **+15.6 points**. A generic gate suite verifies the repo, not the claim.
2. **Termination-condition and step-repetition detectors.** MAST's two largest single modes:
   step repetition 15.7%, unawareness of termination conditions 12.4%. Both are trace-level
   detectors an engine with a dispatch ledger can implement cheaply.
3. **A stated recursion policy for lane agents.** Claude Code permits nesting to depth 3 and
   withholds the Agent tool at the leaf so delegation bottoms out in work. A lane on a host that
   permits nesting can fan out inside itself, unmetered and outside the supervisor's model.
4. **Fresh-context adversarial review** distinct from gate-running — with the documented
   counter-warning adopted verbatim: a reviewer prompted to find issues **will** manufacture them.
   Bound it by severity and require findings to name a failing input.
5. **Drift detection** between tracker artifacts and code — the dominant reported failure of
   spec-driven approaches, and this repo has two recorded incidents of release claims
   contradicting code.
6. **An eval harness over our own catalog.** 8 of 34 skills have ever been exercised [M]. Dead
   definitions are unfalsifiable claims in our own catalogue.
7. **Evidence binding at SHIP** as a separate pass — the writer of a claim is the wrong context
   to audit it.

---

## 12. Observability and the two factory modes [D6]

**Measured** [M]: the harness already defaults claude dispatch to `--output-format stream-json
--verbose` (`runner.py:278`) and reads it line by line (`runner.py:1180`). It spends the stream
entirely on token accounting. `--forward-subagent-text` — verified present on Claude Code
2.1.224 — is **passed nowhere**.

| | Dark factory (`claude -p`) | Light factory (one session, built-in subagents) |
| --- | --- | --- |
| Task state | per subprocess, isolated | shared across subagents and parent |
| Progress | stream exists, unused | live, each event linked to its spawner |
| Permissions | pre-approved | prompts reach the human |
| Context | one window per lane | **one window shared by everything** |

The last row is why light cannot replace dark: many lanes cannot share one context window. Light
is for few lanes with a human present. INTAKE is inherently light — by definition it cannot run
without a human unless a requirements document is supplied.

Three items, cheapest first, none requiring headless to be abandoned: surface the stream already
read; pass `--forward-subagent-text`; add light mode as a second dispatch path.

---

## 13. Open questions

| # | Question | Blocks |
| --- | --- | --- |
| ~~OQ-1~~ | ~~AC notation~~ — **resolved**: EARS, ratcheted (D8) | — |
| ~~OQ-2~~ | ~~Deriving checks from criteria~~ — **resolved**: each criterion names its own check at plan time (D10) | — |
| ~~OQ-3~~ | ~~Verify fails, validate passes~~ — **resolved**: sequential, validate gated on verify green (D1 amended) | — |
| ~~OQ-4~~ | ~~Who assigns integrity level~~ — **resolved**: deterministic path rule (D9) | — |
| ~~OQ-13~~ | ~~L3 over-classification~~ — **resolved**: deterministic diff-size downgrade (D11) | — |
| ~~OQ-14~~ | ~~Rework allocation~~ — **resolved**: per-gate allowance (D12) | — |
| ~~OQ-5~~ | ~~Artifact storage~~ — **resolved**: typed events in the owned ledger (D13) | — |
| ~~OQ-6~~ | ~~File-size threshold~~ — **resolved**: 4,000 tokens, `SCOPE_FILE_READ_CAP` (§9.3) | — |
| ~~OQ-7~~ | ~~Exemption list or deadline~~ — **resolved**: ratchet, first touch brings the file under cap, per-file waiver with a recorded reason (§9.3) | — |
| ~~OQ-11~~ | ~~Waiver approval~~ — **resolved**: reason at L1/L2, approval at L3 (D14) | — |
| ~~OQ-12~~ | ~~What is a "touch"~~ — **resolved 2026-08-08 in two parts**: an added top-level import is not a touch (the ratchet was charging for the splits it exists to force), and the bring-it-under obligation applies only below 2× the cap. **No open questions remain** | — |
| ~~OQ-8~~ | ~~Kill approval~~ — **resolved**: human at every level (D15) | — |
| ~~OQ-9~~ | ~~PEP 758 house direction~~ — **resolved 2026-08-08**: paren-free `except A, B:` is the house form, recorded in the `python-guidelines` skill rather than in a linter, since none enforces either direction | — |
| ~~OQ-10~~ | ~~Plugin channel~~ — **resolved**: second channel, same projected output (D16) | — |

---

## 14. Context control [D21, D22]

**The owner's framing, and the measurement that redirected it.** The proposal was to put only the
needed tokens in the window and to use the format that costs least — with a hypothesis that XML
stores more per token than JSON, and a requirement that any transformation be **deterministic in
both directions with zero semantic loss**. The goal was explicitly *not* a smaller context for its
own sake, but a more effective factory.

The measurement supports the goal and refutes the mechanism.

### 15.1 The format hypothesis is refuted [M]

Measured with `tiktoken` `o200k_base`, cross-checked against `cl100k_base` (agreed within 1%), on
real payloads from this repo. **XML never won a single payload at any size** — it cost 1.07x to
1.80x compact JSON. The reason is the hypothesis inverted: JSON names a key once per record
(`"k":`), XML names it **twice** (`<k>…</k>`). XML buys unambiguous nesting, never density.

Winner by *shape*, because the shapes disagree: record-shaped and scalar-heavy → tabular (0.54x);
record-shaped and prose-heavy → tabular, but the win collapses to 0.94x; tree-shaped → compact JSON
or YAML, tied; prose-shaped → not re-serialisable at all.

**The empirical kill-shot** [M]. `br` already ships a "token-optimized object notation". On this
repo's real ready-queue:

```text
br ready --format text    10,489 chars    3,075 tok    2.0% of a p50 lane
br ready --format json   223,961 chars   55,751 tok   36.9%
br ready --format toon   225,768 chars   57,323 tok   37.9%   <- 2.8% WORSE than json
projected to 5 fields      9,613 chars    2,507 tok    1.7%   <- 22x
```

The purpose-built optimized format **lost to plain JSON on real data**. The human text format,
which nobody calls optimized, is 18x cheaper for one reason: it prints fewer fields.

### 15.2 Where the waste is [M]

A p50 lane occupies **151,099 tokens** (n=79 recorded dispatches). **basicly authors 3,812 of them
— 2.52%.** Re-serialising every byte we control into the best measured format saves **1.01% of one
lane**. Meanwhile `br ready --json`, which our own `tool-br` skill instructs every agent to run,
costs **36.9% of a lane**, and projecting it to the five fields a lane needs costs 1.7%.

**Selection beats serialisation by roughly 500x here**, which is why D21 is stated as it is.

### 15.3 The frame is serialization, not compression [D]

Compression presupposes a decompressor and a consumer that need not understand the compressed form.
Here **the model is the consumer and reads the wire format directly** — there is no decode step. So
the risk is not "can I invert it" (bijective codecs invert trivially, and one is round-trip-gated
against this repo's own 682 beads, 3,775 events and 297 run-records) but **"does the model read it
as accurately"**.

That risk is documented and it is severe [S]: reformatting identical content plaintext→JSON moved
HumanEval on GPT-4-32k from **76.22% to 21.95%** (arXiv 2411.10541), and cross-model overlap below
0.2 means a format tuned on one model does not transfer. A dense tabular form scored **0.0%** on
deep-nested *generation* where JSON scored 18.6%. So a codec is confined to **uniform record arrays
the model only reads** — never nested data, never anything the model must emit — and ships only
after an accuracy A/B whose intervals are reported.

**Encryption is a red herring, and it was checked rather than assumed**: `redact.py` and the
secret-scan hook perform *sanitisation on the way out*, which is the opposite requirement.

### 15.4 Two things that must be said about the naive win [M]

**The 0.54x tabular winner is not lossless.** It destroys `null` vs `""`, `int` vs `str`, `bool` vs
`str`, embedded tabs and newlines, and absent-key vs null-key. A bijective encoding costs ~6% more
and round-trips; the six loss modes become a committed adversarial fixture.

**Projection is a filter, not a codec, and must be labelled so.** It is deliberately lossy by
design. Only the codec carries a zero-semantic-loss claim; conflating the two is how that claim
would come to be made falsely.

### 15.5 Caching changes the economics [S]

Cache reads cost **0.1x** base input, writes 1.25x–2x. So compressing a *stable* prefix saves 40%
of a 0.1x line item, and a *dynamic* compression breaks the prefix and converts 0.1x reads into
1.25x writes. **Compression and caching are substitutes and caching wins by an order of magnitude
on stable text.**

This makes one defect the gate on everything else [M]: `runner_usage.claude_json_usage` never
populates `cache_read_tokens`/`cache_write_tokens`, though `runner_usage.py:177` populates them for
codex. **0 of 297 dispatch records carry a cache split.** Until that lands, a 40% apparent saving
that actually broke a cached prefix is indistinguishable from a real win, on the agent that does 76
of 77 dispatches.

### 15.6 The context ceiling is deleted, not retuned [D]

`DEFAULT_CONTEXT_CEILING = 0.6` against a declared 1,000,000 window is 600,000, and **0 of 79
recorded lanes cross it** (max observed 403,051). Against the stale hardcoded 200,000 it was
120,000 and **51 of 79 crossed**, which is where the twelve overrun follow-ups came from. A gate
that has never once fired correctly — first at a fifth of its intended point, then never — is
removed rather than given a third constant. Its follow-up machinery goes with it.

---

## 15. Sources

Institutional and primary, fetched directly: Kanban Guide (kanbanguides.org), Kanban University,
lean.org lexicon (jidoka, andon, error-proofing, heijunka, continuous-flow, pull-production,
genchi-genbutsu), Scrum Guide 2020, NIST/SEMATECH e-Handbook, Toyota UK, deming.org, PEP 8/20,
docs.python.org What's New 3.12–3.14, Astral ruff rules index, pyright configuration,
agents.md, agentskills.io/specification, agent-plugins-spec 1.0.0 and FUTURE_CONSIDERATIONS,
Anthropic engineering (multi-agent research system, building effective agents), Claude Code docs.

Paywalled, corroborated via named secondary sources: ISO/IEC/IEEE 12207:2017, 15288:2023,
29148:2018, IEEE 1012-2024, ISO 13053, ASQ (DMAIC, FMEA, fishbone, Pareto — all returned 403).

Academic: MAST failure taxonomy (arXiv 2503.13657, CC BY-NC-ND — **cite, do not reuse**);
Card, *BMJ Quality & Safety* 2017 on the limits of iterated-why; Landman et al. 2016 and
El Emam et al. 2001 on complexity metrics versus size.

Books without institutional URLs: Ohno *Toyota Production System*; Shingo *Zero Quality Control*;
Goldratt *The Goal*; Reinertsen *Principles of Product Development Flow*; Radice et al., ETVX,
IBM Systems Journal 24(2), 1985; Cooper, Stage-Gate, *Business Horizons* 33(3), 1990.

**Licence flags**: `anthropics/skills` is per-folder, and its document skills are
source-available, **not** open source. `awesome-claude-code` and the MAST paper are CC BY-NC-ND —
no commercial reuse, no derivatives.
