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

The loop was specified in prose across `factory-design.md` and `agent-roster-design.md`, both
absorbed into this document and deleted 2026-08-08, and the `harness-loop` skill. The engine
implements a
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
| D23 [D] | **A sizing control with no recorded correct firing becomes observability; a control that has earned one keeps its teeth** | §15.7. Measured 2026-08-08: the grant spend ceiling fired correctly 5 times and the rework cap 78, while the runner timeout, the working-set band and the context ceiling have **zero** between them — and all three of those predict how large a unit of work will be, which this repo has never predicted well. A prediction that blocks must be right; a prediction that reports costs nothing when it is wrong. Demotion is not deletion: the number stays recorded, surfaced and falsifiable, because §15.6's gate was wrong for months *with the telemetry already contradicting it* |
| D24 [D] | **`factory-design.md` is no longer the tiebreaker.** Authority runs: measured evidence in this repo → this document → `factory-design.md` | Owner, 2026-08-08. That document's §9 — "the honest answer to *is the design real?*" — contradicts itself on `kjc5.8`/`kjc5.11`; it keeps a context ceiling §15.6 deleted for never firing correctly; and its D6 rests on light mode having "one window shared by everything", which architecture §5 records as **isolated** context (the citation also had the wrong section: it is §1 of that document, now absorbed). A factory-design decision no measurement contradicts still stands — this removes tiebreaker status, not content |
| D25 [D] | **Agent-authored guidance never reaches the shared catalog without a human, at any grant level** — a decision class no autonomy level auto-disposes, an exception to the L0-L3 ladder rather than a rung in it | Roster R9, absorbed 2026-08-08. The argument is asymmetry, not the risk of a bad suggestion: a wrong implementation bounces off a gate, while a wrong fragment is **absorbed** and silently degrades every later lane with nothing mechanical to detect it. An agent that can amend the catalog under a grant widens its own constraints, and the next session inherits the widening as ground truth. Not in code [M]: `supervise.DELEGABLE_KINDS` is `("escalation", "needs-input")` (`supervise.py:1650`), so a never-auto-dispose class does not exist. Corollary: a retrospective's output is a **diff against catalog YAML**, never prose advice, so `catalog lint` and the projection checks bound what the human is asked to approve |
| D26 [D] | **Route each role to the cheapest tier that can be relied on, priced per landed package** — total tokens, wall clock and human interventions per landed *correct* package, never the price of one dispatch. The predicate for "cheap is safe" is **specification completeness, not work category** | Roster R5 and its 2026-07-26 amendment, absorbed 2026-08-08. A brief carrying the literal code and the literal test cases is transcription and is mechanically verifiable; a brief that is a prose description is not. A cheap dispatch returns as rework, extra review cycles, bounced merges and human attention, all charged to the same package. **Operationally a dispatch with no resolved tier is a bug, not a default** — an omitted model inherits the session's, usually the most expensive, which defeats the rule silently. The four-tier ladder is already shipped (`.basicly/core/models/anchors.yaml`, `schema.MODEL_TIERS`); only this routing rule was unrecorded |
| D20 [D] | **`change-shape` — the shape of the whole change, derived not authored, emitted by CLASSIFY** | See §8.2. It is the structure `decompose` needs to cut end-to-end instead of by directory, and `basicly-agzx.2` already proposes deriving it from an AST at zero token cost. **Derived, so it is not a state**: states exist to hold a gate and a persona, and a derivation needs neither — DECOMPOSE's entry predicate gains it, nothing else moves |
| D19 [D] | **Diff size is a plan-time signal, not a review-time discovery** | The sizing governor already forecasts in tokens; a child whose forecast implies a diff far past reviewable is reported when splitting is still cheap. Deliberately **not** a human-review requirement — L1/L2 stay delegable (§4), and a 2,000-line lane is hard to review whether the reader is a human or the next agent |
| D27 [D] | **Thin engine: the catalog defines, the host executes.** We author agents, skills and hooks as catalog sources and project them to both families; the host runtime spawns the subagent, resolves its tier and fires the boundary hooks. The engine supervises *lanes*, owns the tracker, the gates and the merge queue | Owner, 2026-08-09, against measured host capability rather than the 2026-08-07 assumption that we must build dispatch. Both installed runtimes ship what §6.3 said we had to build: claude 2.1.226 takes `--agents <json>` and `--append-subagent-system-prompt`; copilot 1.0.78 carries `subagents.agents.<name>.{model,effortLevel,contextTier}`. Reimplementing a shipped mechanism is the reuse-before-reinventing rule inverted |
| D28 [D] | **§6.4's Conductor refusal is amended, not dropped: an agent may spawn only a role the engine authored, and every boundary is gated by a host hook** | The refusal's target was an agent inventing unmetered helpers (§11.3), not delegation as such — and both hosts spawn subagents natively, so a blanket ban is unenforceable prose. The amended form is *stronger*: copilot hooks can intercept a subagent finishing **before its results return to the parent**, and claude's `--include-hook-events` puts hook lifecycle on the stream, so the DSM boundary becomes a runtime gate instead of a process boundary we hope holds |
| D29 [D] | **Spend caps compose: our grant ceiling is the outer bound, the host's own cap is the inner one**, derived per dispatch from the lane's remaining budget | The grant ceiling has 5 recorded correct firings (§15.7) and stays. What it cannot do is stop a *subagent* mid-flight — it only refuses the next dispatch. `claude --max-budget-usd` counts subagent spend and stops background subagents (v2.1.217+); `copilot --max-ai-credits` is shared by a session's subagents. Note copilot's is explicitly a **soft** cap — usage is known only after a response returns — so it bounds, it does not guarantee |
| D30 [D] | **A provider model id never appears in an agent file, generated or not.** The source declares a tier; the id is injected at spawn | Owner, 2026-08-09. Not style: our own tier kit records that *"a definition that pins its own `model` is left alone"*, so a projected `model:` line **disables** tier injection rather than implementing it — which is what `basicly-a3yi`'s projection plan would have shipped. Verified the constraint is satisfiable on both families: claude injects the alias at spawn via the hook or `--agents <json>`; copilot 1.0.78 carries the model in **config** (`subagents.agents.<name>.model`), outside the `.agent.md`. The kit's note that copilot is frontmatter-only is stale as of 1.0.78 |
| D31 [D] | **A tier resolves by declared vendor order, verified at install.** `anchors.yaml` gains a `vendor_order` per tier; resolution walks it and takes the first the map marks available for the surface in effect; `basicly install`/`upgrade` probes each chosen model once and records a rejection | `model-map.json` already resolves tier→vendor→surface and already refuses to substitute another tier's model. Two gaps closed: nothing ranked vendors *within* a tier, and `status: available` is a claim from the generator rather than this consumer's entitlement. Neither host lists its models non-interactively (verified: claude has no `models` subcommand; copilot has `--model`/`auto` and BYOK env vars only), so entitlement must be probed once, not queried per dispatch — which keeps the dispatch path offline and deterministic |
| D32 [D] | **A handoff artifact is a file on the work's own `harness/<issue>` branch, deleted at teardown; the ledger keeps its kind, digest and gate verdict** | Owner, 2026-08-09, superseding §8's marker-only mechanism. Git is the only transport this design has, so an artifact that must survive a machine hop has to be committed — which rules out a gitignored directory. Committed on the branch it is not dirt, so `merge.foreign_dirt` (`merge.py:469`) is unaffected; deleting the branch is the delete, so `main` never carried it. **Consequence: the harness branch must be created at INTAKE**, not at worktree provisioning (`loop.py:327`), because INTAKE, CLASSIFY and DECOMPOSE all emit artifacts before any worktree exists |
| D34 [D] | **The comments rule is the divergence rule, and it lives in `python-guidelines`, not in the always-on layer.** A comment that contradicts the code is a defect and the code is what ships; deleting the comment is not the fix. The proposed strong form — "comments that describe the code must not exist" — is **rejected** | Owner, 2026-08-09, choosing against their own initial framing on the measurement. Four independent grounds, any one sufficient. (1) **It targets an empty set here**: a 120-block hand sample over `src/basicly/` and `.basicly/core/` classifies 41% contract, 40% why, 16% navigation, 3% directive, **0% narration**, and two whole-population probes each validated against synthetic narration return 0 narration-opener hits and 9 code-echoing blocks of 1,139, every one a cross-reference [M]. (2) **Its strong form contradicts PEP 8**, which *mandates* a describe-what comment for non-public methods; Google's "never describe the code" — which `.ruff.toml` already pins via `convention = "google"` — is stated immediately after a *requirement* to comment complicated operations. (3) **It arms a live gaming path**: stripping standalone comments returns 36.3% of `config.py`'s §9.3 ratchet tokens, 17.0% of `merge.py`'s and 11.6% of `loop.py`'s [M], and `python-guidelines/skill.yaml:72` already names comment deletion as the way to game that gate — an always-on rule licensing it authorises it on every lane. (4) **No budget**: `AGENTS.md` is 13,135 characters against `codex.yaml`'s 12,000 cap, and the parent epic `basicly-a3ab` exists to *relieve* the always-on layer. Also **not agent-actionable**: "outside of best practices" is an undefined exemption and "if you need to read the comments" is a counterfactual about a reader the agent cannot query — neither is falsifiable, while divergence is checkable against an observation. The literature does not settle it either way: "comments are always failures" traces to *Clean Code* ch.4, a trade book with no cited study, and the measured work is mixed — Nielebock et al. 2018 (n=277) "the real effect of comments on software development remains uncertain", and a 2026 eye-tracking study (n=20) spans a 30% decrease to a 34% increase [S] |
| D35 [D] | **`python-guidelines` stays a skill and gains `paths: ["**/*.py"]`; it is not demoted to an always-on fragment** | Owner, 2026-08-09, re-taking §7.2's demotion plan because the premise under it is false. That plan rested on "as a model-invoked skill it loads only when an agent thinks to ask" — refuted at claude 2.1.226, where a skill's frontmatter takes a `paths:` glob that limits *and triggers* automatic activation [S, vendor doc, fetched 2026-08-09]. The glob buys the same always-loads-on-`.py` behaviour at **zero** always-on characters, and it unblocks the work from `basicly-a3ab.1`'s eviction, which the fragment plan was waiting on. **The gap it does not close is codex**: it has no glob-based instruction scoping and never loads a nested `AGENTS.md` below the cwd (architecture §7.4), so the fragment remains the only mechanism there — deferred until codex has headroom, rather than paid for now on all three families |
| D36 [D] | **Skill frontmatter gains a per-target vendor fence; the portable six stay portable.** An unportable key is declared once under its target and emitted only into the roots that understand it | Owner, 2026-08-09, resolving D35's mechanism. `skill.schema.json` carries exactly the Agent Skills portable subset — `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools` — with `additionalProperties: false`, and `paths:` is outside it [M, vendor doc, 2026-08-09]. Putting `paths:` at top level would make every projected `SKILL.md` unportable to buy one behaviour; refusing it leaves `python-guidelines` with no trigger, which is the gap D35 exists to close. The fence takes neither cost, and `agent.schema.json` already establishes the shape so this is a second use of an existing pattern rather than a new mechanism. **The general rule it settles**: a host-specific capability is expressible without the portable artifact absorbing it, so the next such key does not re-open this decision. `.agents/` gets the six; `.claude/` gets the six plus its fenced keys |
| D37 [D] | **The agent-hook event vocabulary widens to the events we can name a consumer for, and a stage lands with the catalog source that uses it** | Owner, 2026-08-09. `claude_settings.py:51` maps **2 of the 31** documented host events, which is why no catalog source can name a `stop` stage and why §11 item 8 has no engine to bind to. Widening to all 31 was refused on the argument this document already makes about dead definitions — 29 stages with no consumer is the 8-of-34-skills problem in a second place, and each is a surface to keep true against a vendor that moves. The pairing rule is what stops that: a stage is added by the change that consumes it. Two consumers exist today — §11 item 2's in-dispatch termination gate (`Stop` + `decision: block`, probed reachable under our own `claude -p`, capped at 8 consecutive blocks, OQ-16) and `basicly-0p8n`'s tool-call-boundary enforcement. **Claude-only**: copilot accepts `preToolUse`/`postToolUse` and nothing else (`hooks.py:55`), so a widened stage projects to one family and the parity gap is declared rather than silently uneven |
| D33 [D] | **`docs/` carries only architecture, tutorial, how-to and a contributor guide.** No new requirement or plan document is ever created as a file; a new requirement enters as `01-solution-design.md` on a branch | Owner, 2026-08-09, making §9's register mechanical instead of disciplinary. The four existing requirement/plan documents exit on the triggers already recorded there; the review's Appendix A moves to architecture rather than being deleted, because a licence and provenance register is a decision record. A `docs/` path gate makes the rule a free deterministic check, which the standing constraints already prefer over a judged one |

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

| State | Entry predicate | Exit gate | Role | Handoff artifact |
| --- | --- | --- | --- | --- |
| **INTAKE** | a requirements artifact exists (light: produced conversationally; dark: supplied as a document) | the five `solution-design` sections validate [D17] | human (light) / none (dark) | `solution-design` |
| **CLASSIFY** | `solution-design` valid | integrity level assigned; loop depth chosen; `change-shape` derives | `decider` at L2+ | `classification`, `change-shape` [D20] |
| **DECOMPOSE** | `classification` **and** `change-shape` valid; depth = decompose | **plan gate** (§3.3) | `decomposer` | `implementation-plan` |
| **BUILD** | plan gate green **and** downstream WIP below limit | self-check green; work committed on the branch | `implementer` | `change-summary` |
| **VERIFY** | `change-summary` valid | deterministic gates green **and** checks derived from this unit's acceptance criteria green | none (D4 of factory design) | `verification-evidence` |
| **VALIDATE** | `verification-evidence` **green** [D1 amended] | the change exercised **as a consumer would**, against the original requirements | `validator`; `reviewer` by lens | `validation-transcript` |
| **REPAIR** | verify or validate failed | Go / Kill / Hold / Recycle | `implementer`, repair mode | updated `change-summary` |
| **SHIP** | verify and validate green | claims bound to evidence; post-ship action pre-declared | `curator` | `release-record` |

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
Role: `retrospector` (§6). A documentation-tier outcome is recorded as a downgrade with the reason no
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

**And one more, added 2026-08-08, shipped `basicly-u2hl.20`** [D18]: **every child names how it is
demonstrated end-to-end** — a command to run, a request to make, or a test that exercises it
through the consumer surface. A child that cannot name one is sliced horizontally, and a horizontal
child is why D10 fails: there is no consumer-visible behaviour yet, so there is no check to derive.
This is the cheapest available check on a property that is otherwise only discovered at verify, when
the tokens are already spent.

As shipped the field is refused on two grounds — absent, and present but naming nothing runnable —
where "runnable" is a backticked span, the same machine-readability rule a `## Scope` glob already
carries. It binds on the **proposed** plan only. Every child recorded before the field existed
carries a `## Plan` heading and no demonstration line, so the build-entry predicate cannot tell that
population from a defect, and a predicate that refuses the whole tracker is a stopped harness rather
than a bound one (the §9.3 ratchet argument, applied to a field instead of a file).

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

### 5.1 Gate types — what a failure does [D]

Absorbed 2026-08-08 from `gates-and-rework-design.md`, which is deleted. The four verbs say what a
gate *answers*; four **types** say what its failure *does*. `policy.GATE_TYPE_BY_GATE` types the
five gates the engine names and defaults the rest to revision. Two rules govern a new one:

- **Selection.** Start at pre-flight. A check that runs *after* work is produced is a revision
  gate; one the revision loop cannot resolve escalates; one where continuing is dangerous aborts.
- **Cap sizing.** A cap reflects the cost of one iteration. A landing bounce and a re-review of a
  three-line fix must not share a budget.

The gates the engine gives no name have nothing to key on, so they are classified here rather than
in code — `policy.py:88-92` delegates to this table:

| Unnamed gate | Type |
| --- | --- |
| Scope-disjointness at decompose | Pre-flight |
| `fast` / `full` gates at sub-task and lane integrate | Revision |
| Merge-queue bounce-back | Revision |
| commit-msg / secret-scan / projection checks | Pre-flight |
| Ship preconditions | Pre-flight |
| `needs-input.json` | Escalation — the engine's escalation gate, still un-named as one |
| Uncommitted work blocks a landing | Abort |

**Corrected by D23.** The band ceiling was classified as a pre-flight refusal at dispatch and it
still refuses (`working_set.py:41`). With **zero correct firings** it is observability under §15.7,
so it types as no gate at all until it earns one.

---

## 6. Agents — the specialists that drive the loop

### 6.1 What an agent is here, and where it lives

**An agent is a catalog source, exactly like a skill.** It is authored as
`.basicly/core/agents/<role>/agent.yaml` against `schemas/agent.schema.json`, projected by
`basicly agents-build` into every agent root, and **vendored to a consumer by `basicly install`**.
It is never a hand-written file under `.claude/agents/` — that directory is projected output, and
editing it directly is the drift the projection gates exist to catch.

**The file is named for the role, not for a persona.** `decomposer`, `implementer`, `validator`,
`reviewer`, `decider`, `retrospector`, `curator` — the same vocabulary the state table uses, so a
reader who knows the state knows the file. The roster design gave each a human name (Dana, Kai,
Vera, Remo, Juno, Lumi, Tala); those are **display-only and carry no authority**, and no policy,
gate or scheduling decision may key on one. The engine keys on the **role id**, which is the
directory name. A name that appears in a dispatch record is a label; a role id is a contract.

### 6.2 Two classes, and only one of them is bound to a state

| Class | Invoked by | Bound to | Examples |
| --- | --- | --- | --- |
| **Loop agents** | the engine, at a state boundary | exactly one state (or two, for the implementer's repair mode) | `decomposer`, `implementer`, `validator`, `reviewer`, `decider`, `retrospector`, `curator` |
| **Ad-hoc agents** | a human or an agent, on demand | nothing — available whenever they fit | `code-reviewer`, `security-auditor`, `test-runner` |

The distinction matters because it decides what a missing one costs. A missing **loop agent** means
that state has no specialist and falls back to the default runner, which is the current situation
for all seven. A missing **ad-hoc agent** costs only the convenience of not having it — it is the
same relationship skills already have, where a model-invoked skill loads when it fits and a
user-invoked one waits to be asked.

**The three that exist today are all ad-hoc**, and none is a loop agent. They also need evaluating
rather than assuming: each must still pass D5's admission test (differ in **tier, tools or
artifact**), and any overlap with a loop agent about to be authored — `code-reviewer` against
`reviewer`, `test-runner` against the verify gates — must be resolved rather than left as two
things with one job.

### 6.3 The gap, measured 2026-08-08

```text
rg -w 'dana|kai|vera|remo|juno|lumi|tala' src/     ->  0 hits
.basicly/core/agents/                              ->  code-reviewer, security-auditor, test-runner,
                                                       researcher (+ blocks/, 4 shared prompt blocks)
.claude/agents/                                    ->  the same four, written by agents.sync()
dispatch code that READS an agent root             ->  none
```

**Re-measured 2026-08-09**: four agent sources, not three — `researcher` was added this session, and
`.basicly/core/agents/blocks/` holds four composable prompt blocks (`context-priming`,
`escalation-honesty`, `evidence-discipline`, `read-only-discipline`) that this section did not
previously record. All four agents are still ad-hoc; the loop-agent column is still empty.

So the projection works and **nothing consumes it**: every dispatch ends at `Popen` of a CLI with a
prompt built inline, and the only other consumer of `.claude/agents/` is `cli.py:1261`, which globs
the directory to *delete* files. Authoring the seven loop agents is therefore necessary and not
sufficient — `basicly-4kdm` owns the sources, and the engine must also learn to resolve a state to
a role and dispatch it, which is what makes the roster real rather than projected.

| Role | State | Source [M 2026-08-09] | Engine |
| --- | --- | --- | --- |
| `decomposer` | DECOMPOSE | **authored**, loads `decompose-plan` | unnamed equivalent (`loop.py:1057-1094`) |
| `implementer` (+ **repair mode** [D5]) | BUILD, REPAIR | **authored**, loads `python-guidelines` + `repair-in-place` | equivalent exists (`loop.py:663-700`); repair mode does not |
| `validator` | VALIDATE | **authored**, loads `validate-as-consumer` | **dispatched** (`loop.py:459`, `u2hl.54.3`) |
| `reviewer` (by lens) | VALIDATE | **authored** | **dispatched once per lens** (`loop._dispatch_reviews`, `roles.LENS_ROLE_BY_PHASE`, `basicly-feje`) |
| `decider` | CLASSIFY, escalations | **authored** | exists (`decisions.py`) |
| `retrospector` | RETROSPECTIVE | **authored**, loads `root-cause` | nothing; the state does not exist |
| `curator` | SHIP | **authored** | nothing |

**All seven are authored and dispatched as of 2026-08-09.** The gap this section had
measured since 2026-08-08 — "the projection works and nothing consumes it" — is closed:
`roles.resolve_role` maps a phase to a role by table lookup and the runner puts
`--agent <role>` on the argv, verified against claude 2.1.226 and copilot 1.0.78 rather
than recalled. The `Engine` column above now means "does an equivalent already run at
that state", not "can a role reach it".

**Resolution fails to None in three places, and each falls back to the default runner
rather than failing**: a phase with no persona (VERIFY, by D4), a family that cannot
select one (codex ships no subagent root), and a role whose *projected* file is absent.
The last is checked against the projected file rather than the catalog source, because
that is what the host reads — so a consumer on an older install gets an unspecialised
loop instead of a stopped one.

**Authored past the staged-admission rule, on the owner's instruction 2026-08-09.** D5
admits a role when it differs in tier, tools or artifact, and by that test only
`decomposer` and `implementer` qualified today — the other five wait on
`validation-transcript` and `release-record`. The owner directed all seven. What the rule
still buys is recorded rather than discarded: each of the five carries a contract that
cannot be exercised until its artifact exists, and that is a debt this table now names
instead of a gap it hides.

**Unresolved, and §6.2 requires it resolved**: `code-reviewer` (ad-hoc) against `reviewer`
(loop). They differ in invoker and artifact but not in job. `reviewer` is the stronger
definition — per-lens, adversarial stance, severity-bounded, no cross-lens ranking — and
`code-reviewer` is vendored to consumers today, so superseding it is a breaking change to
a shipped surface. Owner's call; it is not made here.

**Admission is staged, not wholesale** [D5]. A role is authored only when it differs in tier, tools
or artifact. `decomposer` and `implementer` now qualify — `basicly-u2hl.18` shipped
`implementation-plan` and `change-summary`, so each has an artifact of its own. `validator`,
`reviewer`, `retrospector` and `curator` do not yet: their artifacts (`validation-transcript`,
`release-record`) are unbuilt, so they are recorded as blocked on the artifact rather than authored
speculatively. Authoring all seven at once is the accretion the admission test exists to prevent.

A role is a **dispatch contract**: role prompt, tool policy, model tier, gate authority, output
contract. Each judged role additionally carries an explicit adversarial stance and a role-specific
list of how *that* role goes soft, derived from recorded verdict and rework history rather than
invented (§11.1) — a generic rigour instruction is a no-op that costs tokens.

**The host already expresses that contract, and we express a third of it** [M, 2026-08-09, claude
2.1.226]. A subagent definition requires exactly `name` and `description` and accepts seventeen
optional fields, of which `tools`, `disallowedTools`, `model`, `effort`, `maxTurns`,
`permissionMode`, `isolation`, `skills`, `hooks` and `background` are each a clause of the contract
above. Two consequences for §6.1's schema, neither of them a rewrite:

- **`skills:` makes §7.1's "an agent is a dispatch contract; a skill is a method that contract can
  load" mechanical rather than prose.** The field preloads a skill's full body at subagent startup.
  It is claude-only, so it belongs under the schema's `claude:` vendor fence. One constraint: a
  skill with `disable-model-invocation: true` cannot be preloaded.
- **`--agents <json>` is the wiring this section says the engine must learn.** It supplies a role
  definition at spawn without the projected-file round-trip, so "dispatch code that READS an agent
  root -> none" can be closed without teaching the engine to read one. Engine work under D27, not a
  catalog change.

**Agent definitions hot-reload; they are not read once at process start** [M, refuted 2026-08-09].
Claude Code watches `~/.claude/agents/` and `.claude/agents/` and picks up an added or edited file
within seconds, with two exceptions — the *first* agent file in a newly created `agents/` directory,
and `--disable-slash-commands`. Hook config in settings files is watched too, and a `ConfigChange`
event exists for exactly this. The first exception is the first-install case, which is why three
committed places assert the strong form and all three need the narrow rule instead:
`.basicly/core/kit/tier/install_hook.py:115`, `.basicly/core/kit/tier/README.md:72`,
`.basicly/core/skills/tier-injection/skill.yaml:50`. The original claim (`basicly-wbsz.3`) pinned no
version, which is why it cannot be adjudicated as wrong-then or stale-now — and that is the finding.

**A competing harness ships the contract half we lack, from a worse source model** [M, 2026-08-09,
`Chachamaru127/claude-code-harness` v5.6.0, MIT]. Its four agents are hand-written `.md` with no
schema, no projection and no consumer vendoring — strictly weaker than §6.1 as a *source*. But each
one names tools, denied tools, model, effort, turn cap, isolation and a versioned output schema
(`advisor-response.v1`, `test-wiring-audit.v1`), and each is actually spawned. That is exactly the
gap this section measures, observed working in another tree. Its `test-wiring-auditor` contract —
fresh context, inherits no conversation state, emits one JSON object — is a clean model for
`validator`. Concept only; nothing is ported.

**The projected `tools:` line binds on copilot, in the claude spellings, in the form we already
emit** [M, 2026-08-11, copilot CLI 1.0.78]. The concern that `.github/agents/*.agent.md` grants no
tools off-claude is **refuted**, and by the probe that would have shown it: a throwaway agent
carrying our exact projected line, `tools: Read, Grep, Glob`, was asked to run `echo` and answered
*"no shell/bash tool is available to me (only view, grep, glob, sql, and skill tools)"*. The
positive control that makes that a finding rather than a refusal-shaped coincidence is the same
agent with `Bash` added, which ran the command. So the comma-scalar form parses, the PascalCase
names resolve through `copilot_tools.py`'s alias table, and the allowlist is enforced — the two
extra tools are `sql` and `skill`, which that module already records as ungovernable by any
allowlist. Skills need no translation either: `copilot skill list` in this repo enumerates all 34
of our `.claude/skills/` sources as project skills.

**What is not established is the second copilot surface.** Both facts above are the CLI. VS Code
reads the same two directories and states it *"maps Claude-specific tool names to the corresponding
VS Code tools"*, accepting both the array and the comma-separated form [S, vendor doc, 2026-08-11]
— but it publishes no mapping table, its own vocabulary is a third one again (`search/codebase`,
`edit/editFiles`), and nothing here has ever exercised it. Recorded as OQ-18 rather than as a gap,
because the cost of the wrong answer is a read-only agent holding write tools on a surface we do
not measure, and because the alias table itself is hand-pinned from a single 2026-07-31 reading
with no gate that would notice the vendor moving under it.

### 6.4 Deliberately not agents

Each refused by a decision rather than by preference (absorbed from the roster design 2026-08-08,
which is deleted). A refusal here is load-bearing: it is what stops the roster growing a role per
problem:

| Refused | Why |
| --- | --- |
| Merge agent | it would resolve with neither lane's context, at the point of weakest verification. A conflict means the *graph* was wrong, not the merge |
| Tester / verifier | verify is deterministic gates; a model running them adds cost and nondeterminism to the one trustworthy part. Authoring tests is the implementer's, and diagnosing a red gate is a capability of its repair dispatch |
| Scout | a low-tier pre-reader's characteristic error — a slightly incomplete file list — is mechanically undetectable and silently narrows the implementer's view. **Permanently cut as a persona**; the same artifact derived deterministically from an AST is an engine step with no tier and no gate authority, which is D20's `change-shape` |
| Shipper | version bump, changelog, tag and push are a command; the judged residue is curation, which is `curator` |
| Conductor | it is code. **Amended by D28** — the original form was "no agent spawns agents", which both installed runtimes contradict by construction. What survives: an agent may spawn only a role the engine authored, personas never invent helpers, and every boundary is gated by a host hook. If it has a name it is an agent, and its output is a proposal the engine must validate |

**Lens output is reported per lens, never merged into one ranked list** — a change can pass one axis
and fail another, and reranking lets one mask the other.

### 6.5 VALIDATE fans out, and what that costs [decided 2026-08-14, `basicly-feje`]

§3.1 gives VALIDATE two roles — `validator`, and `reviewer` **by lens**. `roles.ROLE_BY_PHASE` was
phase-to-one-role, so the second could not be reached: `reviewer` was authored, projected to both
agent roots and vendored to consumers while no code path could name it. The resolution is the first
of the two the defect offered — VALIDATE dispatches more than one role — and it is recorded here
because it changes the map's shape.

**Two tables, both data.** `ROLE_BY_PHASE` keeps its one entry per phase and now means the role that
*drives* the phase: the one whose reply the engine acts on, which at VALIDATE is the validator and
its gate. `LENS_ROLE_BY_PHASE` names the role a phase fans out beside it, dispatched once per entry
in `REVIEW_LENSES`. Both are lookups; neither asks a model which role to run, which is the property
the original map was built for.

**The lens vocabulary is two, and the count is the decision.** Every lens is a paid dispatch on every
L3 unit, so a lens whose axis a gate already covers spends tokens to restate a green check.
`correctness` is kept because the gates prove the code runs and the validator exercises the
demonstration line, so the input that breaks it is checked by nobody. `security` is kept because
`basicly.toml` scopes bandit to `.scripts`, `.basicly/core/hooks` and `.basicly/core/kit`, leaving
`src/` with no security instrument at all. Maintainability is refused: ruff, pyright, vulture,
`lint-imports`, `module-size`, `comment-density` and `noqa-debt` ratchet that axis mechanically.

**The reviewer is advisory and the validator owns the gate.** A reviewer records findings under
`[harness-review] lens=<lens>` on the unit, one comment per lens, and nothing reads two of them
together — the no-rerank rule above holds by construction rather than by instruction. Each dispatch
is recorded under the `validate` phase, outside `WRITE_PHASES`, so a read-only judge never enters
the sample a lane's cost is calibrated from.

**What it costs.** Two extra read-priced dispatches per VALIDATE advance, on L3 units only: L1 and
L2 never derive the phase, because `loop_state` reaches `validate` only while the
`validate-as-consumer` gate their level did not promote is outstanding. Nothing is dispatched under
the supervisor's landing pass (`repair_dispatch=False`) or past a halted grant, on the same
reasoning that bounds the validator dispatch.

---

## 7. Skills

Discipline: **encode only what a second party can check, or what a gate cannot enforce.** The
research is explicit that the rest is prose, and this repo's root-cause analysis found accreted
prose is the underlying defect.

### 7.1 Three guidance surfaces, and which one a rule belongs on

A rule reaches an agent by exactly one of three routes, and choosing wrong is why guidance rots:

| Surface | Loads | Costs | Use when |
| --- | --- | --- | --- |
| **Fragment** | always, or on a path glob | always-on budget on every family; `AGENTS.md` is 14,428 characters against a 16,000 cap — **1,572 of headroom** [M 2026-08-14] | it must bind even when nobody thought to ask |
| **Skill** | when the model judges it relevant, **when its `paths:` glob matches**, or when a human types it | its description sits in the listing budget; the body costs nothing until invoked | it is a *method* — long, situational, and useless when it does not apply |
| **Agent** (§6) | when the engine dispatches a state, or on demand | a dispatch | it needs its own tools, tier and output contract, not just words |

The three are not alternatives for the same content. **An agent is a dispatch contract; a skill is a
method that contract can load.** An implementer agent says who runs, at what tier, with what tools,
producing `change-summary`; `repair-in-place` says *how* to repair once it is running. Putting the
method in the agent's prompt makes it unshareable; putting the contract in a skill makes it
unenforceable. The agent frontmatter's `skills:` field makes that pairing mechanical (§6.3).

**A skill is not free, and the cost is in the listing, not the body** [M, 2026-08-09, claude
2.1.226]. Every skill's `description` + `when_to_use` is capped at **1,536 characters** per entry,
the whole listing is budgeted at **1% of the context window**, and on overflow descriptions are
dropped **starting with the least-invoked skills**. That composes with a fact this document already
records — 8 of 34 skills had ever been exercised when last measured — into a live defect with a
feedback loop: a rarely-invoked skill is the first whose description is truncated, which makes it
harder to invoke, which makes it more truncated. It converts §11.6's catalog eval from hygiene into
a measurable context cost, and both caps are mechanically checkable by `catalog lint` today.

**Skill scope precedence is the inverse of agent scope precedence, and it is unrecorded anywhere in
this repo** [M, 2026-08-09]. Agents resolve managed > `--agents` > **project > user** > plugin;
skills resolve enterprise > **personal > project**. `basicly install` writes a consumer's *project*
`.claude/skills/`, which is the **lowest-priority writable scope** — so any developer's
`~/.claude/skills/<same-name>` silently overrides a skill we shipped them, while an agent of the
same name would not. For a distribution tool that is a supply-chain-shaped surprise, and it belongs
in `docs/architecture/architecture.md` rather than only here.

### 7.2 Two classes, mirroring §6.2

| Class | Bound to | Today [M 2026-08-08] |
| --- | --- | --- |
| **Loop skills** | a state, loaded by that state's agent | **5 of 5 exist** (2026-08-09) |
| **Ad-hoc skills** | nothing — invoked when they fit | 40 sources: 34 model-invoked, 6 user-invoked |

**Not one of the 37 is named for a loop state.** The ad-hoc class is well populated — `tool-*`
wrappers, `conventional-commits`, `worktree-isolation`, `test-discipline` — and the state-bound
class is empty but for `python-guidelines`. That is the same asymmetry §6.3 measures for agents, and
it has the same cause: the ad-hoc class is what a human reaches for, so it got built.

| Skill | Invoked in | What it carries | Status [M] |
| --- | --- | --- | --- |
| `decompose-plan` | DECOMPOSE | testable criteria notation, dependency declaration, budget assignment | **shipped 2026-08-09**, loaded by `decomposer` |
| `validate-as-consumer` | VALIDATE | run it as a consumer would, in the operational environment — never a re-run of the gate suite | **shipped 2026-08-09**, loaded by `validator` |
| `repair-in-place` | REPAIR | same worktree, briefed with actual findings, no re-plan | **shipped 2026-08-09**, loaded by `implementer` |
| `root-cause` | RETROSPECTIVE | iterated-why with every link citing an observation; output is a named control + tier + covered class | **shipped 2026-08-09**, model-invoked, ahead of its state — see below |
| `python-guidelines` | BUILD, REPAIR | §9.2 — the non-mechanical half | **shipped** (`basicly-u2hl.13`) |

**The pairing is now mechanical, not prose** [2026-08-09]. Each loop skill is named in its
agent's `claude.skills`, which the host preloads at spawn, and `lint_agent_sources` refuses a
name that resolves to nothing — verified by planting a typo. So §7.1's "an agent is a
dispatch contract; a skill is a method that contract can load" is a checked relation rather
than a sentence, which is what `basicly-u2hl.52` was filed to achieve and what the D36 fence
made expressible.

**Two costs arrived with the three skills, both measured**: the projected listing went from
2,081 to **2,342 tokens against a consumer's 2,000-token budget**, and the routing rank-1
rate fell from 91.1% to **88.9%** against an 85% floor. Neither is a reason to unship them —
but `basicly-a3ab.12` is now larger than the 81-token overrun it was filed for, and the
routing headroom is 3.9 points rather than 6.1.

**A loop skill is blocked on its state, not on itself.** `validate-as-consumer` cannot be exercised
while VALIDATE is not a phase. Authoring one ahead of its state produces a skill nothing invokes,
which is the unfalsifiable-claim failure the catalog eval exists to catch — 8 of 34 skills had ever
been exercised when that was last measured. `basicly-4kdm` owns the pairing: a loop skill lands with
the agent that loads it, or not at all.

**Amended 2026-08-09 by the one exception, which sharpens the rule rather than weakening it.**
`root-cause` shipped ahead of RETROSPECTIVE, on the owner's instruction and against this
paragraph's first reading. The reading was too coarse: the failure the rule guards against is a
skill with **no invoker**, not a skill whose *state* is unbuilt. `root-cause` has two invokers
today — a human running a retro under `session-finish`, and the `researcher` agent, whose method
section defers to it — so it is exercised rather than asserted, and it becomes RETROSPECTIVE's
skill unchanged when that state exists. The sharpened test: **name the invoker before authoring.**
A loop skill whose only prospective caller is an unbuilt state still fails it.

It also carries the caveat the rest of this document already holds, which is why it is a skill and
not a slogan: iterated-why yields **one** causal path chosen by the asker and is not reproducible
between analysts (Card, *BMJ Quality & Safety* 2017, §15), so the skill's output contract requires
the branch not taken alongside the chain that was. And it inherits §3.2's guard — a single failure
inside the limits is common cause, and running the analysis on it is tampering.

**Shipped 2026-08-08** (`basicly-u2hl.13`). It **stays a skill and gains `paths: ["**/*.py"]`**
[D35, 2026-08-09] — superseding this paragraph's earlier plan to promote it to a path-scoped
fragment. That plan's premise was "as a model-invoked skill it loads only when an agent thinks to
ask, and the agent that most needs it is the one that does not." **The premise is false**: a skill's
own frontmatter takes a `paths:` glob that limits and triggers automatic activation [M, 2026-08-09,
claude 2.1.226]. The glob buys the same always-loads-on-Python behaviour for **zero** always-on
characters and unblocks the work from `basicly-a3ab.1`'s eviction, which the fragment plan was
queued behind. **Shipped: `basicly-u2hl.17` is closed.**

**The overrun this paragraph was sized against was real and its cause was not what it looked like**
[M 2026-08-14, `a3ab.1` closed]. It read 13,135 characters against a 12,000 cap. The audit found the
excess is the **scoped tier**: claude and copilot receive those four fragments as separate
`paths:`-carrying rules files, Codex has no glob-based instruction scoping and inlines them, so the
gap is structural to this one target. Evicting always-on lines would have charged all three families
to fix one and left the cause standing. `codex.yaml:9` records the cap moving to **16,000 / 320**
instead, and `AGENTS.md` is now 14,428 characters over 242 lines — under both. What that trades away
is stated at the same site: the cap also stood proxy for the vendor's claim that adherence degrades
with length, which this repo has never measured (`basicly-agzx.1`).

**What survives of the plan is the codex gap.** Codex has no glob-based instruction scoping and
never loads a nested `AGENTS.md` below the cwd (architecture §7.4), so a fragment remains the only
mechanism there. It is deferred rather than dropped: paying ~1,500 characters on all three families
to reach one, on a file already over its cap, is the wrong order.

**Amended by the counterfactual test** [S, 2026-08-09]. `root-cause` names a control and the class it
covers, but never asks whether removing it blocks the failure. One sentence closes it — *would
removing this cause block this pathway?*, and at set level *would removing the retained set block the
observed failure?* It is checkable by a second party, which is this section's admission test, and it
is a concept rather than an expression, so it is safe to take from an MIT source without porting.

**Not encoded, deliberately** [S]: *genchi genbutsu* as a principle (its only checkable content is
"claims carry attached evidence", which the repo already has), "make policies explicit" as
exhortation, "quality at the source" as a slogan, vendor tollgate checklists, and RPN
multiplication — deprecated by AIAG-VDA 2019 in favour of an Action Priority lookup.

### 7.3 The 2026-08-09 sweep — 10 declines, 2 adaptations, 0 adoptions

Eleven third-party sources were swept for skills, hooks and agent patterns (`basicly-u2hl.37`).
**Ten declined.** The result is recorded because a decline is the expensive finding to re-derive:
without it the same list gets swept again next time someone links it.

| Declined for | Sources |
| --- | --- |
| The recommended control is the author's own product | `aipatternbook.com` (225 patterns are LLM engine output serving as a sales demo; the publisher states the editions "don't really exist yet" and invites sponsorship), `ThibautMelen/agentic-ai-systems` (every "executable pattern" is a collaborator's `*.nika.yaml` DSL, CI is their action) |
| Already carried by our always-on baseline | `multica-ai/andrej-karpathy-skills` — all four of its `CLAUDE.md` sections map onto lines we already ship, licence is **NONE**, and its own README concedes the attributed author did not write it |
| Rung 6 with nothing our fragments lack | `agentpedia.codes`, `hidekazu-konishi.com`, `WenyuChiou/ai-research-skills`, `kumamaki/Claude-Code-Personalities`, `harperreed/dotfiles` |
| Premise unestablished (see §7.4) | `nicobailon/visual-explainer` |

Two adaptations, both concept-level and neither a new skill:

- **`tjboudreaux/cc-thinking-skills`** (MIT) — its counterfactual test amends `root-cause` (§7.2).
  Adopting its 28 skills would be the accretion this section exists to prevent, and its
  five-whys/TOC/scientific-method skills collide with material we already carry with the Deming
  common-cause gate and the Card 2017 branch rule attached.
- **`Piebald-AI/claude-code-system-prompts`** — holds the host's own code-review agent decomposed,
  pinned to v2.1.226, which is the strongest available evidence for how a subagent dispatch is
  constructed, and §6.3 records `reviewer` as paper-only. **The licence line must be stated where
  the finding is used**: MIT © Piebald LLC covers Piebald's tooling and *cannot* license the vendor's
  copyrighted prompt text. Legitimate to read when authoring `reviewer`'s stance and gate authority;
  never legitimate to paste into a `.basicly/core/` source.

**A skill graph is the one structural idea worth taking** [S, `claude-code-harness` v5.6.0, MIT].
Its `SKILL.md` frontmatter carries `shape` (workflow/delegate), `role` (executor/orchestrator),
`pair` (its counterpart skill) and `base` (the skill it delegates to). That makes "which skill
answers which, and which one this delegates to" machine-readable — the pairing §7.2 currently states
in prose and cannot check. Concept for `basicly-u2hl.25` / `basicly-4kdm`; no code is taken.

### 7.4 A rendered artifact does not fix a checkpoint, because the clock is not measuring reading

The owner's framing was that a rendered view may read better than a terminal wherever a human is in
the loop. Tested against this repo's own ledger rather than argued [M, 2026-08-09]. `policy.py:2293`
writes `[harness-wait]` markers over `checkpoint` and `decision`; 129 are human-answered.

```text
n = 129 human-answered        total 145,640 s (40.5 h)
p25    12 s   median  95 s    p75    378 s
p90 2,090 s   max  29,012 s   54% answered in <= 120 s
17 events > 30 min  ->  123,406 s  =  85% of all human wait
```

The five whys, each link an observation, not an inference:

1. Rendering helps only if reading is slow — refuted at the median. Nobody comprehends a plan in the
   12 s of p25, and 54% are under two minutes.
2. The 40.5 h total is real, but 17 events over 30 minutes carry 85% of it. The mass is all tail.
3. Those 17 are not slow reads. `basicly-hxnf.2` (27,553 s) and `basicly-hxnf.3` (27,510 s) were
   answered **in the same second** — two decisions cannot be comprehended simultaneously; they were
   batched on someone's return.
4. `basicly-sco6` waited 29,012 s; the **next** decision on the same issue, same human, took 81 s.
   Eight hours asleep, eighty-one seconds awake.
5. The clock starts when the *engine* asks, not when the human *arrives*, and
   `CONFIRM_TTL_SECONDS = 900` means every wait past p90 has already expired into a re-challenge.

**Root cause: the checkpoint clock measures rendezvous, not reading.** A renderer cannot move a
quantity the instrument does not contain. Both recorded checkpoint-comprehension incidents
(`basicly-kjc5.34`, `basicly-jr0l.39`) were fixed by *saying the missing thing in words* —
`_CHECKPOINT_MEANING` (`cli.py:2214`) exists because an operator did not know the merge had already
happened. That is missing information, not unreadable format, and §15.1 records the same shape
already refuted in the token domain.

**The branch not taken, per the Card 2017 caveat**: this followed *duration*. The other branch is
*decision quality* — a checkpoint answered in 5 s may be answered wrong, and `cli.py:2219` exists
because of exactly such an approval. That branch is **not measurable here**: `checkpoint_approved`
is a boolean, there is no un-approve, and a regretted approval leaves no distinguishable trace.

**The one datum on the other side, stated because it is the honest reason this is "unestablished"
rather than "refuted":** `wait-decompose` — the only checkpoint with a real artifact to read — ran
2,490 s, roughly 26× the classify and ship medians, at **n = 1**.

---

## 8. Handoff artifact schemas [D4]

Eight artifacts, of which seven now have a schema [M 2026-08-13, basicly-r4jm]. Each is a
validated artifact the producing state must emit and the consuming state must accept. `needs-input.json` is the existing precedent for a schema-validated handoff.

| Artifact | Produced by | Must carry |
| --- | --- | --- |
| `solution-design` | INTAKE | six sections [D17]: `## Problem` (in the requester's terms), `## Success` (an observable, not a feeling), `## Consumer transcript`, `## Out of scope`, `## Constraints`, `## Open questions` (§8.1.1) |
| `classification` | CLASSIFY | integrity level; loop depth; the gate set, tier and budget the level selects |
| `change-shape` [D20] | CLASSIFY (derived) | the call tree of what calls what; the file-tree diff of what appears and what moves; the signatures of the new public functions |
| `implementation-plan` | DECOMPOSE | per task: testable acceptance criteria, scope globs, declared dependencies, budget, integrity level; plus the graph |
| `change-summary` | BUILD | what changed and why; self-check result; the commit |
| `verification-evidence` | VERIFY | per required gate: the check, the command, the result; per acceptance criterion: the derived check and its result |
| `validation-transcript` | VALIDATE | how the change was exercised as a consumer, and against which original requirement |
| `release-record` | SHIP | each claim with the evidence for it; each unsupported claim named and dropped; the post-ship action pre-declared before the tag moves |

`release-record` had no row here until 2026-08-13 while the curator's output contract named it,
which is why it was the last of the set to be noticed. `solution-design` remains the one kind with
no schema: it is specified as *markdown sections* rather than a JSON payload, so whether it is a
handoff artifact of the same family is an open question rather than an omission (`basicly-32qz`).

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

### 8.3 An agent inherits through a durable artifact, not a replayed window

Absorbed 2026-08-08 from `agent-roster-design.md`. Everything pasted into a dispatch prompt — and
everything a subagent prints back — stays resident for the rest of the session and is re-read on
every later turn. So the implementer **writes its full report to a file and returns only**: status,
commits, a one-line test summary, and concerns. The report file *is* the persistent memory a fresh
implementer reads, which is what makes a late-round tier bump work across runners that cannot
resume a live subagent.

Four statuses, because each has a different correct response: **DONE**, **DONE_WITH_CONCERNS**,
**NEEDS_CONTEXT**, **BLOCKED**. And the rule that gives them teeth: **never ignore an escalation,
and never force the same model to retry unchanged.** If the implementer said it is stuck, something
must change.

This is the mechanism `basicly-ejdm` builds against: it resolves the tension between D6's
fresh-context decision and the measured 254x cost of a lane rebuilding what the session already
holds, because a durable artifact is neither a replayed window nor a cold start.

---

## 9. Code quality

The owner's stated pain is module bloat. **Nothing in the stack measured it** when this section was
written [M 2026-08-07]: ruff has no module-length rule, and `C90` was not enabled. `cli.py` was
5,097 lines; `src/basicly/` totalled 36,641.

**Both instruments now exist and the tree grew anyway** [M 2026-08-14]: `C901` is enabled at 15
(§9.1) and §9.3's token ratchet is live with 78 frozen baselines, while `cli.py` is **4,911 lines**
and `src/basicly/` is **42,966** — **+17% in one week**. A ratchet bounds a *file*; it does not bound
a *tree*, and nothing here counts modules. That is the gap an architectural pass has to close with a
gate rather than with one audit.

### 9.1 Deterministic — gate it

| Guideline | Mechanism | Status |
| --- | --- | --- |
| Cyclomatic complexity | ruff `C90`, `max-complexity` | **enabled at 15** (`basicly-u2hl.5`). The 14-at-10 figure re-confirmed 2026-08-08; ratcheting to 10 is `cli.py` ×4, `supervise.py` ×2, then one each |
| File size | **no ruff rule exists.** A script under `.scripts/` wired as a `[[verify.checks]]` fast entry — see §9.3 | **the gap.** Nothing in the stack measures it |
| Blind `except Exception` | ruff `BLE001` | **4 violations measured** 2026-08-08; adopted in `basicly-u2hl.11`. Each is a judgement about a process boundary, not a mechanical narrowing |
| Exception hygiene, perf, builtins shadowing | ruff `TRY`, `PERF`, `FURB`, `A`, `RET`, `TC`, `TID`, `DTZ` | **adopted** in `basicly-u2hl.11`. Measured 2026-08-08: `RET` 1, `TID` 1, `DTZ` 1, `A` 2, `FURB` 4, `PERF` 16, `TRY` less `TRY003` 26, `TC001/002/006` 16. **`TRY003` (442) and `TC003` (111) are deliberately ignored** with the reason recorded in `.ruff.toml` — style at scale, not a defect class |
| Security lint over `src/` | ruff `S` | **adopted for `src/` only**, per-file-ignored elsewhere so bandit keeps the trees it already scans. 25 violations less `S101`. `S101` (6,931, every one an `assert` in `tests/`) mirrors the existing bandit `skips = ["B101"]` rather than inventing a second answer |
| Type completeness | pyright `basic` → `standard` | **done** (`basicly-u2hl.10`). Exactly one error, and it was a lying annotation rather than a defect: `tracker_usage._Timer.__exit__` was `-> bool`, which declares a context manager that *may suppress*, so every name bound in the `with` read as possibly-unbound. `-> Literal[False]` fixes it; restructuring the consumer only relocates the error. **The "still open" half of this row closed with `basicly-u2hl.15` and the row did not say so** [M 2026-08-14]: `[tool.pyright] include` names `src`, `tests`, `.scripts` and `.basicly/core`, and `uv run pyright` analyses **306 files with 0 errors**. Nothing is unchecked at either mode |
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
3. **Docstring and comment usefulness.** `D` checks shape; a docstring restating the signature
   passes and is worthless — PEP 257 names exactly this ("The one-line docstring should NOT be a
   'signature' reiterating the function/method parameters") [S]. The same test applies to a comment,
   and it is a test about *content*, not existence: narrating the next statement is the defect,
   recording why that statement is the one that survived is the artifact. The Google convention this
   repo already pins (`.ruff.toml`, `convention = "google"`) draws the line in one sentence —
   "never describe the code" — immediately after mandating one: "Complicated operations get a few
   lines of comments before the operations commence" [S]. **Measured 2026-08-09** [M]: of 1,389
   comment blocks under `src/basicly/` and `.basicly/core/`, a 120-block sample classifies 41%
   contract, 40% why, 16% section navigation, 3% machine directive and **0% narration**; two
   whole-population probes, each validated against synthetic narration, return 0 narration-opener
   hits and 9 code-echoing blocks of 1,139, every one a cross-reference. There is no what-comment
   population here to legislate against, so the rule worth writing is item 10, not a ban [D34].
4. **Whether an abstraction earns its keep.**
5. **Fixing the metric versus gaming it.** Extracting `_part1()`/`_part2()` satisfies `C901` and
   makes the code worse. Extract along a nameable responsibility or do not extract.
6. **`noqa` legitimacy.** Every new suppression carries a reason naming the alternative rejected.
7. **Exception design** — what to catch, what context to attach, no internal detail in
   user-facing errors.
8. **3.14 idiom selection.** PEP 750 t-strings at injection boundaries; PEP 758 paren-free
   `except A, B:` — pick a house direction, no linter enforces either [S].
9. **Free-threading safety** (PEP 779): stop assuming GIL atomicity. Not mechanically checkable.
10. **A comment that contradicts the code is a defect, and the code is what ships** [D34]. PEP 8:
    "Comments that contradict the code are worse than no comments. Always make a priority of keeping
    the comments up-to-date when the code changes!" [S]. Nothing mechanical checks it — `ERA001`
    catches commented-out code and **no rule in the stack reads a comment's meaning** — so it is a
    build obligation: when you change a line, re-read the comment above it; when the two disagree,
    the comment is wrong until shown otherwise. **Deleting it is not the fix and must not become
    one.** Comment lines are counted by the §9.3 ratchet: stripping standalone comments returns
    36.3% of `config.py`'s measured tokens, 17.0% of `merge.py`'s — which is frozen at exactly its
    current count — and 11.6% of `loop.py`'s [M, 2026-08-09]. "The comments were redundant" is
    therefore the cheapest route to ratchet headroom in this tree, and it is the same gaming shape
    item 5 already names.

Test quality is **out of scope** — `test-discipline` already owns it.

**§9.1's "do not re-propose" list does not close the comment question.** It reads as though `ERA001`
settles comments; `ERA001` covers commented-out code only, and item 10 is the uncovered half.

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
  `test_a_module_that_grew_by_code_still_fails*after*the_import_exclusion`; without it the
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
8. **Enforcement at the tool-call boundary, not only at the commit boundary** [M, 2026-08-09].
   Our gates are git hooks and `basicly verify`: they judge an artifact **after** it exists. A
   competing harness adjudicates **before** the tool runs, at PreToolUse and PermissionRequest,
   and this is the single largest capability gap found in the 2026-08-09 sweep. Item 2 above is a
   special case of it — a termination detector that can only refuse the next dispatch is weaker
   than one that can refuse the call. See §11.7.

Two of these are now cheaper than when they were written, both at claude 2.1.226 [M, 2026-08-09]:

- **Item 4 has a host-native shape.** Hook types `prompt` (one small-model call returning
  `{ok, reason}`) and `agent` (a subagent with tools, up to 50 turns) run a verifier **outside the
  producer's context**, which is the fresh-context property item 4 requires. `type: agent` is
  vendor-flagged **experimental** and the vendor steers production workflows to command hooks, so
  it is additive — the deterministic gates stay primary.
- **Item 2 has a reachable enforcement point.** A `Stop` hook returning `decision: block` fires and
  continues the turn under our own `claude -p` dispatch — probed, two firings, `stop_hook_active`
  observed flipping. It is capped at 8 consecutive blocks (OQ-16). This is engine work, not a
  catalog change: `claude_settings.py:51` maps **2 of the 31 documented hook events**
  (`pretooluse`, `posttooluse`), so no catalog source can name a `stop` stage until that vocabulary
  is widened.

**Item 3 is confirmed, and our own document beat a vendor blog on it.** Nesting depth is **3** at
2.1.226, the `Agent` tool is withheld at the limit, and `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`
configures it; concurrency is capped at 20 per session. The Anthropic blog post in the same reading
list still says five — five was the default from v2.1.172 to v2.1.216, and v2.1.219 set 3. Rung
ordering is what caught it.

### 11.1 The judged-output contract — the unbuilt half

Absorbed 2026-08-08 from `gates-and-rework-design.md`. Severity as a required field, the
no-pre-judging lint, the composite rubric gate and the convergence detector all **shipped**. These
did not, and each is deterministic engine code rather than a persona:

- **The reviewer never receives the claim.** It gets artifact plus contract — the diff and the
  criteria — never the producer's conclusion or rationale. Because bundles are assembled by code,
  the assembler can be *structurally incapable* of including it, which is the difference between a
  rule and a guarantee.
- **Record the base before dispatching the producer.** Never derive a review base as `HEAD~1`: it
  silently truncates a multi-commit unit and reviews its last commit while reporting on the whole.
- **A re-review is scoped to the fix range**, verdicting each open finding addressed or not.
  Out-of-scope observations become deferred minors and never extend the loop — otherwise each round
  discovers unrelated work and the loop cannot converge.
- **Adjudicate only at the cap**, per finding, into exactly one of parked-contestable,
  parked-real-deferred, or blocked, each with a recorded ruling. Adjudicating earlier to end a loop
  is pre-judging under another name, and a structural failure is never parked.
- **A deferred minor needs a named consumer** (the ship-time rollup). Without one it is recorded and
  structurally guaranteed to rot.
- **An escalation ladder on late rework rounds.** Early rounds resume the same implementer; late
  rounds dispatch a fresh one **one tier up**, briefed with the prior attempt's record. Ours bounces
  to the same tier with the same framing every time, spending the cap without changing a variable.
  It yields a measurable signal: **if late-round bumps routinely succeed, the initial tier was
  wrong.**
- **Every judged role carries a role-specific "how this role goes soft" list**, derived from
  observed failures and never invented — for example issuing a warning for what is actually a
  blocker, to avoid conflict with the producer. A generic rigour instruction is a **no-op**.
- **Refute-or-promote, targeted.** A finding that would block a landing or a ship earns a refutation
  pass: N independent reviewers, each on a *different* lens, prompted to disprove it, majority
  required to kill it. Not a default — it multiplies the most expensive dispatch class and optimises
  precision, the axis we are less worried about. The trigger is deterministic, so it stays out of a
  model's hands (D9).
- **Two degenerate reviewers are currently invisible**: the rubber stamp (approves nearly
  everything, so its advisory green is worthless) and the noise generator (findings nearly all
  adjudicated contestable, costing rework and buying nothing). Both are computable per lens from
  data already recorded — finding rate and adjudication-outcome distribution over a window — and the
  window and threshold must be **read from recorded history, never guessed**.

**One dropped item argues against a live decision, so it is kept as a caveat rather than lost.**
D10 says VERIFY runs named checks and judges nothing. The counter-case is **compliant
hallucination**: output that satisfies the stated constraint while defeating its purpose, which a
named check passes by construction. D10 stands — moving judgement to plan time is still right — but
a criterion whose check can be satisfied without the behaviour is the failure mode to watch, and
D18's end-to-end demonstration is the partial answer already shipped.

### 11.7 A competing harness, and the line it draws [M, 2026-08-09]

`Chachamaru127/claude-code-harness` **v5.6.0**, SPDX **MIT**, no NOTICE, no per-directory licence
and no rider — read in full before any finding below was written. Verified against the tree and by
live probe, not against its README. It is **alive and fast**: 1,959 commits, 8 releases in the last
month, 3,048 stars, issues closed in days. It is also **bus factor 1** — one person on four
identities is 1,943 of those commits, and 5 of 100 sampled PRs are external.

| Pillar | Us | Them | Overlap |
| --- | --- | --- | --- |
| **Catalog** | one YAML source, planner + per-target renderers, drift-gated | hand-written `CLAUDE.md`; `sync` generates only plugin/hook/settings files. Cross-host reuse is **mirroring** with a drift checker | Partial — same goal, opposite direction: we generate, they replicate and detect divergence |
| **Gates** | git hooks + `basicly verify`, at commit/push/landing time | **agent-runtime**: 27 hook events, a 5-category floor, R01–R15, a tamper-check that refuses to adjudicate rather than fail open, redacted JSONL audit. Their `.githooks/` holds one file | **Barely overlapping, and this is the gap** — they gate the action before it happens, we gate the artifact after |
| **Loop** | engine code; phase **derived**, worktrees provisioned, dispatch, serial merge queue, bounded rework | **prompt assembly**: the verbs "do NOT call any LLM", they emit a prompt to stdout. The loop is prose in `SKILL.md` the model executes | Same phase vocabulary, **inverted authority**. Theirs: the model executes and the engine vetoes. Ours: the engine executes and the model proposes |
| **Tracker** | `br` graph, typed dependencies, phase a pure function of state | `Plans.md`, a Markdown table parsed by regex; status is a text marker. SQLite holds session runtime state, **not** the task graph | Weak — they have a ledger, we have a graph. No derived-phase equivalent exists |

**This is evidence for a decision we already took, not against it.** `architecture.md:86-106`
records phases as engine code and deliberately not configuration, decided 2026-07-30 after reviewing
two projects that made them declarative. This is a third, larger, better-resourced instance of the
same pattern, and the cost is observable: their own issue #269 is a `Stop` hook blocking infinitely
because it keys on `Plans.md` WIP counts. A marker ledger is *remembered*; a graph is *derived*, and
a compacted or crashed session can re-derive only the second.

**What they solve that we have open** — `basicly-0p8n` (harness gates in the coding-agent hooks) is
their entire product; `basicly-66ix` (project claude agent hooks to copilot) has a working shape
there as **one policy kernel + N host codecs**, with a golden-file `--check` gate; `basicly-a3yi`
(tier into each projected surface) is solved with declared precedence; `basicly-u2hl.24` (plugin
channel) is shipped; `basicly-tcmy.20` (redaction) is enforced at write time — hash and length only,
and *nothing at all* for the two most sensitive categories.

**What we solve that they do not attempt**: a tracker as a graph with derived phase; engine-held
authority (theirs can veto a tool call but cannot refuse to advance a phase, enforce a checkpoint,
cap rework or rank a ready set — all four are prose); guidance projection from one source into three
families; multi-lane supervision with a serial merge queue and contention preflight; install/upgrade
convergence preserving a consumer overlay.

**The `external-review` rule caught the inverse of the gastown case here, and the check was
different.** gastown advertised a feature that was unreachable — found by looking for callers. This
repo's defect is a **reachable feature described too strongly**: its README says the runtime floor is
"Overridable: **No.** Not by any config, env var, or permission mode", and two documented escape
hatches flip a deny to an allow, with the source file's own comment three clicks away conceding
"except for their two explicit operator-configured exceptions". Finding it required *exercising the
override*, not finding the caller. Its README also claims "claims in this README are machine-checked
… a feature appears here only after a gate proves it is reachable"; the gate asserts version
consistency and marketing wording, and **no assertion ties a README row to a reachable symbol**.
That is worth adding to `external-review` as a second worked example.

---

## 12. Observability and the two factory modes [D6]

**Measured** [M]: the harness defaults claude dispatch to `--output-format stream-json --verbose`
and reads it line by line. **The "spends the stream entirely on token accounting" half is now
false** [M 2026-08-14]: `--forward-subagent-text` is passed at `runner.py:179`, and `basicly-rrah`
persists each lane's transcript, so the stream reaches a durable artifact rather than an in-memory
sink. What this section still describes correctly is the third item below — light mode as a second
dispatch path — which is `basicly-xjd2`.

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
| **OQ-15** | **Does a checkpoint's artifact take real reading time?** §7.4 shows the clock measures rendezvous, so the question is unanswerable with today's instrument — arrival latency and comprehension are fused in one number. Settled by **one field**: a third marker written when the operator first *views* the checkpoint. That splits the two at near-zero cost and decides the rendering question for good | any rendered-artifact work |
| **OQ-16** | **How many `Stop`-hook iterations survive under `claude -p`?** One block-and-continue is probed and works; the documented override after **8 consecutive blocks** is not exercised. This bounds any in-dispatch termination gate to 8 turns and must be measured before one is designed on it | an in-dispatch BUILD termination gate |
| **OQ-18** | **Does a projected agent's tool allowlist bind in VS Code, and does the alias table stay true?** §6.3 measures the copilot **CLI**: the comma-scalar `tools:` line parses, the PascalCase names resolve, the allowlist enforces. The second surface reads the same files with a third vocabulary and is [S] only — VS Code claims to map claude tool names but publishes no table, and `copilot_tools.py`'s table is hand-pinned from one 2026-07-31 reading with no gate that fails when the vendor moves. The wrong answer costs a read-only agent holding write tools on a surface nothing here exercises | any work that certifies an agent read-only off-claude |
| ~~OQ-17~~ | ~~Does comment density causally track module oversize?~~ — **resolved 2026-08-09: not separable in this repository, and the number that shows why is the collinearity.** Over 77 modules in `src/basicly/`, density against `log(tokens)` is **r = +0.385**, density against `log(commits touching the file)` is **r = +0.272**, and the two predictors correlate with each other at **r = +0.822** [M]. At that collinearity the data cannot discriminate "large modules are too complex, hence annotated" (D34's branch not taken) from "large modules have lived longer and accumulated more recorded decisions" (the confound). Two further observations point away from the complexity reading: density rises monotonically by size quartile (2.4 → 9.9 → 9.0 → 13.3 per 100 code lines) yet the **single largest module is among the sparsest** — `cli.py`, 50,482 tokens at 4.4 — which is the opposite of what a complexity-drives-annotation mechanism predicts, and the two densest modules (43.9, 42.5) are small and under cap. **D34 is not reopened.** Settling it properly needs per-line age, not per-file commit count, and a repo where size and age are not near-collinear — neither of which this one offers | — |

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

**Corrected 2026-08-09 [M], and the correction shrinks the fix to a parser.** The paragraph above
reads as though the numbers are unavailable. They are not: claude's `result` event carries
`cache_creation_input_tokens` and `cache_read_input_tokens` today, and `modelUsage.<id>.contextWindow`
beside them — captured on a live probe of 2.1.226. So this is a gap in *our* extractor, not in the
stream. **Copilot's split is already extracted** (`copilot_store.py`, from `session.shutdown`
`modelMetrics`), which means the family with the worse reputation here has the better telemetry and
the claim that gated this whole section was ours to close.

**And the economics the section reasons about are now measured rather than argued [M, 2026-08-09],
on four real dispatches of one seeded session:**

```text
cold seed              cache_create 21,578   cache_read      0   $0.2165
--resume  (miss)       cache_create 21,592   cache_read      0   $0.2163
--resume  (hit)        cache_create     28   cache_read 21,592   $0.0112
--resume --fork-session               28     cache_read 21,620   $0.0115   <- new session id
```

**19x on a cache hit, and `--fork-session` inherits the context *and* the cache while issuing a
fresh session id** — verified by recalling a token seeded in the parent. That is the mechanism
`basicly-ejdm` and `basicly-xjd2` were both open questions about: seed one session with the corpus,
fork it per lane, and every lane gets the context at cache-read price with its own session. It also
relocates the per-dispatch floor — 21.6k tokens of system prompt and tool definitions is a *cache
miss* cost, not a token cost, so 41 cold dispatches is ~$8.90 of floor against ~$0.46 forked.

**Two corrections to the figure above, both measured on claude 2.1.231, 2026-08-13**
[M, basicly-w20y]. Neither retracts the mechanism; both change how a lane is sized against it.

**The 19x denominator is the host floor, not a repo corpus.** The ~21,800 cached tokens are the
system prompt plus tool definitions. Cache reads bill at roughly 10% of the standard input rate, so
forking a *corpus* is nearer **10x on that corpus** than 19x. Quoting 19x for corpus reuse
over-promises by about half.

**The cross-directory penalty is one-time per working directory, not per fork.** A fork whose cwd
differs from the seed's pays a partial read once, because cwd, platform and a git-status snapshot sit
inside the cached prefix — and the vendor notes this "includes worktrees of the same repository".
Every later fork into that *same* directory reads the prefix whole:

```text
dir B  1st fork    cache_create 2,768   cache_read 19,075   $0.0376   (87.3% read)
dir B  4th fork    cache_create     0   cache_read 21,843   $0.0113   (100%  read)
dir C  1st fork    cache_create 5,578   cache_read 16,265   $0.0643   (74.5% read)
dir C  2nd fork    cache_create     0   cache_read 21,843   $0.0113   (100%  read)
```

The earlier "5.4x degradation, ~87% recovery" figure was a **first-fork measurement read as a steady
state**. The position control is what separates the two: re-running the *identical* no-flag command
later in the sequence returned `cache_create 0`, so the create cost belongs to the directory rather
than to the command.

For lane sizing that means a worktree-per-lane design pays the penalty once per worktree, so a lane
dispatched **once** pays it whole and a lane dispatched repeatedly — repair, sub-tasks — amortises
it. The penalty is a function of dispatches-per-worktree, which is a number the engine controls.

**`--agent` composes with `--resume --fork-session`.** Measured: a fork carrying
`--agent implementer` returned the parent's seeded canary, so an agent-scoped fork still inherits the
context.

**Whether `--exclude-dynamic-system-prompt-sections` composes with `--agent` remains
unestablished.** A probe was run and is deliberately *not* reported as a result: its arms ran
sequentially against one warming prefix, and the position control above shows ordering alone
reproduces the whole effect the flag appeared to have. Settling it needs one fresh directory per arm
with the arm order randomised. `--help` still says the flag is ignored with `--system-prompt`, and
`--agent` is documented as replacing the system prompt the same way, but that chain is inference.

### 15.6 The context ceiling is deleted, not retuned [D]

`DEFAULT_CONTEXT_CEILING = 0.6` against a declared 1,000,000 window is 600,000, and **0 of 79
recorded lanes cross it** (max observed 403,051). Against the stale hardcoded 200,000 it was
120,000 and **51 of 79 crossed**, which is where the twelve overrun follow-ups came from. A gate
that has never once fired correctly — first at a fifth of its intended point, then never — is
removed rather than given a third constant. Its follow-up machinery goes with it.

**The fraction frame itself is refuted, independently of this gate's telemetry** [S]. Degradation is
driven by absolute tokens of material the model must reason over, not by window fraction: NoLiMa
(arXiv 2502.05167) measures the same ~8-16K effective band across an 8x window gap, and RULER
(arXiv 2404.06654) finds claimed-versus-effective ratios from 25% to over 100%, so no constant
fraction exists. The "50-70% of window" rule appears in **no primary source**. One fractional effect
is real and it is behavioural — models cut corners near their *perceived* limit — which makes it a
guard, never a fill target. Recorded so a fraction is not re-proposed.

**One clause of the reproducibility set is still unbuilt** [M 2026-08-08]. `run_record` persists
`prompt_sha256`, `model`, `model_tier`, `model_source` and `adapter_version`, but **not the ids of
the found-info records folded into the prompt** — so a re-dispatch is diffable in its prompt and not
in its inputs, which is short of what D9 requires.

### 15.7 D23 — a sizing control that never fires correctly becomes observability [D]

**Decided 2026-08-08 by the owner**, generalising §15.6 from the one gate it removed to the class
it belongs to. The owner's form: *the user can always stop the work themselves, but only if they
can see what is happening* — so a number's job is to be **visible**, and only a number that has
earned it may also **block**.

The discriminator is evidence, not category. Every control in the engine was checked for a
recorded **correct** firing [M, 2026-08-08, over `run-records.json` and the tracker]:

| Control | Correct firings | Disposition |
| --- | --- | --- |
| Grant spend ceiling | **5** (`stopped_bound → {'spend': 5}`) | **stays a control** |
| Rework cap (`max_rework 2`) | **78 markers** — 43 merge, 25 dispatch, 10 verify | **stays a control**, with the fix that a flake must not spend an attempt |
| `stall_after 900` | n/a — already "a flag, never a kill" | already the target shape |
| Runner timeout (hard kill) | **0** — no timeout in any `stopped_bound` | → observability |
| Working-set band **ceiling** | **0 correct.** Retuned 64k→112k→56k→72k→132k→200k→248k, each time chasing the last dispatch; twice in one landing on 2026-08-08 | → observability |
| Working-set band **floor** | never refuses by construction | already advisory; `basicly-esxp` asks whether it should bind |
| Context ceiling | **0** — §15.6 | deleted |

**So the rule is not "demote the controls".** Two of them work and pay for themselves; the ones
with no correct firing are all *sizing* estimates — predictions about how large a unit of work
will turn out to be — and this repo has never once made that prediction well. A prediction that
blocks is a prediction that must be right; a prediction that reports costs nothing when it is
wrong.

**What a demoted control must still do**, so this is not simply deletion: it is recorded on the
run, surfaced in `preflight` and in the pass line, and kept falsifiable against the ledger. The
failure §15.6 describes is a gate firing at a fifth of its intended point *for months* with the
telemetry already contradicting it — that is a visibility failure as much as a calibration one,
and demotion without a visible number reproduces it.

`basicly-rrah` is the prerequisite: until a lane's transcript survives its exit, "observability"
names an intention rather than a mechanism.

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
