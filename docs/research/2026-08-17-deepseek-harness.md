# Research — The DeepSeek Harness and the Cordis Paradigm

Reviewed 2026-08-17. Four primary sources read at pinned revisions: the paper PDF, a shallow
clone of the harness repository, the Cordis primer, and two vendor web pages. Provenance and
pins are in §10; **read §2 before citing "the DeepSeek harness paper"** — the paper is not
about the harness, and that mis-attribution is the single easiest error to make here.

This document is **findings, not a plan.** It records what was established and what was not.
The critique and any architecture revision are separate steps.

## 1. Verdict

The **DeepSeek Harness (`dsh`)** is an open-source, MIT-licensed agent harness from DeepSeek-AI
in developer preview at `0.1.0-rc.8` (**§12**; reviewed at rc.7), built as a TypeScript
monorepo of 226 `package.json` files and **219 pnpm workspace members** at rc.7 (233 / 226 at
rc.8) in which
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
tools, subagents and context. It has **no decomposition, no dependency-ordered landing, and
essentially no git integration** (§5.6). Anyone reading "harness" as a synonym for `basicly`'s
loop will mis-map the whole system.

> **AMENDED at rc.8 — this paragraph said "no work graph" and that clause is refuted.** A
> dependency-ordered task graph now exists, in `packages/experimental/agent-team`, with
> `blockedBy` edges, cycle rejection and readiness gating. It is `private: true`, excluded
> from the release family, and mounted in no shipped preset. The other three clauses hold
> unchanged, and `decompos` is still 0 against a working control. **[§12.2](#122-the-work-graph-the-one-framing-clause-that-is-refuted)**

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

226 `package.json` files under `packages/`, of which **219 are pnpm workspace members** — the
remaining 7 sit deeper than the `packages/*/*` glob. The review's "226 packages" is the file
count, not the member count [re-measured §12.1]. Plus `apps/cli`, `apps/web`, a Python SDK under `python/`, and a
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

> **Pinned at `99f6f02f` (rc.7). Two numbers below moved at rc.8** — `blocked_by` 0 → 7 and
> `blockedBy` 0 → 36, from `packages/experimental/agent-team`. **`decompos` is still 0**, so the
> heading stands and the table's own conclusion does not. [§12.2](#122-the-work-graph-the-one-framing-clause-that-is-refuted)

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

**Agent Notes.** `.agents/notes/` holds **1 387 Markdown files** — not notes. Each note is an
English/Chinese pair, so this is **≈693 distinct notes** [re-measured §12.5; the file counts below
reproduce exactly, only the unit label was wrong]. **1 030 implemented**, **285
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
| **Decomposition** | none: `decompos` **0 at both pins**, `depends_on` 0. `blocked_by` 0 → **7** and `blockedBy` 0 → **36** at rc.8, from one private release-excluded package (§12.2). Controls: `subagent` 2 267 → 2 414 | a decomposed leaf is a child issue on a dependency edge; DECOMPOSE is a phase behind a plan gate [arch §23.1-23.2, L1394-1425] |
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
| **Design-record system** | 1 387 Agent Note **files** = ≈693 notes (English/Chinese pairs, §12.5) in git, lifecycle folders (proposed/implemented/rejected/archived), required sections gated by `verify-agent-note-format.ts`; archived notes frozen | **decision records** in the architecture document (§38, L3854), plus tracker records |
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

2. **SETTLED 2026-08-20 — see [§12.6](#126-q2--one-test-now-swaps-an-agent-plane-plugin-under-a-live-child). One test now covers it; the runtime half stays unestablished.** **Is HMR of a live agent actually exercised, or only available?** This is the capability the
   whole thesis turns on for a self-evolving harness, and the paper lists it as future work.
   **How to settle:** run `dsh`, edit a loaded plugin, confirm the swap occurs mid-session with
   state preserved; and check whether any test in `packages/**/tests` covers HMR of an agent-plane
   plugin, as opposed to the loader in isolation.

3. **SETTLED 2026-08-20 as far as this source can settle it — see [§12.4](#124-q3--the-cost-is-still-unmeasured-and-benchmarkmd-is-not-a-benchmark). `BENCHMARK.md` states no number; overhead is unmeasured in both artifacts.** **What does the paradigm cost?** Every access is `Proxy`-mediated (paper §5.1.4, p.61) and every
   mutation allocates a tracked inverse. The paper explicitly leaves overhead unmeasured (§5.3,
   p.67). **How to settle:** `BENCHMARK.md` exists at the repo root and I did not read it; start
   there, then measure a turn under Code Mode versus minimal.

4. **SETTLED 2026-08-19 — see [§11.2](#112-q4--half-the-paradigm-ports-and-the-half-that-carries-the-guarantee-does-not). Both of the paper's two claims are refuted.** **Would the paradigm survive translation to Python?** Paper §6.4 (p.70) says the runtime needs
   transparent access interposition and names Python's descriptor protocol (`__get__`) as the
   analogue of JavaScript's `Proxy`, and needs a module registry supporting eviction. Both claims
   need testing against CPython's real import machinery before any adoption argument.
   **How to settle:** a spike — can a Python component be introduced and *retracted* such that its
   registrations and its module both go away?

5. **SETTLED 2026-08-20 — see [§12.3](#123-q5--no-privileged-core-is-false-as-an-absolute-at-three-rows). False as an absolute at three rows; true of the configuration tree.** **Is the "no privileged core" claim true under measurement?** [`docs/architecture.md:13`]
   **How to settle:** take the `--dump-config` output for the `web` profile and test whether each
   row can in fact be replaced by a patch, or whether some rows are load-bearing in a way the
   loader does not permit overriding. A positive control is needed: at least one row that
   demonstrably *can* be swapped.

6. **SETTLED 2026-08-20 — see [§12.5](#125-q6--the-archival-trigger-is-a-judgement-and-there-is-deliberately-no-index). The trigger is qualitative by decision, there is no index by decision, and the freeze is a real gate.** **How is the corpus kept from becoming sediment?** 1 030 implemented and 285 archived **files** (≈693 notes),
   with archived notes frozen and a `dsh-archive-agent-notes` skill. What triggers archival, and
   what does an agent load at read time out of 2 376 Markdown files?
   **How to settle:** read `.agents/notes/README.md` (its archiving policy) and
   `scripts/archived-agent-notes.ts`.

7. **SETTLED 2026-08-19 — see [§11.3](#113-what-we-adopt-what-we-refuse-and-the-invariant-each-would-move). It is author-facing only and costs zero model tokens.** **What is the actual retrieval cost of this documentation discipline?** The gated
   `Model Experience` sections are a strong idea, but 226 packages × mandatory sections is a large
   corpus. Is any of it projected into agent context, or is it human-facing only?

8. **SETTLED 2026-08-20 — see [§12.7](#127-q8--the-round-choice-was-about-attribution-and-the-token-half-has-no-consumer). The choice was about attribution, not units, and it teaches one adopt-shaped and one refuse-shaped lesson.** **Does the goal/round model have anything to teach our cost model?** `maxGoalRounds` is a
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
- **Benchmarks.** `BENCHMARK.md` **has now been read** [§12.4]: 3 lines, 231 bytes, and it states
  **no number at all** — it is a procedure for running agent-capability task batches, not a
  performance report. So the gap below is not closable from this source. No performance claim in
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

**Licence note.** The harness repository is MIT [`LICENSE`] **except `native/landlock-run/` and
its two platform sub-packages, which are BSD 3-Clause** [`native/landlock-run/LICENSE`,
`native/landlock-run/packages/linux-{arm64,x64}/LICENSE`; measured 2026-08-20, and true at rc.7
too]. Both licences are permissive so no finding here is affected, but a per-directory licence is
exactly the trap `.claude/rules/external-review.md` names. Quoting its source and
documentation is unrestricted. The paper carries no licence statement I located; it is quoted here
only in short excerpts for identification and criticism, and every finding drawn from it is stated
as a fact about what the paper claims rather than as reproduced expression.

**Second pass, 2026-08-19.** Sections 8.1, 8.4 and 8.7 were settled against the **same** clone pin
(`99f6f02f`, **depth-1 at the time**, so no finding in §11 could be dated against the harness's own
history — **that limit is now lifted, see §12**) plus two
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
**now run — see [§12.3](#123-q5--no-privileged-core-is-false-as-an-absolute-at-three-rows), which
measures the claim false as an absolute at three bootstrap rows. The refusal is unchanged; its
basis moves from "unverified vendor claim" to "measured false".** Trading an unverified claim
against §6's "the engine disposes, agents propose" and
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
- ~~Any dating of a harness finding against the repository's own history. The clone is depth-1.~~
  **Lifted 2026-08-20.** `git fetch --unshallow` succeeded, `.git/shallow` is absent, and 12 940
  commits are available. Every finding in §12 is dated against a two-pin diff.

**One caution for a later editor of this file.** §6.1's row saying `ctx.effect` is the sole
context-mutation primitive is true of **context** mutation, and reads easily as covering effects on
the world. §11.1 shows the filesystem mutation path never enters it.

## 12. Re-established at rc.8, and the five remaining questions settled — 2026-08-20

The clone was re-pulled and **unshallowed**, so for the first time every finding here is dated
against the harness's own history. `basicly-e2mz.45` carried §11; this section is its second pass.

**Read §12.2 first.** It refutes a clause of §1, and §1 governs how §11.3 reads.

### 12.1 The new pins, and what moved

| Source | Pin | Retrieved | Reachability |
| --- | --- | --- | --- |
| `deepseek-ai/deepseek-harness` | commit `141eb6fef83422698aef7a981029e843e8161534`, committed 2026-08-19T23:11:50+08:00, "Merge pull request #2783 from deepseek-harness/release/dsh-0.1.0-rc.8"; tag `dsh-v0.1.0-rc.8`; `package.json` `0.1.0-rc.8`; 7 807 tracked files; default branch **`master`**, verified by `git ls-remote --symref origin HEAD` | 2026-08-20 | OK. `git fetch --unshallow` succeeded; `.git/shallow` absent; **12 940 commits** available |
| Vendored Cordis | `@deepseek-ai/cordis` **`4.0.1`, unchanged**; `vendor/cordis/src/fiber.ts` blob `38a3197e` **identical at both pins** | 2026-08-20 | OK (in-tree) |
| Vendored Cordis HMR plugin | `@deepseek-ai/cordis-plugin-hmr` `1.0.16` | 2026-08-20 | OK (in-tree); not previously pinned |
| `BENCHMARK.md` | 3 lines, 231 bytes, blob identical at both pins, **no numeric measurement** | 2026-08-20 | OK (in-tree) |
| `.agents/notes/archived/manifest.json` | `version: 1`, **429** sealed sha256 entries (426 at rc.7) | 2026-08-20 | OK |
| Licences | `LICENSE` MIT · `vendor/{cordis,cosmokit,group,hmr}/LICENSE` MIT · **`native/landlock-run/` + both `linux-{arm64,x64}` sub-packages BSD 3-Clause** | 2026-08-20 | OK. No `NOTICE`; `THIRD_PARTY_NOTICES.md` is generated |

**536 commits over about two days.** The repository holds exactly two tags, rc.7 and rc.8.

| Measure | rc.7 | rc.8 | Probe |
| --- | --- | --- | --- |
| Tracked files | 7 466 (control: reproduces the review) | 7 807 | `git ls-tree -r --name-only <pin> \| wc -l` |
| `package.json` under `packages/` | 226 (reproduces) | 233 | `... -- packages \| grep -c '/package\.json$'` |
| pnpm workspace members | 219 | 226 | same, `awk -F/ 'NF==4'` |
| Top-level `packages/` families | 54 | 55 | `cut -d/ -f2 \| sort -u` |

One family added, `experimental`; none removed. Nine workspace members added, two removed
(`client/schema-form`, `client/web-react`).

### 12.2 The work graph: the one framing clause that is refuted

`packages/experimental/agent-team` provides `ctx.agentTeams` — a Lead/teammate roster, a durable
peer mailbox, and **a shared task graph in the Lead session log**.

`task-graph.ts` is 69 lines of real dependency validation: `assertTaskGraphCandidate()` rejects
`missing`, `duplicate` and `cycle` violations, self-blocking included, with a DFS over `blockedBy`.
`task-board.ts` (297 lines) holds the board, with compare-and-set revisions
(`TEAM_TASK_STALE_REVISION`), tombstoned deletes and Lead-only cross-assignment. *A pending task
is ready only after every blocker completes.* The model-facing surface exposes it:
`tool-agent-team/src/index.ts` registers `team_task_create` with `blocked_by: array<string>`.

**Our prior zero was correct at its pin, not a probe error.** This is the falsification run
against our own finding, `*.ts` under `packages/`:

| Probe | rc.7 | rc.8 |
| --- | --- | --- |
| `subagent` (**positive control**) | 2 267 | 2 414 |
| `decompos` | 0 | **0** |
| `depends_on` | 0 | 0 |
| `blocked_by` | 0 | **7** |
| `blockedBy` | 0 | **36** |

**Three qualifications, and they are why no disposition moves.**

1. **Mounted in no shipped preset.** Config rows for it exist only in
   `examples/headless-agent/team.cordis.snapshot.yml:31-34`. Control on the same probe:
   `tool-todo` appears in three `apps/cli/config/agent-presets/*/agent.cordis.yml`.
2. **Declared out of the product.** `packages/experimental/AGENTS.md`: every package here
   "sets `private: true`, and omits `publishConfig`; the workspace constraints gate enforces
   these declarations and the dsh release family excludes this directory."
3. **Still no decomposition and still no landing.** The model creates every task by hand. The
   package's own limitations say it "provides no worktree, remote member, merge, or filesystem
   lock", and that write scopes are **advisory** — "Bash, formatters, code generators, and direct
   external writers can bypass filesystem version checks."

So the correct reading is that they built the **graph** and neither the **decomposition** above it
nor the **landing** below it. §7's comparison rows stand; only the absolute in §1 does not.

Landing and git integration are unchanged, and one line is worth keeping:
`workflow-worker-thread/src/runtime.ts:41` holds
`DEFERRED_AGENT_OPTIONS = new Set(['effort', 'isolation', 'agentType'])`, so a script passing
`isolation: 'worktree'` is refused loudly. That line predates rc.7. Worktree isolation is a
**named, deliberately refused option**, not an absence.

### 12.3 Q5 — "no privileged core" is false as an absolute, at three rows

`docs/architecture.md:13` claims "There is no privileged core to patch." `boot()` at
`packages/boot/app-boot/src/index.ts:757-786` mounts three things **before configuration is read**:

```ts
const ctx = new Context()                    // 1. the Cordis root context, not a plugin
ctx.provide('dshHomePath', dshHomePath)      // 2. a service provided in code, pre-config
await ctx.plugin(Loader)                     // 3. the Loader, mounted in code, pre-config
await mountRootInclude(ctx, absoluteConfigPath, ...)   // config first read here
```

Verified non-overridable, with controls: `id: loader` over every `*.yml`/`*.yaml` returns **0 hits,
exit 1**, while the same probe shape finds `id: hmr` 3 times and `id: tools` 5+ times.
`dshHomePath` is provided only at `:770`; config rows *consume* it
(`bundle/base/cordis.patch.yml:101`) and cannot replace it. `ctx.plugin(` across
`apps/cli/src/` and `packages/boot/app-boot/src/` returns exactly one hit. The privilege is a
**bootstrap, not an allowlist**.

**The positive control the question demanded, and it passes.**
`packages/bundle/headless/cordis.patch.yml` replaces a row's config by id, sets
`- id: hmr / disabled: true` — switching off **the HMR engine itself** — and inserts new rows. The
swappable population is 78 rows in `bundle/base` and 84 in `bundle/web-app`.

A dependent finding: `app-boot/tests/user-patches.spec.ts:374` shows `watchUserPatches` rejecting
with `'requires the Cordis HMR service'`. Because `headless` disables `hmr`, that path degrades to
a documented watch-only fallback. **A swappable row can still be load-bearing for a feature, just
not for boot.**

The accurate claim is *"every row in the composed configuration tree is replaceable from
configuration"*, which is well supported. The absolute is not, and the sentence was not weakened
at rc.8.

### 12.4 Q3 — the cost is still unmeasured, and `BENCHMARK.md` is not a benchmark

The file in full is 3 lines and 231 bytes, identical at both pins: it tells the reader to follow
the Python SDK guide and run the `jsonrpc-agent` minimal variant, using separate workspaces per
task. **It measures agent task capability, not runtime overhead, and states no number, baseline or
comparison.**

Repo-wide, with controls: markdown files matching `benchmark` = **3**, against `session` = 472, so
the probe works. `ops/sec`, `ns/op` and `p99` over markdown = **0 files each**. `overhead` matches
3 files, all about token *estimation* heuristics in `token-meter` and `compaction-basic`.

So the paper's §5.3 admission stands unchallenged by the product: **overhead is future work in both
artifacts.** Only the second half of §8's stated method survives — measure a turn under Code Mode
versus minimal — and that requires running `dsh`.

One incidental datum that explains a design choice: the two-tool `minimal` preset exists partly
*as* a benchmark harness, which is why mounting a tool in the global registry layer meant "a
two-tool benchmark preset really presented three".

### 12.5 Q6 — the archival trigger is a judgement, and there is deliberately no index

The trigger is **explicitly not mechanical**. `.agents/notes/README.md`, "Archiving and deletion":
archive when the shipped decision is complete and its rationale is unlikely to guide future work;
keep it while its "alternatives, ownership boundary, negative guarantee, durable or wire semantics,
security rule, or reintroduction condition" remains useful; never archive a *proposed* note, reject
it. And: use the calibrated workflow "rather than **word count, age, or a target quota**."

**What an agent loads at read time: no index, by decision.** "The active lifecycle tree is the
working inventory… **Do not add a centralized `INDEX.md`**", with a note owning the rationale.
Retrieval rests on three mechanical properties instead: the path *is* the metadata
(`{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`, closed class set); cross-references are relative
markdown links so they are "mechanically checkable and survive moves"; and archived notes leave the
retrieval surface — "Documentation gates skip archived sources, including their outbound links."

**The freeze is a real gate.** `scripts/archived-agent-notes.ts` computes `sha256:` content hashes
and rejects any sealed entry whose hash changed or that is missing; 429 files are sealed. Wired at
`lefthook.yml:15`, `:50` and `run-gates.ts:672`.

**The count, corrected.** Our review's figures are **file** counts over English/Chinese pairs, and
they reproduce exactly at rc.7 — so the probe was right and the unit label was wrong.

| | rc.7 files | rc.8 files | rc.7 notes | rc.8 notes |
| --- | --- | --- | --- | --- |
| `implemented/` | 1 030 ✓ | 1 090 | 515 | 545 |
| `archived/` | 285 ✓ | 287 | 142 | 143 |
| `proposed/` | 50 ✓ | 50 | 25 | 25 |
| `rejected/` | 22 ✓ | 22 | 11 | 11 |

Archival velocity is very low: **+30 implemented, +1 archived** over 536 commits. §5.5's finding
that the "every non-trivial change MUST add or update a note" rule is **not gated** still holds, on
a bounded search over `scripts/*.ts`, `lefthook.yml` and `run-gates.ts`.

### 12.6 Q2 — one test now swaps an agent-plane plugin under a live child

`packages/experimental/tool-agent-team/tests/tool-team.spec.ts:331`, titled *"removes and reinstalls
every scoped registration across plugin HMR without stopping the child"*, spawns a live continuable
teammate, disposes the plugin fiber, asserts the tools are gone from both scopes, asserts
`ctx.agents.get(childId)` is **the same live object**, then remounts and asserts the tools return in
both scopes.

**Dated, and the dating is the point.** HMR-titled test cases across both pins: **65 → 67**, four
added and two removed. This test's title is one of the four added, so it is genuinely new at rc.8
rather than something §8 missed.

**The qualification that keeps this from settling the thesis: 67 HMR-titled cases are
overwhelmingly the *revert* half.** The dominant shape is "unregisters everything on fiber disposal"
/ "drops the key when the fiber unloads". At rc.7 the only remount-shaped cases were on the client
and diagnostics planes. **None was an agent-plane plugin with a live session across the swap.**
`apps/web/tests/hmr-live.e2e.ts` is a genuine live-HMR end-to-end test, but it is the browser plane
and it predates rc.7.

**Unestablished:** the runtime half. No `dsh` was run, no install, no test executed. *A test that
exists and a test that passes are different claims.*

### 12.7 Q8 — the round choice was about attribution, and the token half has no consumer

`maxGoalRounds` did **not** choose rounds over tokens as a budgeting philosophy. It chose which
events may consume a budget. From
`.agents/notes/implemented/feature/2026-07-19-persisted-same-session-goal-domain.md`:

- The motive is mis-attribution, not cost: "Treating every session turn as progress also charges
  unrelated human messages against an automatic-work budget" [`:9`].
- The rejected alternative is an attribution alternative: counting all session turns as goal rounds
  was rejected "because one session can contain human clarification, inspection, and unrelated
  work; only goal-attributed continuation turns consume this budget" [`:46`].
- The counter is **derived by validated replay, not incremented by the spender**: rounds advance
  only from "positive sequential admitted `user/message` source numbers for the current active
  revision", and "a malformed current-format record fails replay rather than being ignored or
  repaired" [`:23`].
- The unit conversion is delegated: "policy consumers map round, token, currency, time, and
  provider limits to blocked reasons" [`:55`]. `defaultMaxGoalRounds` is a validated setting
  defaulting to **256** [`:15`].

**The falsification that matters to us: the token half is dark code.** Non-test `ctx.goals.block()`
call sites yield exactly four codes — `round-limit`, `queue-failed`, `prompt-rejected`,
`model-reported` — and **none is a token, currency, wall-time or provider-quota mapping**.
`ctx.goals|dsh-goal` over `packages/llm`, `packages/compaction`, `packages/spill` non-test returns
**0, exit 1**, against a positive control of 6 non-test files holding `ctx.goals`. The harness *can*
recognise an exhausted quota — `QUOTA_EXCEEDED_CODE` at `llm/src/error.ts:94`, consumed by two
adapters — and nothing routes it to a goal block.

**Two separable lessons, and only one is adopt-shaped.**

- **Adopt.** A budget counter should advance only on units attributable to the automatic work, and
  should be **derived from the durable log by validated replay** — sequentiality checked, a
  malformed record failing rather than being repaired — rather than incremented by whoever spends.
  That is a second derivation over the same data, and it is the direct analogue of §32's
  independent-fold invariant.
- **Refuse.** "Policy consumers map … token … limits" is a contract with **no consumer that can
  refuse**. This is the anti-pattern §33 names and the ground §11.3 refused P3 on. It does not tell
  us to meter in rounds; it tells us that *declaring* a deferral is not *building* the mapping.

**It does not touch our forecast defect.** `dsh` does not forecast cost at all — it caps a count.
Nothing here would have caught a 3-to-11x under-forecast, because nothing here predicts.

### 12.8 Disposition deltas — none moves

| # | Proposal | rc.8 verdict | Why |
| --- | --- | --- | --- |
| P1 | Effect-inverse or undo layer over git or the filesystem | **confirmed, strengthened** | The whole evidence base is byte-identical: `fsio.ts`, `win32.ts`, `fs-local/src/index.ts` same blob hashes; **`fs-local` holds 0 `.effect(` sites including tests**; `win32.ts:20` still `backup: null`; the 91 mutation-syscall occurrences reproduce exactly, 91 → 91. Across 536 commits and 7 new packages, **not one filesystem inverse was added** |
| P2 | Cordis-style dynamic component runtime in Python | **confirmed** | `vendor/` had **0 commits** in range against a control of 418 for `packages/`. §12.3 adds support: even the industrial application needs three privileged bootstrap rows outside the dynamic tree |
| P3 | A `before`-content field on the change-summary artifact | **confirmed, corroborated** | `fs/src/types.ts` byte-identical, so `FsWriteOutcome.before` stays presentation-only. §12.7 supplies a second instance of the same anti-pattern **inside `dsh`** — which raises confidence that the refusal is about contract shape, not about our tooling |
| P4 | "No privileged core", every row replaceable from configuration | **confirmed, premise now measured** | §11.3 refused it partly because the premise was unrun. It is now run and **false as an absolute** (§12.3). The narrow adoptable half survives and is better supported: `--dump-config` still exists at `apps/cli/src/args.ts:32/102`, now beside a `--dump-default-config` |
| P5 | A declared token cost on a catalog source, and a validated exemption table | **confirmed, adopt** | Both gates still wired (`run-gates.ts:668`, `:683`). `verify-package-readme-limitations.ts` is **byte-identical**, still failing an exemption entry naming no scanned package or carrying a blank justification. Re-measured: `Model Experience` occurs **0** times in `packages/**/*.{ts,tsx}` against `systemPrompt` at **503**. Still author-facing, still zero model tokens |

Effect sites across the three named packages went **5 → 7**, and both additions are
`tool-pwsh-persistent` mirroring `tool-bash-persistent` line for line. **Restore 4, compensate 3,
no-op 0, and still zero on a filesystem mutation path.** Tree-wide `.effect(` non-test reproduces
the review's 203 at rc.7 and reads 210 at rc.8.

### 12.9 What this pass did not establish

- **Any runtime behaviour.** No `dsh` executed, no install, no test, no gate run. Everything above
  is git objects, source, configuration and committed documentation. Existence is established;
  outcome is not. §9's limit stands, now for the second pass in a row.
- **Whether the rc.8 suite passes**, and whether the 429-entry archive manifest is enforced in CI
  as opposed to declared in `run-gates.ts` and `lefthook.yml`.
- **Whether `experimental/agent-team` is used by anyone.** Private, release-excluded, mounted in one
  examples snapshot. Internal use is not observable from the repository.
- **`packages/e2b`.** Still unread. `fs-e2b` exists and the effect-site probe was **not** re-run over
  it, so "filesystem inverses outside the three named packages" stays open — the same hole §11.4
  recorded.
- **Whether a per-PR Agent-Note-presence check exists** outside `scripts/*.ts`, `lefthook.yml` and
  `run-gates.ts`. A check inside an inline GitHub Action, a bot or branch protection would not be
  caught. **Not found, not absent.**
- **What the other 418 `packages/` commits did.** The diff was driven by a fixed question list; the
  seven new packages outside `experimental/` were identified by name only.
- **The paper.** Not re-fetched, for the second pass. Nothing above depends on it except by citation
  of §3.
- **The Chinese-language corpus.** English side only, again.

**Two probes failed their control and are recorded as failures, not findings.** `rg -ril 'hmr'`
parses as `-r il` and silently rewrote its own output — the identical trap §10's method note already
records, hit a second time, which is itself evidence the note belongs in a gate rather than a
document. And a "files holding both `fiber.dispose()` and `ctx.plugin(`" probe returned 258 files at
rc.8 against about 250 at rc.7; it cannot separate co-occurrence in one test body from
co-occurrence in a file, so it was discarded.

## 13. Re-measured at `dsh-v0.1.1-rc.2` — verified 2026-08-22

`basicly-6oa3mt`. Architecture rule D-36 says an absorbed *Because* inherits a measurement that can
expire, so before `basicly-e2mz.46` carries anything from this document into `architecture.md`, every
number in it is re-run. This section is that run. It changes no finding above; it dates them.

**Read this first: the pin moved.** §12 was established at `dsh-v0.1.0-rc.8`. That is no longer the
head of the repository, and §12.1's sentence *"The repository holds exactly two tags, rc.7 and rc.8"*
is now false.

| | Old pin (§12.1) | New pin | Command |
| --- | --- | --- | --- |
| Tag | `dsh-v0.1.0-rc.8` | **`dsh-v0.1.1-rc.2`** (intermediate: `dsh-v0.1.1-rc.1`) | `git ls-remote --symref origin HEAD` |
| Commit | `141eb6fef8`, 2026-08-19T23:11:50+08:00 | **`b150a551b8`**, 2026-08-21T20:03:37+08:00 | `git log -1 --format=%cI <tag>` |
| `package.json` | `0.1.0-rc.8` | **`0.1.1-rc.2`** | `git show <tag>:package.json` |
| Tags in repo | 2 | **4** | `git tag \| wc -l` |
| Distance | — | **207 commits** over about two days | `git rev-list --count dsh-v0.1.0-rc.8..dsh-v0.1.1-rc.2` |

**What changed between the pins, at the level this document measures.** The minor version moved
`0.1.0` → `0.1.1`. No `packages/` family was added or removed (55 at both). One workspace member was
added. The vendored Cordis is untouched — same version, same `fiber.ts` blob — so every §11 finding
that rests on `vendor/cordis` is carried unchanged by construction, not by re-reading. `BENCHMARK.md`
is byte-identical across all three tags.

### 13.1 The instrument, recovered

This document never recorded the commands behind its `rg` figures, which made every one of them
unverifiable on its face. They are recovered here, because the recovery is what makes the rest of
this section auditable — and the recovered instrument reproduces four of the document's own control
figures **exactly**, which is the evidence that it is the right one:

```sh
# §5.2 corpus. Note -c counts MATCHING LINES, not occurrences: `rg -o | wc -l` gives 2383, not 3385.
rg -i --no-ignore-vcs -g 'packages/**/*.ts' -c 'subagent' . | awk -F: '{s+=$NF;f++} END{print s" / "f}'
# §5.6 corpus adds apps/:            -g 'packages/**/*.ts' -g 'apps/**/*.ts'
# §11.1 corpus excludes tests:       -g '!**/tests/**' -g '!**/*.test.ts'
```

| Control, at the pin it was recorded against | Recorded | Re-run 2026-08-22 | |
| --- | --- | --- | --- |
| `subagent`, §5.2, rc.7 | 3 385 hits / 226 files | **3 385 / 226** | reproduces |
| `session`, §5.6, rc.7 | 30 624 hits / 1 029 files | **30 624 / 1 029** | reproduces |
| `rollback`, §11.1, rc.7 | 126 | **126** | reproduces |
| `.effect(`, §11.1, rc.7 | 203 sites | **203** | reproduces; "tree-wide" is loose — the corpus is `packages/**/*.ts` less tests |

Trees are read with `git archive <tag> | tar -x` into a scratch directory, so no clone working tree
is disturbed and all three tags are measured by one instrument.

### 13.2 The evidence table

Every row verified **2026-08-22**. `=` means the figure is unchanged from the recorded one; a changed
figure carries the command that produced it. `✓` marks a recorded figure this pass reproduced at its
own pin — the control for every delta beside it.

| Claim | § | Recorded | rc.8 re-run | **0.1.1-rc.2** | Command |
| --- | --- | --- | --- | --- | --- |
| Tracked files | 12.1 | 7 466 (rc.7) / 7 807 (rc.8) | ✓ both | **7 903** | `git ls-tree -r --name-only <tag> \| wc -l` |
| `package.json` under `packages/` | 12.1 | 226 / 233 | ✓ both | **234** | `git ls-tree -r --name-only <tag> -- packages \| grep -c '/package\.json$'` |
| pnpm workspace members | 12.1 | 219 / 226 | ✓ both | **227** | same, `\| awk -F/ 'NF==4' \| wc -l` |
| Top-level `packages/` families | 12.1 | 54 / 55 | ✓ both | 55 `=` | `... \| cut -d/ -f2 \| sort -u \| wc -l` |
| Commits reachable | 12.1 | 12 940 | ✓ | **13 147** | `git rev-list --count <tag>` |
| Vendored Cordis version | 12.1 | `4.0.1` | ✓ | `4.0.1` `=` | `git show <tag>:vendor/cordis/package.json` |
| `fiber.ts` blob | 12.1 | `38a3197e` | ✓ | `38a3197e` `=` | `git rev-parse --short=8 <tag>:vendor/cordis/src/fiber.ts` |
| `BENCHMARK.md` | 12.1/12.4 | 3 lines, 231 bytes | ✓ | 3 lines, 231 bytes, blob `d5e9dc78` `=` | `git cat-file -s <tag>:BENCHMARK.md` |
| Sealed manifest entries | 12.1/12.5 | 426 (rc.7) / 429 (rc.8) | ✓ both | 429 `=` | `git show <tag>:.agents/notes/archived/manifest.json \| grep -c sha256` |
| `.agents/notes/archived/` files | 12.5 | 285 / 287 | ✓ both | 287 `=` | `find .agents/notes/archived -name '*.md' \| wc -l` |
| `.agents/notes/proposed/` files | 12.5 | 50 / 50 | ✓ both | **52** | same, `proposed` |
| `.agents/notes/rejected/` files | 12.5 | 22 / 22 | ✓ both | 22 `=` | same, `rejected` |
| `.agents/notes/implemented/` files | 12.5 | 1 030 / 1 090 | 1 029 / 1 089 — **off by one at both pins**, see 13.4 | **1 119** | same, `implemented` |
| `scripts/run-gates.ts` | 5.5/7 | 909 lines (rc.7) | ✓ 909; **967 at rc.8** | **968** | `wc -l < scripts/run-gates.ts` |
| `standard/agent.cordis.yml` | 5.7 | 251 lines (rc.7) | ✓ 251; 252 at rc.8 | 252 | `wc -l < apps/cli/config/agent-presets/standard/agent.cordis.yml` |
| `packages/goal/goal/src/fold.ts` | 5.5 | 349 lines | ✓ | 349 `=` | `wc -l < packages/goal/goal/src/fold.ts` |
| `docs/cordis-primer.md` | 3.4/10 | 44 lines | ✓ | 44 `=` | `wc -l < docs/cordis-primer.md` |
| `task-graph.ts` | 12.2 | 69 lines | ✓ | 69 `=` | `wc -l < packages/experimental/agent-team/src/task-graph.ts` |
| `task-board.ts` | 12.2 | 297 lines | ✓ | 297 `=` | `wc -l < packages/experimental/agent-team/src/task-board.ts` |
| CI workflows | 7 | 14 | **15 at rc.7** — see 13.4 | **18** | `ls -1 .github/workflows \| wc -l` |
| `subagent` (control) | 5.2 | 3 385 / 226 | ✓ | **3 575 / 235** | 13.1 |
| `decompos` | 5.2 | 0 | ✓ 0 | **0** — §12.2's heading still stands | 13.1 |
| `depends_on` | 5.2 | 0 | ✓ 0 | 0 `=` | 13.1 |
| `blockedBy` | 5.2/12.2 | 0 (rc.7) → 36 (rc.8) | ✓ both | 36 `=` | 13.1 |
| `session` (control) | 5.6 | 30 624 / 1 029 | ✓ | **31 695 / 1 083** | 13.1 |
| `rollback` (control) | 11.1 | 126 | ✓ | **147** | 13.1, tests excluded |
| `revertible` | 11.1 | 0 | ✓ 0 | **0** against a live 147 control | 13.1, tests excluded |
| `.effect(` sites | 11.1 | 203 | ✓ | **209** | `rg --no-ignore-vcs -g 'packages/**/*.ts' -g '!**/tests/**' -g '!**/*.test.ts' -oF '.effect(' . \| wc -l` |
| `fs-local` `.effect(` sites | 11.1/12.8 | 0 | ✓ 0 | **0** — P1 holds at a third pin | `rg --no-ignore-vcs -oF '.effect(' packages/fs/fs-local \| wc -l` |
| `win32.ts:20` `backup: null` | 11.1/12.8 | present | ✓ | present `=`, still line 20 | `rg -n 'backup: null' packages/fs/fs-local/src/win32.ts` |
| `id: loader` in `*.yml`/`*.yaml` | 12.3 | 0, control `id: hmr` = 3 | ✓ 0 / 3 | 0 / 3 `=` | `rg -g '*.yml' -g '*.yaml' -c 'id: loader' .` |
| `ctx.plugin(` in `apps/cli/src` + `app-boot/src` | 12.3 | 1 | ✓ | 1 `=` | `rg -oF 'ctx.plugin(' apps/cli/src packages/boot/app-boot/src \| wc -l` |
| Swappable rows, `bundle/base` | 12.3 | 78 | ✓ | 78 `=` | `rg -o '^\s*- id: ' packages/bundle/base \| wc -l` |
| Swappable rows, `bundle/web-app` | 12.3 | 84 | ✓ | 84 `=` | same, `web-app` |
| `docs/architecture.md:13` "no privileged core" | 12.3 | present, unweakened at rc.8 | ✓ | **still present, still unweakened** | `sed -n '13p' docs/architecture.md` |
| Markdown files matching `benchmark` | 12.4 | 3 | ✓ | 3 `=` | `rg -li -g '*.md' 'benchmark' . \| wc -l` |
| Markdown files matching `ops/sec`, `ns/op`, `p99` | 12.4 | 0 each | ✓ 0 each | **0 each**, control `benchmark`=3 | `rg -li -g '*.md' -F '<term>' . \| wc -l` |
| Markdown files matching `overhead` | 12.4 | 3 | **2** at rc.7 and rc.8 — see 13.4 | **2** | `rg -li -g '*.md' 'overhead' . \| wc -l` |
| `paper.pdf` size | 10 | 2 140 840 bytes, HTTP 200 | — | **2 140 840, HTTP 200** `=` | `curl -sI https://raw.githubusercontent.com/cordiverse/paper/main/paper.pdf` |
| `basicly` `architecture.md` | 10 | 4 485 lines at `ee7d263` | — | **4 808 lines** on `main` | `wc -l < docs/architecture/architecture.md` |

**The one finding this pass strengthens.** §12.5 called archival velocity "very low" at +30 implemented
/ +1 archived over 536 commits. Over the next 207 commits it is **+30 implemented / +0 archived / +2
proposed**. The trigger being a judgement rather than a gate (§12.5) continues to produce a monotonic
`implemented/` and a static `archived/`.

### 13.3 Droppable — claims that only restate shipped `basicly` code

Marked so `basicly-e2mz.46` **drops** rather than moves them.

- **§7's comparison rows that describe our own gate stack** — "git hooks (lefthook) + a topologically
  sorted gate DAG + CI workflows" against "four strictly-linear layers … [arch §36.1, L3296-3310]".
  The right-hand column is `architecture.md` quoting itself. Absorbing it into `architecture.md`
  writes the file's own §36.1 back into it at a second location, where the two copies can drift. Keep
  the harness column, drop the `basicly` column and cite §36.1.
- **§7's determinism row**, same shape: *"two builds on identical sources produce byte-identical
  output" [arch §11, L558-560]* is a quotation of the target document.
- **§10's `basicly` architecture provenance row.** It pins our own file at a commit that is 323 lines
  stale (13.2). Inside `architecture.md` a self-pin is not evidence, it is a stale mirror.

**Both line citations are already stale, measured 2026-08-22** — which is the concrete cost of a
self-pin and the reason these rows are droppable rather than merely redundant. `architecture.md` has
grown 4 485 → 4 808 lines since §10 pinned it, and `sed -n '558,560p' docs/architecture/architecture.md`
now returns three lines of a mermaid diagram; the determinism sentence has moved to lines **365** and
**568**. `sed -n '3296,3299p'` returns prose about `docs_claim_layers.py`, not the four-layer gate
stack. Absorbed as written, each row would land inside `architecture.md` pointing at the wrong part
of `architecture.md`.

### 13.4 Not re-measurable, and why — stated rather than restated

- **`overhead` matches 3 markdown files (§12.4).** Re-run gives **2**, at all three tags, and §12.4's
  own prose names only two packages — `token-meter` and `compaction-basic` — which are exactly the two
  files matched. The figure is a transcription slip, not a change: there is no pin at which 3 was
  true. The finding it supports (both hits are about token *estimation*, not runtime overhead) is
  unaffected.
- **`implemented/` = 1 030 files (§12.5).** Re-run gives 1 029 at rc.7 and 1 089 at rc.8: a constant
  offset of one at both pins, so it is an instrument difference and not drift. §12.5 does not record
  its filter, and `find … -name '*.md'` is one file short of whatever it used. The pairs conclusion
  (515 notes) is unaffected by which of the two counts is right; the odd file is why 1 029 is not even.
- **14 CI workflows (§7).** `.github/workflows` holds **15** entries at rc.7, the pin §7 was written
  against. §7 does not record its filter, so the one excluded file cannot be identified. The claim
  cannot be reproduced and is not restated; the current count is 18.
- **91 raw filesystem syscall occurrences in `packages/fs` outside tests (§11.1).** §11.1 does not
  name the syscall set, and a substituted ten-name set gives 45. **Unverifiable**: the probe is not
  recoverable from the text. §12.8's "91 → 91 reproduces exactly" was run by the author against their
  own probe and stands on that basis, not on this one.
- **1 087 files import cordis (§6.2).** §6.2 does not record its import pattern;
  `from ['"](@deepseek-ai/)?cordis` gives 1 116 at rc.7. **Unverifiable** for the same reason. The
  neighbouring "226 packages" in that row *does* reproduce (13.2).
- **`fiber.dispose()` + `ctx.plugin(` = 258 files (§12.9).** Already discarded by §12.9 as a probe
  that cannot separate co-occurrence in a test body from co-occurrence in a file. It stays discarded;
  re-running it would launder a failed probe into a datum.
- **The paper's page count, `/CreationDate` and Typst version (§10).** Only the byte length and HTTP
  status were re-checked, and both reproduce. The PDF was not re-parsed.
- **Everything requiring `dsh` to run (§12.4's Q3).** Still unmeasured, and this pass did not run it.
