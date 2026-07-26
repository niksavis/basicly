# Catalog Efficacy — Proving the Guidance Layer Works

Status: **design, not yet decomposed.** Opened 2026-07-26 out of the state-of-the-art review
([`research/2026-07-26-sota-review.md`](../research/2026-07-26-sota-review.md) §6.1). No
implementation starts from this document; §8 names what has to be true before it does.

Scope boundary, so this document does not collide with its two siblings:

- [`harness-eval.md`](harness-eval.md) asks **"is the whole harness better than a bare agent?"** —
  one A/B, arm-level, already piloted (`basicly-8z52`).
- **This document** asks **"does each individual catalog entry earn its place?"** — per-entry,
  routing and behaviour, the unbuilt half of `basicly-4t9z`.
- [`steering-surfaces-design.md`](steering-surfaces-design.md) asks **"which surface should a
  given piece of guidance live on?"** — this document is how that question gets an answer from
  data instead of taste.

## 1. The problem, stated without flinching

We ship roughly thirty catalog entries — always-on fragments and on-demand skills — projected into
three agent families and installed into consumer repos. We have behavioural evidence about
**one** of them, from a single-task `n=1` pilot whose own write-up notes the result was partly
circular.

Two failure modes follow, and both are **silent**:

- **A skill that never fires.** Its description sits in every context window on every turn
  forever, and delivers nothing. There is no signal distinguishing "this skill is rarely needed"
  from "this skill's description does not contain a single word a user would actually say."
- **A rule that no longer binds.** An always-on fragment that has drifted past the point where the
  model attends to it still passes every gate we have — `catalog lint`, `check`, `skills-check`,
  `agents-check` all verify **that it is well-formed and projected**, never that it **changes
  behaviour**. Our structural gates are excellent and they are measuring the wrong axis.

The review found exactly one project in the field measuring routing (`addyosmani/agent-skills`)
and exactly one measuring behavioural lift rigorously (`DietrichGebert/ponytail`). Nobody does
both. The gap is therefore both our largest weakness and the clearest available differentiator.

**The governing principle: a catalog entry is a behavioural claim, and an unmeasured behavioural
claim is a liability, not an asset.** It costs context on every turn and it confers confidence
nobody earned.

## 2. Three tiers, adopted with the reasons they exist

Adopted from `agent-skills` (`evals/README.md`), whose own framing is worth keeping: Tier 2 is
*"a deterministic, CI-safe check for a multi-skill catalog"*, which is the thing neither
Anthropic's skill-creator nor superpowers provides.

| Tier | Question | Determinism | Runs | Cost |
| --- | --- | --- | --- | --- |
| **1 Structural** | Is it well-formed and projected? | fully deterministic | every commit | free |
| **2 Routing** | Does it fire when it should, and only then? | deterministic (lexical) | every commit | free |
| **3 Behavioural** | Does an agent following it produce different, better work? | judged | on demand + release | tokens |

**We already have Tier 1.** `catalog lint`, `check`, `skills-check --all-default-roots`,
`agents-check`, `hooks-check`. Tier 1 needs one addition only: **an entry without an eval case
file is a Tier-1 failure** (§6).

**Tier 2 is the new deterministic gate and the highest-value single deliverable in this
document.** It is free, it runs in CI, and it catches the two dominant real-world trigger bugs.

**Tier 3 is where the honesty lives**, and where §4's controls decide whether a result means
anything.

## 3. Tier 2 — routing, deterministic and free

### 3.1 What it checks

Three assertions over the projected catalog, no model involved:

1. **Positive routing.** For each declared realistic prompt, the owning entry ranks in the top-k
   of a lexical relevance ranking over all entry descriptions. `top_k` defaults to 3; an entry's
   signature ask declares `top_k: 1`.
2. **Negative routing, pairwise.** Each negative prompt belongs to a *different* entry and names
   it as `owner`. The assertion is that **the owner outranks this entry** — not merely that this
   entry fails to rank first. Their reason for the stronger form is decisive: a bare
   "must not rank first" negative *"can pass vacuously when the prompt matches nothing."*
3. **Collision.** No two descriptions exceed a pairwise similarity ceiling. Error at ≥75%, warn at
   ≥50%.

### 3.2 The metric that goes in CI

**Rank-1 rate** — the share of positive prompts whose owning entry ranks *first*, not merely
top-k. Top-k passing hides gradual drift; rank-1 does not.

Three rules govern the floor, and the third is the one that matters:

- Establish the baseline by measurement, then set the CI floor **below** it, leaving headroom so
  an unrelated description edit does not immediately redden CI.
- Raise the floor as routing improves.
- **Never lower the floor to make a regression pass.** Lowering it is the same act as deleting the
  test, performed in a way that looks like maintenance.

### 3.3 The ranking function must be deterministic and boring

Stemmed TF-IDF over descriptions, or equivalent. Two properties are load-bearing and one
temptation must be refused:

- **Deterministic**, so a Tier-2 result is reproducible and a diff in the score is caused by a
  diff in the catalog. This is the same requirement D9 places on the scheduler.
- **Pure Python, no new runtime dependency** — the constraint `work-tracker.md` §4 argues for the
  tracker applies identically here. A stemmer and a TF-IDF ranker are a small amount of code we
  can own.
- **Refuse embeddings.** They would make Tier 2 semantic and therefore better at judging
  relevance — and would also make it non-deterministic, network-dependent, and unownable. Tier 2's
  value is that it is *free and always runs*; semantics are Tier 3's job. `agent-skills` names
  their own Tier 2 a *"lexical approximation"* and is right to.

### 3.4 What a Tier-2 failure means

**It means fix the description, not the eval.** Stated explicitly because the reflex runs the other
way. If a realistic prompt cannot rank its entry, the description is missing vocabulary a user
actually says — and that is a real finding about a real defect.

Two authoring rules follow, both adopted:

- **Do not copy the description into the prompt.** That is gaming the eval. Prompts paraphrase how
  users actually talk.
- **A description states triggers, and must not summarise the workflow.** This is superpowers'
  observed failure, not a stylistic preference: a description reading "code review between tasks"
  caused an agent to perform **one** review where the body specified two, because *"descriptions
  that summarise workflow create a shortcut agents will take. The skill body becomes documentation
  agents skip."* Tier 2 rewards vocabulary coverage, so it must not be allowed to push authors
  toward stuffing the workflow into the description. **A Tier-1 check should reject a description
  containing imperative process steps.**

### 3.5 Interaction with the invocation axis

Tier 2 only makes sense for entries the *model* can reach. The review surfaced an axis our catalog
lacks (review §2.1): an entry is either **model-invoked** (keeps its description; agent-reachable;
pays permanent context load) or **user-invoked** (description stripped; reachable only by a human
typing it; zero context load).

That distinction is a prerequisite for Tier 2, not an aside:

- A **model-invoked** entry is subject to all three Tier-2 assertions. Its description is the
  thing being tested.
- A **user-invoked** entry is exempt from positive/negative routing (nothing routes to it) but
  **must still be checked for collision**, and it must **not** carry a model-facing description at
  all — otherwise it is paying context load for reach it does not have.

So the first deliverable is not the ranker. It is **declaring the invocation axis in catalog
sources**, because until an entry knows whether it is agent-reachable, "does it route correctly"
is not a well-posed question. This also directly relieves §6.2 of the review: every entry
correctly reclassified as user-invoked stops paying context load forever.

## 4. Tier 3 — behavioural, and the controls that make it mean something

Tier 3 is expensive, so it must be *decisive*. The review's methodological centrepiece
(`ponytail`) plus our own pilot's self-criticism give us the design.

### 4.1 The trap we already found ourselves

`harness-eval.md`'s pilot noted: *"The C4 win is partly circular — Arm H was told to stub, and
did. That validates the mechanism ('skills change output') more than net quality."* That is the
correct diagnosis and it generalises into the rule that shapes every Tier-3 case:

**Separate *mechanism confirmed* from *outcome improved*, and never report one as the other.**

- **Mechanism confirmed** — the guidance changed behaviour on the axis it targets. Easy to
  demonstrate, near-worthless on its own, and the thing a naive eval measures.
- **Outcome improved** — a **hidden** criterion the arms never saw came out better. This is the
  only result that supports a claim about quality.

Every Tier-3 case therefore carries **both** an expectation set (mechanism) and at least one
**hidden objective check** (outcome), and the report shows them in separate columns. A case with no
hidden check may be run, but its result may never be cited as evidence of quality.

### 4.2 Arms — the baseline and two controls

An entry-level Tier-3 run compares four arms. The two controls are not optional decoration; each
one kills a specific alternative explanation, and without them a positive result is
uninterpretable.

| Arm | What it is | What its absence would let us wrongly conclude |
| --- | --- | --- |
| **baseline** | same agent, same model, no catalog entry | — (the reference) |
| **entry** | the catalog entry as really projected | — (the thing under test) |
| **naive** | one or two sentences of obvious advice on the same topic | that a whole authored entry was needed, when a sentence would have done |
| **generic** | an unrelated entry of similar length | that the effect came from the *content*, rather than from any guidance being present at all |

The `naive` control is the one authors will want to skip and the one that most often kills a
result: ponytail's seven-word `yagni-oneliner` arm captured a large share of the full skill's
effect on some metrics. If `naive` matches `entry`, **the honest action is to shrink the entry to
those two sentences**, which is a win, not a defeat.

### 4.3 Isolation, and the contamination bug to expect

ponytail published a contamination bug we would certainly have hit: their plugin's `SessionStart`
hook fired on **every** arm, so *"the baseline was secretly running ponytail"*, producing a
falsely tiny ~4% gap they nearly published.

Our exposure is worse than theirs, because our whole product is always-on projection plus managed
hooks. Concretely, an arm must control for: the projected always-on baseline
(`AGENTS.md`/`CLAUDE.md`/`copilot-instructions.md`), managed git hooks, the loop's own dispatch
preamble, and any globally installed plugins or user-level skills on the runner's machine.

**Requirements:**

- Each cell runs in a **fresh throwaway repo** with only the arm's guidance present.
- Projection is **explicit per arm**, never inherited from the developer's environment.
- The harness **asserts the isolation** — a pre-flight check that reads back what guidance is
  actually live in the cell and fails if it does not match the arm's declaration. Detecting
  contamination must not depend on someone noticing an implausible result.
- **Publish contamination bugs when found.** ponytail's disclosure is why the rest of their
  numbers are credible. The same standard applies to us; a quietly fixed benchmark bug makes every
  earlier number unciteable.

### 4.4 Sample size and what may be claimed

`n=1` per cell supports no claim beyond "the pipeline runs". Default `n=4` per (case, arm), matching
ponytail, with the report stating `n` beside every number and **reporting per-task results, not
only the mean** — ponytail's effect is 94% on one task and ~0 on another, and a mean alone would
have hidden both facts.

Where an effect is near zero, say so. *"Near zero on code that is already minimal"* is a finding
about the entry's applicable scope, and it belongs in the entry's own documentation.

### 4.5 The safety tier is separate and non-negotiable

ponytail's most important structural choice: an adversarial tier, scored independently, that
**executes** the produced code against hostile input — so an improvement on the headline metric
cannot be bought by dropping validation. Their naive-prompt arm scores 95% there against
ponytail's 100%, and that difference is invisible without the tier.

We need the same, generalised: **no Tier-3 improvement is accepted if the safety tier regresses.**
Our equivalent of "less code" is any entry that makes an agent faster, terser, or more decisive —
`ponytail`-style minimalism guidance, scope discipline, "don't over-engineer" rules. Each is a
plausible route to dropping input validation at a trust boundary, and our own Secure Coding rules
are exactly what would be dropped.

**Decided: the safety tier is a gate, not a metric.** A regression there fails the entry
regardless of every other number.

## 5. Micro-tests — verify the wording before spending a scenario

Adopted from superpowers, whose head-to-head wording tests are the only measured results in the
field on *how guidance should be phrased*. Full pressure scenarios are the final gate but are slow
and expensive per iteration; a micro-test verifies the wording first.

Protocol:

1. **One fresh-context sample per call.** System prompt is *the realistic context the guidance
   will live in* — the full projected fragment or skill, not the sentence in isolation. User
   message is a task that tempts the failure.
2. **Always include a no-guidance control.** And the stopping rule that gives this teeth: *"if the
   control doesn't exhibit the failure, there is nothing to fix — stop, don't author the
   guidance."* This is the cheapest **no-op** detector we can build, and the no-op is the single
   most common defect in an always-on layer.
3. **5+ reps per variant.** Single samples lie.
4. **Read every flagged match manually.** Automated scoring overstates both failure and success
   because *"template echoes and quoted counter-examples masquerade as hits."*
5. **Variance is a metric.** When guidance binds, reps converge on the same shape. *"Five different
   interpretations across five reps means the wording isn't binding — tighten the form before
   adding words."* Report variance beside the effect; do not average it away.

### 5.1 Match the form to the failure

Guidance form is not style — superpowers measured that *the form which bulletproofs one failure
measurably backfires on another*. This table is therefore a design rule for catalog authoring, and
the micro-test is how an author checks they picked right:

| Baseline failure observed | Right form | Wrong form |
| --- | --- | --- |
| Knows the rule, skips it under pressure | prohibition + rationalisation table + red-flag list | soft guidance ("prefer…", "consider…") |
| Complies, but the output has the wrong shape | **positive recipe**: state what the output IS, its parts, in order | prohibition list ("don't restate", "never narrate") |
| Omits a required element from something it already produces | **structural**: a REQUIRED slot in the template it fills | prose reminders near the template |
| Behaviour should depend on a condition | conditional keyed to an **observable predicate** | unconditional rule plus exemption clauses |

Two measured corollaries, both counter-intuitive enough to be worth stating as rules:

- **No nuance clauses.** *"Appending a single nuance clause to a winning recipe degraded it from
  consistent to noisy."* Express a real exception as its own conditional on an observable
  predicate.
- **Exemption clauses do not scope.** *"'This limit doesn't apply to code blocks' still suppresses
  code blocks."* If part of the output must be exempt, restructure so the rule cannot reach it.

And the underlying reason prohibitions fail on shaping problems, which is the sentence to remember:
under a competing incentive, agents **negotiate** with "don't X"; *"a recipe leaves nothing to
negotiate."*

## 6. The eval case file, and its CI status

One case file per catalog entry, colocated with the entry's catalog source so a reviewer sees the
entry and its evidence in one diff.

Contents:

- `invocation`: `model` or `user` (§3.5).
- `trigger.positive[]`: ≥3 realistic paraphrased prompts with `top_k`.
- `trigger.negative[]`: ≥2 prompts owned by a named other entry.
- `evals[]`: ≥1 behavioural case with `prompt`, `expectations[]` (mechanism), **≥1 hidden check
  (outcome)**, and fixtures for execution cases.
- `form`: which row of §5.1 the guidance targets, and the micro-test result that justified it.

**Decided: a catalog entry without a case file is a Tier-1 failure**, on the same footing as
malformed frontmatter. Missing case files, incomplete counts, unknown kinds, and invalid fixture
paths are all CI errors. The reason to make this structural rather than cultural is §5.1's own
table: an omitted required element is fixed by a REQUIRED slot, never by a reminder.

Consequence to accept deliberately: **this raises the cost of adding a catalog entry.** That is the
intended effect. Our catalog's failure mode is accretion — sediment, in the review's vocabulary —
and a per-entry evidence requirement is a brake on exactly that. It also gives `catalog new
skill` something concrete to scaffold.

## 7. What this measures that nothing else does

Stated plainly because it is the competitive claim, and it should be defensible or dropped:

- `agent-skills` has routing + behavioural evals but **no control arms** — so a Tier-3 pass there
  cannot distinguish "the skill worked" from "any guidance would have".
- `ponytail` has rigorous arms and a safety tier for **one skill**, hand-built, not a catalog
  gate.
- `superpowers` has the best wording methodology but **no catalog-level routing check**.
- Nobody has **deterministic enforcement of the whole thing at commit time**, which is the one
  thing we are already unusually good at.

The combination — Tier 1 and 2 as free deterministic commit gates, Tier 3 with controls and a
safety gate, micro-tests as the authoring discipline, all enforced by hooks rather than
convention — is not assembled anywhere in the field. That, not the individual mechanisms, is what
this document is for.

## 8. Preconditions before implementation

This document is deliberately not a build plan. Three things gate it:

1. **The invocation axis must be declared in catalog sources first** (§3.5). Tier 2 is not
   well-posed without it, and the reclassification is itself the cheapest win available.
2. **A baseline must be measured before a floor is chosen** (§3.2). Picking a rank-1 floor from
   intuition would produce either a vacuous gate or an immediately red one.
3. **`basicly-7bur`'s cost-per-landed-package baseline** should exist before Tier 3 runs at scale,
   so the eval's own token cost is accounted in the same unit as everything else it informs. The
   hard constraint from `harness-eval.md` carries over unchanged: **the eval must not cost more
   than the thing it measures** — cheap models on the arms, the strong model only for judging.

## 9. Non-goals

- **Not a general prompt-optimisation framework.** We measure the entries we ship; we do not
  search for better ones automatically. Automatic harness evolution is a live research direction
  (review §4 references it) and it collides with R9 — the catalog is not agent-writable, so an
  optimiser that rewrites fragments is out of scope by decision, not by capability.
- **Not semantic routing.** Tier 2 stays lexical and deterministic (§3.3).
- **Not a replacement for `harness-eval.md`.** Whole-harness lift and per-entry lift are different
  questions and should share plumbing, not scope.
- **Not a benchmark for publication.** The audience is our own CI. If a number ever goes in a
  README it inherits ponytail's disclosure standard, including publishing the bugs.
