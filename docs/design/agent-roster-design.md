# Specialist Agent Roster — Named Roles Inside the Factory

Status: **agreed design — reviewed 2026-07-25 (§7), amended 2026-07-26 (§9). No implementation
until `basicly-kjc5` (the parallel factory) is complete.**

**Authority changed 2026-08-08 (owner).** This paragraph used to make
`docs/design/factory-design.md` the tiebreaker over everything below. It no longer is — see
[`gates-and-rework-design.md`](gates-and-rework-design.md) §Status for the measured reasons. The
order is now **measured evidence, then
[`factory-loop-requirements.md`](factory-loop-requirements.md), then `factory-design.md`**, and a
factory-design decision no measurement contradicts still stands.

**Two clauses in this document are now under review rather than binding**, because their premises
were measured false on 2026-08-08 (`basicly-xjd2`, `basicly-4kdm`): §R7's rule that role prompts
"cannot be Claude subagent files — the factory is agent-agnostic and dispatches headless Claude,
Codex, and Copilot alike" rests on an agnosticism that 338 run records do not show (claude 156,
manual 182, **codex and copilot 0**); and §1's "every dispatch pays fresh context priming
(deliberate, per factory D6)" is contradicted in cost by the same ledger, where a corpus-handed
`decide` dispatch costs 254x fewer tokens than a `lane` told to go and read. Neither clause is
deleted here — each is the subject of its own bead, and this note exists so a reader does not
build against a premise that is already being tested. Tracking bead: `basicly-eqp6`.

**§9 carries the amendments from the state-of-the-art review**
([`research/2026-07-26-sota-review.md`](../research/2026-07-26-sota-review.md)). One of them reopens
a decision this document closed — the Scout (§4) was cut on reasoning that turns out to apply
only to a *model-based* scout — and one supplies the persona hardening R8 was reaching for.
Sections 1–8 are unchanged; where §9 amends one, the amendment says so.

## 1. What a roster is for

The factory dispatches agents as pure functions today: one generic prompt shape for every
lane, one model for every job. A roster replaces that with **named roles**, each carrying
its own instructions, tool policy, model tier, and output contract. The engine then routes
each judgment step to the role built for it.

The three objectives, in the order they conflict:

- **Quality** — a narrow contract beats a broad one. An agent told "you are reviewing this
  diff for security weaknesses at trust boundaries, read-only, report findings with
  evidence" outperforms the same model told "review this change", because the second prompt
  makes the model choose its own success criterion.
- **Cost** — routing is the lever, not headcount, and the unit of cost is **total tokens,
  wall-clock, and human interventions per landed, correct package** — never the price of a
  single dispatch (R5). The savings come from paying *no* model where deterministic code
  suffices, and from routing every judgment to the cheapest tier that can be *relied on*
  for it — which, for consequential outputs, is an expensive tier that one-shots rather
  than a cheap one that reworks.
- **Speed** — every persona is a dispatch, and every dispatch pays fresh context priming
  (deliberate, per factory D6). More roles is more latency unless the roles run
  concurrently or shrink the work of a slower role downstream.

A roster that adds roles without changing the model tier or the tool policy adds cost and
latency for the illusion of specialization. R3 is the admission test that keeps that from
happening.

## 2. Decisions

### R1 — The Conductor is code, and it is the one component with no name

The supervisor keeps the name **Conductor** as a component, and deliberately gets **no
human name and no persona**, because it is not an agent. Factory D1/D2 make it a
deterministic event loop that owns dispatch, the concurrency cap, and the merge queue; §4
of the factory design explicitly rejects an LLM orchestrator ("loses reproducible
scheduling, `br`-based resume, enforcement-by-construction, and agent-agnosticism") and an
uber-orchestrator that "cannot outlive its own context window".

This is the one place the requested shape needs pushing back on. **No agent spawns
agents.** The Conductor dispatches every persona; personas never dispatch each other. That
is what keeps depth-1 write parallelism (D7) true, keeps the global process budget
accountable, and keeps a crashed run recoverable by derivation from `br` rather than replay
of an agent's intentions.

The delegation model the request describes is real and already the design — it is just
deterministic. Naming the scheduler like a colleague would invite exactly the mistake D2
exists to prevent: treating the thing that enforces the rules as something that can be
persuaded. Leaving it unnamed makes the boundary legible: **if it has a name, it is an
agent, and its output is a proposal the engine must validate.**

### R2 — A persona is a dispatch contract, not a model and not a personality

A persona is five fields:

| Field | Meaning |
| --- | --- |
| role prompt | the projected instructions defining the job and its success criterion |
| tool policy | the allow/deny overlay at invocation (read-only vs write, `br` access) |
| model tier | low / medium / high / maximum (R5), resolved to a concrete model by config |
| gate authority | what it may record — see R4 |
| output contract | the structured artifact the engine validates (plan, commit, verdicts) |

Consequences: two personas may share one model; one persona may be re-pinned to another
model by config without touching its prompt; and **names are display-only**. No policy,
gate, or scheduling decision may key on a persona name — the engine keys on the role id.

### R3 — Admission test: three conditions, all required

A proposed role becomes a persona only if it clears all three:

1. **Judgment** — the work is genuinely non-deterministic. If code can decide it, code
   decides it.
2. **A distinct, checkable success criterion** — someone reading the output can say whether
   the role did its job, without reading the rest of the session.
3. **A materially different tool policy or model tier than its neighbours** — otherwise it
   is a prompt section of an existing persona, not a separate dispatch.

Fail any one and the work becomes either a capability of an existing persona or a
deterministic engine step. This test is what stops the roster growing to fifteen roles that
are all the same model reading the same files.

### R4 — Gate authority is bounded: no persona can pass a required gate

Factory D4 splits deterministic **verify** from acceptance **validate**, and the existing
rule is that AI-semantic verification reports a *non-required* gate. The roster does not
change that: **no persona records a required gate pass.** A green required gate is always
the output of deterministic checks.

There is a real tension here that this design must resolve rather than inherit. D4 promotes
the `rubric` gate to **required** at lane and session level, but a rubric's judged checks
cannot fail a gate today — so a lane's validate gate is only as strong as its deterministic
checks, and with the task/chore rubrics still unwritten (`basicly-kjc5.19`) it currently
passes having checked nothing.

**Decided: a judged NO routes a decision-queue item**, and D4 was amended on 2026-07-25 to
provide the disposition path, so this is a standing decision rather than a proposal the factory
design does not know about. The lane does not land, and a human —
or the Decider under an L2+ grant — disposes of it. That preserves
engine-disposes/agent-proposes, makes a disputed verdict visible and attributable, and costs
one decision instead of one wasted rework cycle. An unsatisfied acceptance criterion is a
decision, not a test failure.

Rejected alternatives:

- **Judged NO fails the lane** once two or more independent judged verdicts agree. Strongest,
  but it hands a model the power to block, and a false NO burns a rework attempt.
- **Keep judged advisory**, with the teeth in deterministic rubric checks only. Cheapest and
  weakest; validate stays a formality for anything not mechanically checkable.

### R5 — Four model tiers, routed by reliability and priced per landed package

Providers ship three-to-four capability classes, so the roster uses **four tiers — low,
medium, high, maximum** — as the portable abstraction; the concrete model behind each tier
is config (R2). The Claude family is the worked example, cheapest to most expensive: haiku,
sonnet, opus, fable. Notably **opus, not sonnet, is the actual workhorse**, and **fable is
the designer / architect / thinker**; sonnet covers daily tasks and haiku covers simple
mechanical work. The GPT classes are luna, terra, sol — luna cheapest, sol the most
powerful — and a three-class ladder simply resolves high and maximum to the same top class:

| Tier | What it carries | Claude | GPT class |
| --- | --- | --- | --- |
| low | bounded, mechanically verifiable micro-work: a commit message, a tool call, finding a named thing in files | haiku | luna |
| medium | daily tasks whose output is cheap to check and cheap to redo | sonnet | terra |
| high | the workhorse: implementation, and judgments whose output is expensive to be wrong | opus | sol |
| maximum | design, architecture, decomposition — the thinking everything downstream inherits | fable | sol |

The routing rule is **not** "cheapest sufficient model". That framing prices a single
dispatch, and a dispatch is the wrong unit: the unit of cost is **total tokens — plus
wall-clock and human interventions — per landed, correct package**. Priced in that unit,
the economics invert:

- A cheaper model is a weaker model, and its mistakes are not free. They come back as
  rework dispatches, bug-fix beads, extra reviewer and validator cycles, bounced merges,
  and human attention — all charged to the same package. The cheap dispatch routinely
  produces the expensive package, in tokens and in wall-clock alike.
- A frontier model one-shots the same work and needs barely any rework — *provided the
  design, the requirements, and the implementation plan are clear*. That proviso is
  exactly what the factory supplies: deterministic gates, bounded rework, structured
  acceptance criteria, scope declarations, and the loop's phase discipline. Inside the
  harness a frontier model makes close to zero mistakes; the harness turns its one-shot
  rate from a good day into a property the budget can rely on.
- So the rule is: **route each role to the cheapest tier that can be relied on for that
  role.** For any role whose output is expensive to be wrong — implementation,
  decomposition, acceptance judgment, architecture — the reliable tier *is* the expensive
  one, and it is the cheaper choice in the only metric that matters.

The low tier keeps the work that genuinely belongs to it: bounded, mechanically
verifiable, hard-to-get-wrong steps — writing a commit message, tool-call glue, finding a
named thing in files. The qualifying property is that a wrong answer is detected
mechanically and redone cheaply; work whose errors propagate silently never qualifies,
however simple it looks. No roster persona runs at the low tier — work that bounded is
either a deterministic engine step already or a micro-step inside another persona's
dispatch, not a role.

Two structural corollaries. First, the single biggest cost win is still **not paying a
model at all** for merge, verify, and release mechanics (§4). Second, the next biggest is
shrinking the implementer's input — but that is Dana's scope declarations and the engine's
deterministic bundle assembly (D6/D8) doing the shrinking, not a cheap pre-reading persona
(see the Scout, §4).

### R6 — Review is one persona with lenses, not one reviewer of everything

Pushing back on "reviewer specialized in all reviews (code, design, architecture)": one
prompt spanning three abstraction levels regresses to generic advice, because the model has
to pick which level to work at and will pick inconsistently. Instead one **Reviewer** role
parameterised by **lens**, each lens a separate dispatch with its own tier, run at the level
where its artifact exists:

- `correctness` — high tier, at lane integrate, over the lane's diff.
- `security` — high tier, at lane integrate, only when the diff touches a trust boundary
  (input handling, credentials, subprocess, network) — otherwise skipped, deterministically.
- `architecture` — maximum tier, at session level over the landed set, not per lane.
  Reviewing architecture inside a lane is reviewing a keyhole.

**Decided: the lenses run on every lane**, not above a diff-size or risk threshold. The
per-lens skip conditions above are the only gating, and they are deterministic — the
`security` lens is skipped by inspecting the diff for trust-boundary paths, not by asking a
model whether the change looks risky. A tuned diff-size or risk threshold is cheaper but
buys that saving exactly where it is least affordable: a reviewer's failure mode is the miss,
and a small diff is not a safe diff. If measurement (§6) shows a lens not paying for itself,
it gets cut wholesale rather than sampled.

No lens runs below the high tier: a reviewer's failure mode is the miss, not the false
alarm, and an advisory green from a weak reviewer confers confidence no one earned (R5).
Diverse lenses find failure modes that redundant reviewers cannot. All lens output is
advisory (non-required gate) per R4; findings become `br` comments and, where they imply a
missed coupling, a found-info record (factory §7.4).

### R7 — Personas ship as catalog YAML; the mapping is overridable config

The factory's dual-use constraint applies unchanged: engine behavior as CLI code, guidance
as catalog YAML projected per agent family, enforcement as managed hooks, nothing
basicly-specific hardcoded.

- Role prompts are **catalog sources**, authored per `catalog-authoring` and projected for
  every agent family. They cannot be Claude subagent files — the factory is agent-agnostic
  and dispatches headless Claude, Codex, and Copilot alike.
- Role → runner / model / tool-policy mapping lives in a new overridable `[runner.roles]`
  section with sane defaults, so a consumer with no overlay gets a working roster and a
  consumer who re-pins a role survives `basicly install`.
- Persona identity flows into **`br` attribution** — `--agent-name <role>`, `--model`,
  `--harness` are already plumbed by factory D3. That makes every comment, plan, and verdict
  attributable to a role and a model, which in turn makes per-persona telemetry (component
  1) and per-persona cost-per-landed-package (`basicly-7bur`) fall out for free.

### R8 — Quirks are operational contracts, not flavour text

"Expertise, quirks and identity" earns its keep only where it changes output. A quirk must
be a rule an observer could see being followed or broken:

- Vera refuses to credit an acceptance criterion the diff does not evidence, and never
  infers intent that the criterion does not state.
- Remo attaches `path:line` evidence to every finding, and never proposes the rewrite.
- Kai commits before they report, always referencing their bead.
- Lumi never proposes "be more careful" — every retro proposal is a concrete diff to a named
  fragment, skill, or hook, or it is not filed.

Personality that does not change behavior is tokens with no return, and anthropomorphism
has one specific failure mode worth naming: trusting a persona's judgment over a gate. The
roster is a routing device, not a team of colleagues with standing.

Prose rule: personas are referred to as **they/them** everywhere, in projected prose and in
engine output.

### R9 — The retro is a persona, but the harness's own guidance is never agent-writable

Turning a rejection into a concrete fragment/skill change is the highest-leverage work for
the long-run quality of every lane, and it clears R3: the input (a rejection plus its cause)
is bounded, the output (a diff to a named catalog source) is checkable, and it is judgment no
gate can derive. So it earns a persona — **Lumi**, the Retrospector, at the maximum tier,
read-only over the session record.

**Decided: Lumi proposes, a human disposes — always.** The proposal is enqueued as a decision
item whose disposition is **human-only at every grant level, including L3**. This is the one
decision class the Decider may never take, and the reason is structural rather than cautious:
the catalog is the layer that constrains every future dispatch, so an agent that can amend it
under an autonomy grant can widen its own constraints, and the next session inherits the
widening as ground truth. A wrong implementation is caught by a gate and bounces; a wrong
fragment is *absorbed* — it silently degrades every lane afterwards, and nothing mechanical
detects it. The asymmetry, not the risk of a bad suggestion, is what keeps the human in this
loop.

Two consequences. The engine needs a decision class that the grant ladder cannot escalate —
an exception to the L0-L3 progression rather than a rung in it (`basicly-kjc5.3`'s ledger has
to express "never auto-dispose" for this class). And Lumi's output must be a diff against
catalog YAML, not prose advice, so the existing projection gates (`basicly catalog lint`,
`check`, `skills-check`, `agents-check`) mechanically bound what a human is being asked to
approve.

## 3. The roster

Six personas, plus one that lands with release automation. Every one clears R3.

| Name | Role | Runs at | Tier | Tools | Output contract |
| --- | --- | --- | --- | --- | --- |
| **Dana** | Decomposer | session, and lane sub-task split | maximum | read-only + `br` propose | a child plan: beads, scope globs, acceptance criteria |
| **Kai** | Implementer | lane sub-task | high | full write inside the lane worktree | a committed change on the harness branch, tests included |
| **Vera** | Validator | lane integrate, session ship | high | read-only | judged rubric verdicts against the acceptance criteria, with evidence |
| **Remo** | Reviewer (lens: correctness \| security \| architecture) | lane integrate, session | high / maximum by lens | read-only | advisory findings with `path:line` evidence |
| **Juno** | Decider | session, per decision item | high | read-only, corpus-bounded | `{decision, rationale, confidence, abstain}` |
| **Lumi** | Retrospector | session close, after a rejection | maximum | read-only | a proposed diff to a named catalog source, as a human-only decision item (R9) |
| **Tala** | Curator *(provisional — lands with `basicly-kjc5.12`)* | ship | medium | read + changelog write | curated release notes from the generated changelog |

Notes on the roster as proposed versus the request:

- **Juno already exists.** The Decider is factory §7.1, built in `basicly-kjc5.4`
  (`decisions.invoke_decider`) with corpus-bounded authority and an abstain path. The roster
  adopts and names it rather than inventing it. It was absent from the requested roster and
  is the highest-leverage persona in the system for *speed*, because it is the only one that
  removes human stops.
- **Kai was absent from the requested roster too**, and it is where nearly all tokens go.
  The Implementer runs at the high tier — the workhorse class — because implementation is
  the output most expensive to be wrong (R5); getting the contract right (one sub-task at
  a time, fresh context, commit before reporting, tests as part of the change) matters
  more to quality and cost than every other persona combined.
- **Dana runs at the maximum tier** because decomposition is the design work everything
  downstream inherits: a bad split poisons every lane under it, and a clear plan is the
  stated precondition for the high tier's one-shot rate (R5). The output is small in
  tokens and the most consequential in the system.
- **The Scout is cut.** An earlier draft carried a low-tier Scout (then named Sol) as the
  cost play; priced per landed package the play inverts — see §4.
- **Lumi is the one persona whose proposals a grant can never auto-approve** (R9). They are
  also the only persona that runs after the work has already landed, which is why the retro
  is worth a role at all: it is the only step whose output improves *future* lanes rather
  than the current one.
- **Tala is provisional** but likely justified: the release process has repeatedly needed the
  generated changelog reordered and curated by hand, which is judgment with a checkable
  output. They are also the only persona below the high tier: a curation error is visible
  on one read of the notes and cheap to redo, which is the medium-tier criterion (R5).
- **One Implementer, not one per task class.** Kai's prompt is conditioned on the bead's
  class (bug / task / chore), but the tool policy and tier do not differ by class, so R3
  condition 3 is not met for a split — a conditioned prompt is a parameter, not a role. Same
  reasoning as R6's lenses, applied in the opposite direction: Remo's lenses differ in tier
  and in the artifact they read, so they split; Kai's classes differ in neither, so they
  do not.

### Naming convention

Single given name, no surname; pronoun-neutral; **pronounceable by non-English speakers** —
short, open syllables, phonetically transparent. Four ways a name fails the bar:

1. it needs an **English-specific vowel** or a reading only English orthography teaches (a
   silent e);
2. it needs a **consonant cluster** that is hard outside English;
3. its **first letter is read differently across languages** in a way that changes the whole
   name, not just its accent;
4. it **collides** with a provider's model or class name.

Alliteration with the role id is a tiebreak, not a requirement — it applies only where the
name already clears the bar (`decomposer`→Dana, `validator`→Vera, `reviewer`→Remo). Names are
display-only (R2), so this slate is the cheapest thing in the design to change.

One operational rule beyond phonetics: **every name starts with a distinct letter** (D, K, V,
R, J, L, T). Role attribution is read in logs, `br` comments, and decision items, where a
one-letter prefix is often all the eye takes in.

How the slate got here, applying the bar to earlier drafts:

- **Dex** → **Dana** (rule 2: closed syllable ending in a /ks/ cluster).
- **Rune** → **Remo** (rule 1: the reading depends on the English silent e — everywhere else
  it reads as two syllables).
- **Sol** → cut with the scout role (§4), and untenable regardless under rule 4: it now names
  a GPT model class.
- **Ivo** → **Kai** (rule 3: the initial I reads /i/ in most languages but /aɪ/ in English, so
  the name splits into "EE-vo" and "EYE-vo" — two different names in the same log). **Kai** is
  a single open syllable, /k/ and the /ai/ diphthong are near-universal, and it reads
  identically wherever it is spoken.
- **Cleo** → **Tala** (rule 3: an initial C before a vowel is read /k/, /s/ or /ts/ depending
  on the language — the /kl/ cluster was never the problem, the C was). **Tala** is two open
  syllables of pure /a/, built from two of the most widely shared consonants.
- **Lumi** (new, R9): two open syllables, pure vowels, /l/ and /m/ universal, and no reading
  ambiguity in any of the four rules.
- **Vera**, **Juno**, **Dana**, **Remo** stand. **Juno** passes with a note: its initial reads
  /j/ or /dʒ/ by language, but both readings are two open syllables anyone can produce and
  both name the same person — the bar is that everyone can say the name, not that everyone
  says it identically.

Rejected naming variants: model-suffixed identities (`Vera-7`) leak the model into the
identity, which R2 makes config; job-title names (`ReviewerBot`) give up the legibility
that made naming worth doing; and anything that trips rule 4 — a display name must never read
as a model claim.

## 4. Roles that must not be personas

Each of these was proposed; each is refused by a factory decision or by the cost model
(R5), not by preference.

- **Merge agent** — refused by **D5** and §4 of the factory design: "no merge-time AI
  resolution… A conflict means the decomposition's scope declarations missed a coupling —
  the graph was wrong, not the merge." A merge specialist resolves with neither lane's
  context at the point of weakest verification. The merge queue is code; a conflict bounces
  back to the owning lane, where **Kai** re-applies the intent with full context, bounded by
  the rework cap. The only sanctioned automation is *deterministic* resolution of mechanical
  classes (lockfiles, generated files) — never semantic ones.
- **Tester / verifier** — refused by **D4**: verify is deterministic gates (tests, lint,
  type, build). Making a model run the tests adds cost, latency, and nondeterminism to the
  one part of the system that is trustworthy precisely because it is mechanical. The real
  agent work nearby is *authoring* tests, which belongs to **Kai** as part of their change,
  and *diagnosing* a red gate — a triage capability worth adding to Kai's rework dispatch
  before it is worth a persona (R3 condition 3).
- **Scout** — a low-tier pre-reader that localises files and symbols for the implementer.
  Refused by the cost model (R5), not by a factory decision: an earlier draft of this
  roster carried it (as "Sol") justified as cheap tokens replacing expensive ones, which
  prices the dispatch instead of the landed package. The scout's output sits upstream of
  the most expensive dispatch, and its characteristic error — a slightly wrong or
  incomplete file list — is exactly what the low tier must never be handed: nothing
  mechanical detects the omission, and it silently narrows Kai's view until the damage
  surfaces as rework or a bounced merge, making the implementer's job harder rather than
  cheaper. The localisation it would provide is also already supplied: Dana declares scope
  globs per child bead (D8 sizes from them), the dispatch bundle is a deterministic
  function of `br` state (D6), and a high-tier implementer localises competently on their
  own inside the D8 working-set band. If telemetry ever shows Kai's self-localisation
  dominating lane spend, a scout can be reintroduced behind a config flag under D7's
  read-only-helper allowance and priced per landed package like everything else (§7,
  question 3). Its old name was untenable anyway: "Sol" now collides with a GPT
  model-class name, which R2's model-free identities cannot tolerate.
- **Shipper** — mostly deterministic: version bump, changelog generation, tag, push are a
  command (`basicly-kjc5.12`) gated behind an L3 grant with hard preconditions. The judged
  residue is changelog curation, which is **Tala**, not a shipper.
- **Conductor** — R1.

## 5. Mapping to the loop

| Loop phase | Deterministic engine work | Persona |
| --- | --- | --- |
| intake | record work type, run DoR | none — the human's interactive session is the client |
| classify | checkpoint + DoR gate | human at L0/L1; **Juno** at L2+ |
| decompose | sizing governor, scope-disjointness, plan validation, checkpoint | **Dana** proposes |
| build — sub-task | worktree binding, dispatch, commit-presence probe | **Kai** |
| build — sub-task verify | `fast` gates | none (D4) |
| build — lane integrate | `full` gates, rubric gate record | **Vera** validates; **Remo** reviews (correctness, security-when-relevant) |
| landing | merge queue, dependency order, bounce-back, coupling edges | none (D5); a bounce returns the work to **Kai** |
| verify / ship | gate inspection, checkpoint, teardown, close | human; **Juno** at L3 with preconditions green |
| release | version, changelog, tag | **Tala** curates |
| session close | retro prompt, rejection record | **Lumi** proposes; human disposes at every level (R9) |

Read the table as the answer to "which expert owns which part of the loop": the engine owns
every step where the right answer is derivable, and a persona owns every step where it is
not.

## 6. Cost and speed model

Per landed package, dispatch counts scale like: one Dana plan per lane, one Kai per
sub-task, one Vera plus one-to-two Remo lenses per lane, plus Juno per decision, plus at most
one Lumi per session — and only after a rejection. Kai therefore dominates token spend, and
the levers in priority order are:

1. **Never dispatch where code decides** (§4) — removes whole classes of spend.
2. **Keep every dispatch inside the D8 working-set band**, with tight scope declarations
   from Dana, so no dispatch approaches the context ceiling — a clear, right-sized bundle
   is the stated precondition for the reliable tier's one-shot rate (R5).
3. **Tier reliability discipline** (R5) — the tempting economy is down-tiering Kai or
   Vera, and it is a false one: a cheap dispatch that buys a rework cycle, an extra Remo
   pass, and a human intervention costs more per landed package than the high-tier
   dispatch it replaced. Down-tier only work whose errors are mechanically caught.
4. **Concurrency, not sequence** — Vera and the Remo lenses are read-only and independent,
   so they run concurrently at integrate and cost latency once, not three times.

None of these are worth asserting without measurement — and the corrected cost model is
exactly what makes measurement decisive. Component 1 telemetry records per-run tokens and
cost, R7 attribution (`br --agent-name`, `--model`) pins every run to a role and a model,
and `basicly-7bur` reports **cost per landed package** — the precise unit R5 argues in. So
**the roster's claims are falsifiable end to end**: if down-tiering a role genuinely lands
packages cheaper, the numbers will say so and the tier drops; if a lens does not pay for
itself, it gets cut. That evaluation, not this document, is what should decide the final
roster.

## 7. Design review — resolved

Reviewed 2026-07-25. Five questions were settled and are folded into the decisions above;
one is deferred to measurement rather than to opinion.

| Question | Resolution | Recorded in |
| --- | --- | --- |
| Judged-verdict authority | a judged NO routes a decision-queue item; it neither fails the lane nor stays advisory | R4 |
| Lens budget | lenses run on every lane; the only skips are deterministic per-lens conditions | R6 |
| A retro persona? | yes — Lumi proposes, a human disposes at every grant level | R9 |
| One Implementer or one per class? | one Kai, prompt conditioned on the bead's class | §3 |
| The name slate | Dana / **Kai** / Vera / Remo / Juno / **Lumi** / **Tala**; Ivo and Cleo re-cast under naming rule 3 | §3 naming convention |

**Deferred to measurement — scout reintroduction.** §4 cuts the scout on the cost model, not
on a factory decision, so the cut is falsifiable and the honest answer is a number this repo
does not have yet. The signal to watch is the share of a lane's tokens that Kai spends
*before* their first edit — reading and localising rather than changing. There is no defensible
threshold to write down today: it needs `basicly-7bur`'s cost-per-landed-package baseline
first, because the only comparison that decides the question is total package cost with and
without the scout, not the share itself. If it is reintroduced it goes behind a config flag
under D7's read-only-helper allowance, and its tier is an open sub-question — the cheap tier
is the whole point of the role and is also what makes its characteristic error undetectable
(§4). Revisit when 7bur has a baseline; until then the scout stays cut.

## 8. Implementation shape (not yet decomposed)

Sketch only; this document is design-only and no work starts until `basicly-kjc5` closes.
Roughly: a role registry in the engine (role id → prompt source, tier, tool policy, output
schema), `[runner.roles]` config with defaults, role-aware dispatch in the supervisor
(replacing the single generic dispatch prompt), catalog sources for each role prompt with
projection gates, tool-policy overlays at invocation (the confinement mechanism
`basicly-kjc5.16` builds for Juno generalises to every read-only role), per-role attribution
plumbed into `br`, and eval cases per role prompt under `basicly-4t9z` as the regression
gate for the prose layer.

Two pieces do not fall out of that shape and need their own beads. R4's disposition path is a
new producer for the decision queue (`basicly-kjc5.4`) — a judged NO has to enqueue an item
carrying the failing criterion and Vera's evidence, and the lane has to hold rather than land
or bounce. R9 needs the grant ledger (`basicly-kjc5.3`) to express a decision class that no
level auto-disposes, which is an exception to the L0-L3 progression rather than a rung in it;
without it, an L3 grant would quietly hand the catalog to the Decider.

## 9. Amendments from the 2026-07-26 state-of-the-art review

Eleven comparable projects were read at pinned revisions
([`research/references.md`](../research/references.md)). Four of them run named agent rosters, so
this document's decisions now have external comparanda rather than only internal reasoning.
Findings are grouped by whether they **confirm**, **amend**, or **reopen** a decision above.

### 9.1 Confirmed by independent convergence

Three of this document's more contested decisions were reached independently elsewhere, which is
the strongest evidence available short of measurement:

- **R1 — the Conductor is code, and no agent spawns agents.** `addyosmani/agent-skills`'
  orchestration catalog states the identical governing rule (*"the user or a slash command is the
  orchestrator; personas do not invoke other personas"*) and lists persona-calls-persona, router
  personas, deep persona trees, and **the paraphrasing sequential orchestrator** as its four
  anti-patterns. Its argument against an LLM lifecycle orchestrator is ours nearly verbatim: it
  loses nuance to hand-off summarisation, skips the human checkpoints that catch wrong-direction
  work early, and doubles token cost. `gsd-core` — the largest roster in the set at 34 agents —
  also keeps a thin orchestrator that *"never touches source files."* On Claude Code the rule is
  enforced by construction; subagents cannot spawn subagents.
- **R3's admission test is vindicated by the counter-example.** `gsd-core`'s 34 agents are mostly
  researchers and checkers that R3 would fold into an existing persona or replace with
  deterministic code. Their roster is the outcome R3 exists to prevent, and it is worth noting
  they pay for it in a per-runtime agent registry, per-agent model resolution, and an INVENTORY
  document to keep track.
- **Read-only reviewers with a separate fixer** is the field's settled permissions pattern —
  `gsd-core`'s auditor states it positively (*"implementation files are READ-ONLY … implementation
  bugs → ESCALATE. Never fix implementation"*), and the adversarial-review literature frames it as
  "the maker shouldn't grade the checker."

### 9.2 R8 amended — quirks become an adversarial stance and a soft-list

**R8 as written is the right instinct aimed at the wrong target.** "Vera refuses to credit an
acceptance criterion the diff does not evidence" is a good rule; as a *quirk* attached to an
identity it is doing less work than it could.

`gsd-core` demonstrates the effective form. Each judged agent opens with an explicit **FORCE
stance** — *"Assume every plan set is flawed until evidence proves otherwise. Your starting
hypothesis: these plans will not deliver the phase goal"* — followed by an enumerated list of
**how this specific role goes soft**. Theirs, for a plan checker, includes: accepting a plausible
task list without tracing each task to a requirement; crediting a decision reference without
verifying the task delivers its full scope; treating scope reduction (*"v1"*, *"static for now"*)
as acceptable; letting dimensions that pass anchor judgment (*"a plan can pass 6 of 7 dimensions
and still fail the phase goal on the 7th"*); and **issuing warnings for what are actually blockers
to avoid conflict with the producer.**

That last item is the one R8 cannot express: it names reviewer conflict-avoidance as a *predicted*
failure of the role and pre-empts it by name.

**Amended:** every judged persona's projected prompt carries (a) an explicit adversarial stance and
(b) a **role-specific** soft-list. Generic rigour instructions stay out — "be thorough" is a
**no-op** in the review's vocabulary, since the model is already somewhat thorough, so the line
costs tokens and changes nothing. A soft-list derived from *observed* failures is not generic and
therefore is not a no-op.

**The lists must be derived, not invented.** Our loop already records verdicts, rework rounds, and
adjudications; that history is the raw material, and mining it is the highest-value thing Lumi
could be pointed at (§9.7).

R8's prose rule stands unchanged: personas are referred to as **they/them** everywhere.

### 9.3 R5 amended — the predicate is specification completeness, not work category

R5 argues that for outputs expensive to be wrong, the reliable tier *is* the expensive one, priced
per landed package. `superpowers` argues the opposite headline — *"use the least powerful model
that can handle each role"* — and routes mechanical implementation cheap.

The two reconcile, and the reconciling sentence is theirs: **"Turn count beats token price.
Wall-clock and context cost scale with how many turns a subagent takes, and the cheapest models
routinely take 2-3× the turns on multi-step work — costing more overall. Use a mid-tier model as
the floor for reviewers and for implementers working from prose descriptions."** That is R5's
argument, priced in turns rather than rework cycles — a second independent derivation of the same
conclusion.

Their genuine addition is a **predicate for when cheap is actually safe**: *"when the task's plan
text contains the complete code to write, the implementation is transcription plus testing."*

**Amended:** the reliable tier is a function of **specification completeness**, not of the work's
nominal category. R5's claim that a cheap dispatch against a prose description is a false economy
stands. Its implicit corollary — that implementation is *always* high-tier — does not: a dispatch
whose brief contains the literal code and the literal test cases is transcription, and
transcription is mechanically verifiable, which is R5's own qualifying property for the low tier.

Two operational notes, both adopted:

- **Always set the model explicitly on every dispatch.** *"An omitted model inherits your session's
  model — often the most capable and most expensive — which silently defeats this section."* For
  us the dispatch is assembled by code, so this is enforceable: **a dispatch with no resolved tier
  is a bug, not a default.**
- **Late rework rounds bump the tier** rather than retrying at the same one — see
  [`gates-and-rework-design.md`](gates-and-rework-design.md) §3.1. This also produces a signal
  worth reading: if late-round bumps routinely succeed, the *initial* tier was wrong, and
  `basicly-7bur` can see that directly.

§6's falsifiability claim is unchanged and now has a sharper variable: label dispatches by
specification completeness, and the tier question becomes measurable rather than arguable.

### 9.4 §4 reopened — the Scout is cut, but the *work* comes back as engine code

**This is the review's most consequential single finding for this document.**

§4 cut the Scout on the cost model: a cheap pre-reader's characteristic error — a slightly wrong or
incomplete file list — is *"exactly what the low tier must never be handed: nothing mechanical
detects the omission, and it silently narrows Kai's view."* That reasoning is sound and it is
**entirely about a model**.

`Graphify-Labs/graphify` produces the same artifact **deterministically**: tree-sitter AST
extraction, no LLM, no tokens, with every edge labelled `EXTRACTED` (explicit in source),
`INFERRED` (resolved by a second pass), or `AMBIGUOUS` (flagged for review). A parser does not
hallucinate a call site, and its coverage is a checkable property of the parser rather than a
judgment nobody can audit — which dissolves the exact objection §4 raised.

So the localisation work moves from *"a dispatch we refused"* to *"an engine step we never
considered"*, which is precisely where R5's first corollary says the largest wins live: **not
paying a model at all.** It is also the one place `gsd-core`'s larger roster genuinely beats ours —
their codebase-mapper runs four parallel sub-probes to build a map — except the deterministic
version needs no agents.

**Amended:**

- **The Scout stays cut as a persona**, permanently. §4's reasoning against a *model* pre-reader is
  correct and is not weakened.
- **A deterministic localisation artifact is a new candidate engine step**, not a role. It has no
  tier, no prompt, and no gate authority, so R3 does not apply to it.
- **It changes what Dana's scope declarations must carry.** If the engine can derive a call graph,
  Dana declares *intent and boundaries* and the engine derives *reachable surface* — which is a
  cleaner split than asking a maximum-tier persona to enumerate files by hand.
- **It should be measured before the roster is implemented**, because it changes Dana's output
  contract. The signal is §7's deferred one: the share of a lane's tokens Kai spends before their
  first edit. This is now a cheap experiment rather than a blocked one.

§7's deferred scout question is **closed as asked** — the tier sub-question is moot, because the
answer is no model at all.

### 9.5 R6 amended — no reranking across lenses, and a trend instrument

Two additions.

**No reranking.** `mattpocock/skills` runs review as two axes — Standards and Spec — in parallel
subagents, reports them **separately and un-reranked**, and states why: *"a change can pass one
axis and fail the other"*, so merging them lets one mask the other. Their instruction is explicit:
end with the worst issue *within each axis*, and *"don't pick a single winner across axes — that's
the reranking the separation exists to prevent."*

R6 already splits lenses by tier and artifact but says nothing about aggregation. **Amended:** lens
output is reported per lens, never merged into one ranked list. This also usefully clarifies a
structural overlap worth naming: their **Spec** axis is our **Vera**, and their **Standards** axis
is our **Remo/correctness**. Two projects arriving at the same two-axis split independently is
evidence the split is real.

**A trend instrument.** R6 promises *"if measurement shows a lens not paying for itself, it gets
cut wholesale"*, and §6 promises the roster's claims are falsifiable — but nothing currently
produces the trend. `lattice` appends every review to a rolling log (scope, atoms applied, findings
by severity, key findings) precisely to answer *which checks catch the most issues, whether
anti-patterns recur, whether findings per review decline.*

**Amended:** R6's cut criterion requires a per-lens record over a window — finding rate and
adjudication-outcome distribution — which also yields the two degenerate-reviewer detectors
(rubber stamp, noise generator) in [`gates-and-rework-design.md`](gates-and-rework-design.md) §5.6.
Without it R6's promise is unexecutable, which is worse than not making it.

### 9.6 Kai's contract gains a self-check and a report file

Two changes to the Implementer's output contract, neither affecting tier or tool policy, so R3 is
not engaged.

**A scoped self-check before reporting done.** `lattice`'s two-pass model — *"asking AI to generate
and validate simultaneously is unreliable … the creative task and the analytical task compete for
attention, and one always suffers"* — argues for generate → STOP → verify → present *within* one
dispatch. We already have the stronger cross-dispatch version (Kai then Vera), and it stays:
*"implementer self-review never replaces the task review; both are needed."* The within-dispatch
pass is nearly free and its gain is **cheaper rework**, since a defect Kai catches never consumes a
review round. Two constraints keep it from becoming theatre: it never substitutes for independent
review, and its checklist is **the bead's own acceptance criteria**, not a generic quality list.
Flagged honestly: lattice's superiority claim is asserted, not measured — micro-test it under
[`catalog-efficacy-design.md`](catalog-efficacy-design.md) §5 before it becomes a contract.

**A report file, not a printed report.** `superpowers`: *"everything you paste into a dispatch
prompt — and everything a subagent prints back — stays resident in your context for the rest of the
session and is re-read on every later turn"*, with a measured failure of a dispatch reaching 42k
chars of which 99% was pasted history. Kai writes the full report to a file and returns only
status, commits, a one-line test summary, and concerns. This also makes §9.3's tier-bump work
across runners that cannot resume a live subagent: the report file *is* the persistent memory the
fresh implementer reads.

Their four-status contract is worth adopting wholesale because each status has a different correct
response: **DONE** · **DONE_WITH_CONCERNS** · **NEEDS_CONTEXT** · **BLOCKED**. And the rule that
gives it teeth: *"never ignore an escalation or force the same model to retry without changes. If
the implementer said it's stuck, something needs to change."*

### 9.7 Lumi gains a source, and R9 gains a middle rung

**A source.** R9 establishes that Lumi proposes catalog diffs and a human always disposes, and §3
notes Lumi is the only persona whose output improves *future* lanes. What R9 never specifies is
**what Lumi reads**. §9.2 answers it: the verdict, rework, and adjudication history the loop
already records is the raw material, and the highest-value output is a **role-specific soft-list
entry derived from an observed failure** — a concrete, checkable line, which is exactly the shape
R9 already demands ("a diff to a named catalog source, not prose advice").

**A middle rung.** `headroom`'s `learn` mines failed sessions and writes corrections to a
**gitignored, machine-local** file by default rather than the shared instruction file. That is a
rung R9 does not have between "drop the retro" and "ask a human to amend the shared catalog": a
local, unshared lane for a proposal that has not yet earned team-wide authority.

**Amended, cautiously.** The asymmetry argument behind R9 is untouched — a wrong implementation
bounces off a gate, a wrong fragment is absorbed and silently degrades every later lane — so
**nothing agent-authored reaches the shared catalog without a human, at any grant level.** But a
machine-local scratch lane is not the shared catalog, and it lets a proposal accumulate evidence
before it costs a human a decision. The risk to watch is that it becomes a bypass by accretion:
guidance that shapes a machine's sessions while never being reviewed by anyone. Any such lane must
therefore be **visibly non-authoritative and expiring**, not a quiet second catalog.

### 9.8 Naming — one collision to re-check

§3's naming convention rule 4 forbids a name that *"collides with a provider's model or class
name."* The review surfaced no collision in the current slate (Dana / Kai / Vera / Remo / Juno /
Lumi / Tala) — but it did surface that the rule has already fired once, retiring "Sol" when a GPT
class took the name. **Rule 4 needs re-checking at implementation time, not just at design time**,
because the namespace it guards against is owned by other people and changes without notice. Names
are display-only (R2), so the fix stays cheap; the point is to check rather than assume.

### 9.9 What the review did not change

- **R2, R4, R7** stand as written. R4's disposition path is strengthened, not altered, by
  [`gates-and-rework-design.md`](gates-and-rework-design.md) §4.1, which argues the validate step
  is a *composite* of a deterministic pre-flight gate (which can fail the lane) and a judged
  escalation gate (which enqueues a decision) — giving the required gate real teeth without any
  persona passing it.
- **R9's human-only rule** is explicitly reaffirmed against `lattice`'s user-confirmed learnings
  flywheel, which is weaker.
- **The refusal of agent-to-agent messaging.** `agent-skills` documents a real capability we
  forgo — teammates challenging each other's hypotheses converge on a root cause better than
  independent reporters do. We decline it because it costs reproducible scheduling and resumability
  (D1/D2), which we will not trade. Recorded as a known limitation, not a gap to close.
- **Seven personas.** Nothing in the review argues for an eighth. §9.4 moves work *out* of the
  roster and into the engine, which is the direction R3 was written to encourage.
