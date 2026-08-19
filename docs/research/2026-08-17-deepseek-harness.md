# Research — The DeepSeek Harness and the Cordis Paradigm

Reviewed 2026-08-17. Four primary sources read at pinned revisions: the paper PDF, a shallow
clone of the harness repository, the Cordis primer, and two vendor web pages. Provenance and
pins are in §10; **read §2 before citing "the DeepSeek harness paper"** — the paper is not
about the harness, and that mis-attribution is the single easiest error to make here.

This document is **findings, not a plan.** It records what was established and what was not.
The critique and any architecture revision are separate steps.

## 1. Verdict

The **DeepSeek Harness (`dsh`)** is an open-source, MIT-licensed agent harness from DeepSeek-AI
in developer preview at `0.1.0-rc.7`, built as a 226-package TypeScript monorepo in which
*every* part of the product — the model adapter, the tool registry, the session log, and the
agent loop itself — is a hot-swappable plugin mounted from configuration, with no privileged
core to patch. Its single most important idea is **revertible effects**: every mutation a
component makes to the shared context is performed through one primitive that carries an
inverse the runtime tracks, so unloading a component provably restores the context and a
running system can be recomposed without a restart. The harness is the industrial application
of that idea; the formal argument for it lives in a separate paper about **Cordis**, the
framework `dsh` vendors, and the paper's own case study is a chatbot framework, not `dsh`.

Two framing corrections follow immediately, because both change how everything below reads.

**The harness is a runtime, not a work-orchestration factory.** It manages sessions, turns,
tools, subagents and context. It has no work graph, no decomposition, no dependency-ordered
landing, and essentially no git integration (§5.6). Anyone reading "harness" as a synonym for
`basicly`'s loop will mis-map the whole system.

**`dsh` and Cordis are two artifacts with two evidence bases.** The paper formalises Cordis
and validates it on Koishi. `dsh` vendors Cordis and is named in the paper only as *future*
work. §6 keeps these apart row by row.

## 2. What the paper actually is

The source the task names as "the paper" is:

> Yifan Shi, Wei Zhang, Tianyi Cui. *A Programming Paradigm for Spatiotemporal Composability.*
> Peking University; DeepSeek-AI. 88 pages.

[paper.pdf p.1; PDF `/CreationDate` `D:20260813182359+08'00`, `/Creator` `Typst 0.15.1`;
fetched from `raw.githubusercontent.com/cordiverse/paper/main/paper.pdf` 2026-08-17, HTTP 200,
2 140 840 bytes]

**It is a programming-languages paper, not a harness paper.** Measured over the extracted text:
`Cordis` occurs 61 times, `Koishi` 13, `harness` 13, and `deepseek` exactly once — as the second
author affiliation on p.1. There is no section describing `dsh`, no measurement of it, and no
figure from it.

The word "harness" appears in three roles only: as a *motivating example* (§1.2.2, "Self-Evolving
Agent Harnesses", p.5), as *future work* (§8, p.79), and in two citations to OpenAI and Anthropic
harness-engineering posts (refs [8], [9], p.80).

The conclusion is explicit that the harness application is **not yet done**:

> "Beyond human-curated plugin ecosystems, a compelling direction for future validation is
> self-evolving agent harnesses (Section 1.2.2) … **Applying Cordis in such a setting would
> validate** the temporal guarantees of complete recovery under rapid component replacement, as
> well as the spatial guarantees of dependency coordination under frequent topological change."
> [paper §8, p.79]

So the paper's own validation claim rests on Koishi, and it explicitly flags the limit
(§5.3, "Threats to validity", p.67): a single ecosystem, a single host language, observational
rather than controlled, an "existence-and-adoption result rather than a quantitative one", with
overhead and productivity measurement left as future work. It also notes Koishi runs **Cordis v3**
while the paper presents **v4** (footnote 4, p.66).

**The link to `dsh` is nonetheless real, and the repository asserts it, not the paper.** The
harness README states it is "powered by [Cordis](https://github.com/cordiverse/cordis), whose
design is described in *A Programming Paradigm for Spatiotemporal Composability*"
[`README.md:7`]. That is the load-bearing connection, established from the repo side.

### 2.1 The problem the paper poses

Two orthogonal dimensions of *dynamic* composability, both trivial statically and both hard at
runtime [paper §1.1, p.4]:

| Dimension | What it demands | Static analogue |
| --- | --- | --- |
| **Temporal composability** | removing a component completely and safely reverses every modification it made to the shared environment | lexical scoping, RAII, bracket patterns |
| **Spatial composability** | components declare, discover and resolve dependencies on one another verifiably, and lifecycles coordinate as the topology changes | module import resolution |

The motivating failure is VSCode, with numbers the paper measured itself: of the top 100
extensions by install count, **87 contain executable code** and so require an extension-host
restart to remove, and **only 7 declare `extensionDependencies`** on non-built-in extensions
[paper §1.2.1, pp.4–5; footnote 1 dates the Marketplace pull to 2026-06-09].

The paper's diagnosis of why this is tolerated is the sharpest paragraph in it: operating systems
already supply temporal composability *at process granularity* and container orchestrators supply
spatial composability *at service granularity*, so everyone defers to the coarse-grained
workaround — restart the process, let the orchestrator handle the dependency [paper §1.2.3, p.5].
The cost is a granularity mismatch: a restart discards all process-local state and rebuilding it
"takes seconds to minutes", while container orchestration cannot express dependencies between
components sharing an address space [paper §1.2.3, p.6].

### 2.2 The move

Effect systems formalise how a computation *modifies* its environment; coeffect systems formalise
what it *requires* of that environment. Both are classically compile-time analyses over lexically
fixed scopes. The paper lifts each to a runtime mechanism [paper §1.3, pp.6–7]:

1. **Revertible effects** — every context transformation carries an explicit inverse the runtime
   tracks; tracking and recovery both preserve composition. Establishes *local temporal*
   composability.
2. **Reactive coeffects** — a component declares required coeffects as a specification, and every
   context change notifies it as **activating**, **deactivating** or **neutral**. Establishes
   *local spatial* composability.
3. **A unified context type** merging the effect and coeffect contexts, in which an observational
   equivalence on coeffects supplies the effects with independence. This is what the paper calls
   *the context paradigm*.
4. **A calculus of dynamic composition** whose metatheory carries the two local properties up to a
   whole system of interleaved components.
5. **Cordis**, the implementation.

## 3. The conceptual model and its vocabulary

Three vocabularies are in play and they do not fully coincide. The paper is the formal register;
the primer is the practitioner register; the harness adds its own layer on top. §3.4 records where
they diverge.

### 3.1 The paper's constructs

The paper supplies its own theory-to-implementation dictionary [paper Table 2, p.55]. The
runtime-name column is what appears in code.

| Term | What it is | Runtime name |
| --- | --- | --- |
| **Context** (`Γ∞`) | the first-class, recursive object that is simultaneously the effect context and the coeffect context | `ctx` |
| **Component** | a coeffect specification `inject` paired with an effect function `apply`; the unit of composition | a plugin |
| **Fiber** | the *instantiation* of a component — a component may be instantiated many times | `fiber` |
| **Revertible effect** | a context transformation bundled with its inverse; the sole primitive through which a context is mutated | `ctx.effect(callback)` |
| **Coeffect** | a named capability a component requires from its environment | a service under a key |
| **Effect store** (`Σ`) | realm-symbol → typed value | `ctx[@@store]` |
| **Realm table** | coeffect key → realm symbol; the indirection isolation exploits | `ctx[@@isolate]` |
| **Interception table** | coeffect key → metadata adjusting *how* a binding is used | `ctx[@@intercept]` |
| **Isolation** | derive a child context redirecting a key to an independent binding | `ctx.isolate(key, realm)` |
| **Interception** | merge metadata governing a binding's use, without changing what it resolves to | `ctx.intercept(key, metadata)` |
| **Committed view** (`ω`) | the bindings a fiber resolved at load, read for as long as it is loaded | `fiber.committed` |
| **Target** | the digest of which fibers currently provide each declared key | `fiber.target` |
| **Inertia** | the handle of the lifecycle transition in flight | `fiber.inertia` |
| **Entry** | the declarative record of one fiber in the loader's configuration | a config row |

Four ideas in that table carry most of the weight.

**A single mutation primitive.** "Every context mutation in Cordis flows through a single
primitive, `ctx.effect`: coeffect provision, component instantiation, and every other
context-mutating operation reduces to a `ctx.effect` call" [paper §5.1.1, p.56]. That is what
makes complete recovery structural rather than a matter of author diligence. Inverses compose
LIFO, and a child effect's inverse is itself an effect on the parent — so unloading a parent
cascades to its children [paper §5.1.1, pp.56–57].

**The runtime does not verify the inverse.** This is the honest limit and the paper states it
plainly: "the callback supplies an inverse, and that the inverse recovers the effect it
accompanies is an obligation on the component author rather than a property the runtime verifies"
[paper §5.1.1, p.56]. §6.1 (p.67) then draws a *system boundary*: a location is inside it when the
system can modify it exclusively and restore the prior state, and outside otherwise, where an
operation acts as identity and is neither tracked nor recovered. Operations crossing outward split
into an **acquisition** stage (inside; `open`, `malloc`, `fork` install a revertible record) and an
**emission** stage (outside; the bytes a `write` hands over). For emissions, recovery needs either
withholding (the output-commit problem) or **compensation** — and the paper notes compensation
composes LIFO like an inverse but that the metatheory does *not* transfer, since commutation was
proved against a finer equivalence [paper §6.1, p.68].

**Identity by provider, not by value.** `fiber.target` tuples the *uid* of the providing fiber
rather than the bound value, and a uid is "drawn fresh and never reused", so a replaced provider
can never be mistaken for the one it replaced even when both provide equal values. A consequence
worth naming: a provider that overwrites its own binding in place "is therefore not observed"; a
component that wants replacement to propagate must withdraw the binding and reinstall it
[paper §5.1.3, p.60].

**Withdrawal is visible one step early.** `refresh` marks a fiber `UNLOADING` *before* its
transition task is created, so it stops providing and its dependents recompute an unsatisfied
target while its bindings are all still in place; `unload` then waits for each notified dependent
to reach `INACTIVE` before running any inverse [paper §5.1.3, pp.58–60, Algorithm 5 lines 10 and
25]. That ordering is the whole reason a dependency stays readable to a component whose teardown
that dependency triggered.

### 3.2 The lifecycle

A fiber is an **inertial state machine** over `LOADING` / `ACTIVE` / `UNLOADING` / `INACTIVE`
[paper §5.1.3, Algorithm 5, pp.59–60]. "Inertial" means once a transition begins it runs to
completion before the system responds to a target change; `reload` and `unload` then chain into
each other by mutual recursion. Staleness is checked at two levels: at transition boundaries
(enabling inertial chaining) and at each iteration boundary inside a transition (enabling partial
rollback) [paper §5.1.3, p.60].

Access is mediated by a `Proxy` whose `get` trap walks the fiber chain upward, returning the first
committed binding, throwing `INACTIVE_ACCESS` at a fiber that declares the key without having
committed it, and `UNDECLARED_ACCESS` at the root [paper §5.1.4, Algorithm 6, p.61]. So the
declared coeffect specification is enforced *at the point of use*, which is the difference between
`ctx[key]` and the never-failing `ctx.get(key)`.

### 3.3 The loader

Above the imperative core sits a declarative layer for orchestrators. An **entry** records
`id`, `url`, `isolate`, `intercept`, `config`, `disabled` [paper Definition 74, §5.2.1, pp.62–63].
The loader reconciles incrementally and dispatches per changed field: `id`/`url` rebuild;
`isolate` reassigns realms; `intercept` updates in place with no reload; `config` is handed to the
component to diff; `disabled` unloads and reloads [paper §5.2.1, p.63].

Two consequences the paper draws are worth keeping. **Order-independence** — the quiescent state
is a function of the final configuration alone, so there is no load order for the orchestrator to
arrange and modules can be fetched concurrently, "where bringing up a large configuration spends
its time" [paper §5.2.1, pp.62–63, resting on Theorems 73, 66, 63 and Corollary 62]. And
**annotation-free HMR** — because a fiber already bounds all of its component's effects, hot
replacement needs "no developer-annotated acceptance boundaries, as opposed to Webpack or Vite
HMR" [paper §5.2.2, p.64]. The HMR engine runs three phases: a fixed-point module classification
into accepted/declined (a module caught in an import cycle defaults to declined), stale-entry
detection, and a transactional reload that backs up caches and rolls every swap back if any import
throws [paper §5.2.2, Algorithms 8–10, pp.64–66].

### 3.4 The primer, and where the vocabularies diverge

`docs/cordis-primer.md` is the harness's own practitioner-facing distillation — 44 lines, five
ideas [`docs/cordis-primer.md:9-13`]. It is the conceptual substrate the harness's plugin authors
actually read, and the architecture document makes it a prerequisite: "It assumes you know Cordis;
if you do not, start with the primer" [`docs/architecture.md:5`].

Mapping the primer onto the paper, four of the five ideas are the paper's constructs under
practitioner names:

| Primer idea [`cordis-primer.md`] | Paper construct | Note |
| --- | --- | --- |
| "A plugin is a object that implements Service" (`:9`) | **component** (`inject` + `apply`) | the paper records the rename itself: "Koishi uses the term *plugin* for the concept this paper formalizes as *component*" [footnote 5, p.66] |
| "A context is a repository of services" (`:10`) | the unified **context** `Γ∞` | primer frames it as a service registry; the paper's point is that it is *simultaneously* the effect and coeffect context |
| "Declare service dependency via `inject`" (`:11`) | **coeffect specification** `d` → `fiber.inject` | primer's "waits until those services exist" is the paper's L-Begin |
| "Registrations are reversible effects" (`:13`) | **revertible effects** | note the word: primer says *reversible*, paper says *revertible* (30 occurrences vs 11 for "reversible") |

**The fifth idea has no counterpart in the paper.** "Typed Events for communication … dispatched
as `emit`, `waterfall`, `parallel`, or `serial`" [`cordis-primer.md:12`, table at `:19-24`] is an
implementation layer the formalism does not cover: in the paper's extracted text `waterfall`
occurs **0** times and `dispatch mode` **0** times. `ctx.waterfall` is around-middleware — a
listener receives `(...args, next)`, calls `next()` to delegate or returns without it to
short-circuit [`cordis-primer.md:30-34`]. This matters because the harness's principal extension
points are waterfalls (§4.2), so the mechanism agent authors most rely on is precisely the one
carrying no formal guarantee.

The primer also states the practical obligation the paper leaves as an author duty: "Every
registration should have a disposer … If teardown order matters, keep the related work in one
effect so disposal unwinds in the intended sequence" [`cordis-primer.md:44`].

## 4. What the harness is built from

### 4.1 Shape

226 packages under `packages/`, plus `apps/cli`, `apps/web`, a Python SDK under `python/`, and a
`native/landlock-run` sandbox helper. 7 466 tracked files: 2 376 Markdown, 2 330 TypeScript, 1 096
YAML. Cordis is **vendored**, not depended on: `vendor/cordis` at version **4.0.1**, rescoped to
`@deepseek-ai/cordis` by `scripts/rescope-vendor.ts`. **1 087 TypeScript/TSX files import
`@deepseek-ai/cordis`** — it is the spine, not a utility.

The vendored source files are `context.ts`, `events.ts`, `fiber.ts`, `logger.ts`, `reflect.ts`,
`registry.ts`, `service.ts`, `utils.ts` — a direct match to the paper's constructs. Seven further
cordiverse packages are vendored alongside: `hmr`, `loader`, `group`, `include`, `schemastery`,
`cosmokit`, `timer`, `logger-console`.

### 4.2 Composition

A running `dsh` is a plugin tree composed at boot from ordered layers [`docs/architecture.md:17-27`]:

- a **profile** is a named composition in the Harness home listing the bundles it stacks
  (`web` and `headless` ship as templates);
- a **bundle** is a distribution format for Cordis config rows plus the code they mount;
- layers apply to an empty entry list in order — each bundle, then the profile's
  `cordis.patch.yml`, then the home-level one, then any `--patch` overlay. A patch targets a row
  by id and replaces its whole config, or inserts new rows.

`dsh --profile web --dump-config` prints the tree a machine actually boots, and "any row it prints
can be replaced by a patch of your own" [`docs/architecture.md:32-35`].

The **turn flow** is the loop [`docs/architecture.md:63-90`]. A **step** is one model request plus
the tools it calls; a **turn** is zero or more steps, opening before its first input is claimed and
closing once nothing is owed. `agent/pre-step`, `agent/request`, `llm/stream` and the three
`tools/*` events are **waterfalls** whose listeners must call `next()`; `agent/turn-stopping` is
serial with no `next()`.

Three event domains, and "picking the right domain is the first decision in most changes"
[`docs/architecture.md:55-59`]: **session events** (durable facts appended to the log),
**agent events** (`agent/*`, carrying a live `Agent`), and **capability events** (`fs/*`,
`tools/*`, `telemetry/*`).

### 4.3 The capability seam

A **seam** is a swappable capability with three roles — a **Service Definition** declaring the
interface, a **Service Provider** implementing it, and a **Consumer** using it. "A package may
combine roles, but one role alone is not a seam; adding a capability means designing all three"
[`docs/architecture.md:100`]. The payoff claimed: filesystem and subprocess providers share one
execution world, so pointing them at a remote sandbox moves Bash, PTY and LSP with them "with no
provider forks" [`docs/architecture.md:102`].

`packages/sandbox/sandbox` is a clean instance — it owns only the contract
(`SandboxProvider`, `SandboxMode` of `read-only`/`workspace-write`/`danger-full-access`,
`SandboxEnforcement`, the fail-closed `SANDBOX_UNAVAILABLE`) and "depends only on cordis (+ the
harness error base), never on a backend" [`packages/sandbox/sandbox/README.md:5-9`]. Its one-line
contract: `ctx.sandbox.confine(argv, policy)` returns the argv to spawn *instead of* your own,
"and when no backend is usable it throws rather than passing the argv through unconfined".

### 4.4 The isolate realm — theory doing load-bearing work

This is the clearest place the paper's formalism is visibly carrying the product. The standard
agent preset's composition file opens with a rule stated in the paper's own vocabulary:

> "A service row here MUST sit inside a group carrying an `isolate` realm. Without one it
> publishes into the root realm, where it is process-global — another preset publishing the same
> name collides, and a host reader would resolve one preset's instance for every session;
> `dsh-agent-presets` rejects that at mount."
> [`apps/cli/config/agent-presets/standard/agent.cordis.yml:11-15`]

And the rejection is real code reading the paper's `@@isolate` and `@@store` slots directly:

```ts
export function leakedServices(ctx: Context, mount: Fiber): string[] {
  const store = ctx.reflect.store
  const rootIsolate = ctx.root[Context.isolate]
```

[`packages/preset/agent-presets/src/mount.ts:189-191`; the error text
"a preset service must sit behind an `isolate` realm or move to the host composition" at
`mount.ts:365` and `invariant.ts:41`]

So `ctx.isolate(key, realm)` from paper §5.1.2 is the mechanism by which one process serves many
agent sessions without their services colliding — and violating it is a mount-time refusal, not a
convention.

## 5. Mechanisms: how work is represented, dispatched and checked

The task asks how work is represented, decomposed, dispatched, verified and landed. `dsh` has the
first, the third in a specific sense, and a strong fourth. It has **no** decomposition and
**no** landing. Each is evidenced below, including the absences.

### 5.1 Work representation — a single goal, plus a flat todo list

**Goal.** `ctx.goals` holds "one current completion objective" per agent session
[`packages/goal/goal/README.md:5`]. The durable shape is small and closed:

- `GoalPhase = 'active' | 'paused' | 'blocked' | 'complete'`
  [`packages/goal/goal/src/types.ts:44-48`]
- `GoalSnapshot` = `{ id, revision, objective, phase, blockedReason?, maxGoalRounds }`
  [`types.ts:59-68`]
- `GoalRef` is a compare-and-set fence: "Positive revision; every durable mutation increments it"
  [`types.ts:19-24`]
- **activation** (`'armed' | 'disarmed'`) is process-local and "never persisted"
  [`types.ts:71`, `:82`]

**At most one goal is current, enforced in code**, not just documented: creating a second throws
``GOAL_ALREADY_EXISTS`` — `` `goal "${current.id}" already exists with phase "${current.phase}"` ``
[`packages/goal/goal/src/index.ts:256`].

Notably, a *single* durable `blocked` phase absorbs every stall cause — "provider limits,
configured budgets, execution errors, and requests for human input all use this one durable phase
rather than multiplying lifecycle states", discriminated by a lower-kebab-case `code` plus a
normalised message [`packages/goal/goal/README.md:15`; `GoalBlockReason` at `types.ts:51-56`].

The package's own limits are declared: "**State, not scheduling** — this package does not decide
when an armed goal continues"; "**Round-count budget only** — `maxGoalRounds` does not meter
tokens, currency, wall time, or provider quotas"; "**No independent evaluator** — the caller that
records completion or blocking is authoritative" [`packages/goal/goal/README.md`, Known Limitations].

**Todo.** `todo_write(todos: [{ content, status }])` with `status` in
`pending | in_progress | completed`; the model "sends the ENTIRE list every call — there are no
partial updates or per-item edits", appended as a full snapshot with last-write-wins on replay
[`packages/todo/tool-todo/README.md:7-11`]. The list "belongs to the ONE agent session that called
the tool. There is no subagent/shared/swarm scope". Items are rejected if they carry any key beyond
`content`/`status`, so "an extended item shape (ids, nesting) fails loud instead of silently
flattening" — i.e. the flatness is deliberate and enforced.

**Plan mode** is separate and explicitly advisory: `plan/mode` is a log-only boolean, and "Plan
mode is soft guidance; sandbox mode and approval policy enforce restrictions independently and do
not read or write plan state" [`packages/plan/plan-mode/README.md:5`]. Exit runs through
`exit_plan_mode`, which "leaves it only after an exact user approval through `ctx.userQuestions`".

### 5.2 Decomposition — absent

There is no work-graph decomposition in the harness. Probes over `packages/**/*.ts`, with
`subagent` as the positive control at **3 385 hits across 226 files**:

| Probe | Hits | Reading |
| --- | --- | --- |
| `subagent` (control) | 3 385 | probe works |
| `decompos` | 0 | no decomposition vocabulary |
| `depends_on` | 0 | no work-item dependency edge |
| `blocked_by` | 0 | no blocking edge |
| `DAG` (case-insensitive) | 89 "hits" | **false positive** |

The 89 `DAG` hits are a probe artifact: case-insensitively, `dag` matches inside
`grandchildAgent` (13), `oldAgentsHome` (9), `resumedAgent` (6), `resolveChildAgentOptions` (6),
`scopedAgent` (5) and similar. There is no DAG concept over work. A real dependency DAG does exist
in this repository, but over **gates**, not work (§5.5).

### 5.3 Dispatch — subagents and model-written workflows

Two mechanisms, both real.

**The subagent seam.** `ctx.subagents` lets one agent delegate to a child through a named provider;
"providers decide whether the child runs in this process, in another process, or through a future
transport" [`packages/subagent/subagent/README.md:5`]. Providers shipped:
`subagent-acp`, `subagent-claude-code`, `subagent-codex`, `subagent-dsh-sdk`,
`subagent-fork-in-process`, `subagent-in-process-driver`, `subagent-spawn-in-process` — so
delegating to Claude Code or Codex is a first-class provider, not an integration.

Two child kinds: **one-shot** (`start(name, request)`, holder-owned run) and **continuable**
(`startContinuable(spec)`, a durable child with an inbox that accepts later `followup()` messages,
cold-resuming from its persisted session if not resident). Authority is checked structurally:
"Follow-up authority comes from the exact live direct parent recorded in the child's durable
header. Cold resume checks that authority before reconstruction and again in the final no-await
inbox-admission span, so a parent unregistered or replaced during materialization cannot authorize
delivery." `interrupt()` admits only a human durable parent address or an exact live ancestor;
anything else rejects `UNAUTHORIZED` [`packages/subagent/subagent/README.md`, Service API table].

**The workflow seam.** `ctx.workflowEngine` "executes a model-written orchestration script that can
fan out subagents" [`packages/workflow/workflow/README.md:5`]. The current engine runs it in a
worker thread (`workflow-worker-thread`); "a future process or sandbox engine can replace the
implementation without changing the tool". `WorkflowStartRequest` is
`{ meta, script, args?, subagentProvider?, maxTotalAgents?, parent, signal? }`, and both the
provider route and the agent ceiling are "invisible to the script". `WorkflowRun.result` **never
rejects** — execution failures resolve with `stopReason: 'error'`, cancellation with `cancelled`.

Its declared limits are the interesting part for anyone reading this as an orchestration engine:
"**No journaling or resume** — scripts, child progress, and intermediate values are not
checkpointed, so a process restart cannot continue a run"; "**No token-budget vocabulary** —
engines cap concurrency, items, and children, but neither the request nor result accounts for
model tokens across children"; "**Foreground collection only**"
[`packages/workflow/workflow/README.md`, Known Limitations].

**Background work** is a third path: `ctx.jobs` gives long-running producers "shared ids, owner
isolation, reads, cancellation, waiting, notices, and cleanup under one contract"
[`packages/jobs/jobs/README.md:5`], and `dsh-schedule` gives durable reminders whose state the
session log owns, with "timers, tool values, and model follow-ups … disposable projections of that
log" [`packages/schedule/schedule/README.md:5`].

### 5.4 The session log — the durability substrate

"The session log is the source of the context the model sees. `deriveMessages()` projects model
history from it … Fork, resume, transcripts, telemetry, and persistence all derive from this
stream" [`docs/architecture.md:94`].

The rule stated as an invariant: "**Model-visible means logged.** Anything that reaches a model
request must be reconstructable from the log, and a runtime invariant asserts it. This is why a new
model-visible input requires a new session event: extend `SessionEventMap` and render from the log"
[`docs/architecture.md:96`]. The phrase is used in code as a repo rule — "it is required outright by
the repo's model-visible ⟺ logged rule" [`packages/preset/agent-presets/src/session.ts:9`] — and a
model-visible **surface** is a real module [`packages/core/session/src/surface.ts:22`, `:44`].

`SessionEventMap` members are "required-on-read by default — builds that do not know its type
refuse the log unless the event carries the envelope's `ignorable: true`; only structural format
changes bump `SESSION_FORMAT_VERSION`" [`AGENTS.md:104`].

This is the same architectural shape `basicly` uses for its tracker: an append-only event log with
a fold. `dsh` names its folds directly — `foldPlanMode(events)`
[`packages/plan/plan-mode/README.md:9`], `packages/goal/goal/src/fold.ts` (349 lines).

### 5.5 Verification — a gate DAG, runtime invariants, and gated documentation

Three distinct layers, all built.

**(a) Git hooks (lefthook).** `lefthook.yml` runs on `pre-commit`: translation pairing for staged
`*.i18n.yaml`, archived-agent-note verification, staged oxlint with `stage_fixed: true`,
third-party-notice regeneration (`&& git add THIRD_PARTY_NOTICES.md`), `git diff --cached --check`
for whitespace, and a vendor manifest guard. `pre-push` runs `pnpm run typecheck`. The file's own
comment sets the division of labour: "Keep these local checkpoints fast; CI owns the full
repository-wide gate matrix."

One design note worth recording: the notices job **regenerates rather than rejects**, with the
reason written in the file — "a dependency edit that forgot the notices would otherwise fail the
test lane long after the commit" — together with the case it knowingly does not catch (a deleted
manifest, since "lefthook only inspects files present on disk"), which falls through to a freshness
assertion in the test lane.

**(b) The gate runner.** `scripts/run-gates.ts` (909 lines) declares gates as records with a
`needs:` field and topologically sorts them (`visit` at `:694`) — so the real DAG in this repository
orders *gates*, not work. Gates include `typecheck`, `lint`, `build`, `build:web`, `test`,
`duplication` (jscpd), `knip`, `publint`, `constraints`, `coverage`, `snapshot`,
`verify-runtime-closure`, `verify-cordis-config`, `verify-module-graph`,
`verify-package-invariants`, `verify-dsh-package-licenses`, `verify-node-next-types`,
`verify-optional-dependency-imports`, `test:issue-management`, plus Windows lanes and smoke gates
(`source-worker-smoke`, `jsonl-zstd-smoke`, `dsh-source-launch-smoke`, `vitest-jsdom-smoke`,
`cli-lazy-search-startup-smoke`). Fourteen GitHub workflows sit above it.

**(c) Runtime invariants as a composable plane.** **219 non-test `invariant*.ts` modules** across
226 packages. `ctx.invariants` is itself a plugin: "Configurable registry service for package-owned
runtime invariant checks. The root plugin registers `ctx.invariants`; it contains no product checks
or product-package imports. Every workspace package publishes a `./invariant` companion that
registers its exact npm package name" [`packages/runtime-diagnostics/invariants/README.md`].
Selection is by regex allowlist/blocklist, defaulting to enabled, and "a valid pattern may match no
currently loaded package so later loading and HMR remain deterministic".

Some invariants are deliberately independent re-implementations. The goal package ships one:
"The separately published `./invariant` companion maintains an **independent fold** of each attached
session. It rejects malformed goal changes, discontinuous revisions, illegal lifecycle transitions,
timestamp regressions, and non-sequential admitted rounds **before the candidate event enters the
durable log**" [`packages/goal/goal/README.md`]. That is a second derivation path over the same
data — the same discipline `basicly`'s own rules demand of a number in a claim.

**(d) Documentation as a gated artifact.** Two gates enforce README *structure*, which is unusual
enough to be the most portable idea in the repository.

`scripts/verify-package-readme-limitations.ts` requires the verbatim h2
`## Known Limitations and Deferred Work` in every package README, "rejects missing or variant
sections, and requires one top-level bullet", with an explicit audited exemption table
(`NO_LIMITATIONS`, currently one entry: `packages/util/brand`, reason recorded inline).

`scripts/verify-package-readme-model-experience.ts` requires `## Model Experience` containing three
exact subsections — `#### What the model sees`, `#### Token effect`, `#### KV Cache effect` — and
validates "audited package classifications … package-owned text blocks, generated-catalog links,
and final-section order".

So every package must state, under gate, what the model sees, what it costs in tokens, what it does
to the KV cache, and what it does not do. Both gates cite the Agent Note that introduced them
[`2026-07-10-readme-known-limitations-gate.md`, `2026-07-12-package-model-experience-contract.md`].

**Agent Notes.** `.agents/notes/` holds **1 390** Markdown notes: **1 030 implemented**, **285
archived**, **50 proposed**, **22 rejected**, classified by type (`feature`, `architecture`,
`process`, `testing`, `bug-fix`, `simplification`). `scripts/verify-agent-note-format.ts` enforces a
status-line grammar per lifecycle folder (`Status: proposed`, `Status: implemented`,
`Status: rejected — .+`) and required h2 sections — for `proposed`: `## Problem`, `## Proposal`,
`## Acceptance criteria`, `## Risks`. It also pins the date the rules took effect
(`FORMAT_ADOPTED = '2026-07-05'`), permits exactly one grandfathering comment for older notes, and
**bans two retired legacy markers "so [they] cannot creep back"**. `verify-agent-note-classification`
and `verify-archived-agent-notes` are separate gates; archived notes are frozen: "never edit or
treat them as current authority" [`AGENTS.md:122`].

**One claimed rule is not gated.** `AGENTS.md:122` states "**Non-trivial changes MUST include an
Agent Note in the same PR;** only mechanical/local edits are exempt". The gates verify note
*format*, *classification* and *archival* — none verifies that a given diff is accompanied by a
note. The presence rule is prose plus review, not a check. (Stated as a bounded search: I looked
for a script correlating changed files against notes and found none; see §9.)

### 5.6 Landing — absent

There is no landing machinery. Counted over `packages/` and `apps/` TypeScript, with `session` as
the positive control at **30 624 hits across 1 029 files**:

| Probe | Hits | Files |
| --- | --- | --- |
| `session` (control) | 30 624 | 1 029 |
| `merge` | 635 | 237 |
| `worktree` | 5 | 4 |
| `rebase` | 5 | 4 |
| `pull request` | 4 | 3 |
| `git commit` | 3 | 2 |

The `merge` count is dominated by config and declaration merging, not git. `dsh` gives an agent
tools to edit a workspace; it does not own the branch, the worktree, or the merge. Its own *project*
has PR discipline (stacked PRs, `--force-with-lease`, "abort on remote movement, never raw
`--force`", a `kind/*` + `area/*` label taxonomy) [`AGENTS.md:127-128`] — but that is the DeepSeek
team's process for developing `dsh`, enforced by CI and the `dsh-merging-stacked-prs` skill, not a
capability `dsh` offers its users.

### 5.7 Presets — the four shipped modes

The vendor page's four modes are built, as YAML under `apps/cli/config/agent-presets/`. The shipped
`preset.yml` names are Chinese with i18n pairs:

| Directory | `name` | Description (translated from `preset.yml`) |
| --- | --- | --- |
| `standard` | 标准模式 | full coding agent: file editing, shell, file and web search, skills, plan, goals, subagents, workflows |
| `code` | PTC 模式 | all of standard, with tools presented through the Code Mode SDK so the model composes multi-step operations as one TypeScript program |
| `minimal` | 极简模式 | two-tool coding agent: persistent bash and `str_replace_editor` |
| `cordis` | 创造模式 | all of standard, plus runtime inspection, plugin experimentation and preset-authoring guidance — this is the page's "Creator mode" |

`standard/agent.cordis.yml` is 251 lines of declarative rows. Code Mode is real code, not a
description: `packages/core/tools/src/code-mode.ts` (503+ lines) generates the `run_code` schema at
"schema-emission time so the model-visible `run_code` schema always matches" [`code-mode.ts:103`].

## 6. Claimed versus built

Separated by source, because the paper and the product make different claims.

### 6.1 The paper's claims

| Claim | Verdict | Rung | Evidence | Pinned |
| --- | --- | --- | --- | --- |
| Two composability dimensions, formalised via effects/coeffects | **Built as theory** | 3 (paper) | §§3.1–3.3, 79 theorems, 196 definitions | paper 2026-08-13 |
| `ctx.effect` is the sole context-mutation primitive | **Implemented** | 2 (vendored artifact) | paper §5.1.1 p.56; `vendor/cordis/src/context.ts`, `fiber.ts` present | cordis 4.0.1 |
| Isolation/realms redirect a key to an independent binding | **Implemented and load-bearing** | 1 (repo code) | `mount.ts:189-203`, `:365`; `standard/agent.cordis.yml:11-15` | dsh 0.1.0-rc.7 |
| The runtime verifies that an inverse truly reverts | **Explicitly NOT claimed** | 3 (paper) | §5.1.1 p.56 — "an obligation on the component author" | paper 2026-08-13 |
| Metatheory is machine-checked | **NOT claimed; not done** | 3 (paper) | 0 occurrences of Coq / Agda / Isabelle / "proof assistant"; the 30 `mechani*` hits are all `mechanism(s)` | paper 2026-08-13 |
| Validated in production | **Claimed for Koishi only** | 3 (paper) | §5.3 pp.66–67; 4 000+ community plugins | paper 2026-08-13 |
| The case study runs the version presented | **NO** | 3 (paper) | footnote 4 p.66 — Koishi uses **v3**; the paper presents **v4** | paper 2026-08-13 |
| Quantitative overhead / productivity measured | **NO — future work** | 3 (paper) | §5.3 "Threats to validity" p.67 | paper 2026-08-13 |
| Validated on a self-evolving agent harness | **NO — future work** | 3 (paper) | §8 p.79 — "would validate" | paper 2026-08-13 |
| The paper is about the DeepSeek harness | **REFUTED** | 3 (paper) | `deepseek` occurs once, as an affiliation; case study is Koishi | paper 2026-08-13 |

### 6.2 The product's claims

| Claim (source) | Verdict | Rung | Evidence |
| --- | --- | --- | --- |
| "everything is a plugin" (`README.md:5`, vendor page) | **Substantially built** | 1 | 226 packages; 1 087 files import cordis; model adapter, tool registry, session log and agent loop all plugins [`docs/architecture.md:11`] |
| "powered by Cordis" (`README.md:7`) | **Built** | 1 | `vendor/cordis` 4.0.1 rescoped to `@deepseek-ai/cordis` |
| "Everything the model sees is recorded in an append-only session log" (vendor page) | **Built, with an asserted invariant** | 1 | `docs/architecture.md:94-96`; `core/session/src/surface.ts`; `agent-presets/src/session.ts:9` |
| Four modes: standard / code / minimal / creator (vendor page) | **Built** | 1 | `apps/cli/config/agent-presets/{standard,code,minimal,cordis}/preset.yml`; "creator" ships as `cordis` (创造模式) |
| Pluggable "models, tools, skills, sessions, sandboxes, storage, loops, scheduling, and the UI" (vendor page) | **Built** | 1 | one package family each: `llm/`, `core/tools`, `skill/`, `core/session`, `sandbox/`, `storage/`, `core/agent-loop`, `schedule/`, `apps/web` |
| Provider swap moves Bash, PTY and LSP together "with no provider forks" (`docs/architecture.md:102`) | **Claimed, not verified here** | — | architectural argument; I did not exercise a remote-sandbox swap |
| "Non-trivial changes MUST include an Agent Note in the same PR" (`AGENTS.md:122`) | **Prose, not a gate** | 1 | format/classification/archival gated; presence-per-diff not found (§5.5) |
| Developer preview, breaking changes expected (`README.md:11`) | **Stated** | 1 | "THERE WILL BE COMPATIBILITY-BREAKING CHANGES" |

### 6.3 Where the harness exceeds the paper

Three mechanisms carry no formal treatment, which is worth saying because the paper's guarantees
are often invoked loosely for the whole system:

1. **Event dispatch modes** (`emit`/`waterfall`/`parallel`/`serial`) — 0 occurrences of
   `waterfall` or `dispatch mode` in the paper, yet the harness's principal extension points are
   waterfalls [`docs/architecture.md:84`].
2. **The runtime invariant plane** — 219 modules, no paper counterpart.
3. **The gated documentation contracts** — no paper counterpart.

## 7. Comparison with `basicly`

Rows are concepts; cells state what each system does, with evidence. No judgement of better or
worse is expressed or implied. `basicly` evidence is from
`/home/niksa/development/basicly/docs/architecture/architecture.md` at the line ranges cited, read
2026-08-17; `[TARGET]` marks text that document itself flags as specified-not-shipped.

| Concept | DeepSeek harness | basicly |
| --- | --- | --- |
| **What the system is** | an agent runtime: sessions, turns, tools, subagents, context [`docs/architecture.md:63-90`] | two planes — distribution (catalog → agent-readable files + hooks) and execution (a unit of work → a merge) [arch §5, L258-266] |
| **Unit of composition** | a **plugin/component**: `inject` + `apply(ctx)`, instantiated as a **fiber** [`cordis-primer.md:9`; paper Table 2 p.55] | a **fragment**, **skill**, **hook** or **subagent definition** in the catalog, projected into target files [arch §§13–16, L686-986] |
| **Where composition is declared** | ordered layers: bundles → profile patch → home patch → `--patch`; a row targeted by id [`docs/architecture.md:17-27`] | authored YAML under `.basicly/core/` + `.basicly-local/`; selection on 4 axes, total sort [arch §11, L537-560] |
| **Determinism of composition** | quiescent state is a function of the final configuration alone, order-independent [paper §5.2.1 pp.62–63] | sort is total; "two builds on identical sources produce byte-identical output" [arch §11, L558-560] |
| **Unit of work** | one **goal** per session: `{ objective, phase, maxGoalRounds }`, at most one current [`goal/src/types.ts:59-68`; `index.ts:256`] | one **tracker issue**, typed as a **work class** (epic/feature/task/bug/chore), each class selecting a nesting **track** [arch §23.1, L1387-1400] |
| **Work states** | `active` / `paused` / `blocked` / `complete`; one `blocked` phase absorbs every stall cause, discriminated by a `code` [`types.ts:44-48`; `goal/README.md:15`] | phase **derived**, not stored: INTAKE → CLASSIFY → DECOMPOSE → BUILD → VERIFY → (VALIDATE) → SHIP → DONE [arch §§23.2, 24, L1398-1546] |
| **Decomposition** | none: `decompos` 0 hits, `depends_on` 0, `blocked_by` 0 in `packages/**/*.ts` (control `subagent` 3 385) | a decomposed leaf is a child issue on a dependency edge; DECOMPOSE is a phase behind a plan gate [arch §23.1-23.2, L1394-1425] |
| **Dependency graph** | over **services** (`inject` → provider fiber) and over **gates** (`needs:` in `run-gates.ts:694`); none over work | over **work items**, in the tracker; readiness and a definition-of-ready lint are tracker primitives [arch §23, L1376-1379; §32, L2461-2466] |
| **Durable state** | append-only **session log**; `deriveMessages()` projects model history; folds per subsystem (`goal/src/fold.ts`, `foldPlanMode`) [`docs/architecture.md:94`] | append-only **event log**, `.basicly/ledger/events-NNNN.jsonl`; a record's state is a fold; canonical sort is a function of the event set, not append order [arch §32.2, L2520-2540] |
| **Rule tying state to visibility** | "**Model-visible means logged**… a runtime invariant asserts it" [`docs/architecture.md:96`] | the tracker "**is** the loop's state"; a resume re-reads it, which is what makes the loop cross-agent [arch §32, L2456-2470] |
| **Delegation / dispatch** | `ctx.subagents` one-shot and continuable children; providers for ACP, Claude Code, Codex, in-process, spawn [`packages/subagent/subagent/README.md:5`] | **runner adapters**: one `RunnerSpec` per agent family driving the same agent-neutral loop [arch §29, L1951-1958] |
| **Fan-out** | model-written workflow script in a worker thread, `maxTotalAgents` ceiling, invisible provider routing [`workflow/README.md:5-13`] | parallel **lanes** with admission control and a supervisor [arch §28, L1841] |
| **Isolation of concurrent work** | `isolate` realm per preset — a service row without one leaks to the root realm and is refused at mount [`mount.ts:365`; `standard/agent.cordis.yml:11-15`] | a sibling **git worktree** at `<repo>.worktrees/<name>` on `harness/<name>`, never inside the repo; provisioning installs the toolchain **and the gates** [arch §27.1, L1710-1718] |
| **Shared-state discipline under concurrency** | services keyed per Session/Agent inside plugins; realms keep preset instances apart [`standard/agent.cordis.yml:3-8`] | "zero-touch tracker state": a lane worktree holds no store of its own; every read/write reaches the base checkout's one store [arch §27.1, L1723-1727] |
| **Removal / rollback** | **revertible effects**: unloading recovers every tracked effect in LIFO order; HMR is transactional with cache rollback [paper §5.1.1 p.56; §5.2.2 Alg 10 p.66] | rework loop modelled with gate results and comments (the tracker has no rework status); four operator verbs Go/Recycle/Hold/Kill [arch §23.1 L1402-1404; §25, L1547] |
| **Gates** | git hooks (lefthook) + a topologically-sorted gate DAG in `run-gates.ts` (909 lines) + 14 CI workflows | four strictly-linear layers: tool-call boundary → git hooks → verify runner → CI; layer 3 runs the same checks as layer 2 [arch §36.1, L3296-3310] |
| **Pre-artifact refusal** | approval policy + `ctx.sandbox.confine()` returning argv to spawn instead of yours, throwing when no backend is usable [`sandbox/README.md:9`] | layer 1 (tool-call boundary) is "the only one that can refuse an edit before there is anything to judge" [arch §36.1, L3308-3310] |
| **Runtime assertions** | 219 non-test `invariant*.ts` modules; `ctx.invariants` registry with regex allow/blocklist; goal's is an **independent fold** [`runtime-diagnostics/invariants/README.md`; `goal/README.md`] | evidence markers and gate results recorded on the tracker; VALIDATE phase with its own gate [arch §26, L1592] |
| **Documentation as a gated artifact** | `## Known Limitations and Deferred Work` and `## Model Experience` (`What the model sees` / `Token effect` / `KV Cache effect`) required verbatim per package, with audited exemption tables | `docs-claims` gate; the repo's own rule notes it "catches only an invented command" [`.claude/CLAUDE.md`, Quality Gate] |
| **Design-record system** | 1 390 **Agent Notes** in git, lifecycle folders (proposed/implemented/rejected/archived), required sections gated by `verify-agent-note-format.ts`; archived notes frozen | **decision records** in the architecture document (§38, L3854), plus tracker records |
| **Landing / merge** | none: `worktree` 5 hits, `rebase` 5, `git commit` 3 (control `session` 30 624) | exactly one advance merges — build→verify; neither the ship checkpoint nor teardown touches git history [arch §23.2, L1430-1435] |
| **Human checkpoints** | `exit_plan_mode` requires "an exact user approval through `ctx.userQuestions`"; approval policy governs tool execution [`plan-mode/README.md:11`] | GATE (engine-computed verdict) vs **checkpoint** (a human or a covering autonomy grant; nothing is computed) [arch §23.2, L1401-1406] |
| **Cost metering** | `maxGoalRounds` counts rounds only — "does not meter tokens, currency, wall time, or provider quotas"; workflow has "no token-budget vocabulary" | cost, grants and metering are a first-class section [arch §31, L2239] |
| **Extensibility contract** | a **capability seam** = Service Definition + Service Provider + Consumer; "one role alone is not a seam" [`docs/architecture.md:100`] | catalog is "data, never code"; `.basicly/` never holds engine code and `src/basicly/` never holds catalog data [arch §5, L272-283] |
| **Language / distribution** | TypeScript monorepo, 226 packages, pnpm; `npx @deepseek-ai/dsh web`; MIT | Python 3.14+, `uv`; consumed by other repos via `basicly install` [`.claude/CLAUDE.md`, Project Overview] |

## 8. Open questions a follow-up must settle before we act

Ordered by how much they would change a decision.

1. **SETTLED 2026-08-19 — see [§11.1](#111-q1--the-guarantee-stops-before-the-filesystem-and-the-harness-does-not-claim-otherwise). The guarantee stops before the filesystem, and the harness does not claim otherwise.** **Does the revertible-effect guarantee survive contact with a filesystem?** The paper confines
   it to locations the system can modify exclusively and restore (§6.1, p.67), and a coding agent's
   principal effects — files in a user's repo, spawned processes, network calls — are on the
   *outside* of that boundary by the paper's own test. What fraction of `dsh`'s effects are
   genuinely revertible versus compensated versus untracked? **How to settle:** enumerate
   `ctx.effect` call sites in `packages/fs`, `packages/shell`, `packages/subprocess`, and classify
   each inverse as restore / compensate / no-op.

2. **Is HMR of a live agent actually exercised, or only available?** This is the capability the
   whole thesis turns on for a self-evolving harness, and the paper lists it as future work.
   **How to settle:** run `dsh`, edit a loaded plugin, confirm the swap occurs mid-session with
   state preserved; and check whether any test in `packages/**/tests` covers HMR of an agent-plane
   plugin, as opposed to the loader in isolation.

3. **What does the paradigm cost?** Every access is `Proxy`-mediated (paper §5.1.4, p.61) and every
   mutation allocates a tracked inverse. The paper explicitly leaves overhead unmeasured (§5.3,
   p.67). **How to settle:** `BENCHMARK.md` exists at the repo root and I did not read it; start
   there, then measure a turn under Code Mode versus minimal.

4. **SETTLED 2026-08-19 — see [§11.2](#112-q4--half-the-paradigm-ports-and-the-half-that-carries-the-guarantee-does-not). Both of the paper's two claims are refuted.** **Would the paradigm survive translation to Python?** Paper §6.4 (p.70) says the runtime needs
   transparent access interposition and names Python's descriptor protocol (`__get__`) as the
   analogue of JavaScript's `Proxy`, and needs a module registry supporting eviction. Both claims
   need testing against CPython's real import machinery before any adoption argument.
   **How to settle:** a spike — can a Python component be introduced and *retracted* such that its
   registrations and its module both go away?

5. **Is the "no privileged core" claim true under measurement?** [`docs/architecture.md:13`]
   **How to settle:** take the `--dump-config` output for the `web` profile and test whether each
   row can in fact be replaced by a patch, or whether some rows are load-bearing in a way the
   loader does not permit overriding. A positive control is needed: at least one row that
   demonstrably *can* be swapped.

6. **How is the 1 390-note corpus kept from becoming sediment?** 1 030 implemented and 285 archived,
   with archived notes frozen and a `dsh-archive-agent-notes` skill. What triggers archival, and
   what does an agent load at read time out of 2 376 Markdown files?
   **How to settle:** read `.agents/notes/README.md` (its archiving policy) and
   `scripts/archived-agent-notes.ts`.

7. **SETTLED 2026-08-19 — see [§11.3](#113-what-we-adopt-what-we-refuse-and-the-invariant-each-would-move). It is author-facing only and costs zero model tokens.** **What is the actual retrieval cost of this documentation discipline?** The gated
   `Model Experience` sections are a strong idea, but 226 packages × mandatory sections is a large
   corpus. Is any of it projected into agent context, or is it human-facing only?

8. **Does the goal/round model have anything to teach our cost model?** `maxGoalRounds` is a
   round-count budget with no token accounting, and both `goal` and `workflow` declare that gap. If
   DeepSeek chose rounds over tokens deliberately, the reasoning is likely in an Agent Note.
   **How to settle:** `.agents/notes/implemented/feature/2026-07-19-persisted-same-session-goal-domain.md`,
   cited from `packages/goal/goal/README.md:5`.

## 9. Not established

Explicitly listed. None of the following is filled in from recall.

- **Whether the paper is peer-reviewed, published, or a preprint.** No venue, DOI or submission
  note appears in the extracted text; PDF metadata carries only title, authors, Typst 0.15.1 and
  the 2026-08-13 timestamp. I did not fetch the `cordiverse/paper` repository itself.
- **Whether an Agent Note is required per PR by any automated check.** I searched
  `scripts/*.ts`, `.github/`, `lefthook.yml` and `.gitlab-ci.yml` for a diff-to-note correlation
  and found only format, classification and archival gates. The search was manual and bounded to
  those paths; a check living in a GitHub Action's inline script or in repository settings
  (required reviewers, a bot) would not have been caught. Recorded as "not found", not "absent".
- **The runtime invariant that asserts model-visible ⟺ logged.** `docs/architecture.md:96` asserts
  it exists. I found the rule cited in code comments and a `surface.ts` module, but did not locate
  and read the assertion itself, so I cannot say what it checks or whether it fails open.
- **Whether `dsh` actually performs HMR on itself in normal operation.** The capability is
  vendored (`vendor/hmr`) and the paper describes the engine, but I ran nothing. Open question 2.
- **Any runtime behaviour whatsoever.** I did not execute `dsh`, did not run `pnpm install`, did not
  run the test suite, and did not run `run-gates.ts`. Every statement about the harness is from
  reading source, config and committed documentation. Gate *existence* is established; gate
  *outcome* is not.
- **Benchmarks.** `BENCHMARK.md` exists at the repo root; I did not open it. No performance claim in
  this document comes from measurement.
- **The Python SDK's scope.** `python/sdk` and `python/sdk-runtime` exist (19 `.py` files tracked
  repo-wide); I did not read them, so I cannot say whether the Cordis model is reproduced there or
  whether it is a thin client.
- **The `e2b` and remote-sandbox path.** `packages/e2b` and `.github/workflows/e2b-e2e.yml` exist;
  unread. This bears directly on the "provider swap moves Bash, PTY and LSP together" claim, which
  therefore stays unverified.
- **Cordis v3 → v4 differences.** Footnote 4 (p.66) says v4 "refines the effect and coeffect
  semantics and redesigns the loader" while "the core compositional model is shared". I did not
  read v3, so I cannot characterise what changed or whether Koishi's validation transfers.
- **Whether the vendored `@deepseek-ai/cordis` 4.0.1 matches upstream `cordiverse/cordis`.**
  `scripts/rescope-vendor.ts` performs textual rewrites and `pnpm run rescope-vendor:check` gates
  them, but I did not diff vendored source against upstream.
- **Correctness of the paper's 79 theorems.** Not machine-checked (§6.1) and not reviewed by me. I
  read the statements of the results the implementation section leans on; I verified no proof.
- **The Chinese-language sources.** `README.zh.md`, `AGENTS.zh.md` and 1 096 `.i18n.yaml` files are
  part of the corpus. I read the English side only; the translation-pairing gate suggests they are
  kept in sync, but I did not verify semantic equivalence.

## 10. Provenance

| Source | Pin | Retrieved | Reachability |
| --- | --- | --- | --- |
| `cordiverse/paper` → `paper.pdf` | 88 pp.; `/CreationDate` `D:20260813182359+08'00`; Typst 0.15.1; 2 140 840 bytes; HTTP 200 | 2026-08-17 | OK, via `raw.githubusercontent.com`. Text extracted with `pypdf`; `pdftotext` was unavailable |
| `deepseek-ai/deepseek-harness` | commit `99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`, committed 2026-08-17T19:03:17+08:00, "Merge pull request #2620 from deepseek-harness/release/dsh-0.1.0-rc.7"; `package.json` version `0.1.0-rc.7`; 7 466 tracked files | 2026-08-17 | OK. `git clone --depth 1` into `/home/niksa/development/reference-repos/deepseek-harness` |
| Vendored Cordis | `vendor/cordis/package.json` name `@deepseek-ai/cordis`, version `4.0.1` | 2026-08-17 | OK (in-tree) |
| Cordis primer | `docs/cordis-primer.md`, 44 lines (in-tree) and the rendered page at `deepseek-harness.github.io/deepseek-harness/en/reference/cordis-primer` | 2026-08-17 | Both OK; the in-tree file is quoted in preference to the rendered page |
| Quickstart | `deepseek-harness.github.io/deepseek-harness/en/guide/quickstart` | 2026-08-17 | OK; thin — names `dsh`, sessions, workspace, plugins, tools, and that `dsh` "uses its invoking directory as the default filesystem location". Carries no install command |
| Product page | `www.deepseek.com/harness/en/` | 2026-08-17 | OK; marketing surface, used only for claims cross-checked against code in §6.2 |
| `basicly` architecture | `/home/niksa/development/basicly/docs/architecture/architecture.md`, 4 485 lines, at working-tree state on branch `main`, commit `ee7d263` | 2026-08-17 | OK |

**Licence note.** The harness repository is MIT [`LICENSE`], so quoting its source and
documentation is unrestricted. The paper carries no licence statement I located; it is quoted here
only in short excerpts for identification and criticism, and every finding drawn from it is stated
as a fact about what the paper claims rather than as reproduced expression.

**Second pass, 2026-08-19.** Sections 8.1, 8.4 and 8.7 were settled against the **same** clone pin
(`99f6f02f`, still depth-1, so no finding can be dated against the harness's own history) plus two
Python spikes run locally on 3.14.6. The spike sources are working files and are deliberately not
committed: they establish a fact about CPython, not about this repository, and §11.2 records the
observed output that the fact rests on. `basicly-e2mz.45` carries the record.

**Method note.** Every zero reported in this document was run against a positive control on the
same corpus and probe, per the repository's External Facts rule. Two probes failed their control
and are recorded as failures rather than findings: the case-insensitive `DAG` probe (§5.2), which
matched inside `grandchildAgent`, and a `rg -r` invocation whose `-r` is a replace flag, not a
recursion flag, and which silently rewrote its own output.

## 11. Q1 and Q4, settled 2026-08-19

Section 8 listed eight open questions. Two of them decide whether adoption is possible at all, and
both are now answered against the same pinned clone (§10), re-verified rather than recalled. Q7 is
answered as a byproduct. The other five stay open.

`basicly-e2mz.45` is the record. The proposals are §11.3.

### 11.1 Q1 — the guarantee stops before the filesystem, and the harness does not claim otherwise

`ctx.effect` is the right symbol: `vendor/cordis/src/fiber.ts:403-418` declares
`effect(execute, label?)` and `Context` re-exports it at `fiber.ts:10`.

**Five call sites exist across the three packages the question named** [measured 2026-08-19,
`rg -n '\.effect\(' --glob '!**/tests/**' --glob '!**/*.test.ts' packages/fs packages/shell
packages/subprocess`].

| Site | Inverse performs | Class |
| --- | --- | --- |
| `packages/subprocess/subprocess-local/src/index.ts:49` | kills whole process trees, then removes the host exit listener | compensate |
| `packages/shell/tool-bash-persistent/src/index.ts:201` | aborts creation, kills each live shell | compensate |
| `packages/shell/tool-bash-persistent/src/index.ts:230` | deletes two owner-keyed map entries | restore |
| `packages/shell/shell-env/src/index.ts:111` | deletes exactly the contributor name and its owned keys | restore |
| `packages/fs/fs-observation-policy/src/index.ts:109` | clears a `WeakMap` the plugin created | restore |

**Restore 3 · compensate 2 · no-op 0.** Positive controls run first, on the same corpus and probe:
`subagent` 2,383 occurrences over 216 files; `.effect(` 203 sites tree-wide; `rollback` 126 against
`revertible` **0**.

**Zero of the five is on a filesystem mutation path.** `packages/fs` holds exactly one site, and it
tracks *observation* state rather than file bytes. Nine mutation call sites in `fs-local` register no
inverse at all, of which three publish the final rename or replace
(`src/fsio.ts:586`, `:591`, `:594`). 91 raw filesystem syscall occurrences appear in `packages/fs`
outside tests, none inside an effect.

**One line decides it.** `ReplaceFileW`'s third parameter is a backup path. `packages/fs/fs-local/src/win32.ts:20`
types it `backup: null`, and `:54` binds the foreign-function signature to match. Overwriting is
unrecoverable **by construction**, on the one platform whose own interface offers the backup.

**The near-refutation, recorded so a later reader does not mistake it for one.** `dsh` does capture
prior file content — `FsWriteOutcome.before` at `packages/fs/fs/src/types.ts:134-144` — and hands it
to the diff renderer, not to the runtime. Its four consumers are all presentation
(`tool-fs/src/write.ts:96-98`, `:126`; `tool-fs/src/edit.ts:108`, `:144`). It is lossy three ways
and so could not serve as an inverse even if it were wired to one: `null` above a 10 MiB basis
limit, `null` for a non-UTF-8 body, and line-ending normalised, so a CRLF file is not byte-faithful.

**The transferable rule is not the disposer.** Site 4 is the only place the guarantee is total, and
the mechanism is why: the forward operation *refuses* a duplicate name or key before registering,
with six explicit refusals at `shell-env/src/index.ts:118-135`. Because no overwrite is reachable, a
delete **is** a restore. So: **an inverse is a restore only where the forward operation refuses to
overwrite.** Everywhere else it is a compensation wearing the same word.

### 11.2 Q4 — half the paradigm ports, and the half that carries the guarantee does not

Two spikes, run on Python 3.14.6. Paper §6.4 (p.70) is quoted here as §8 recorded it; the PDF was
not re-fetched, so both claims below are **sourced**, and both verdicts are **measured**.

| Paper claim | Verdict |
| --- | --- |
| The descriptor protocol (`__get__`) is the `Proxy` analogue | **refuted** |
| Python has a module registry supporting eviction | **refuted as stated** |

**Eviction is advisory, and the obvious probe for it does not discriminate.** Deleting from
`sys.modules` and collecting the module object does not evict the code. A reference obtained before
retraction survives it, keeps executing the evicted module's body, and keeps mutating its
module-level state — because a function's `__globals__` is the module's `__dict__`, not the module.
A weakref on the *module object* reported success in exactly the run where the code was still
running; only a weakref on a **namespace sentinel** separated the two arms of the control. Re-import
then yields a second live class with the identical qualified name, so `isinstance` against the
replacement fails for pre-retraction instances: a silent split-brain, not a refusal.

**Interposition fails on five shapes.** A descriptor is refused outright by `__slots__`
(`ValueError: 'x' in __slots__ conflicts with class variable`); cannot be installed on a C-level
type (`TypeError: cannot set 'tracked' attribute of immutable type 'dict'`); observes an attribute
*binding* and never a mutation of the bound object, so `h.items.append(...)` logged two reads and no
write; is bypassed by a direct `__dict__` write, which a data descriptor silently discards and a
non-data descriptor shadows permanently; and must exist per name at class-definition time, so an
undeclared key is invisible. `__getattribute__`/`__setattr__` is the closer analogue and has two
holes reachable from plain Python — `object.__setattr__` and a `__dict__` write — neither blockable.

Cordis does not have this problem, and the reason is structural: a consumer holds a proxy whose trap
consults live fiber state and refuses after unload, so the held reference is neutralised **at the
reference**. Python hands out the object itself. There is no seam to neutralise. [sourced from §3.2's
reading of paper §5.1.4; not re-verified]

### 11.3 What we adopt, what we refuse, and the invariant each would move

Read against `docs/architecture/architecture.md` §5, §6, §10.2, §27.2, §32, §33, §34, §36.7.

| # | Proposal | Disposition |
| --- | --- | --- |
| P1 | An effect-inverse or undo layer over git or the filesystem | **refuse** |
| P2 | A Cordis-style dynamic component runtime in Python | **refuse** |
| P3 | A `before`-content field on the change-summary artifact | **refuse** |
| P4 | "No privileged core", every row replaceable from configuration | **refuse** |
| P5 | A declared token cost on a catalog source, and a validated exemption table | **adopt** |

**P1 refuses on measurement, not on taste.** The industrial application of the paradigm has zero
effect-tracked filesystem mutations across 91 mutation sites, and types away the one backup slot its
platform offers. We would be building what the reference implementation declined to build. §6 and
§27.2 are **confirmed rather than changed**: an append-only log already gives us what a LIFO inverse
gives Cordis, and §27.2's bounce-on-conflict already implements the paper's *sound* remedy for an
irreversible emission, which is to withhold it. Adopting rollback would trade that refusal for a
compensation.

**P2's cost is an invariant, and the spike is only the corroboration.** A Cordis plugin is code
mounted from configuration, so adopting it abandons §5's rule that `.basicly/` never holds engine
code, and breaks §34's layering contract, which is *exhaustive* — a module joins a tier because a
maintainer placed it there. Dynamic mount and retract is definitionally not exhaustive. The trade is
a statically checkable contract for a dynamically unenforceable one. One instrument is worth keeping
regardless: **if plugin loading is ever proposed, the eviction probe is a namespace-sentinel
weakref**, because the two cheaper probes report success while the code runs.

**P3 is refused by our own document.** §33 already names the failure mode: five of eight artifact
kinds carry a contract nobody can exercise. A field with no consumer that can refuse is the
anti-pattern that section exists to flag.

**P4 rests on a vendor's self-description.** Its only source is the harness's own
`docs/architecture.md:13`, and §8's open question 5 — whether any row is in fact swappable — is
still unrun. Trading an unverified claim against §6's "the engine disposes, agents propose" and
D-01 is not a trade. **The narrow adoptable half is a different thing**: `dsh --dump-config` prints
the tree a machine actually boots, and a basicly command printing the composed catalog selection
with each item's origin would be genuinely useful. Whether one already exists was not established.

**P5 is the one to build, and the sharper half is not the documentation section.** Two scripts are
wired into the harness gate runner at `scripts/run-gates.ts:618` and `:633`, requiring
`## Model Experience` with `What the model sees`, `Token effect` and `KV Cache effect` per package.
The cost objection dies on measurement: the corpus is **never projected into model context** —
`Model Experience` occurs 0 times in `packages/**/*.ts` against a control of `systemPrompt` at 492.
It costs zero model tokens and forces an author to state a cost they would otherwise not compute.
That answers §8's Q7 as well: the discipline is author-facing only.

The sharper import is `verify-package-readme-limitations.ts`, which **validates its own exemption
table against the scanned population**: an entry naming a package that no longer exists fails, an
entry with a blank justification fails, and `isLimitationsLike()` rejects variant spellings rather
than only checking presence. §32.1 records the matching defect on our side, measured and in our own
words — *a gate written as prose is not a gate*. So the rule to take is **every exemption list in
basicly is machine-validated against the population it exempts from.**

The carrier is refused even where the content is adopted: a per-package Markdown section would
violate §10.2, every catalog source is YAML. The requirement lands in YAML and
`basicly catalog lint` enforces it. §36.7 gains a fifth gate kind, a catalog-source assertion on
declared token cost.

**The branch not taken.** Making the always-on character cap the control instead. That ratchet
already exists, and it refuses the last straw rather than the author who never computed the cost, so
it cannot attribute. The declaration branch was taken for that reason.

### 11.4 What these two answers did not establish

- Whether the harness's effect inverses **run** correctly. Source was read; no TypeScript, no
  install, no test and no gate was executed. §9's limit still stands.
- Whether filesystem inverses exist **outside** the three named packages. `packages/e2b` was out of
  scope and stays unread.
- Whether the paper says exactly what §6.4 is quoted as saying. The PDF was not re-fetched.
- Whether a C-extension or `ctypes` route could close the `__dict__` bypass, and whether a metaclass
  sweep could reach undeclared keys. Neither was attempted; the other four shapes would still hold,
  so neither can rescue the claim.
- Whether basicly already prints a composed catalog selection with per-item origin. §11 and §22 of
  the architecture were not read.
- Any dating of a harness finding against the repository's own history. The clone is depth-1.

**One caution for a later editor of this file.** §6.1's row saying `ctx.effect` is the sole
context-mutation primitive is true of **context** mutation, and reads easily as covering effects on
the world. §11.1 shows the filesystem mutation path never enters it.
