# Gates, Rework, and Adversarial Verification

Status: **design, not yet decomposed.** Opened 2026-07-26 out of the state-of-the-art review
([`research/2026-07-26-sota-review.md`](../research/2026-07-26-sota-review.md) §§6.4–6.5).
[`factory-design.md`](factory-design.md) remains authoritative; where this document and the
factory design appear to disagree, the factory design wins until amended. This document proposes
amendments rather than asserting them.

What it covers: a **taxonomy** for the gates we already have, **convergence detection** for the
rework loop we already bound, and a **verification contract** for the judged output we already
collect. Nothing here adds a persona; almost everything here is deterministic engine code.

## 1. Why classify gates at all

We have a lot of gates — DoR, `fast`, `full`, rubric, verify, commit-msg, projection checks, ship
preconditions — and each one answers "what happens when I fail?" **at its own call site**. That
means the answer is re-decided per gate, inconsistently, and a new gate's author has no template
to follow.

Four behaviours are actually in use, and they are the four `gsd-core` names. Adopting the taxonomy
costs nothing and makes "what kind of gate is this" a question with an answer.

| Gate | Purpose | Behaviour on failure | Recovery | Key property |
| --- | --- | --- | --- | --- |
| **Pre-flight** | validate preconditions before work starts | blocks entry; **no partial work created** | fix the precondition, retry | cheap, deterministic, wastes nothing |
| **Revision** | evaluate produced output, route back to its producer | loops with specific feedback, **bounded by a cap** | producer addresses, checker re-evaluates | must have a cap *and* §3's stall detector |
| **Escalation** | surface an unresolvable issue for a human decision | pauses, presents options, waits | human disposes, work resumes | the safety valve between revision and abort |
| **Abort** | stop to prevent damage or waste | halts, **preserves state**, reports the reason | fix root cause, resume from checkpoint | must never lose state |

**Selection heuristic** (adopted verbatim in substance): start with pre-flight; if the check
happens *after* work is produced it is a revision gate; if the revision loop cannot resolve it,
escalate; if continuing is dangerous, abort.

**Cap-sizing rule:** the cap reflects the cost of one iteration. Expensive operations get fewer
retries. A landing bounce is not the same price as a re-review of a three-line fix and should not
share a budget.

### 1.1 Mapping our existing gates

Deliberately included, because the value of a taxonomy is entirely in whether it classifies the
real cases. Where the mapping is uncomfortable, that discomfort is a finding.

| Existing gate | Type | Note |
| --- | --- | --- |
| DoR at classify | Pre-flight | correct already: blocks before decomposition, creates nothing |
| Scope-disjointness / sizing governor at decompose | Pre-flight | blocks dispatch; the D8 band verdict is a precondition |
| `fast` gates at sub-task | Revision | producer is Kai; cap is the rework bound |
| `full` gates at lane integrate | Revision | same |
| Rubric / validate | **Revision, currently mis-shaped** | see §4.1 — a judged NO routes a decision item, which makes it an *escalation* gate wearing a revision gate's name |
| Merge-queue bounce-back | Revision | producer is the owning lane; §3's stall detector is most valuable here |
| commit-msg / secret-scan / projection checks | Pre-flight | they block the commit; nothing partial lands |
| Ship preconditions | Pre-flight | correct already |
| `needs-input.json` | **Escalation** | this is our escalation gate and it is currently un-named as one |
| Uncommitted work blocks a landing | Abort | halts, preserves, reports — correct behaviour, unclassified |
| Supervisor refuses build/ship from a linked worktree | Abort | ditto |

Two observations fall straight out of the table. First, **we already have all four types** and have
simply never named them — so this is documentation of existing behaviour more than new design.
Second, **the rubric gate is the one genuine misclassification** (§4.1), and it is also the gate
R4 already knew was unsatisfying.

## 2. Pre-flight gates create nothing

Worth stating as a rule because our own history contains its violations. Our recorded incidents —
a hand-recorded verify gate that shipped a bead with code stranded unmerged, an approved ship
checkpoint that wedged phase derivation with no un-approve path — are both cases where **something
that should have blocked entry instead recorded state**.

**Rule: a pre-flight gate is read-only.** It reads the world, returns a verdict, and writes
nothing. If a check needs to write in order to decide, it is not a pre-flight gate and its failure
mode needs re-deriving from §1.

This is also the cheapest guard against the class of bug that produced those incidents: a gate that
cannot write cannot leave the tracker in a state no command can undo.

## 3. Stall detection — the missing half of bounded rework

Our rework is capped. A cap alone answers "when do we stop?" but not "are we still making
progress?", so a loop that has stopped converging burns its entire remaining budget before anyone
notices.

`gsd-core`'s revision gate *"escalates early if issue count does not decrease between consecutive
iterations."* The CAAF paper names the underlying failure **stochastic oscillation in reflection
loops** — a review loop cycling between states without reaching one. Having the name matters: this
is not "the agent is bad at fixing things", it is a predictable property of a loop whose exit
condition depends on a stochastic judgment.

**Decided: every revision gate carries both a cap and a stall detector.**

The detector is deterministic and needs no model:

- Compare the **open-finding set** between consecutive iterations, not just the count. Count alone
  is fooled by a round that fixes one finding and introduces one.
- **Stall** = the open set did not shrink, i.e. no previously-open finding was resolved this round.
- **One** stalled round is a warning recorded on the bead. **Two consecutive** stalled rounds
  escalate immediately, without consuming the remaining cap.

Why the open *set* rather than the count: it also makes the loop's history legible. `2 addressed, 1
new` is a converging round; `0 addressed, 0 new` is a stalled one; `0 addressed, 2 new` is a
diverging one and should escalate on the first occurrence, not the second.

### 3.1 Capability escalation on late rounds

Adopted from superpowers, whose reasoning is better than "try again harder": *"a loop that survives
three resumes usually means the implementer cannot see its own problem — fresh eyes and a
capability bump in one move."*

Their shape: rounds 1–3 **resume the original implementer** (its context is intact — it knows the
code and its own choices); rounds 4–5 dispatch a **fresh** implementer **one tier up**, framed as
*"a prior implementer attempted this task N times; you own it now. Read the report file for what
was tried."*

Ours bounces to the same tier with the same framing every time, which spends the cap without ever
changing a variable. Two adaptations are needed because our architecture differs:

- **Resume vs fresh is a runner capability, not a given.** Not every agent family we dispatch can
  be sent a follow-up message. Where resume is unavailable, a fresh dispatch carrying the prior
  attempt's record is the equivalent — which is exactly why the record must be a **file the
  dispatch points at**, not accumulated context (§5.2).
- **The tier bump interacts with R5.** R5 already argues the reliable tier is the cheap one per
  landed package; a bump on late rounds is that argument applied within a single package. It also
  produces a measurable signal: **if late-round bumps routinely succeed, the initial tier was
  wrong** — which is evidence `basicly-7bur` can read directly.

**Proposed:** rework rounds carry an explicit escalation ladder — same agent (resume where
available) for early rounds, fresh dispatch one tier up for late rounds, then the cap. Every
transition is recorded on the bead so the ladder's effectiveness is measurable rather than assumed.

## 4. Adjudication at the cap, and the disposition path R4 needs

### 4.1 The rubric gate is really an escalation gate

R4 decided that a judged NO **routes a decision-queue item**: the lane does not land, and a human —
or the Decider under a sufficient grant — disposes. That is the correct decision and it is
*escalation* behaviour, not revision behaviour. The confusion is worth removing because D4 promotes
the rubric gate to **required** at lane and session level, which currently means: a required gate
whose judged checks cannot fail it, guarding rubrics that in some cases are still unwritten — so
it can pass having checked nothing.

**Clarification — landed 2026-07-26 (`basicly-imnu.1`).** The validate step is a **composite** of
two gates with different types, recorded separately:

- `rubric` — a **pre-flight** component, deterministic rubric checks, which *can* fail the lane;
  and
- `rubric-judged` — an **escalation** component, judged checks, which never fails the lane and
  instead enqueues a decision item carrying the failing criterion and its evidence.

This keeps "no persona passes a required gate" (R4) intact, gives the required gate real teeth from
its deterministic half, and stops a judged NO looking like a test failure. **An unsatisfied
acceptance criterion is a decision, not a red test** — R4's own sentence, now with a gate type
behind it.

Two implementation notes, because each names a way the split could be undone by accident:

- The escalation gate is **absent from `[policy] required_gates`**, and that absence is the
  mechanism. `policy.gate_status` treats any gate outside the required set as advisory, so
  `rubric-judged` may record an honest `fail` without blocking advancement. Adding it to the
  required list would silently restore the incoherence this section exists to remove.
- **Both halves are recorded on every validate**, including when a half has no checks of its kind.
  A gate that appears only when it has something to say cannot be read afterwards: a missing
  `rubric-judged` would be ambiguous between "no judged checks existed" and "the judged half never
  ran", and only one of those is acceptable.

What the split fixed in practice: a judged NO previously left the single combined gate reading
`pass`, surviving only as text in the note — so the gate record could not distinguish a satisfied
acceptance criterion from a disputed one.

### 4.2 Adjudicate only at the cap

Adopted from superpowers, including the rule that gives it force. When the cap is reached with
findings still open, the engine cannot decide and a human (or the Decider) adjudicates each open
finding into exactly one of three outcomes:

| Outcome | When | Record |
| --- | --- | --- |
| **Parked — contestable** | the reviewer is wrong, or the point is genuinely arguable | park with a **ruling**: why the code stands |
| **Parked — real, deferred** | the finding is real but nothing downstream builds on it | park with a ruling saying exactly that |
| **BLOCKED** | real *and* load-bearing — a later unit builds on it, or it reveals a plan defect | stop; report with the finding, the plan text it collides with, and the fix history |

Three rules, all adopted, all load-bearing:

- **Adjudicate only at the cap.** *"Adjudicating earlier to end a loop is pre-judging with a
  different name."*
- **Every adjudication is a ledger entry. A silent discard is forbidden.** This is D11 applied to
  the rework loop, and our comment-marker families already provide the mechanism.
- **Never park a structural failure.** *"Parking a structural failure lets every dependent task
  build on it and hands the final review a problem it cannot fix either."*

### 4.3 Deferred minors need a named consumer

superpowers' rule: minor findings never enter the loop; they are recorded and *"the final
whole-branch review is pointed at that list so it can triage which must be fixed before merge"* —
because *"a roll-up nobody reads is a silent discard."*

For us the consumer is the **session-level review** and the **ship-time rollup**
(`basicly-kjc5.50`). A deferred minor with no named consumer is a `no-trigger` item in the review's
vocabulary — recorded, and structurally guaranteed to rot.

## 5. The verification contract

This section is about the *quality of judged output*, which is currently the weakest link: our
judged gates collect verdicts whose reliability nobody has characterised, and an advisory green
from an unreliable reviewer confers confidence nobody earned.

### 5.1 What the reviewer receives — and what it must not

Adopted from `doubt-driven-development`, whose separation is precise:

- **Pass ARTIFACT + CONTRACT.** The diff or unit under review, plus the criteria it must satisfy.
- **Never pass the CLAIM.** *"Handing the reviewer your conclusion biases it toward agreement."*
- **Strip the producer's reasoning.** *"If you hand over conclusions, you'll get back validation of
  your conclusions."*

For us this is enforceable rather than aspirational, because dispatch bundles are a deterministic
function of tracker state (D6). The bundle assembler can be *structurally incapable* of including
the implementer's rationale in a reviewer's bundle. That is the difference between a rule and a
guarantee.

### 5.2 Artifacts are handed over as files

superpowers: *"Everything you paste into a dispatch prompt — and everything a subagent prints back
— stays resident in your context for the rest of the session and is re-read on every later
turn."* Their measured failure: *"a real session's dispatch hit 42k chars of which 99% was pasted
history."*

So the reviewer receives **paths**, and a package file carries the commit list, the stat summary,
and the full diff with context. Two operational details worth stealing outright:

- **Record BASE before dispatching the producer.** Never derive the review base as `HEAD~1` — it
  *"silently truncates multi-commit tasks"*, reviewing the last commit of a unit and reporting on
  the whole.
- **Re-reviews are scoped to the fix range**, verdicting each open finding ADDRESSED or NOT
  ADDRESSED and flagging new breakage **in the fix diff only**. Out-of-scope observations become
  deferred minors; *"they never extend the loop."* Without this the loop cannot converge, because
  each round can discover unrelated work.

Our supervisor already keeps no side-state and derives from `br`, so the package-file pattern fits
without architectural change. It is a bundle-assembly change, not a design change.

### 5.3 The no-pre-judging rule, as a lint

superpowers states it as a checkable string test on the prompt being written: *"If the prompt you
are writing contains 'do not flag', 'don't treat X as a defect', 'at most Minor', or 'the plan
chose' — stop: you are pre-judging, usually to spare yourself a review loop."*

Because our dispatch prompts are **assembled by code**, we can do better than guidance: **lint the
emitted reviewer bundle** and refuse to dispatch one containing a finding-suppressing directive.
A rule an observer can mechanically check is worth ten rules of good intent — and this one is
checkable at the exact moment it matters.

Corollary for the human-facing side: if the engine believes a finding would be a false positive,
the correct move is to let the reviewer raise it and **adjudicate it** (§4.2), never to pre-empt
it.

### 5.4 Severity is a required field

`gsd-core`: *"Issues without a severity classification are not valid output."* Structural, not
advisory — which is precisely the right form for an omitted-element failure per the efficacy
document's form-matching table.

**Proposed:** the judged-verdict output contract requires a severity per finding, and a verdict
missing one is a **malformed response** the engine rejects and re-requests, exactly as it would
reject unparseable JSON. Not a quality complaint — a schema violation.

Minimum vocabulary, deliberately small: `BLOCKER` (the goal is not achieved unless this is fixed)
· `IMPORTANT` (fix before landing) · `MINOR` (record, do not loop — §4.3). Two named classes is
`gsd-core`'s choice and three is ours only because §4.3 needs a class that is explicitly excluded
from the loop.

### 5.5 Adversarial framing, and how reviewers go soft

The strongest single pattern in the review is `gsd-core`'s **FORCE stance** plus an enumerated list
of that role's degradation modes. Their plan-checker opens: *"Assume every plan set is flawed until
evidence proves otherwise. Your starting hypothesis: these plans will not deliver the phase
goal."* Then it names how a plan checker goes soft:

- accepting a plausible task list without tracing each task back to a requirement;
- crediting a decision reference without verifying the task delivers its full scope;
- treating scope reduction ("v1", "static for now", "future enhancement") as acceptable;
- letting dimensions that pass anchor judgment — *"a plan can pass 6 of 7 dimensions and still fail
  the phase goal on the 7th"*;
- **issuing warnings for what are actually blockers to avoid conflict with the producer.**

That last item names reviewer conflict-avoidance as a predicted failure and pre-empts it, which is
worth more than any amount of "be rigorous". This is the form R8's quirks were reaching for, done
properly: each item is a specific, observable behaviour, derived from how the role actually fails.

**Proposed:** every judged role's projected prompt carries (a) an explicit adversarial stance, and
(b) a **role-specific** "how this role goes soft" list. Generic rigour instructions are a **no-op**
in the review's vocabulary — the model is already somewhat rigorous, so the line changes nothing.
The soft-list is not generic and therefore is not a no-op.

The lists must be **derived from observed failures**, not invented. Our loop already records
verdicts, rework rounds, and adjudications; those are the raw material, and this is the highest-
value thing Lumi could be pointed at.

### 5.6 Doubt theater — a computable anti-sycophancy signal

`doubt-driven-development` defines it as a checkable signal: across ≥2 cycles where the reviewer
surfaced substantive findings, **zero** findings were classified as actionable → *"You are
validating, not doubting. Stop and escalate."*

We can compute the mirror image from data we already record. Two degenerate reviewers are both
worthless and both currently invisible:

- **The rubber stamp** — a lens that approves nearly everything. Its advisory green means nothing,
  and it is exactly the reviewer R6 warns about (*"an advisory green from a weak reviewer confers
  confidence no one earned"*).
- **The noise generator** — a lens whose findings are nearly all adjudicated as contestable. It
  costs rework rounds and buys nothing.

Both are measurable per lens over a window: **finding rate**, and **adjudication outcome
distribution**. This is the instrument R6 needs to execute its own promise (*"if a lens does not
pay for itself, it gets cut"*), and it is the trend gap the review flagged (§6.6).

### 5.7 Refute-or-promote, where the finding is expensive

For high-consequence findings — a security lens finding at a trust boundary, an architecture
finding that would reshape a design — the field's answer is a **refutation stage**: N independent
reviewers each prompted to **disprove** the finding, with a majority required to kill it. Reported
at 3–5 refuters. `doubt-driven-development` adds a refinement worth more than raw redundancy:
**give each verifier a different lens** where a finding can fail in more than one way, since
diversity catches failure modes redundancy cannot.

**Not proposed as a default.** It multiplies the most expensive dispatch class, and R6 already
argues the reviewer's failure mode is the miss rather than the false alarm — refutation optimises
precision, which is the axis we are *less* worried about.

**Proposed as a targeted mechanism** with a deterministic trigger: a finding that would **block a
landing or a ship** earns a refutation pass; an advisory finding does not. That keeps the cost
proportional to the consequence and keeps the trigger out of a model's hands, which is where D9
requires it to be.

The relevant cautionary datapoint: curl closed its bug bounty permanently after AI-generated
submissions drove the confirmed rate below 5%. An unverified finding stream does not merely waste
effort — past a threshold it destroys the reviewing party's willingness to engage at all. That is
the failure mode a refutation stage exists to prevent, and it is a reason to build the §5.6
instrument even if refutation itself stays targeted.

## 6. Two-pass generation inside a single dispatch

`lattice`: *"Asking AI to generate and validate simultaneously is unreliable — like asking a writer
to write and proofread in the same pass. The creative task and the analytical task compete for
attention, and one always suffers."* Their remedy is generate → **STOP** → verify against explicit
checklists → present.

We have the *cross-dispatch* version already: Kai implements, Vera and Remo judge, in separate
contexts with separate tool policies. That is stronger than lattice's version — a genuinely
independent reviewer beats self-review, which is why `subagent-driven-development` insists
*"implementer self-review never replaces the task review; both are needed."*

But we do not have the **within-dispatch** version, and it is nearly free: the implementer runs a
scoped self-check against its own acceptance criteria before reporting DONE. The gain is not
better judgment; it is **cheaper rework**, because a defect the implementer catches itself never
consumes a review round.

**Proposed:** the implementer's output contract includes a self-check pass with an explicit
cognitive boundary, and its result is reported as a distinct section rather than mixed into the
narrative. Two constraints keep this from becoming theatre:

- **It never substitutes for the independent review** (superpowers' rule, adopted).
- **Its checklist is the bead's own acceptance criteria**, not a generic quality list — otherwise it
  is a no-op.

Worth flagging honestly: lattice's claim that separating the passes outperforms combining them is
*asserted*, not measured, in their docs. It is a good candidate for a micro-test under
[`catalog-efficacy-design.md`](catalog-efficacy-design.md) §5 rather than an assumption to build on.

## 7. Two failure modes worth naming

From CAAF, because a named failure is one a review can look for:

- **Compliant hallucination** — output that technically satisfies the stated constraint while
  defeating its purpose. This is the specific thing `gsd-core`'s soft-list guards against
  (*"crediting a decision reference without verifying the task delivers its full scope"*), and the
  reason a rubric of deterministic checks alone is insufficient. It is also the strongest argument
  for the hidden-criterion discipline in the efficacy document: a criterion the producer can see is
  a criterion the producer can satisfy without achieving anything.
- **Stochastic oscillation** — a reflection loop cycling without converging. §3's detector exists
  for exactly this, and having the name is what turns "the loop burned its cap again" from an
  anecdote into a class.

## 8. What this document does not propose

- **No new persona.** Everything here is engine code, an output contract, or a projected prompt
  section. The stall detector, the pre-judging lint, the severity schema, and the doubt-theater
  metric are all deterministic.
- **No change to R1/R2/D1/D2.** Agents propose, the engine disposes; personas do not spawn
  personas.
- **No weakening of "no persona passes a required gate."** §4.1 *strengthens* it by giving the
  required gate a deterministic half that can genuinely fail.
- **No agent-to-agent debate.** The refutation pass in §5.7 is independent reviewers voting, not
  reviewers messaging each other. The distinction is the one D1/D2 rest on.

## 9. Preconditions before implementation

1. **§1.1's mapping should be reviewed against the code**, not just against my reading of the
   design docs. If a gate resists classification, that is the interesting case and it changes this
   document.
2. **§5.6's metrics need a window and a threshold**, and neither should be guessed. They need the
   verdict and adjudication history the loop already records, read once, before a number is
   written down.
3. ~~**§4.1 is an amendment to D4** and needs to land in
   [`factory-design.md`](factory-design.md) before anything is built against it.~~ **Closed
   2026-07-26 (`basicly-imnu.1`)**: the composite is recorded in `factory-design.md` §D4 and
   implemented as the `rubric` / `rubric-judged` pair, so Phase 2's severity contract and gate
   taxonomy now have an amended shape to build against.
4. **§6's claim is unmeasured** and should be micro-tested before it becomes a contract.
