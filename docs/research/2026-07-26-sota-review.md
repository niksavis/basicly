# State-of-the-Art Review — Agent Harnesses, Skills, and Trackers

Reviewed 2026-07-26. Eleven repositories read at pinned revisions, plus first-party vendor
documentation and a general literature sweep. Provenance, licences, and confidence grades are in
Appendix A; **read §2 of that file before touching the tracker work** — one
licence is not what our own docs claimed.

This document is **findings, not a plan.** Its job is to be the durable evidence base we cite
later, so that a future design decision can point at a paragraph here instead of re-running the
review. Proposals derived from it live in the design documents indexed in §9.

## 1. What was actually being asked

The goal is to make `basicly` enterprise-grade and competitive with this field, on its own
strengths, with no obligation to preserve current implementation. So the review is organised
around four questions, and §§5–7 answer them:

- Where is `basicly` **already ahead**, and is the lead real or asserted?
- Where is it **behind**, and how much does the gap cost?
- What in the field should be **rejected**, with a reason better than "not our style"?
- What **vocabulary** should we adopt, since a shared word is the cheapest thing to import and
  the most expensive thing to get wrong?

One framing correction up front, because it changes how the rest reads. The field has largely
converged on a name for what `basicly` is: **harness engineering** — the claim that the
deterministic scaffolding around the model matters more than the model choice. That is `basicly`'s
existing thesis, arrived at independently. The competitive question is therefore not *whether*
the harness approach is right; it is whether our harness is measurably better than the eight
other projects that also believe it.

## 2. Per-repository findings

Each entry: what the project is, what is worth taking, and what is worth refusing.

### 2.1 mattpocock/skills — the vocabulary of skill authoring

A deliberately small, hackable skill set positioned explicitly *against* process-owning
frameworks ("GSD, BMAD, Spec-Kit … take away your control and make bugs in the process hard to
resolve"). Its highest-value artifact is not a workflow but a **domain model for what makes a
skill good**: `writing-great-skills/GLOSSARY.md`.

**Take — the authoring vocabulary.** These are precise, mutually distinct terms we currently
lack, and every one of them is a lever on a failure we have hit:

| Term | What it names | Why we need the word |
| --- | --- | --- |
| **Predictability** | the agent taking the same *process* every run — not the same output | the root virtue; a brainstorming skill should predictably diverge |
| **Context load** vs **cognitive load** | tokens spent on an always-loaded description vs what the *human* must remember | names the real trade-off in model- vs user-invoked; cognitive load is "the price of human agency", not a cost to minimise |
| **Information hierarchy** | steps → in-file reference → disclosed reference | a ladder, so "where does this line go" has an answer |
| **Completion criterion** | the condition telling the agent a step is done, on two axes: *clarity* (resists premature completion) and *demand* (sets legwork) | our gates have this; our prose steps mostly do not |
| **Leading word** | a compact pretrained concept (*tracer bullet*, *fog of war*) that anchors a region of behaviour in one token | the cheapest compression available: recruits priors instead of paying definition tokens |
| **Premature completion** | ending a step early because attention slipped to *being done* | a between-steps failure; cure the criterion first, hide later steps only if that fails |
| **No-op** | a line the model already obeys by default | the single best pruning test; explicitly *model-relative*, settled by running the skill, not by debate |
| **Negation** | steering by prohibition, which makes the banned behaviour more available | "don't think of an elephant"; prompt the positive |
| **Sediment** / **sprawl** / **duplication** | length from staleness / length itself / length from repeated meaning | three different diseases with three different cures |

**Take — the invocation axis as a real design decision.** Model-invoked keeps a description and
so is reachable by the agent *and* by other skills, at permanent context cost. User-invoked
strips the description: zero context load, but nothing except the human can reach it. The
consequence they draw is sharp and correct: **a router skill can only ever hint, never fire**,
because the skills it points at have no descriptions. Our catalog has no such axis; every
projected skill is effectively model-invoked, so we pay context load on descriptions for skills
only ever invoked by hand.

**Take — `implement` as a lesson in restraint.** The whole skill is nine lines: use `/tdd` at
pre-agreed seams, typecheck often, full suite once at the end, `/code-review`, commit. It works
because the *other* skills carry the discipline. A long skill is often evidence that a
neighbouring skill is missing.

**Take — two-axis code review.** `Standards` (repo conventions plus a fixed Fowler smell
baseline) and `Spec` (does it implement what the issue asked) run as **parallel sub-agents** and
are reported **separately and un-reranked** — because "a change can pass one axis and fail the
other", and merging them lets one mask the other. Our roster's Remo has lenses; it does not have
this no-reranking rule, and Vera's acceptance judgment is arguably the Spec axis under a
different name.

**Take — `wayfinder`'s fog of war.** For work too big for one session: a map issue whose children
are *decision* tickets, plus an explicit **Not yet specified** section for in-scope work you can
see coming but cannot phrase sharply yet, and a separate **Out of scope** section that never
graduates. The test for which is which is exact and useful: *can you state the question
precisely now?* — not *can you answer it?* This is a better answer than we have to "what do you
do with a decomposition you cannot finish", and it maps directly onto our decision queue.

**Take — "refer by name, never by id."** *"A wall of `#42, #43, #44` is illegible; names read at
a glance."* Our engine output is dense with bare bead ids.

**Refuse — the local-markdown tracker fallback.** Sensible for their audience, strictly worse
than what we have.

### 2.2 obra/superpowers — the most mature subagent-orchestration loop in the set

A complete methodology (brainstorm → spec → plan → subagent-driven execution) shipped as a plugin
to eleven harnesses. It is the closest existing analogue to our factory, and it has clearly been
run enough to have scar tissue. The scar tissue is the valuable part.

**Take — the bounded fix loop with capability escalation.** Five rounds maximum per task.
Rounds 1–3 **resume the original implementer** (its context is intact; it knows the code and its
own choices). Rounds 4–5 dispatch a **fresh implementer on a more capable model**, framed as *"a
prior implementer attempted this task N times; you own it now. Read the report file for what was
tried."* Their reasoning is exactly right: *"a loop that survives three resumes usually means the
implementer cannot see its own problem — fresh eyes and a capability bump in one move."* Our
rework cap bounces to the same tier with the same framing every time.

**Take — the ledger as the recovery map.** *"Conversation memory does not survive compaction. In
real sessions, controllers that lost their place have re-dispatched entire completed task
sequences — the single most expensive failure observed."* Hence a per-plan ledger file whose
first line names its plan, with `Task N: complete (commits a7..b7, review clean)` lines, and the
instruction: **after compaction, trust the ledger and `git log` over your own recollection.** We
have this property already — `br` is the ledger and the engine keeps no side-state — which is a
genuine structural lead. Worth noting we got for free the thing they had to learn the hard way.

**Take — "adjudicate only at the cap."** When round 5 still leaves findings open, the controller
adjudicates each one: *reviewer wrong or contestable* → park with a ruling; *real but nothing
builds on it* → park with a ruling; *real and load-bearing* → **STOP and report BLOCKED**. And
critically: *"Adjudicating earlier to end a loop is pre-judging with a different name … a silent
discard is forbidden."* This is the disposition path our R4 needs, expressed better than R4
expresses it.

**Take — the no-pre-judging rule, stated as a checkable string test.** *"If the prompt you are
writing contains 'do not flag', 'don't treat X as a defect', 'at most Minor', or 'the plan chose'
— stop: you are pre-judging, usually to spare yourself a review loop."* A rule an observer can
mechanically check on the dispatch prompt is worth ten rules of good intent. Our dispatch
assembly is deterministic code, so this is enforceable as a *lint on the emitted prompt*, not
merely as guidance.

**Take — hand artifacts over as files, never as pasted text.** *"Everything you paste into a
dispatch prompt — and everything a subagent prints back — stays resident in your context for the
rest of the session and is re-read on every later turn."* So: a `review-package` script writes
commit list + stat + `git diff -U10` to one file and the reviewer gets the *path*. And the
measured failure: *"a real session's dispatch hit 42k chars of which 99% was pasted history."*
Also: never `HEAD~1` as the review base — it silently truncates multi-commit tasks; record BASE
before dispatching.

**Take — "one fix dispatch, not one fixer per finding."** *"Per-finding fixers each rebuild
context and re-run suites; a real session's final-review fix wave cost more than all its tasks
combined."*

**Take — writing skills IS TDD.** RED: run the pressure scenario **without** the skill and record
the rationalisations verbatim. GREEN: write the minimal skill addressing *those* rationalisations.
REFACTOR: close the new loopholes. Iron law: *"NO SKILL WITHOUT A FAILING TEST FIRST"* — and
*"if you didn't watch an agent fail without the skill, you don't know if the skill teaches the
right thing."*

**Take — "Match the Form to the Failure," which is the most empirically grounded finding in the
entire review.** They ran head-to-head wording tests and report that *the form which bulletproofs
one failure type measurably backfires on another*:

| Baseline failure | Right form | Wrong form |
| --- | --- | --- |
| Skips a rule under pressure (knows better, does it anyway) | prohibition + rationalisation table + red flags | soft guidance ("prefer…", "consider…") |
| Complies but output has the wrong shape | **positive recipe / contract**: state what the output IS, its parts, in order | prohibition list ("don't restate", "never narrate") |
| Omits a required element from something it already produces | **structural**: a REQUIRED slot in the template it fills | prose reminders near the template |
| Behaviour should depend on a condition | conditional keyed to an **observable predicate** | unconditional rule + exemption clauses |

With two corollaries they measured: *"appending a single nuance clause to a winning recipe
degraded it from consistent to noisy"*, and *"exemption clauses don't scope — 'this limit doesn't
apply to code blocks' still suppresses code blocks."* And the reason prohibitions fail on shaping
problems: under a competing incentive, *"agents negotiate with 'don't X'. A recipe leaves nothing
to negotiate."*

**Take — micro-test the wording before the scenario.** One fresh-context sample per call, system
prompt = the realistic context the guidance will live in, **always include a no-guidance
control** (*"if the control doesn't exhibit the failure, there is nothing to fix — stop, don't
author the guidance"*), 5+ reps, **read every flagged match manually** because template echoes
and quoted counter-examples masquerade as hits, and treat **variance as a metric**: *"five
different interpretations across five reps means the wording isn't binding."*

**Take — the description must not summarise the workflow.** Their observed failure: a description
saying "code review between tasks" caused an agent to do **one** review when the skill body
specified two. *"Descriptions that summarise workflow create a shortcut agents will take. The
skill body becomes documentation agents skip."*

**Refuse — `<EXTREMELY-IMPORTANT>` / "you do not have a choice" shouting.** It is the negation
anti-pattern their sibling repo names, applied to itself, and it is unnecessary where a hook can
enforce the same thing. We have hooks.

**Refuse — the model-selection rule as stated**, but keep its best sentence. See §4.1: their
"least powerful model that can handle each role" collides with our R5, and the honest resolution
is neither project's headline but their footnote.

### 2.3 addyosmani/agent-skills — the only project measuring whether its skills route

24 skills, 4 personas, 8 slash commands, hooks, references — and, uniquely in this set, an
**eval harness for the catalog itself**.

**Take — the three-tier eval model. This is the single most directly transferable artifact in the
review.**

| Tier | Checks | Runs | Cost |
| --- | --- | --- | --- |
| 1 Structural | frontmatter, naming, required sections, command parity | CI | free |
| 2 Trigger & routing | positive prompts rank their skill top-k; negative prompts don't; no two descriptions near-collide | CI | free |
| 3 Behavioural | an agent following the skill satisfies its `expectations[]` | on demand | tokens |

Tier 2 is the part nobody else has and the part we most need. It is a deliberately **lexical
approximation** (stemmed TF-IDF over descriptions) and they say so — it cannot judge semantics,
that is Tier 3's job — but it catches the two failure modes that dominate real trigger bugs: *a
description missing the vocabulary users actually say* (false negative) and *an over-broad
description that outranks the right skill* (false positive). Concrete mechanics worth copying
wholesale:

- A **rank-1 rate** metric (share of positive prompts ranking their skill *first*, not merely
  top-k), with CI enforcing a floor **below** the current baseline so an unrelated edit does not
  immediately redden CI — *"raise the floor as routing improves; never lower it to make a
  regression pass."*
- A **pairwise description-collision check**: error at ≥75% similarity, warn at ≥50%.
- Negative cases declare an `owner` skill, so the runner asserts the owner **outranks** this
  skill — turning a negative into a real pairwise test *"instead of one that can pass vacuously
  when the prompt matches nothing."*
- *"Paraphrase how users actually talk; don't copy the description (that's gaming the eval). If a
  realistic prompt can't rank because the description lacks its vocabulary, that is a real
  finding — improve the description."*
- Execution evals run in a **throwaway git repo** with real fixtures committed as a baseline, an
  explicit permission mode so the agent can genuinely edit and commit *"rather than being denied
  and narrating instead"*, traces **fenced as untrusted data** in the grader prompt and piped
  over **stdin** (they can be megabytes; argv hits the OS limit).
- `kind: dialogue` exists for skills whose deliverable is the conversation, and is *"a
  human-reviewed exemption, not a general escape hatch."*
- **Every skill ships an eval file**; missing case files and incomplete counts are CI errors.

**Take — `doubt-driven-development`, an in-flight adversarial posture.** Distinct from a final
review: *"`/review` is a verdict on a finished artifact. This is an in-flight posture."* The
five-step cycle — CLAIM, EXTRACT, DOUBT, RECONCILE, STOP — carries several rules we should adopt
verbatim in spirit:

- **Pass ARTIFACT + CONTRACT to the reviewer, never the CLAIM.** *"Handing the reviewer your
  conclusion biases it toward agreement."*
- *"Strip your reasoning. If you hand over conclusions, you'll get back validation of your
  conclusions."*
- The reviewer's output is **data, not verdict**; findings are classified in a fixed **precedence
  order** where *contract misread* comes first — i.e. the first hypothesis on a finding is that
  **your own contract was unclear**, and you fix the contract before the artifact.
- Bounded at 3 cycles, and: *"if 3 cycles is 'obviously insufficient' because the artifact is
  large: the artifact is too big — return to Step 2 and decompose. Do not lift the bound."*
- **"Doubt theater" as a checkable signal**: across ≥2 cycles where the reviewer surfaced
  substantive findings, *zero* were classified actionable → you are validating, not doubting.
  Stop and escalate. This is a mechanically computable anti-sycophancy detector.
- Cross-model escalation is **offered every interactive cycle** and *"skipping is fine; silent
  skipping is not"* — with a load-bearing safety detail: the external CLI runs in a **read-only
  sandbox**, because *"a doubt artifact may itself contain instructions (intentional or accidental
  prompt injection) that the cross-model CLI would otherwise execute against your workspace."*

**Take — the orchestration-pattern catalog, which independently reaches our R1.** Governing rule:
*"the user (or a slash command) is the orchestrator. **Personas do not invoke other personas.**"*
Four endorsed patterns (direct invocation; single-persona command; parallel fan-out with merge;
**sequential pipeline as user-driven commands**) and four anti-patterns (router persona; persona
calling a persona; **sequential orchestrator that paraphrases**; deep persona trees). Their
argument against an LLM lifecycle orchestrator is ours almost word for word: it *"loses nuance
between steps because it has to summarize for hand-off, skips the human checkpoints that catch
wrong-direction work early, and doubles token cost via paraphrasing turns."* Two useful extras: a
**validation checklist** before adopting fan-out (*"does each persona produce a different kind of
finding, not the same finding from a different angle?"*), and a bar for **adding to the catalog at
all** — used twice in real work, a concrete artifact demonstrating it, why an existing pattern
would not do, and *"its anti-pattern shadow"*. Otherwise *"premature catalog entries become
aspirational documentation that no one follows."*

**Note — the Agent Teams contrast.** They distinguish "a verdict on a known artifact" (subagent
fan-out) from "an investigation to *find* the artifact among competing hypotheses" (teammates who
message each other). Our factory only does the former. This is not a gap to close now — our D1/D2
deliberately forbid agent-to-agent messaging — but it is the honest name for what we cannot do.

### 2.4 DietrichGebert/ponytail — how to prove a guidance artifact works

A single-discipline skill (write the laziest solution that works) that matters here almost
entirely for its **measurement methodology**, which is the most rigorous in the set.

**Take — the benchmark design, in full.** The headline is *-54% LOC, -22% tokens, -20% cost, -27%
time, 100% safe* against a no-skill baseline. What earns trust is the construction:

- **Unit of work is a real headless agent session** editing a real pinned public repo, scored on
  `git diff` added lines — not a bare-model completion scored on answer length.
- **The baseline is the same agent with no skill**, which removes the chatty-model artifact.
- **Two control arms**, each killing a specific alternative explanation: a *terse-prose* skill
  (if the effect were "be brief", this would match it) and the seven-word naive prompt *"Follow
  YAGNI principles, and prefer one-liner solutions"* (if a short prompt would do, no skill is
  needed).
- **A separate adversarial safety tier** that *executes* the produced code against hostile input,
  so "less code" cannot be bought by dropping validation. The naive-prompt arm scores 95% here
  while ponytail scores 100% — the safety tier is what makes that difference visible.
- **Per-cell isolation**, `n=4`, and honest reporting of where the effect is ~0 (*"near zero on
  code that is already minimal"*).
- **A disclosed contamination bug**: an earlier run showed a ~4% gap because the plugin's
  `SessionStart` hook fired on *every* arm, so *"the baseline was secretly running ponytail."*
  Fixed by `--setting-sources project,local` plus exactly one plugin per arm. They published this
  because *"it is the kind of error that makes a benchmark lie, and finding it is the reason to
  trust the rest."*
- **A retracted claim.** The old single-shot 80–94% figure is explicitly reframed as *"the
  per-task ceiling, not the average"* after a critic pointed out the baseline was unfair.

That last pair is the standard we should hold ourselves to. Our own harness-vs-bare pilot (`basicly-8z52`)
already flags the same class of problem in its own results ("the C4 win is partly circular"),
which is encouraging — we independently know the trap. We have not yet built the controls that
answer it.

**Take — the decision ladder as a form.** Seven numbered rungs, *stop at the first that holds*.
It is dense, ordered, and mechanically self-checkable ("which rung did you stop at?"), and it
carries a guard against its own worst failure: *"the ladder runs after you understand the
problem, not instead of it … laziness that skips comprehension to ship a small diff is the
dangerous kind: it dresses up as efficiency and ships a confident wrong fix."*

**Take — the `ponytail:` debt marker, harvested mechanically.** A deliberate shortcut leaves an
inline comment naming **its ceiling and its upgrade trigger** (`# ponytail: global lock,
per-account locks if throughput matters`), and a separate skill greps them into a ledger — tagging
any marker with **no upgrade trigger** as `no-trigger`, *"those are the ones that silently rot."*
A self-documenting debt convention with a mechanical harvest and a rot detector, at the cost of
one comment.

**Take — capability tiers for portability.** Their 20-host adapter table classifies each host as
**instruction-tier** (only reads a rules file), **skill-tier**, or **plugin-tier** (hooks,
commands, mode switching), with a thin-adapter rule: *"when a host supports skills or hooks, point
it at the existing `skills/` and `hooks/` files."* We project to three agent families as if they
were equivalent; they are not, and naming the tier makes the difference in delivered guarantee
explicit rather than silent.

### 2.5 techygarg/lattice — composition tiers and a living context layer

27 skills in three tiers, plus a `.lattice/` directory that accumulates project knowledge across
feature cycles.

**Take — atoms / molecules / refiners.** Atoms are single-principle guardrails; molecules are
multi-step workflows that **compose** atoms; **refiners are guided interviews that produce the
project's own standards**, customising how atoms behave for that team. The third tier is the
interesting one and we have no equivalent: our catalog ships guidance and lets a consumer
override it in `.basicly-local/`, but nothing *interviews* the consumer to generate their overlay.
That is the difference between a configurable product and one that configures itself.

**Take — the two-pass generation model, and its stated reason.** *"Asking AI to generate and
validate simultaneously is unreliable — like asking a writer to write and proofread in the same
pass. The creative task and the analytical task compete for attention, and one always suffers."*
Hence generate → **STOP** → verify against checklists → present. We already have the cross-
dispatch version of this (Kai then Vera); the *within-dispatch* version is much cheaper and we do
not use it.

**Take — the verification hierarchy.** Level 1 per-component self-check → Level 2 cross-component
coherence → Level 3 independent review with no generation bias. *"Same reason human teams have
self-review, then peer review, then QA."* Note Level 2 — cross-component coherence — is the level
our lane-scoped verification structurally cannot see, and the level our session-level Remo
architecture lens is supposed to cover.

**Take — the review log as a trend instrument.** Every review appends scope, atoms applied,
finding counts by severity, key findings, strengths to `.lattice/reviews/review-log.md`, on a
rolling 15–20 entry window. What the trend answers is exactly what our roster cannot currently
answer: *which atoms catch the most issues, whether anti-patterns recur (learnings aren't being
absorbed), whether findings per review decline over time.*

**Take — the AI-compliance techniques, as candidate forms to micro-test.** Imperative language
with a cognitive boundary (*"STOP and verify ALL of the following"* beats *"apply these checks as
you write"*); numbered labelled constraints over prose; **active** anti-pattern scans as
checkboxes rather than passive "here are bad patterns"; and **show your work** — *"silent
compliance is unreliable; visible compliance is accountable."* These are hypotheses, not results
— which is precisely what §2.4's harness is for.

**Refuse — agent-writable standards.** Their learnings flywheel has the AI propose and the user
confirm. Our R9 is stricter (catalog changes are human-only at every grant level) and the
asymmetry argument behind it still holds: a wrong implementation bounces off a gate, a wrong
fragment is *absorbed* and silently degrades every later lane. Keep R9.

### 2.6 open-gsd/gsd-core — the closest cousin, and the best gate taxonomy

A phase loop (Discuss → Plan → Execute → Verify → Ship) driven by 34 named agents, with a thin
orchestrator that *"never touches source files"* and `.planning/` as the durable substrate. Read
it as the mature version of the same bet we are making.

**Take — the four-gate taxonomy.** Clean, complete, and immediately applicable:

| Gate | Purpose | Behaviour | Recovery |
| --- | --- | --- | --- |
| **Pre-flight** | validate preconditions before starting | blocks entry; **no partial work created** | fix the precondition, retry |
| **Revision** | evaluate output quality, route back to the producer | loops with specific feedback, **bounded by an iteration cap** | producer addresses, checker re-evaluates |
| **Escalation** | surface an unresolvable issue for a decision | pauses, presents options, waits for a human | human chooses, workflow resumes |
| **Abort** | terminate to prevent damage or waste | stops, **preserves state**, reports reason | fix root cause, restart from checkpoint |

With a selection heuristic — *"start with pre-flight. If the check happens after work is
produced, it is a revision gate. If the revision loop cannot resolve the issue, escalate. If
continuing is dangerous, abort."* — and a cap-sizing rule: *"the cap should reflect the cost of
each iteration; expensive operations get fewer retries."*

**Take — stall detection.** The revision gate *"escalates early if issue count does not decrease
between consecutive iterations."* Cheap, deterministic, and it catches the CAAF paper's
*stochastic oscillation in reflection loops* before the cap burns. Our bounded rework has a cap
and no stall detector, so we pay for every remaining round of a loop that has already stopped
converging.

**Take — the `<adversarial_stance>` block, with its "how reviewers go soft" list.** Their checker
agents open with a **FORCE stance** — *"Assume every plan set is flawed until evidence proves
otherwise. Your starting hypothesis: these plans will not deliver the phase goal"* — followed by
an explicit enumeration of the ways this role's judgment degrades. For the plan checker:
*accepting a plausible-sounding task list without tracing each task back to a requirement;
crediting a decision reference without verifying the task delivers its full scope; treating scope
reduction ("v1", "static for now") as acceptable; letting dimensions that pass anchor judgment — a
plan can pass 6 of 7 dimensions and still fail on the 7th; issuing warnings for what are actually
blockers to avoid conflict with the producer.* That last one names reviewer conflict-avoidance as
a predicted failure and pre-empts it. This is the concrete hardening our Vera and Remo prompts
need, and it is far better than R8's quirks at doing the same job.

**Take — severity as an output-contract requirement.** *"Issues without a severity classification
are not valid output."* A structurally required field, which is exactly the right form per §2.2's
form-matching table.

**Take — read-only enforcement by role, stated positively.** The Nyquist auditor:
*"Implementation files are READ-ONLY. Only create/modify: test files, fixtures, VALIDATION.md.
Implementation bugs → ESCALATE. Never fix implementation."* Plus a resolution contract that admits
no silent third state: *"Every gap must resolve to FILLED (test passes), ESCALATED (BLOCKER), or
explicitly justified SKIP"* — and *"a skipped gap is an unverified requirement, not a resolved
one."*

**Take — the escape hatch, which we are missing entirely.** `/gsd-quick` and `/gsd-fast` exist for
work below the loop's threshold, and the threshold is written down: *"if the task could be fully
specified in a single, short prompt and completed in one agent turn without further
clarification, skip the phase loop."* And they price their own ceremony honestly: *"the phase
loop introduces real friction … for a small, well-understood change, that overhead is not
justified."* Our `harness-loop` is the mandated path for "non-trivial work" with no named
primitive below it and no written threshold, so the ceremony cost of small work is unpriced and
the rule is unenforceable by anyone but the agent's own judgment.

**Take — runtime context-headroom measurement.** Lifecycle hooks (`PreCompact`, `Stop`,
`SubagentStop`) give a per-turn signal *"to inspect how much context has been consumed and emit a
warning before the window is exhausted"*, with the reason stated: an auto-compaction can
*"silently discard planning state the orchestrator was relying on."* They are honest that it is
*"a heuristic … a signal, not a guarantee."* This is the empirical complement to our static D8
sizing bands, and it is compatible with our finding that a fixed 50–70% rule was unfounded:
measure, do not legislate.

**Take — `effort:` per skill.** Heavy orchestrator skills declare `effort: max`, quick status
skills `effort: low`. A budget signal orthogonal to model tier — and their footnote is a real
trap worth recording: they *removed* `context: fork` from spawning orchestrators because *"a
forked subagent context does not have the `Agent` tool"*, i.e. isolating an orchestrator breaks
the orchestration. Context isolation must come from the children, never from forking the parent.

**Take — Diátaxis documentation structure.** Tutorials / how-to / reference / explanation, with
each document declaring which it is. Our `docs/` is a flat pile of design documents with no
tutorial or how-to layer at all, which is a real enterprise-readiness gap independent of any
mechanism in this review.

**Refuse — `--no-verify` for wave commits.** They have executors commit with `--no-verify`
*"to prevent build-lock contention … when multiple agents commit in parallel"*, then run the hook
once per wave. The contention is real and we should record it as a known problem, but the
mitigation is forbidden by our rules and our merge queue already solves it a better way: lanes
commit on isolated worktree branches and the queue serialises landing, so the gate runs on every
landing without contention. Keep our stance; take their problem statement.

**Refuse — 34 agents.** Not because the count is wrong for them, but because most of their roster
is researchers and auditors that our R3 admission test would either fold into an existing persona
or replace with deterministic code. Their roster is the *reason* R3 exists. That said, §4.3 notes
one place where their fan-out genuinely beats ours.

### 2.7 satococoa/wtp — declarative worktree provisioning

A focused Go tool extending `git worktree`: derived paths from branch names, and a `.wtp.yml`
declaring **typed post-create hooks** — `copy` (explicitly permitted for gitignored files like
`.env`, always resolved relative to the *main* worktree), `symlink`, and `command`.

**Take — the declarative typed-hook config.** Our `basicly worktree` provisions dependencies and
git hooks imperatively in code. A declared list of typed steps is inspectable, diffable,
overridable per consumer, and testable step-by-step. The `copy`-from-main-worktree type in
particular is the exact shape of a problem we solve ad hoc, and naming `.env` as the motivating
case matches our own experience.

**Take — `remove --with-branch` as one atomic operation.** *"Remove worktree, then manually delete
the branch. Forget the second step? Orphaned branches accumulate."* Our own history has orphaned
harness branches and stranded session JSON after a failed advance; the fix shape is atomicity,
not a longer checklist.

**Refuse — the platform matrix.** Linux and macOS only. We must support Windows, which rules out
adopting the tool and constrains any mechanism we borrow from it.

### 2.8 headroomlabs-ai/headroom — context economics as a product

A local compression layer between agent and provider (Apache-2.0): content-aware compressors for
JSON/AST/prose, reversible with a retrieval tool, plus a proxy and MCP surface.

**Take — reversible compression with a retrieval tool, as a design pattern.** Originals are cached
locally and the model calls `headroom_retrieve` if it needs them. Applied to our dispatch bundles:
when a bundle approaches the D8 band, the current answer is to *omit* — and omission is exactly
the failure mode that made us cut the Scout, because nothing detects what was left out. Compress
reversibly and hand the implementer a retrieval affordance, and the omission becomes recoverable
by the agent rather than fatal.

**Take — cache alignment as a first-class cost lever.** Their `CacheAligner` *"stabilizes prefixes
so provider KV caches actually hit."* Our dispatch bundle is already a deterministic function of
`br` state (D6), which means we are unusually well placed to also make it *prefix-stable* —
ordering the bundle so the invariant part (role prompt, global constraints, repo conventions)
precedes the variable part (this bead, this diff). That is a pure-win cost reduction available
without changing any content, and we have never considered it.

**Take — `headroom learn`'s default target.** It mines failed sessions and writes corrections to
`CLAUDE.local.md` — **gitignored by default** — rather than the shared instruction file. A useful
middle rung between our two options (silently drop the retro, or ask a human to amend the shared
catalog): a machine-local, unshared lane for a proposal that has not yet earned team-wide
authority.

**Refuse — adopting headroom itself.** A compression proxy in the critical path is precisely the
unowned external dependency `work-tracker.md` §1 argues against, and it would sit between us and
the provider — a worse position than the tracker ever occupied.

### 2.9 Graphify-Labs/graphify — provenance labels, and a deterministic answer to the Scout

Maps a codebase into a queryable knowledge graph. Code is parsed with **tree-sitter AST —
deterministic, no LLM**; only docs, PDFs and media take a semantic pass.

**Take — confidence labels on every derived edge. This is the highest-value idea in this repo.**

| Label | Meaning |
| --- | --- |
| `EXTRACTED` | explicitly stated in the source (an import, a direct call) |
| `INFERRED` | a reasonable deduction (call-graph second pass, co-occurrence) |
| `AMBIGUOUS` | uncertain — **flagged for human review** in the report |

Applied to our tracker, this closes a real hole. Our dependency edges and found-info records are
currently unlabelled: an edge a human asserted and an edge an agent guessed from a scope overlap
are indistinguishable in the graph, even though only one of them should be trusted to gate a
landing. Labelling them makes the graph auditable, tells the engine which edges need confirmation
before they constrain the merge queue, and gives `AMBIGUOUS` a natural disposition — the decision
queue. It also fits D11 exactly: evidence should carry its own provenance.

**Take — the deterministic-extraction principle, which reopens a decision we closed.** Our roster
cut the Scout because a cheap-tier pre-reader's characteristic error — a slightly incomplete file
list — is undetectable and silently narrows the implementer's view. That reasoning is sound *for a
model-based scout* and does not apply to a **deterministic AST-derived localisation artifact**: a
tree-sitter call graph does not hallucinate a call site, its coverage is a checkable property of
the parser, and it costs no tokens. That is the same principle our own R5 already states — pay no
model where deterministic code suffices — applied to the one place we concluded the work simply
should not happen. §4.3 records this as the review's most consequential single reopening.

**Take — pipeline-of-pure-functions architecture.** Seven stages, each one function in its own
module, communicating through plain dicts, *"no shared state, no side effects outside
`graphify-out/`"*, with a schema validated before the next stage consumes it. A good shape for
our own projection pipeline to be measured against.

### 2.10 gastownhall/beads (MIT original) — the fork in the road for the tracker

The upstream original, now **backed by Dolt** — a version-controlled SQL database with cell-level
merge, native branching, and sync via Dolt remotes under `refs/dolt/data`.

**Take — the confirmation that our divergence is real and deliberate.** Upstream's answer to
"the DB and the export disagree" was to make the **DB more authoritative**: *"The local Dolt
database is the source of truth … `.beads/issues.jsonl` is an export. It exists for viewers,
interchange, migration, and backup. It is **not** the canonical cross-machine sync channel."*
`work-tracker.md` chooses the opposite: the **log is the truth** and every other file is a
disposable projection. Both are coherent; they are a genuine fork, and ours is the one that does
not require a second binary or a database.

**Take — three concrete migration risks this creates for our §5 import plan.**

1. **The JSONL path is a second-class citizen upstream and will drift.** Our import plan reads a
   format its owner has explicitly demoted.
2. **`import` is upsert-only** — *"it cannot infer that records absent from an export were
   deleted, pruned, or simply never exported."* So a JSONL round-trip cannot express deletion,
   and our importer must handle tombstones as a first-class concern rather than discovering this
   at cutover.
3. **The upgrade procedure is itself the argument for owning this.** Crossing a schema migration
   on a remote-backed database requires *"exactly one designated clone"* to run `bd migrate` and
   `bd dolt push` while *"other clones install the new binary and run `bd bootstrap`."* That is a
   coordinated, human-sequenced, multi-machine operation in our critical path — precisely the cost
   `work-tracker.md` §1 predicted from an unowned dependency, now observed rather than
   hypothesised.

**Take — adaptive ID length with an explicit collision budget.** Hash IDs scale 4 → 5 → 6
characters by database size, sized from the birthday paradox `P ≈ 1 - e^(-n²/2N)` against a stated
maximum collision probability, with automatic disambiguation on collision and both records
preserved. Our §9.4 already chose an opaque collision-checked root token plus a dotted child
suffix — the same shape — but has no stated collision budget. Adopting an explicit probability
target turns a "collision-checked" hand-wave into a specified property.

**Take — the ID-derivation correction.** Their ID is derived from title **+ creation timestamp +
random salt** — so it is *not* stably content-derived, and the docs' claim that it is
"content-based" is loose. This validates our §9.4 split (opaque ids for mutable records,
content-derived ids only for immutable evidence) and is a good example of why we should read the
data rather than the marketing.

**Take — `bd remember` / `bd prime`.** Persistent project memory stored in the tracker and
injected as agent context on demand, with an explicit instruction not to create `MEMORY.md`
files. Our found-info records are the same primitive; what we lack is the `prime` half — a single
command that assembles the memory into session context.

**Note — compaction.** Upstream ships *"semantic memory decay"* that summarises old closed tasks
to save context. `work-tracker.md` §9.1 declines lossy compaction on evidence grounds (every one
of our 330 records is level 0; git already packs the ledger better than record-shrinking would).
That decision stands, but it should be restated as *"we decline it because git plus the ship-time
rollup already bound growth"* rather than implying nobody needs it — upstream clearly has users
who do.

### 2.11 gastownhall/gastown — the orchestration half, on the fork we declined

Added 2026-07-30, from the owner's competitive read. §2.10 analysed this org's *tracker*; this is
the *orchestrator* built on it, and it was the single biggest gap in this review.

Gastown covers most of our orchestration half: worktree-backed per-agent workspaces ("hooks"), a
merge queue, a supervisor patrol tier (Deacon/Witness/Dogs) monitoring concurrent workers
("polecats"), and human-gated work bundles ("convoys", with a `--human` flag). Go, MIT.

> **Correction, 2026-07-30 — and the method failure matters more than the fact.** This section was
> first written from gastown's README, fetched and summarised rather than read from source. It
> claimed a *"Bors-style merge queue that bisects to isolate a failing MR"* and recommended we build
> the same. **That code is unreachable.** `AssembleBatch` and `ProcessBatch` appear only at their own
> definitions (`internal/refinery/batch.go:58`, `:211`) with no call sites outside tests; the config
> field that would enable them is never read (`engineer.go:177-179`); `ProcessMRInfo`
> (`engineer.go:1321`) has no callers at all, including tests; and the Go merge engine cannot be
> entered, because the Refinery is a Claude agent in a tmux session and the Go-driven foreground mode
> returns a hard error — *"foreground mode is deprecated; use background mode"*
> (`internal/refinery/manager.go:154-156`). The agent's own runbook processes the queue
> **sequentially** (`.claude/commands/patrol.md:126`). `README.md:96` and `:653-661` describe an
> algorithm the shipped binary cannot run.
>
> This repo's own rule is that a README claim is not evidence, and §1 pins every finding to a
> revision for exactly this reason. The rule was broken by reading a summary of a summary. Every
> corrected fact above came from the clone at `649b832`.

**Take — this is not evidence we are reinventing; it is evidence the fork in §2.10 is real and
widening.** Gastown requires **`bd` 0.57+ plus Dolt plus tmux 3.0+**. It is the DB-authoritative
branch shipped as a product, with a database, a patrol process and a terminal multiplexer in its
critical path. `work-tracker.md` chose the opposite branch — the log is the truth, no second binary,
no daemon. Both are coherent. Ours is the one that can reach a zero-runtime-dependency 1.0.0; theirs
structurally cannot.

**Take — our merge queue is ahead of what gastown actually runs, and the recommendation to build
bisecting is withdrawn.** `merge.py` lands serially, detects conflicts with `git merge-tree` before
touching a working tree, and bounces the conflicted lane to its owner — deterministically, in engine
code. What gastown *runs* is a Claude agent working the queue sequentially. So this is not a gap to
close; it is a place where a deterministic implementation beats a nondeterministic one at the same
throughput.

Bisecting should not be filed even on its own merits. The analysis put its break-even at a per-lane
pass rate of about **0.86 for a batch of five** — below that, re-verification costs more than the
batch saves — for roughly **2,100 lines**, and it introduces a blame defect: a batch that fails
attributes the failure to the batch rather than to a lane, which would corrupt the coupling records
D9 depends on. Reject on cost, not only on absence.

**Take — the one thing here genuinely worth adopting is much smaller and we have the scar for it.**
Gastown never believes its own merge: after merging and pushing it re-resolves the ref and *proves*
the submitted commit is an ancestor of the target, and it refuses to land at all if the lane branch
moved since it was queued (`engineer.go:1478-1499`, `:1538-1560`, `internal/git/git.go:2091-2144`).
About twenty lines of `rev-parse` plus `merge-base --is-ancestor`, no dependency. We have the
ancestry check but only at the *ship* gate (`loop.py:620-624`) — and this repo has twice recorded the
incident it guards: a bead closed with its code stranded unmerged. The lesson is to assert inside the
merge function, so a `MergeResult(status="merged")` can never be returned without proof.

**Take — reject the supervision tiers, adopt the detection code underneath them.** Gastown runs five
tiers, three of them LLM sessions, justified as *"the daemon is mechanical (can't reason), but health
decisions need intelligence"* (`docs/requirements/dog-infrastructure.md:20-22`). The repo then refutes its
own thesis: three separate gates exist purely to *avoid* invoking the triage agent — one credited
with saving *"~480 Claude sessions/day"* (`internal/daemon/daemon.go:1338-1353`, `:1369-1383`) — and
both shipped plugin dogs instruct the model **not** to reason (*"Run this command EXACTLY. Do NOT
interpret"*, `internal/plugin/types.go:220-221`). The tiers are compensation for an **agent-driven
loop**: the agent calls `gt patrol report`, which closes the cycle and spawns the next
(`internal/cmd/patrol_report.go:47`), so nothing deterministic owns continuation and a hung agent
stops the world silently. Our engine owns the loop and derives phase from the tracker, which makes
"did the phase advance within T?" a plain predicate. Their *mechanical* guards are still worth
taking: the worktree-teardown guard stack, where a **transient** git error must never authorize a
deletion (`internal/polecat/manager.go:1138-1305`), and the restart governor's hard crash-loop stop
(`internal/daemon/restart_tracker.go:44-54`).

**Take — do not read their config layer as a projection layer.** Gastown configures *runtime*
(`settings/config.json` per rig, `.claude/settings.json`, `~/.codex/config.toml`). That is
permissions and command wiring, not a catalog compiled into instruction files with a drift gate. The
two halves of basicly still do not co-exist in one tool anywhere we have looked — but note that is a
statement about the field today, not a moat.

### 2.12 The 2026-07-30 sweep — six more repos, one agent each

Full reports live outside the repo in `reference-repos/_analysis/*.md` (they are long and quote
freely from other projects' source). Each was read from a pinned clone, not from a README, after
§2.11 demonstrated the cost of the alternative. Revisions are cited inline per subsection.

**Licences reviewed 2026-07-30 — and two of the six are restricted.** Recorded in
the review's Appendix A §1 and §§2.2–2.3. `oh-my-openagent` is under the **Sustainable Use License
1.0**, which is not open source and permits use only for internal or non-commercial purposes;
`hankweave-runtime` is stock Apache-2.0 but its `NOTICE.md` incorporates Southbridge's Terms of
Service, whose provisions include *"Competition restrictions on using Hanks to build competing
products"*. **In both cases the `LICENSE` file alone would have cleared them.** The other four
(`oh-my-agent`, `symphony`, `ccpm`, `Archon`) plus `gastown` are stock MIT or Apache-2.0.

Read the two restricted subsections below as **analysis only**: their measurements and concepts are
usable, their source is not an implementation reference, and the clean-room posture §2.1 sets for
`beads_rust` applies to them too.

> **The order in which this section was written is itself a finding.** The paragraph above replaces
> one that said the licences were *"not yet reviewed"* and warned that *"deriving an implementation
> from a quoted snippet requires reading that project's `LICENSE` first"* — written in the same
> commit that then recommended porting ~400 lines from one of them. The warning was correct and was
> not followed. Reviewing licences **before** writing the adopt findings, rather than after, is the
> cheap fix; §2.1 was supposed to have taught that already.

**`first-fluke/oh-my-agent` (TypeScript, MIT, `2c28bc4`) — our closest competitor on projection.**
Adopt `oma skills audit` (`cli/commands/skills/audit.ts:9-36`,
`cli/utils/text-similarity.ts:105-129`): dependency-free TF-IDF/cosine over skill *descriptions*
flagging near-duplicate pairs and "black-hole" skills whose mean similarity to the library is an
outlier. Our routing rests on 33 descriptions and we have **no cross-skill check at all** — every
`catalog lint` rule inspects one file in isolation (`src/basicly/catalog_lint.py:213-291`). Sharpens
`basicly-m4zv.2` rather than adding work. We stay ahead on three axes with evidence: our sources are
deliberately non-discoverable YAML while their SSOT *is* `SKILL.md`/`AGENTS.md` defended by a prose
instruction (`cli/platform/rules.ts:267`); our composition is a real render-time merge while their
`_shared/` is relative-markdown pointers that silently broke their own emit
(`cli/scripts/check-emit-drift.mjs:7-14`); and our token budget reads real run-record spend and halts
dispatch (`src/basicly/policy.py:869-899`) while theirs counts `promptContent.length / 4`
(`cli/commands/agent/spawn-status.ts:466`). They are ahead on target *breadth* — 13 skill roots to our
2 — but buy it by writing to **gitignored** directories at install time, which is a different
architecture rather than a better projector.

**`code-yeongyu/oh-my-openagent` (`f287227`) — the answer to our capability-tier question, but
concept-only.** **Licence: Sustainable Use License 1.0 — see Appendix A §2.2.** The files below
sit in `packages/model-core`, which carries no licence of its own and is therefore governed by the
non-commercial root licence. **Their source is not an implementation reference for us**; what follows
is the design, which is an idea and freely usable.

The concept: a work item declares a named *tier*, never a model id, and a pure injected resolver
walks an ordered `(providers[], model, variant)` fallback chain against what is actually reachable,
returning the chosen model **plus a `provenance` enum** (`override | category-default |
provider-fallback | system-default`) so the choice is explainable after the fact. Alongside it, an
unsupported setting is never refused — it is clamped down a fixed ladder and the downgrade recorded as
`{field, from, to, reason}`, which is the graceful-degradation contract `basicly-kjc5.58`/`.59`/`.61`
need. Implemented in `model-resolution-pipeline.ts`, `category-model-requirements.ts` and
`model-settings-compatibility.ts`; cited for provenance, not for transcription.

> **Recommendation withdrawn.** This entry originally called it *"about 400 lines of pure logic that
> ports to stdlib Python unchanged"* and said so on `basicly-kjc5.58`. Under the Sustainable Use
> License a port is distribution of a derivative work outside the permitted purposes, and `basicly`
> is distributed. Build it from the description above, clean-room, exactly as §2.1 requires for
> `beads_rust`.

**Their HEAD is decisive for our open question, and this part is a fact about published history
rather than anything derived from their code**: `fix/task-reject-category-with-model` makes tier and
raw model id *mutually exclusive* with a typed error, because a call-site override would silently
bypass the routing — so if a catalog entry may declare both, the tier is decoration. Reject their stated thesis: the ROADMAP
refuses a harness abstraction (*"duplication causes less pain"*) and the repo then pays the bill —
three hand-ported rules-injection implementations, two background-agent engines of which only the
older detects a wedge, two contradictory harness-id enums four lines apart (`schema/harness.ts:3` vs
`:7`), and two byte-identical committed copies of a 25,905-byte skill with a third silently diverged
by 1,606 bytes. Our single-source projector is the correct call.

**`SouthBridgeAI/hankweave-runtime` (`66a9921`) — the only production append-only journal in the
set, and the most important input to `work-tracker.md`.** **Licence: Apache-2.0, but `NOTICE.md`
incorporates Terms of Service carrying competition restrictions — see Appendix A §2.3.** The
measurements below are properties of published data and stay usable; **the mechanism adoption is held
pending a licence question we are not qualified to settle**, and `basicly-vkh0.9` has been narrowed
accordingly. Read what follows as analysis, and do not use their source as an implementation
reference. Adopt the **denormalized running
aggregate**: every event carries the totals that hold *after* it
(`state.transition.data.resultingState`, `event-schemas.ts:429-434`), so the common query is answered
by reading the tail while the fold remains the checkable authority. That is a concrete answer to the
cost `work-tracker.md` §4 admits, and it defers the index without hand-waving. Adopt **honest
truncation** (`truncated` + `originalLength`, `event-schemas.ts:259-268`) as a better growth bound
than the lossy compaction §9.1 declines, because it records that evidence was dropped and how much.
Reject their sentinels — a sentinel is an injected LLM call with a per-million-token cost model
(`sentinels/sentinel.ts:108`, `:502`), inadmissible under the product rule and already covered by
`StallWatchdog`. Reject their ordering model outright and cite it as vindication: **no sequence
numbers**, ids minted as `Date.now()` + `Math.random()` (`utils.ts:26-28`), and **44.5% of events in
their own committed 6,467-event production fixture share a millisecond** — total order exists only as
an unrecorded side effect of append order, exactly what §9.5 forbids. Net: our *unbuilt* design is
more rigorous than their *shipped* one.

**`openai/symphony` (`f8e8b8a`) — the prior art we went looking for does not exist.** Adopt the
phase machine as *data*: their engine knows only three predicates about a work item — active,
terminal, neither — with the seven-stage lifecycle living in `WORKFLOW.md` front matter, so adding a
stage costs no code. Note the `## Codex Workpad` journal (`WORKFLOW.md:295-329`) as a file-format
sketch. Reject the state model: `grep File.write lib/` finds nothing but rotating logs — claim,
running, retry and blocked live in RAM, re-derived from a network tracker on a five-second poll,
`SPEC.md:1691` calling this *"intentionally in-memory"* and `SPEC.md:2238` still carrying *"TODO:
Persist retry queue and session metadata across process restarts"*. **The absence is the finding.**
Reject their conflict policy: `land/SKILL.md:32-34` has the LLM resolve merge conflicts.

**`coleam00/Archon` (`3044829`) — matched its billing on phases, not on gates.** A genuine
Zod-validated node DAG in `.archon/workflows/*.yaml` with per-run worktrees and Kahn-layered
execution (`packages/workflows/src/schemas/dag-node.ts:499`,
`packages/workflows/src/dag-executor.ts:1174`). Adopt `evidence_policy.required` plus typed node
artifacts: refuse terminal success unless a declared evidence file exists on disk, the engine gating
on *presence* while the workflow produces the content — *"code computes, YAML coordinates"*
(`schemas/workflow.ts:105`). Pure filesystem, no dependency, and it plugs a hole we have too: a lane
can currently claim a phase done with no artifact to point at. Reject their completion gate and cite
it: `completionDetected = signalDetected || bashComplete` (`dag-executor.ts:4602`) lets a model's
self-emitted `<promise>DONE</promise>` **short-circuit the deterministic check**, the exact inversion
of `rubrics.py`'s contract. Their "validation gates" are `bash:` nodes; the module that would turn a
result into structured state is dead code reachable only from its own test.

**`automazeio/ccpm` (`7d7e462`) — a prompt pack, and its headline is false.** It does **not** use
GitHub Issues as its work store: `gh` appears in one setup script while all twelve readiness and
status scripts grep local markdown frontmatter — so it is evidence *for* owning our store, not
against it. Adopt two things anyway: turning declared file-scope from a planning input into a
**verified postcondition** (filed as `basicly-jr0l.44`), and a **designated owner** for a shared path
(`execute.md:212`), which fixes a real pathology in our `group_children` where one common
`pyproject.toml` collapses every child into a single serial group (filed as `basicly-jr0l.45`).

**What the sweep changes about §5.** Two of the six independently made phases declarative (Archon's
DAG, Symphony's three predicates) and **both kept determinism weak** — one lets the model self-certify
completion, the other persists nothing at all. We have the opposite profile: hard-coded phases,
strong determinism. So the open design question is not whether to copy declarative phases but whether
phases can become data *without* surrendering derive-from-state and deterministic gating. Archon's own
slogan is the best framing available for it: **code computes, YAML coordinates.**

## 3. Where independent projects converge

Convergence across projects that did not copy each other is the strongest evidence in this review.
Each row is something we should treat as close to settled.

| Convergent finding | Independently reached by |
| --- | --- |
| **A deterministic orchestrator, never an LLM one.** Agents propose; code disposes. Personas never spawn personas. | our D1/D2 + R1, agent-skills' orchestration catalog (anti-patterns A–D), gsd-core's thin orchestrator, superpowers' controller |
| **Shared state must live in files, not in a conversation.** This is what *makes* fresh-context dispatch viable. | our `br`-as-truth, gsd's `.planning/` + `STATE.md`, superpowers' ledger, lattice's `.lattice/` |
| **Fresh context per unit of work, with a precisely constructed bundle** — never the parent's history. | our D6, superpowers ("never inherit your session's context"), gsd-core, agent-skills' research isolation |
| **Bounded rework with a hard cap, then escalate to a human.** Loops do not converge past the cap. | our bounded rework, superpowers (5 rounds), gsd's Revision Gate (3), doubt-driven (3) |
| **The reviewer must be adversarially framed and must not see the author's conclusion.** | doubt-driven (ARTIFACT+CONTRACT, never CLAIM), gsd's FORCE stance, Refute-or-Promote, "the maker shouldn't grade the checker" |
| **Reviewers are read-only; a separate actor fixes.** | our roster's tool policy, gsd's Nyquist auditor, the adversarial-review permissions pattern |
| **Determinism beats instruction: if a hook can enforce it, do not ask the model.** | our "hooks are the deterministic floor", the steering blog ("the model choosing to run a formatter is different from the formatter running automatically"), CLAUDE.md best-practice write-ups ("remove the instruction, keep the enforcement") |
| **Evidence before claims.** No completion claim without a freshly run verification command. | our Quality Gate, superpowers' Iron Law, Karpathy's goal-driven execution |
| **Structured acceptance criteria per unit, written before implementation.** | our DoR/rubrics, gsd's PLAN.md, mattpocock's ticket template, superpowers' Global Constraints |
| **Decomposition into vertical slices sized to one fresh context.** | our D8 bands, mattpocock's tracer bullets, gsd's phase scoping, superpowers' task right-sizing |
| **Prohibitions are a weak steering form; prefer a positive recipe.** | superpowers' measured wording tests, mattpocock's Negation failure mode, lattice's active-checklist technique |
| **A description must carry the user's actual vocabulary, and must not summarise the workflow.** | superpowers' observed one-review-instead-of-two failure, agent-skills' Tier-2 rank-1 metric, mattpocock's leading-word-in-description rule |

## 4. Where the field genuinely disagrees — the open questions

These are the places where reading more will not settle it and only measurement will. Each is
recorded with what would decide it.

### 4.1 Cheapest-sufficient tier vs reliable tier

Our **R5** argues the unit of cost is total tokens, wall-clock and human interventions **per
landed correct package**, and concludes that for consequential outputs the reliable tier is the
expensive one. **superpowers** says *"use the least powerful model that can handle each role"* and
routes mechanical implementation to a cheap model.

They are not as far apart as they look, and the reconciling sentence is superpowers' own footnote:
*"**Turn count beats token price.** Wall-clock and context cost scale with how many turns a
subagent takes, and the cheapest models routinely take 2-3× the turns on multi-step work —
costing more overall. Use a mid-tier model as the floor for reviewers and for implementers working
from prose descriptions."* That is R5's argument, priced in turns. Their genuine addition is a
**predicate for when the cheap tier is safe**: *"when the task's plan text contains the complete
code to write, the implementation is transcription plus testing."*

So the synthesis is: the reliable tier is a function of **specification completeness**, not of the
work's nominal category. R5 is right that a cheap dispatch against a prose description is a false
economy; superpowers is right that a cheap dispatch against a plan containing the literal code is
not. Both also independently warn about the same operational trap — *"always specify the model
explicitly when dispatching; an omitted model inherits your session's model, often the most
capable and most expensive, which silently defeats this section."*

**Decides it:** `basicly-7bur`'s cost-per-landed-package baseline, with dispatches labelled by
specification completeness. Until then R5 stands, amended by the turn-count framing.

### 4.2 Compaction: lossy summarisation vs git plus rollup

Upstream beads ships semantic memory decay; `work-tracker.md` §9.1 declines it on measured
evidence. No change recommended — but the decision should be restated as a trade-off we chose
rather than a mistake others made.

### 4.3 Localisation: is the Scout dead?

**Reopened by §2.9.** Our roster cut the Scout on cost-model grounds: a cheap pre-reader's
incomplete file list is undetectable and silently narrows the implementer. That argument is about
a *model*. graphify demonstrates the same output produced **deterministically** by tree-sitter AST
extraction, at zero token cost, with coverage that is a checkable property of the parser rather
than a judgment.

This is not the Scout coming back as a persona. It is the localisation *work* moving from "a
dispatch we refused" to "an engine step we never considered", which is exactly where R5's first
corollary says the biggest wins live. It also happens to be the one place where gsd-core's larger
roster beats ours — their `gsd-codebase-mapper` runs four parallel sub-probes to produce a map —
except the deterministic version needs no agents at all.

**Decides it:** whether an AST-derived localisation artifact measurably reduces Kai's
pre-first-edit token share without the omission risk. That is a cheap experiment and it should be
run before the roster is implemented, because it changes what Dana's scope declarations need to
carry.

### 4.4 Where the ceremony threshold sits

gsd-core writes its threshold down and ships two primitives below it. We mandate the loop for
"non-trivial work" and ship nothing below it. Nobody in the field has a measured threshold; all
of them have a written one. Having *a* written threshold and a named cheaper path is strictly
better than leaving it to the agent's judgment, which is what we currently do.

### 4.5 Agent-to-agent messaging

agent-skills documents a real capability we forbid: teammates that challenge each other's
hypotheses converge on a root cause better than independent reporters do, *"with multiple
independent investigators actively trying to disprove each other, the theory that survives is
much more likely to be the actual root cause."* Our D1/D2 forbid it and should continue to — the
cost is reproducible scheduling and resumability, which we will not trade. Recorded as a known
limitation, not a gap.

## 5. Where basicly is already ahead

Stated honestly, with the distinction between a structural lead and an asserted one.

**Real, structural leads:**

1. **Enforcement runs at commit time, not only in CI.** Nearly every project in *this* set
   enforces discipline by asking the model nicely — lattice says so outright: *"Atoms are
   markdown — AI can read them but also ignore them. There's no compiler, no linter, no gate.
   Compliance depends on prompt engineering."* We have `basicly check`, `catalog lint`,
   `skills-check`, `agents-check`, `hooks-check`, and commit-msg gates.

   **Narrowed 2026-07-30.** As first written this claimed "enforcement is code and git hooks, not
   prose" and called it the widest gap in our favour. That overstated it: `first-fluke/oh-my-agent`
   ships *"a drift check in CI keeping the generated output honest"* plus `oma verify <agent>`, a
   deterministic per-agent check battery. Enforcement-by-code is therefore **not** unique to us.
   What survives is the *stage*: oh-my-agent installs no git hooks, so its checks run after the
   commit exists. A hook that refuses the commit and a check that fails the build afterwards are
   different guarantees — the first cannot be merged around by someone who does not read CI. Claim
   the stage, not the existence of gates.
2. **State lives in a tracker with a dependency graph, not in markdown plan files.** superpowers'
   most expensive observed failure — a controller re-dispatching completed work after
   compaction — is structurally impossible for us because phase is *derived* from `br` rather than
   remembered. We got that property by design; they got the scar first.
3. ~~**Agent-agnostic projection from one catalog.**~~ **No longer a lead — matched.** The
   original entry read: *"Others ship per-host adapters maintained by hand (ponytail's 20-row table
   is impressive and is also 20 things to keep in sync). We generate, and we gate the generation."*
   True of the ten repos reviewed here, and **false of the wider field**:
   `first-fluke/oh-my-agent` keeps `.agents/` as a single source of truth and `oma emit` projects it
   into each runtime's native layout — Agent-Skills-conformant skill folders, `AGENTS.md`,
   `.claude-plugin/marketplace.json` — with the CI drift check quoted in lead 1.

   So generate-and-gate-the-generation is now table stakes for a serious project in this category,
   not a differentiator. Our remaining edges on the projection half are narrower and should be named
   individually rather than bundled: the **invocation axis**, the **path-scoped rules tier**, and
   **commit-time** rather than CI-time drift enforcement. Whether those are worth what they cost is
   §6.1's question, not this section's.
4. **The engine-disposes / agents-propose split is explicit and enforced**, including for
   autonomy grants. Only gsd-core has a comparable notion, and theirs is a gate taxonomy rather
   than an authority model.
5. **The tracker plan is more defensible than the alternatives on offer.** §2.10's upgrade
   procedure is the strongest available evidence for owning this component.
6. **We already run a whole-harness A/B** (`basicly-8z52`, N=1 pilot; the write-up was deleted 2026-08-08, the result is on the bead) and were already honest about
   its weakness (the circular-criterion problem) before reading ponytail. Almost nobody else in
   this set measures at all; ponytail measures better; we are second and know why.

**Asserted, not yet earned:**

- **That our roster's tiers and lenses pay for themselves.** R6 says lenses run on every lane and
  R5 says down-tiering is a false economy. Neither is measured. The roster document says so, which
  is to its credit, but §6.1 is what turns that admission into evidence.
- **That our always-on baseline is effective at its current size.** See §6.2 — this may be an
  active regression rather than a gap.
- **That the catalog's individual skills change behaviour.** One pilot, one task, `n=1`.

## 6. Where basicly is behind, ranked by cost

### 6.1 No per-skill efficacy measurement, and no routing check at all

We have ~30 catalog entries and evidence about one of them. Worse, we have **no Tier-2 equivalent**
— nothing checks that a skill's description carries the vocabulary a user would actually say, or
that two descriptions have not drifted into each other. Both failures are silent: a skill that
never fires costs its context load forever and delivers nothing, and two colliding descriptions
make routing a coin flip. This is deterministic, free, CI-safe, and we do not have it.

**Cost:** every unfired skill is pure context load; every colliding pair is unpredictable
behaviour. Carried into [`catalog-efficacy-design.md`](../requirements/catalog-efficacy-design.md).

### 6.2 The always-on baseline may be past the point where adherence collapses

Our baseline sits at a ~9000-character soft cap with roughly 1000 characters of headroom. That is
on the order of 1300 words of dense always-on rules. The consistent (if secondary-source)
practitioner finding is that *rules start dropping past ~80 lines, whole blocks are ignored past
~200 lines, and adherence to dense rules collapses past ~500 words.* The vendor guidance is
blunter: keep the always-on file to facts, move procedures to skills, move "every time X" to
hooks, and path-scope anything conditional.

If those thresholds are even roughly right, **we are not managing a budget — we are past a cliff,
and raising the cap made it worse.** We do not know, because nothing measures which baseline rules
actually bind. There is a cheap first test available and it costs one session: open a fresh session
and ask the agent to summarise the rules; anything it cannot recall is not doing work.

**Cost:** potentially the entire always-on layer under-performing while we pay for it on every
turn of every session in every consumer repo. This is the highest-leverage unknown in the review.
Carried into [`steering-surfaces-design.md`](../requirements/steering-surfaces-design.md).

### 6.3 No path-scoped guidance tier

Both Claude Code (`rules/*.md` with `paths:`) and Copilot (`*.instructions.md` with `applyTo:`)
support conditional loading keyed on file globs. Our catalog projects always-on fragments and
on-demand skills — two tiers where the platforms offer three. Anything currently in the always-on
baseline that only applies to, say, subprocess code or test files is paying full price on every
turn to be relevant occasionally. This is the concrete mechanism that relieves §6.2 without
deleting guidance.

### 6.4 No stall detection, no gate taxonomy, no severity contract

Our rework is capped but has no convergence check, so a stalled loop burns its remaining budget.
Our gates are not classified, so "what happens when this fails" is answered per site rather than
by type. Our findings have no structurally required severity. All three are cheap.
Carried into `factory-loop.md` §5.1 and §11.1 (the standalone `gates-and-rework-design.md` was absorbed and deleted 2026-08-08).

### 6.5 Reviewer and validator prompts are not hardened

R8's "quirks are operational contracts" is the right instinct aimed at the wrong target. gsd-core
demonstrates the effective version: a FORCE stance plus an explicit enumeration of *how this role
goes soft*, including naming conflict-avoidance as a predicted failure. Vera and Remo need that,
and doubt-driven's contract needs adopting: never pass the CLAIM, classify contract-misread first,
and compute the doubt-theater signal.

### 6.6 No trend instrument

lattice's review log answers "is this working, and are we getting better" from data. We have
per-run telemetry and a health report but nothing that answers *which lens catches the most*,
*whether findings per lane decline*, or *whether retro proposals get absorbed*. Without it, R6's
"if a lens does not pay for itself, cut it" is unexecutable.

### 6.7 Documentation has no tutorial or how-to layer

`docs/` is nine design documents. There is no "your first loop" walkthrough and no task-focused
how-to guides. For a distribution meant to be installed by other repos, that is an adoption
blocker independent of any capability in this review, and Diátaxis is the obvious remedy.

### 6.8 Smaller, still worth fixing

- **No declared capability tier per agent family.** We project to three families as though the
  delivered guarantee were the same. It is not.
- **No `effort` signal per skill**, so a status query and a decomposition carry the same implied
  budget.
- **No prefix-stable dispatch bundles**, so we forfeit provider cache hits we are unusually well
  positioned to get.
- **No provenance labels on graph edges** (§2.9), so asserted and inferred couplings are
  indistinguishable to the merge queue.
- **Bare ids in human-facing output** (§2.1).
- **No deliberate-shortcut convention** with a mechanical harvest and rot detector (§2.4).
- **No refiner tier** — nothing interviews a consumer to generate their own overlay (§2.5).
- **Worktree provisioning is imperative**, and teardown is not atomic (§2.7).

## 7. What to reject, and why

Recorded so these do not get re-proposed. Each rejection has a reason stronger than taste.

| Rejected | Reason |
| --- | --- |
| An LLM orchestrator / uber-agent | Loses reproducible scheduling, resume-by-derivation, and enforcement-by-construction. Independently rejected by agent-skills and gsd-core. Our D1/D2/R1 stand. |
| Personas spawning personas | Output-format and authority conflicts, hidden cost, multiplied failure modes; on Claude Code it is blocked by construction. |
| Agent-writable catalog / standards | The asymmetry in R9: a bad implementation bounces off a gate, a bad fragment is absorbed and silently degrades every later lane. Keep human-only at every grant level. |
| `--no-verify` for parallel commits | The contention gsd-core hit is real; our merge queue solves it without defeating a gate. |
| Semantic/lossy compaction of the ledger | Measured: git already packs our ledger better, losslessly, and compaction discards the evidence D11 depends on. |
| A maintained TUI | Permanent cost; generated artifacts (JSON, Mermaid/DOT, static HTML) are diffable in review and cost nothing to keep. |
| Adopting Dolt, or any external DB/daemon | Reintroduces exactly the unowned-binary upgrade surface we are removing — and §2.10 shows what that surface costs at migration time. |
| Adopting headroom as a proxy | A compression layer in the critical path is a worse-positioned dependency than the tracker ever was. Take the two ideas, not the dependency. |
| `<EXTREMELY-IMPORTANT>` shouting and "you have no choice" framing | The negation anti-pattern; unnecessary where a hook enforces the same thing. |
| A cheap-tier model Scout | Undetectable omission upstream of the most expensive dispatch. Superseded, not revived, by the deterministic version in §4.3. |
| Agent-to-agent messaging / teams | Real capability, but it costs reproducible scheduling and resumability. Recorded as a limitation. |
| Copying prose or prompts from any reviewed repo | Licence hygiene (Appendix A §1) and, for `beads_rust`, a clean-room boundary (§2). |

## 8. Vocabulary adopted

Importing a word is the cheapest change in this review and the one most likely to be
misremembered later, so the adopted set is fixed here. Where a term names something we already
had, the existing mechanism is noted — the word is new, not the behaviour.

**Skill authoring** (from mattpocock/skills): predictability · context load · cognitive load ·
information hierarchy · progressive disclosure · co-location · completion criterion (clarity and
demand) · legwork · leading word · branch · premature completion · no-op · negation · sediment ·
sprawl · duplication · router skill.

**Gates** (from gsd-core): pre-flight gate · revision gate · escalation gate · abort gate ·
stall detection · FORCE stance.

**Review** (from agent-skills and the literature): doubt-driven posture · CLAIM / ARTIFACT /
CONTRACT separation · contract misread (as the first finding class) · doubt theater ·
refute-or-promote · park with a ruling (superpowers) · deferred minor.

**Failure modes** (from CAAF): compliant hallucination — output satisfies the constraint while
defeating its purpose; stochastic oscillation — a reflection loop cycling without converging.

**Provenance** (from graphify): `EXTRACTED` · `INFERRED` · `AMBIGUOUS`.

**Portability** (from ponytail): instruction-tier · skill-tier · plugin-tier.

**Scoping** (from mattpocock/skills): destination · fog of war · not yet specified · out of scope ·
decision ticket · frontier.

**Debt** (from ponytail): ceiling · upgrade trigger · `no-trigger` (a shortcut with no stated
trigger to revisit).

## 9. Index: findings to design documents

| Finding | Carried into |
| --- | --- |
| Three-tier evals; control arms; micro-tests with a no-guidance control; separating mechanism from outcome; per-skill eval files as a CI requirement | [`catalog-efficacy-design.md`](../requirements/catalog-efficacy-design.md) (new) |
| Gate taxonomy; stall detection; refute-or-promote; severity as a required field; adversarial stance and "how reviewers go soft"; adjudicate-only-at-the-cap; no-pre-judging lint; capability escalation on late rework rounds | `factory-loop.md` §5.1, §11.1 (absorbed 2026-08-08) |
| The seven steering mechanisms; the always-on adherence budget; path-scoped rules; capability tiers per agent family; `effort` signals; the ceremony threshold and a named primitive below it; form-matching for guidance | [`steering-surfaces-design.md`](../requirements/steering-surfaces-design.md) (new) |
| `beads_rust` licence correction and clean-room boundary; Dolt as the rejected alternative; the three JSONL migration risks; adaptive id length with a collision budget; provenance labels on edges; `prime`-style memory assembly | [`work-tracker.md`](../requirements/work-tracker.md) (updated) |
| Adversarial stance hardening; tier-vs-turn-count reconciliation keyed on specification completeness; the deterministic-localisation reopening of the Scout; two-pass generation within a dispatch; no-reranking across review axes; trend instrument; `effort` per role | `factory-loop.md` §6, §11.1 (absorbed 2026-08-08) |
| Declarative typed worktree provisioning; atomic teardown | not yet carried — file against the worktree component |
| Diátaxis restructure of `docs/`; tutorial and how-to layer | not yet carried — documentation workstream |
| Prefix-stable dispatch bundles; reversible bundle compression with retrieval | not yet carried — depends on `basicly-7bur` baseline |
| Refiner tier (interview-generated consumer overlay) | not yet carried — largest genuinely new product idea in the review |

The last four rows are deliberately unassigned. They are real findings with no home yet, and
inventing a design document for each would be the "premature catalog entry" failure §2.3 warns
about. They belong in the tracker as offers, not in a document nobody will read.

---

## Appendix A — sources, licences and provenance

Absorbed 2026-08-08 from Appendix A, which is deleted. **This is the register the tracker
work's clean-room boundary rests on** — `requirements/work-tracker.md` cites it three times — so it
is evidence rather than narrative and is kept verbatim, at its own dated revisions.

Companion to [`2026-07-26-sota-review.md`](2026-07-26-sota-review.md). This file is the
**provenance and licence record**: what was read, at which revision, under which licence, and
how much confidence each source earns. Its job is to make the review re-runnable and to make
every borrowed idea traceable to something we are actually permitted to borrow.

Two rules govern use of everything below:

1. **Concepts are free; text is not.** We adopt ideas, vocabulary, and architectural patterns.
   We do not copy prose, prompts, or code from these repos into `basicly` sources without
   satisfying §1's licence column and adding the required attribution.
2. **A licence claim is checked, not assumed.** §1 records what each `LICENSE` file actually
   says as of the pinned revision. One repo in this set is *not* what our own docs previously
   claimed it was (§2).

### 1. Cloned repositories

Cloned to `development/reference-repos/` (outside `basicly`, ignored by the workspace's
whitelist `.gitignore`). Shallow clones, `--depth 50`.

| Repo | Pinned revision | Date | Licence | Use permitted |
| --- | --- | --- | --- | --- |
| [mattpocock/skills](https://github.com/mattpocock/skills) | `ed37663` | 2026-07-21 | MIT (Matt Pocock, 2026) | Yes — concepts and, with attribution, text |
| [obra/superpowers](https://github.com/obra/superpowers) | `3dcbd5c` (v6.2.0) | 2026-07-23 | MIT (Jesse Vincent, 2025) | Yes — concepts and, with attribution, text |
| [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | `2471e3f` | 2026-07-25 | MIT (Addy Osmani, 2025) | Yes — concepts and, with attribution, text |
| [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | `16f2980` | 2026-07-15 | MIT (DietrichGebert, 2026) | Yes — concepts and, with attribution, text |
| [techygarg/lattice](https://github.com/techygarg/lattice) | `75b7e07` | 2026-07-06 | MIT (Rahul Garg, 2026) | Yes — concepts and, with attribution, text |
| [open-gsd/gsd-core](https://github.com/open-gsd/gsd-core) | `46ba02a` | 2026-07-26 | MIT (Open GSD, 2026) | Yes — concepts and, with attribution, text |
| [satococoa/wtp](https://github.com/satococoa/wtp) | `842920d` | 2026-03-09 | MIT (Satoshi Ebisawa, 2024) | Yes — concepts and, with attribution, text |
| [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom) | `b121223` | 2026-07-25 | **Apache-2.0** | Yes — Apache-2.0 notice/attribution obligations apply |
| [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) | `66d8110` | 2026-07-25 | **Apache-2.0** (+ `LICENSE-MIT` for pre-relicensing contributions; `NOTICE` present) | Yes — must preserve `NOTICE` if any file is vendored |
| [Dicklesworthstone/beads_rust](https://github.com/Dicklesworthstone/beads_rust) | `94fb146` | 2026-07-22 | **MIT with OpenAI/Anthropic Rider** | **Restricted — see §2** |
| [gastownhall/beads](https://github.com/gastownhall/beads) | `d01d62e` | 2026-07-25 | MIT (Beads Contributors, 2025) | Yes — concepts and, with attribution, text |
| [gastownhall/gastown](https://github.com/gastownhall/gastown) | `649b832` | 2026-07-23 | MIT (Steve Yegge, 2025) | Yes — concepts and, with attribution, text |
| [first-fluke/oh-my-agent](https://github.com/first-fluke/oh-my-agent) | `2c28bc4` | 2026-07-30 | MIT (Eunkwang Shin and Gahyun Kim, 2026) | Yes — concepts and, with attribution, text |
| [code-yeongyu/oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) | `f287227` | 2026-07-30 | **Sustainable Use License 1.0** — non-OSI, non-commercial-only (no holder named; some `packages/*` subtrees separately MIT, Yeongyu Kim, 2026) | **Restricted — see §2.2** |
| [openai/symphony](https://github.com/openai/symphony) | `f8e8b8a` | 2026-07-24 | **Apache-2.0** (stock text; `NOTICE` present — "Copyright 2025 OpenAI") | Yes — must preserve `NOTICE` if any file is vendored |
| [automazeio/ccpm](https://github.com/automazeio/ccpm) | `7d7e462` | 2026-03-18 | MIT (Ran Aroussi, 2025) | Yes — concepts and, with attribution, text |
| [coleam00/Archon](https://github.com/coleam00/Archon) | `3044829` | 2026-07-30 | MIT (Cole Medin, 2025-2026) | Yes — concepts and, with attribution, text |
| [SouthBridgeAI/hankweave-runtime](https://github.com/SouthBridgeAI/hankweave-runtime) | `66a9921` | 2026-07-20 | **Apache-2.0** (stock text) **+ `NOTICE.md` Terms-of-Service incorporation with competition restrictions** | **Restricted — see §2.3** |

The Apache-2.0 repos are usable but carry obligations MIT does not: retain the licence and
`NOTICE`, state changes, and do not use the project's marks. Since we intend to take *concepts*
from them (headroom's cache-alignment idea, graphify's edge-provenance labels) rather than code,
the practical obligation is attribution in the design doc — which §4 of the review provides.

**Rows 11–17 were added 2026-07-30**, after the review sweep of the same date. No licence file was
missing in any of the seven. Five are stock and unremarkable; two are not, and in both cases **the
`LICENSE` file alone would have cleared them** — which is precisely the failure mode §2 was written
about. That is now three restricted repos found in this set, so treat "read the LICENSE" as the
*start* of the check and not the whole of it: read every licence-bearing file, including `NOTICE`,
and read the per-subdirectory licences before assuming a monorepo is uniform.

### 2. Restricted licences, and the corrections each forced

Three of the seventeen reviewed repos restrict what we may do, and in every case a casual read of
the licence would have missed it. Each subsection below records the operative clause verbatim, the
consequence, and the claim in our own documents it invalidated.

#### 2.1 `beads_rust` — the OpenAI/Anthropic rider

`docs/requirements/work-tracker.md` §7 asserted: *"Reading beads_rust and bv sources for reference is
explicitly sanctioned while they are MIT."* **That statement was factually wrong** and has been
corrected in that document.

`beads_rust/LICENSE` is titled `MIT License (with OpenAI/Anthropic Rider)`. The rider:

- Defines "Restricted Parties" as OpenAI, Anthropic, their affiliates, **and any person or
  entity acting directly or indirectly on behalf of, for the benefit of, or under the direction
  of any of the foregoing.**
- Grants **no rights** to any Restricted Party, and voids any purported sublicense to one.
- Defines restricted "use" to include, verbatim, *"benchmarking, testing, analyzing, indexing,
  or incorporating the Software or any Derivative Works into any dataset, training corpus,
  evaluation harness, or pipeline for machine learning or other automated systems."*
- States that breach terminates the licence immediately and that the rider must be reproduced
  unmodified in any distribution of the Software or a derivative work.

Three consequences, stated plainly:

1. **The rider is at minimum ambiguous as applied to an Anthropic model reading the source at a
   user's direction**, and it explicitly names "analyzing" as restricted use. This review
   therefore **did not read `beads_rust` source**. Everything the review says about it comes
   from its published `README`/docs — which describe an interface we already consume — or from
   the observable behaviour of the `br` binary we already run.
2. **A clean-room boundary now applies to the tracker work.** `work-tracker.md`'s replacement
   tracker must not be derived from `beads_rust` source. Its legitimate inputs are: our own
   ledger's observable data, `br`'s documented CLI contract, and `gastownhall/beads` (genuine
   MIT), which covers the same conceptual ground and is the upstream original.
3. **This is also a supply-chain argument, not only a legal one.** A dependency whose licence
   can be amended with a rider aimed at a class of users is exactly the "unowned dependency in
   our critical path" risk `work-tracker.md` §1 was written about. The finding strengthens that
   document's thesis rather than weakening it.

Not legal advice. If the tracker work proceeds to implementation, the boundary above should be
confirmed by someone qualified; until then the conservative line costs us nothing, because the
MIT original is available and is the better reference anyway.

#### 2.2 `oh-my-openagent` — not open source at all

`LICENSE.md` is the **Sustainable Use License 1.0**, which is not an OSI-approved licence. The
operative limitation, verbatim:

> You may use or modify the software only for your own internal business purposes or for
> non-commercial or personal use. You may distribute the software or provide it to others only if
> you do so free of charge for non-commercial purposes.

It also requires that *"anyone who gets a copy of any part of the software from you also gets a copy
of these terms"*, and that a modified copy carry a prominent notice of modification.

**Monorepo subtleties that matter, because the safe-looking path is narrower than it appears.** Some
`packages/*` subtrees carry their own MIT (`pi-goal`, `pi-webfetch`, `lsp-tools-mcp`), and
`omo-senpi/plugin/LICENSE` is a *scoped* MIT covering six named portions only. **`packages/model-core`
has no licence of its own**, so the root Sustainable Use License governs it — and `model-core` is
exactly where the three files the review cites live
(`model-resolution-pipeline.ts`, `category-model-requirements.ts`, `model-settings-compatibility.ts`).

**Consequence, and a correction to our own review.** Review §2.12 as first written recommended that
tier-routing logic as *"about 400 lines of pure logic that ports to stdlib Python unchanged"*. Under
this licence that is not an available option: `basicly` is distributed, so a port would be
distribution of a derivative work outside the permitted purposes. The recommendation is withdrawn
there and on `basicly-kjc5.58`.

**What survives is the part that was always the valuable part.** A licence restricts copying
expression, not learning a fact. The *concept* — a work item declares a named capability tier rather
than a model id; the resolver returns provenance for which rule chose the model; an unsupported
setting is clamped and the downgrade recorded rather than refused — is an idea, and the observation
that their HEAD makes tier and model id mutually exclusive is a fact about published history. Both
stay usable. What stops is treating their source as the implementation reference: no port, no
snippet, no line-by-line transcription. Same clean-room posture as §2.1.

#### 2.3 `hankweave-runtime` — a competition restriction asserted through `NOTICE.md`

The `LICENSE` is stock Apache-2.0. The restriction is in `NOTICE.md`:

> By using Hankweave, you agree to Southbridge AI's Terms of Service:
> `https://www.southbridge.ai/blog/terms-of-service`
>
> Key provisions include:
>
> - You retain ownership of your Hanks and Outputs
> - **Competition restrictions on using Hanks to build competing products**
> - Managed services restrictions require prior written consent

**Why this plausibly reaches us.** `basicly` is a coding-agent harness with an orchestration engine;
hankweave is an agent-orchestration runtime. Those are adjacent enough that "competing product" is
not obviously inapplicable, and the review used hankweave specifically as prior art for an
orchestration component we intend to build.

**Genuinely unsettled, and not ours to settle.** Apache-2.0 treats `NOTICE` contents as
informational and does not provide for a NOTICE adding terms; whether an incorporated Terms-of-Service
can bind a recipient who merely reads a public repository is a legal question. Two readings are
available and we are not qualified to choose between them. **So the conservative line applies, and it
costs little:** treat derivation from hankweave *source* as out of bounds pending review by someone
qualified, and stop at its published `README` and docs.

**Consequence for `basicly-vkh0.9`.** That bead was filed recommending we absorb their journal
mechanisms with source line ranges as the reference. Narrowed: the **measurements** stay — that 44.5%
of events in their own committed fixture share a millisecond is a measured property of published
data, and it is the finding that turns `work-tracker.md` §9.5 from an assertion into evidence. The
mechanism adoption is held pending the licence question.

**Resolved 2026-07-30 by owner decision, and closed out 2026-08-06.** We do **not** pursue a legal
review — the question is not worth the cost when the measurements were the value and we already have
them. Two standing consequences. hankweave *source* is **permanently** out of bounds as an
implementation reference, not merely pending something. And the two mechanisms are usable as
**concepts**, obtained clean-room from a written description exactly as §2.1 requires for
`beads_rust`: they are now designed from first principles in `requirements/work-tracker.md` §4.6 (a running
aggregate carried per event, with the fold kept as the authority) and §4.2 (truncation that records
`original_length` rather than summarising), with the clean-room posture recorded in §4.6 itself. No
port, no snippet, and no line ranges from their tree in either section — which is why neither cites
one.

Note also, for a different reason: their `NOTICE.md` records that hankweave orchestrates Claude
through `@anthropic-ai/claude-agent-sdk`, which it states is **not** open source. Irrelevant to our
own dependency policy, but it is the second time in this set that a project's real constraints lived
outside its `LICENSE`.

### 3. Primary documents read, by repo

Listed so a later reader can go straight to the source of a finding rather than re-deriving it.

**mattpocock/skills** — `README.md`, `CLAUDE.md`, `CONTEXT.md`, `.agents/invocation.md`;
skills: `productivity/writing-great-skills/{SKILL.md,GLOSSARY.md}`, `productivity/grilling`,
`productivity/handoff`, `engineering/code-review`, `engineering/tdd`, `engineering/to-tickets`,
`engineering/implement`, `engineering/wayfinder`, `engineering/codebase-design`,
`engineering/diagnosing-bugs`, `engineering/triage`,
`engineering/improve-codebase-architecture`, `engineering/research`,
`engineering/resolving-merge-conflicts`.

**obra/superpowers** — `README.md`, `hooks/hooks.json`; skills: `using-superpowers`,
`subagent-driven-development`, `dispatching-parallel-agents`, `writing-plans`,
`verification-before-completion`, `requesting-code-review`, `receiving-code-review`,
`brainstorming`, `writing-skills`.

**addyosmani/agent-skills** — `evals/README.md`, `evals/cases/code-review-and-quality.json`,
`references/orchestration-patterns.md`, `skills/doubt-driven-development/SKILL.md`; layout of
`agents/`, `commands/`, `hooks/`, `references/`, `docs/`.

**DietrichGebert/ponytail** — `README.md`, `docs/agent-portability.md`,
`skills/ponytail/SKILL.md`, `skills/ponytail-debt/SKILL.md`,
`benchmarks/results/2026-06-18-agentic.md`, `benchmarks/` layout.

**techygarg/lattice** — `README.md`, `docs/framework-intelligence.md`; layout of
`skills/{atoms,molecules,refiners}`, `dev-skills/`, `plugins/`.

**open-gsd/gsd-core** — `README.md`, `docs/explanation/context-engineering.md`,
`docs/explanation/the-phase-loop.md`, `docs/explanation/multi-agent-orchestration.md`,
`gsd-core/references/gates.md`, `agents/gsd-plan-checker.md`, `agents/gsd-nyquist-auditor.md`.

**satococoa/wtp** — `README.md`, `docs/` layout, `internal/` layout.

**headroomlabs-ai/headroom** — `README.md`, `crates/` layout, `docs/` layout.

**Graphify-Labs/graphify** — `README.md`, `ARCHITECTURE.md`, `NOTICE`.

**gastownhall/beads** — `README.md`, `docs/architecture/{index,dolt}.md`,
`docs/core-concepts/{hash-ids,adaptive-ids,sync-concepts}.md`,
`docs/multi-agent/coordination.md`.

**Dicklesworthstone/beads_rust** — `LICENSE` only (see §2).

### 4. Web sources

Confidence is graded because it matters: a repo read at a pinned SHA is near-certain, a
first-party vendor doc is strong, and a PDF summarised by a small extraction model is a lead to
verify, not a citation to lean on.

| Source | What it gave us | Confidence |
| --- | --- | --- |
| [Steering Claude Code: skills, hooks, rules, subagents and more](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more) | The seven steering mechanisms, their load timing, compaction behaviour, context cost and authority; the "every time X → use a hook" decision rule | **High** — first-party vendor doc |
| [How to write a great agents.md: lessons from over 2,500 repositories](https://github.blog/ai-and-ml/github-copilot/how-to-write-a-great-agents-md-lessons-from-over-2500-repositories/) | Six recurring sections; commands-early ordering; one-code-example-beats-three-paragraphs; the always / ask-first / never boundary triad; "never commit secrets" as the most common useful constraint | **High** — first-party, large sample |
| [GitHub Docs: custom instructions for Copilot code review](https://docs.github.com/en/copilot/tutorials/customize-code-review) and [Unlocking the full power of Copilot code review](https://github.blog/ai-and-ml/github-copilot/unlocking-the-full-power-of-copilot-code-review-master-your-instructions-files/) | `*.instructions.md` + `applyTo:` glob frontmatter as the path-scoping mechanism; a file without `applyTo` does nothing automatically | **High** — first-party |
| [AGENTS.md](https://agents.md/) | The cross-agent instruction-file convention basicly already projects to | **High** |
| [CLAUDE.md token budget optimization](https://thepromptshelf.dev/blog/claude-md-token-budget-optimization/), [CLAUDE.md best practices](https://techsy.io/en/blog/claude-md-best-practices), [Claude Code anti-patterns](https://www.aicodex.to/articles/claude-code-antipatterns) | The adherence-decay thresholds (~80 lines rules start dropping, ~200 lines blocks ignored, ~500 words of dense rules adherence collapses); "a rule with a reason generalises, a rule without one is dropped when context shifts"; the new-session "summarise the rules" self-test | **Medium** — consistent across independent write-ups but no primary experiment published; treat the numbers as an order of magnitude, not a constant |
| [Refute-or-Promote: adversarial stage-gated multi-agent review](https://arxiv.org/pdf/2604.19049) | Findings must survive an explicit refutation stage; 3–5 independent refuters vote; adversarial framing rather than "is this good?" | **Medium** — PDF summarised by an extraction model; the mechanism is corroborated by `doubt-driven-development` and `gsd-plan-checker`, the reported numbers are not verified |
| [Harness as an Asset: enforcing determinism via CAAF](https://arxiv.org/pdf/2604.17025) | Two named failure modes we lacked names for: **compliant hallucination** (output satisfies the constraint while defeating its purpose) and **stochastic oscillation in reflection loops** (a review loop cycling without converging) | **Medium** — same caveat; the two failure-mode names are the durable takeaway |
| [Agentic Harness Engineering: observability-driven evolution of coding-agent harnesses](https://arxiv.org/pdf/2604.25850) | Harness components can be improved measurably without changing the model; evolution is driven by execution-trace signals | **Low-Medium** — summary was generic; directionally supports our telemetry work, cite nothing specific from it |
| [Addy Osmani: Agent Harness Engineering](https://addyosmani.com/blog/agent-harness-engineering/) and [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering) | "Harness over model" framing; the harness validates the tool call rather than the model calling tools directly; full-context-reset-from-a-handoff-file for long jobs | **Medium** — practitioner synthesis |
| [Adversarial Code Review: why the maker shouldn't grade the checker](https://www.augmentcode.com/guides/adversarial-code-review) | Read-only checker + separate fixer as a permissions pattern | **Medium** |
| [forrestchang/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) (MIT, created 2026-01-27; widely forked) | Four behavioural principles: think-before-coding (surface assumptions), simplicity-first, **surgical changes** (touch only what you must; clean up only your own mess), goal-driven execution (define success criteria, loop until verified) | **Medium-High** for the content (read from the repo); **Low** for the popularity claims — aggregator blogs report both "144K" and "101K" stars, so cite the artifact, never the count |
| [The `/grill-me` skill](https://www.aihero.dev/skills-grill-me) and [azukiazusa's write-up](https://azukiazusa.dev/en/blog/before-implementation-interview-design-requirements-grill-me/) | The grilling contract: one question at a time, always carry a recommended answer, look facts up rather than asking, walk the decision tree depth-first | **High** — matches the primary source in `mattpocock/skills` |
| curl's bug-bounty closure after AI-submitted reports drove the confirmed rate below 5% (reported in the code-review-agent search results) | The empirical cost of an unverified finding stream | **Low-Medium** — second-hand; useful as an illustration, not as a statistic to quote |

### 5. What was deliberately not done

- **No `beads_rust` source read** (§2).
- **No vendoring.** Nothing from any repo above has been copied into `basicly`. Every finding in
  the review is expressed in our own words against our own design.
- **No implementation.** The review's output is design documents. Nothing in `src/` changed.
- **The arxiv papers were not read in full.** They were fetched and summarised; the PDFs are
  cached under this session's tool-results directory. Where a paper's contribution mattered
  (CAAF's two failure-mode names, Refute-or-Promote's stage gate) the same mechanism was
  independently corroborated in a repo we did read, and the review leans on the repo.

### 6. Re-running this review

```sh
mkdir -p reference-repos && cd reference-repos
for u in mattpocock/skills obra/superpowers addyosmani/agent-skills \
         DietrichGebert/ponytail techygarg/lattice open-gsd/gsd-core \
         satococoa/wtp headroomlabs-ai/headroom Graphify-Labs/graphify \
         gastownhall/beads ; do
  git clone --depth 50 "https://github.com/$u.git" "$(basename "$u")"
done
```

`Dicklesworthstone/beads_rust` is deliberately omitted from that loop; clone it only if you have
resolved §2, and read `LICENSE` first.

These projects move fast — `gsd-core` landed a commit the same day this review was written, and
`superpowers` shipped a minor release three days before. Treat every finding as pinned to the
revision in §1 and re-check before acting on one that has been sitting for a while.

## Appendix B — re-measurement, verified 2026-08-22

`basicly-6oa3mt`. Appendix A §6 says *"treat every finding as pinned to the revision in §1 and
re-check before acting on one that has been sitting for a while."* This review is 27 days old and
`basicly-e2mz.46` intends to absorb it into `architecture.md`, where architecture rule D-36 makes an
inherited measurement re-run or not absorbed. This appendix is that re-check.

It changes no judgement in §§1–9. It dates them, and it retires the five claims in §B.4 that our own
tree has since falsified.

### B.1 The pins — all eighteen still reachable, fourteen have moved

`git ls-remote --symref https://github.com/<repo>.git HEAD` for the head, and
`gh api repos/<repo>/compare/<pin>...HEAD --jq '[.status,.ahead_by,.behind_by]'` for the distance.
Verified **2026-08-22**.

**Positive control first.** Every clone under `reference-repos/` still sits at exactly the SHA
Appendix A §1 records (`git -C <dir> rev-parse --short HEAD`, eighteen of eighteen), so the pin column
is accurate and this table measures upstream movement rather than a mis-transcribed pin. Every
comparison returned `behind_by = 0`, so no pin has been force-pushed out of its own history and every
finding above remains readable at its revision.

| Repo | Pinned | Head 2026-08-22 | Ahead | Latest tag | Default branch |
| --- | --- | --- | --- | --- | --- |
| mattpocock/skills | `ed37663` | `5b15a47` | 140 | `v1.2.3` | `main` |
| obra/superpowers | `3dcbd5c` (v6.2.0) | `b36e082` | 2 | **`v6.3.0`** | `main` |
| addyosmani/agent-skills | `2471e3f` | `5a5ea45` | 59 | `0.6.7` | `main` |
| DietrichGebert/ponytail | `16f2980` | `2ed6c52` | 4 | `v4.9.0` | `main` |
| techygarg/lattice | `75b7e07` | `75b7e07` | **0 — identical** | `v2.0.0` | `main` |
| open-gsd/gsd-core | `46ba02a` | `dacae92` | **642** | `v1.11.0` | **`next`** |
| satococoa/wtp | `842920d` | `842920d` | **0 — identical** | `v2.10.3` | `main` |
| headroomlabs-ai/headroom | `b121223` | `91186b4` | 307 | `v0.36.4` | `main` |
| Graphify-Labs/graphify | `66d8110` | `b2cd362` | 267 | `v1.0.0` | **`v8`** |
| Dicklesworthstone/beads_rust | `94fb146` | `9c45f79` | 159 | `v0.3.2` | `main` |
| gastownhall/beads | `d01d62e` | `5cbe3a2` | **687** | `v1.2.2` | `main` |
| gastownhall/gastown | `649b832` | `649b832` | **0 — identical** | `v1.2.1` | `main` |
| first-fluke/oh-my-agent | `2c28bc4` | `032c988` | 217 | `web-v4.2.3` | `main` |
| code-yeongyu/oh-my-openagent | `f287227` | `bddfeb5` | **1 999** | `v5.0.0-beta.16` | **`dev`** |
| openai/symphony | `f8e8b8a` | `8001b52` | 1 | `v0.0.2` | `main` |
| automazeio/ccpm | `7d7e462` | `7d7e462` | **0 — identical** | none | `main` |
| coleam00/Archon | `3044829` | `c5b3211` | 291 | `v0.9.0` | **`dev`** |
| SouthBridgeAI/hankweave-runtime | `66a9921` | `d0f0a86` | 3 | `v0.10.0` | **`release/alpha`** |

**What changed, where it is cheap to say.** superpowers shipped `v6.3.0` two commits past the pin, so
the §2.2 findings are one minor release behind and no more. `lattice`, `wtp`, `gastown` and `ccpm` are
byte-identical to their pins, so §§2.5, 2.7, 2.11 and the `ccpm` entry in §2.12 are re-verified by
construction rather than by re-reading. The four repos with a non-`main` default branch are flagged
because the comparison above is against *that* branch: for `graphify` (`v8`) and `oh-my-openagent`
(`dev`) the pin and the head are not on the same release line, and a §2.9 or §2.12 finding restated
from today's head would be a statement about a different branch.

**Structural counts the review cites, re-counted at each head**
`gh api repos/<repo>/git/trees/HEAD?recursive=1 --jq '.tree[].path'`, then the filter named in the
row — spelled out rather than left as "a path filter", because the one figure here that moved is
otherwise not reproducible:

| Cited | § | Recorded | 2026-08-22 | Filter | |
| --- | --- | --- | --- | --- | --- |
| addyosmani: skills / personas / slash commands | 2.3 | 24 / 4 / 8 | **24 / 4 / 8** | `grep -c '^skills/[^/]*/SKILL\.md$'`; `'^agents/[^/]*\.md$'`; `'^\.claude/commands/[^/]*\.md$'` | unchanged over 59 commits |
| lattice: skills, in three tiers | 2.5 | 27, `atoms`/`molecules`/`refiners` | **27, same three** | `find skills -name SKILL.md \| wc -l`; `ls skills` — pin identical, read from the clone at `75b7e07` | control |
| gsd-core: named agents | 2.6 | 34 | **35** | `grep -c '^agents/[^/]*\.md$'` | +1 over 642 commits |
| mattpocock: skills | 2.1 | — | 36 | `grep -c '^skills/[^/]*/SKILL\.md$'` | not previously counted |

The addyosmani count needed a corrected probe before it could be reported: `commands/*.md` returns
**0** while `skills/` and `agents/` beside it return 24 and 4, and the zero is the probe — the slash
commands live at `.claude/commands/*.md`, where the count is 8. Recorded because Appendix A §4's
confidence grading exists for exactly this, and an uncontrolled zero here would have read as
"the commands were deleted".

### B.2 The `basicly`-side figures — where this review has aged

These are the numbers absorption would carry into `architecture.md`, and they are the ones that moved.
Verified **2026-08-22** on branch `harness/basicly-6oa3mt`.

| Claim | § | Recorded 2026-07-26 | 2026-08-22 | Command |
| --- | --- | --- | --- | --- |
| Catalog entries | 6.1 | "~30" | **63** — 41 skills + 22 fragments | `find .basicly/core/skills -name skill.yaml \| wc -l`; `find .basicly/core/fragments -name '*.fragment.yaml' \| wc -l` |
| Descriptions routing rests on | 2.12 | 33 | **20** model-invoked, of 41 skills | `python -c "from pathlib import Path; from basicly.routing_evals import _model_invoked_descriptions as d; print(len(d(Path('.'))))"` |
| Always-on baseline, Claude | 6.2 | ~9 000 cap, "roughly 1 000 characters of headroom" | cap 9 000, **8 893 used — 107 left** | `wc -m < .claude/CLAUDE.md` vs `max_size_warning` in `.basicly/core/targets/claude.yaml` |
| Always-on baseline, Copilot | 6.2 | — | cap 9 000, **8 992 used — 8 left** | `wc -m < .github/copilot-instructions.md` |
| Always-on baseline, Codex | 6.2 | — | cap **16 000** (was 12 000), 15 791 used — 209 left | `wc -m < AGENTS.md` vs `.basicly/core/targets/codex.yaml` |
| Baseline in words | 6.2 | "on the order of 1 300 words" | **1 384** | `wc -w < .claude/CLAUDE.md` |
| Tracker records | 2.10 | 330 | **1 049** (808 closed, 236 open) — **volatile, see B.7** | `basicly tracker stats` |
| `docs/` layout | 6.7 | "nine design documents" | **19 markdown files** across 6 directories | `find docs -name '*.md' \| wc -l` |
| `architecture.md` | — | — | **4 808 lines** | `wc -l < docs/architecture/architecture.md` |
| Routing rank-1 rate | 6.1 | not measured | **41/46 = 89.1%**, floor 85.0% | `basicly catalog lint` |

**§6.2's direction is confirmed and its magnitude is now wrong in our favour — the wrong way.** The
review said the baseline sat at a ~9 000-character cap with roughly 1 000 characters of headroom, and
argued we might be past a cliff. Headroom is now **107** characters on Claude and **8** on Copilot.
The word count reproduces (1 384 against "on the order of 1 300"), so the review's reading of the
adherence-threshold literature applies with more force than when it was written, not less. §6.2's
"we do not know, because nothing measures which baseline rules actually bind" is **still true**: the
cheap first test it proposes has not been run, and none of the above measures binding.

### B.3 Claims this pass could not re-measure

- **`work-tracker.md` §9.1's "every one of our 330 records is level 0" (§2.10).** The record *count*
  re-measures (1 049). The *level* does not: `basicly tracker stats` exposes status, not compaction
  level, so the half of the sentence that carries the argument is **unverified**. Restating "330" as
  "1 049" without it would move a number and leave its premise behind.
- **Every benchmark figure attributed to ponytail (§2.4)** — `-54%` LOC, `-22%` tokens, `-20%` cost,
  `-27%` time, the 95%-versus-100% safety arm. Re-running them requires executing their harness
  against a model, which this pass did not do. They remain what Appendix A §4 grades them: a
  first-party published result, four commits behind its pin.
- **The adherence-decay thresholds (§6.2)** — ~80 lines, ~200 lines, ~500 words. Appendix A §4 already
  grades these **Medium** with "no primary experiment published; treat the numbers as an order of
  magnitude, not a constant". Nothing has been published since that changes the grade, and this pass
  did not run the experiment. **They must not be absorbed into `architecture.md` as constants.**
- **`§2.12`'s hankweave figure — 44.5% of events in a 6 467-event fixture share a millisecond.** The
  clean-room resolution in Appendix A §2.3 makes hankweave source permanently out of bounds as an
  implementation reference. Re-reading the committed fixture to re-derive the percentage sits close
  enough to that line that this pass did not do it. The figure stands as recorded, at its pin, three
  commits stale.
- **§5's asserted-not-earned list.** "One pilot, one task, `n=1`" was the state on 2026-07-26 and no
  second whole-harness A/B has been run, so the admission stands unchanged rather than re-measured.

### B.4 Droppable — five claims our own tree has since falsified

Marked so `basicly-e2mz.46` **drops** these rather than moving them into `architecture.md`, where each
would land as a false statement about the code in the same repository.

1. **§2.1: "Our catalog has no such axis; every projected skill is effectively model-invoked."**
   **False.** `invocation` is a *required* property of `skill.schema.json`, and the catalog splits
   **20 `model` / 21 `user`** (`grep -rh 'invocation:' .basicly/core/skills/ | sort | uniq -c`). The
   review's own §2.1 recommendation is shipped. This is also why the description corpus fell from 33
   to 20 (B.2) while the skill count grew.
2. **§2.12: "we have no cross-skill check at all — every `catalog lint` rule inspects one file in
   isolation."** **False.** The function that claim cites is
   `lint_catalog`, `src/basicly/catalog_lint.py:213`, spanning 213–291 today — **not** the `219-292`
   the claim gives, which no longer locates it (B.7). Line **277** inside it is rule 9,
   *"Tier-2 routing evals over the model-invoked set"*, delegating to `basicly.routing_evals` — a
   check over the whole description corpus, which is precisely the cross-skill check §2.12 says we
   do not have.
3. **§6.1: "no Tier-2 equivalent — nothing checks that a skill's description carries the vocabulary a
   user would actually say, or that two descriptions have not drifted into each other."** **False, and
   it is a gate rather than a script.** `src/basicly/routing_evals.py` opens *"Tier-2 on disk"*;
   `src/basicly/stemmer.py` is the conflation layer; `catalog_routing.evaluate` asserts top-k on
   positives, outranking on negatives, and pairwise description collision. `basicly catalog lint`
   prints `routing: rank-1 rate 41/46 = 89.1% (floor 85.0%)` and exits 0. The floor "may be raised but
   never lowered". The **first half** of §6.1 — per-skill *behavioural* efficacy, evidence about one
   entry of 63 — is **not** droppable and stays open.
4. **§6.3: "No path-scoped guidance tier … two tiers where the platforms offer three."** **False.**
   Five path-scoped fragments project five `.claude/rules/*.md` — `code-is-authoritative`,
   `external-review`, `model-tier-routing`, `platform-hermetic-tests`, `rendered-surfaces`. The third
   tier the review specified is built.
5. **§6.7: "`docs/` is nine design documents. There is no 'your first loop' walkthrough and no
   task-focused how-to guides."** **False on both halves.** `docs/tutorial/first-loop.md` exists under
   that name, and `docs/how-to/` holds six task-focused guides (`unblock-a-commit`, `resume-a-track`,
   `wire-up-the-verify-gate`, `customize-the-catalog`, `upgrade-and-check-drift`,
   `run-parallel-lanes`). The Diátaxis remedy the review names was adopted.

**Not droppable, checked and still open.** §6.4 (stall detection, gate taxonomy, severity contract)
and §6.6 (trend instrument) were probed only by grepping `src/` for the words `stall`, `severity`,
`gate_kind` and `trend`. `gate_kind` returns nothing; the other three return modules whose relevance a
word match cannot establish either way. **This pass did not determine whether §6.4 or §6.6 are still
true**, and a grep on a noun is not the evidence that would.

### B.5 Two counting defects in Appendix A itself

Found by re-counting rather than by reading, and left corrected here rather than edited in place so
the original text and its correction stay visible together:

- Appendix A §1 says **"Rows 11–17 were added 2026-07-30"**. The table holds **18** rows and the
  2026-07-30 sweep added **seven** — `gastownhall/gastown` through `SouthBridgeAI/hankweave-runtime`,
  which are rows **12–18**. Row 11 is `gastownhall/beads`, part of the original eleven named in the
  document's own first line.
- Appendix A §2 says **"Three of the seventeen reviewed repos restrict what we may do"**. Three is
  right; **seventeen is not — it is eighteen**. Counted with
  `awk 'NR>=1109 && NR<=1150' | grep -c '^| \['`.

Neither changes a finding: the three restricted repos are identified by name in §§2.1–2.3 and the
clean-room boundaries rest on those names, not on the count.

### B.6 The licences — re-read, not assumed; none has moved

Appendix A's rule 2 says *"a licence claim is checked, not assumed"*, and §2's whole point is that
three repos restrict us in ways the top-level `LICENSE` did not reveal. Those boundaries are the most
consequential thing this review carries, so they are re-checked by content hash rather than by
re-reading prose. Verified **2026-08-22**.

Method: `sha256sum` of the licence-bearing file in the clone at its pinned revision, against the same
path fetched at HEAD via `gh api repos/<repo>/contents/<file> --jq .content | base64 -d`.

**Result: all 21 licence-bearing files are byte-identical between pin and HEAD** — the eighteen
`LICENSE`/`LICENSE.md` files plus `NOTICE` for `graphify` and `symphony` and `NOTICE.md` for
`hankweave-runtime`. **Twenty of the twenty-one were compared by the sweep that produced this row;
the twenty-first was compared separately and afterwards — see B.7.** Across up to 1 999 commits of
upstream movement (B.1), **not one repo has relicensed**. Every conclusion in Appendix A §2 stands unchanged, including all three clean-room
boundaries:

- **`beads_rust` (§2.1)** — the OpenAI/Anthropic rider is present at HEAD, unmodified, 159 commits
  past the pin. No `beads_rust` source read, then or now.
- **`oh-my-openagent` (§2.2)** — the Sustainable Use License 1.0 is unmodified at HEAD, and the
  per-subtree detail the finding actually rests on still holds: `packages/model-core` has **no licence
  of its own** at HEAD, 1 999 commits past the pin, against a positive control — `packages/pi-goal`,
  which §2.2 names as separately MIT, does return a `LICENSE` from the same probe. The root licence
  still governs `model-core`, so the withdrawn port recommendation stays withdrawn.
- **`hankweave-runtime` (§2.3)** — `NOTICE.md` is unmodified at HEAD, so the Terms-of-Service
  incorporation and its competition restriction are unchanged. The 2026-07-30 owner decision stands
  on its own terms and needs nothing from this pass.

**One correction to Appendix A §2.3.** It says *"The `LICENSE` is stock Apache-2.0."* The file is
named **`LICENSE.md`**; there is no `LICENSE` in that repository, at the pin or at HEAD. The
characterisation is right — the file opens `Apache License` and hashes identically at both — but a
re-checker following the sentence literally gets a file-not-found and has to decide whether the repo
dropped its licence. That is the failure this appendix's own probe hit before the filename was
corrected, which is why it is recorded rather than silently fixed.

### B.7 What a second pass over this appendix changed

`basicly-6oa3mt`, **2026-08-22**. B.1–B.6 were re-checked against the scripts that produced them
rather than re-read. Five things did not survive that check. They are recorded here because three of
them are the *instrument* rather than a finding, and a re-measurement whose commands do not run is not
re-measurable by the next reader — which is the whole failure D-36 exists to prevent.

1. **The licence sweep compared twenty files, not twenty-one.** Its repo list probed
   `SouthBridgeAI/hankweave-runtime` at path `LICENSE`, which does not exist — the same slip B.6's own
   closing paragraph corrects in Appendix A §2.3 — so that row returned `PIN-COPY-MISSING` and
   `LICENSE.md` was never hashed. B.6's conclusion is nevertheless correct, and is now measured:

   ```text
   clone HEAD: 66a9921  (pin recorded: 66a9921)
   LICENSE.md   IDENTICAL
   NOTICE.md    IDENTICAL
   LICENSE at HEAD -> HTTP 404, Not Found
   ```

   The 404 is the positive result, not a failed probe: there is no `LICENSE` in that repository at
   HEAD either, so the Apache-2.0 text lives only at `LICENSE.md` and it has not moved. **All 21 are
   now compared and all 21 are identical.** The clean-room boundary in Appendix A §2.3 stands on a
   hash rather than on an inference.

2. **Two commands in B.2 did not run as written.** The catalog-entry row gave the fragment count as
   "same for `fragments/*.yaml`"; that glob matches **nothing**, because fragments sit one directory
   deeper — `.basicly/core/fragments/` holds **nine subdirectories and zero files**. The figure 22 is
   right; the command now shown produces it. The description-corpus row omitted
   `from pathlib import Path`, so it raised `NameError` rather than printing 20. Both are corrected in
   place, because a wrong instrument is not a finding worth preserving beside its own correction.

3. **The `219-292` range cited for `catalog_lint.py` no longer locates `lint_catalog`.** The function
   spans **213–291** on this branch and spanned **198–270** on `main` at `ca72a50`. Rule 9 — the
   cross-skill check that makes B.4's claim false — is correct in both, so the finding is
   unaffected. The stale range is inherited from
   the §6.1 claim being refuted, written 2026-07-26, and it is a third instance of exactly what §13.3
   of the deepseek document calls a self-pin: a line citation into a file in the same repository ages
   silently and points at the wrong code with no gate to catch it. **Absorption should carry the
   symbol name, not the line range.**

4. **The tracker-record figure is a live counter and must not be absorbed as a constant.** B.2 records
   **1 049 (808 closed, 236 open)**, verified this morning. Re-run this afternoon, `basicly tracker
   stats` printed **1 055 records, 811 closed, 239 open** — a drift of six inside one day, from the
   seven tracker commits `main` landed past this branch's base. Re-run once more an hour later, after
   merging that base in, it printed **1 055 records, 812 closed, 238 open**. Nothing is wrong with any
   of the three readings, and the third is the argument: the sentence written here about drift drifted
   before it was committed. The row's *shape* is wrong for `architecture.md`. What §2.10 needs is the
   order-of-magnitude contrast with "330 records" — a threefold growth in four weeks — and that
   survives all three readings, where the digits survive none of them.

5. **B.1's structural-count table said "a path filter" and named no filter.** Of its four rows one had
   moved — gsd-core 34 → **35** named agents — so that row was the only changed figure in either
   document with no reproducible command behind it. Each row now carries its filter, and all four
   re-derive: `grep -c '^agents/[^/]*\.md$'` gives 35, and the addyosmani triple gives **24 / 4 / 8**
   against the same tree listing. This was found by running the demonstration rather than by reading:
   the check that every changed figure names its command is the check that failed, once the probe was
   tightened to reject a backticked SHA as a "command".

**Unchanged by this pass.** B.1's eighteen pins, B.3's five unverifiable claims, B.4's other four
droppable items and B.5's two counting defects were each re-derived from the recorded scripts and
outputs in `.basicly/usage/scratch/` and reproduce as written. B.2's remaining eight rows were re-run
directly: 41 skills, 22 fragments, 8 893 / 8 992 / 15 791 characters against caps 9 000 / 9 000 /
16 000, 1 384 words, 19 markdown files under `docs/`, 4 808 lines of `architecture.md`, five
`.claude/rules/*.md`, six `docs/how-to/` guides, `docs/tutorial/first-loop.md`, the
**20 `model` / 21 `user`** invocation split, and `catalog lint` printing
`routing: rank-1 rate 41/46 = 89.1% (floor 85.0%)`.
