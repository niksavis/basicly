# Specialist Agent Roster — Named Roles Inside the Factory

Status: **proposed design, open for revision. No implementation until `basicly-kjc5` (the
parallel factory) is complete.** `docs/factory-design.md` is the authoritative factory
design and constrains everything below; where this document and the factory design appear
to disagree, the factory design wins until it is amended. Tracking bead: `basicly-eqp6`.

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
- **Cost** — routing is the lever, not headcount. Most spend is implementation tokens; the
  savings come from paying a cheap model for bounded mechanical work, paying an expensive
  one only where a wrong answer is costly and the token volume is small, and paying *no*
  model where deterministic code suffices.
- **Speed** — every persona is a dispatch, and every dispatch pays fresh context priming
  (deliberate, per factory D6). More roles is more latency unless the roles run
  concurrently or shrink the work of a slower role downstream.

A roster that adds roles without changing the model tier or the tool policy adds cost and
latency for the illusion of specialization. §3 is the admission test that keeps that from
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
| model tier | cheap / mid / high, resolved to a concrete model by config |
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
passes having checked nothing. Three options:

- **(a) Judged NO fails the lane** once two or more independent judged verdicts agree.
  Strongest, but hands a model the power to block, and a false NO burns a rework attempt.
- **(b) Judged NO routes a decision-queue item** (recommended). The lane does not land, but
  a human — or the Decider under an L2+ grant — disposes of it. Preserves
  engine-disposes/agent-proposes, makes a disputed verdict visible and attributable, and
  costs one decision instead of one wasted rework cycle.
- **(c) Keep judged advisory** and put the teeth in deterministic rubric checks only.
  Cheapest and weakest; validate stays a formality for anything not mechanically checkable.

Recommended: **(b)**. An unsatisfied acceptance criterion is a decision, not a test failure.

### R5 — Model tier per persona is where "lowest cost" actually comes from

Assign the cheapest tier that clears the role's bar:

- **cheap** — bounded, verifiable, high-volume work: locating files, extracting a symbol
  list, classifying a failure. A wrong answer is cheap to detect and cheap to redo.
- **mid** — implementation and code-level review. Volume is high and quality matters, so
  this tier dominates spend; it is the tier to *shrink the input of*, not to upgrade.
- **high** — decisions that are small in tokens and expensive to get wrong: a decomposition
  plan (a bad split poisons every lane under it), an acceptance verdict, an architecture
  review, a delegated decision.

Two structural corollaries. First, the single biggest cost win is **not paying a model at
all** for merge, verify, and release mechanics (R6/§4). Second, the next biggest is
shrinking the implementer's input — which is what the Scout persona exists to do, and why
it must be measured rather than assumed (§7, open question 3).

### R6 — Review is one persona with lenses, not one reviewer of everything

Pushing back on "reviewer specialized in all reviews (code, design, architecture)": one
prompt spanning three abstraction levels regresses to generic advice, because the model has
to pick which level to work at and will pick inconsistently. Instead one **Reviewer** role
parameterised by **lens**, each lens a separate dispatch with its own tier, run at the level
where its artifact exists:

- `correctness` — mid tier, at lane integrate, over the lane's diff.
- `security` — mid tier, at lane integrate, only when the diff touches a trust boundary
  (input handling, credentials, subprocess, network) — otherwise skipped, deterministically.
- `architecture` — high tier, at session level over the landed set, not per lane. Reviewing
  architecture inside a lane is reviewing a keyhole.

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
- Sol returns paths and ranges, never an opinion about the design.
- Ivo commits before it reports, always referencing its bead.

Personality that does not change behavior is tokens with no return, and anthropomorphism
has one specific failure mode worth naming: trusting a persona's judgment over a gate. The
roster is a routing device, not a team of colleagues with standing.

Prose rule: personas are referred to as **they/them** everywhere, in projected prose and in
engine output.

## 3. The roster

Six personas, plus one that lands with release automation. Every one clears R3.

| Name | Role | Runs at | Tier | Tools | Output contract |
| --- | --- | --- | --- | --- | --- |
| **Dex** | Decomposer | session, and lane sub-task split | high | read-only + `br` propose | a child plan: beads, scope globs, acceptance criteria |
| **Sol** | Scout | lane, before each implementation dispatch | cheap | read-only, no write | the files, symbols and ranges that matter — nothing else |
| **Ivo** | Implementer | lane sub-task | mid | full write inside the lane worktree | a committed change on the harness branch, tests included |
| **Vera** | Validator | lane integrate, session ship | high | read-only | judged rubric verdicts against the acceptance criteria, with evidence |
| **Rune** | Reviewer (lens: correctness \| security \| architecture) | lane integrate, session | mid / high by lens | read-only | advisory findings with `path:line` evidence |
| **Juno** | Decider | session, per decision item | high | read-only, corpus-bounded | `{decision, rationale, confidence, abstain}` |
| **Cleo** | Curator *(provisional — lands with `basicly-kjc5.12`)* | ship | mid | read + changelog write | curated release notes from the generated changelog |

Notes on the roster as proposed versus the request:

- **Juno already exists.** The Decider is factory §7.1, built in `basicly-kjc5.4`
  (`decisions.invoke_decider`) with corpus-bounded authority and an abstain path. The roster
  adopts and names it rather than inventing it. It was absent from the requested roster and
  is the highest-leverage persona in the system for *speed*, because it is the only one that
  removes human stops.
- **Ivo was absent from the requested roster too**, and it is where nearly all tokens go.
  The Implementer is the workhorse; getting its contract right (one sub-task at a time,
  fresh context, commit before reporting, tests as part of the change) matters more to
  quality and cost than every other persona combined.
- **Sol is the cost play.** Cheap tokens replacing expensive ones: localise first, then hand
  the implementer a short list instead of a repository. Factory D7 already permits read-only
  helpers inside a lane, counted against the one global `max_agent_processes` budget.
- **Cleo is provisional** but likely justified: the release process has repeatedly needed the
  generated changelog reordered and curated by hand, which is judgment with a checkable
  output.

### Naming convention

Single given name, no surname; pronoun-neutral; alliterative with the role id
(`decomposer`→Dex, `scout`→Sol, `validator`→Vera, `reviewer`→Rune, `curator`→Cleo);
`decider`→Juno for *judge*, since D-names were taken. Names are display-only (R2), so this
slate is the cheapest thing in the design to change.

Rejected naming variants: model-suffixed identities (`Vera-7`) leak the model into the
identity, which R2 makes config; and job-title names (`ReviewerBot`) give up the legibility
that made naming worth doing.

## 4. Roles that must not be personas

Each of these was proposed; each is refused by a factory decision, not by preference.

- **Merge agent** — refused by **D5** and §4 of the factory design: "no merge-time AI
  resolution… A conflict means the decomposition's scope declarations missed a coupling —
  the graph was wrong, not the merge." A merge specialist resolves with neither lane's
  context at the point of weakest verification. The merge queue is code; a conflict bounces
  back to the owning lane, where **Ivo** re-applies the intent with full context, bounded by
  the rework cap. The only sanctioned automation is *deterministic* resolution of mechanical
  classes (lockfiles, generated files) — never semantic ones.
- **Tester / verifier** — refused by **D4**: verify is deterministic gates (tests, lint,
  type, build). Making a model run the tests adds cost, latency, and nondeterminism to the
  one part of the system that is trustworthy precisely because it is mechanical. The real
  agent work nearby is *authoring* tests, which belongs to **Ivo** as part of its change,
  and *diagnosing* a red gate — a triage capability worth adding to Ivo's rework dispatch
  before it is worth a persona (R3 condition 3).
- **Shipper** — mostly deterministic: version bump, changelog generation, tag, push are a
  command (`basicly-kjc5.12`) gated behind an L3 grant with hard preconditions. The judged
  residue is changelog curation, which is **Cleo**, not a shipper.
- **Conductor** — R1.

## 5. Mapping to the loop

| Loop phase | Deterministic engine work | Persona |
| --- | --- | --- |
| intake | record work type, run DoR | none — the human's interactive session is the client |
| classify | checkpoint + DoR gate | human at L0/L1; **Juno** at L2+ |
| decompose | sizing governor, scope-disjointness, plan validation, checkpoint | **Dex** proposes |
| build — sub-task | worktree binding, dispatch, commit-presence probe | **Sol** then **Ivo** |
| build — sub-task verify | `fast` gates | none (D4) |
| build — lane integrate | `full` gates, rubric gate record | **Vera** validates; **Rune** reviews (correctness, security-when-relevant) |
| landing | merge queue, dependency order, bounce-back, coupling edges | none (D5); a bounce returns the work to **Ivo** |
| verify / ship | gate inspection, checkpoint, teardown, close | human; **Juno** at L3 with preconditions green |
| release | version, changelog, tag | **Cleo** curates |
| session close | retro prompt | open question 4 |

Read the table as the answer to "which expert owns which part of the loop": the engine owns
every step where the right answer is derivable, and a persona owns every step where it is
not.

## 6. Cost and speed model

Per landed package, dispatch counts scale like: one Dex plan per lane, one Sol + one Ivo
per sub-task, one Vera plus one-to-two Rune lenses per lane, plus Juno per decision. Ivo
therefore dominates token spend, and the levers in priority order are:

1. **Never dispatch where code decides** (§4) — removes whole classes of spend.
2. **Shrink Ivo's input** with Sol, and keep sub-tasks inside the D8 working-set band so no
   dispatch approaches the context ceiling.
3. **Tier discipline** (R5) — high tier only for small, consequential outputs.
4. **Concurrency, not sequence** — Vera and the Rune lenses are read-only and independent,
   so they run concurrently at integrate and cost latency once, not three times.

None of these are worth asserting without measurement. Component 1 telemetry already
records per-run tokens and cost, R7 makes every run attributable to a role, and
`basicly-7bur` reports cost per landed package — so **the roster's claim is falsifiable**:
if a lens or a Scout does not pay for itself, the numbers will say so and the role gets
cut. That evaluation, not this document, is what should decide the final roster.

## 7. Open questions for the design review

1. **Judged-verdict authority** — adopt R4 option (b), or (a) / (c)?
2. **Lens budget** — do the Rune lenses run on every lane, or only above a diff-size or
   risk threshold? Every-lane is simpler; thresholded is cheaper.
3. **Is Sol worth it?** It trades one cheap dispatch for a smaller expensive one. Plausible,
   unproven. Ship it behind a config flag and measure, or leave it out of v1?
4. **A retro persona?** Turning a rejection into a concrete fragment/skill change is the
   highest-leverage work for the *long-run* quality of every lane, and today it is the
   interactive session's job. Worth a persona, or worth keeping human?
5. **One Implementer or one per task class?** Recommended: one Ivo whose prompt is
   conditioned on the bead's class, since the tool policy and tier do not differ (R3).
6. **The name slate** — accept Dex / Sol / Ivo / Vera / Rune / Juno / Cleo, or re-cast?

## 8. Implementation shape (not yet decomposed)

Sketch only; this document is design-only and no work starts until `basicly-kjc5` closes.
Roughly: a role registry in the engine (role id → prompt source, tier, tool policy, output
schema), `[runner.roles]` config with defaults, role-aware dispatch in the supervisor
(replacing the single generic dispatch prompt), catalog sources for each role prompt with
projection gates, tool-policy overlays at invocation (the confinement mechanism
`basicly-kjc5.16` builds for Juno generalises to every read-only role), per-role attribution
plumbed into `br`, and eval cases per role prompt under `basicly-4t9z` as the regression
gate for the prose layer.
