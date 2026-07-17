# basicly vs. MS AI Foundry production-agent principles — spike (basicly-zv48)

> **Status:** research spike, not implementation. This document is the written
> deliverable for `basicly-zv48` and its seven child dimensions. It maps, for each
> production-agent principle drawn from Microsoft AI Foundry engineers, four things:
> **(a)** what the underlying code agent (Claude Code / Codex / Copilot) already
> provides, **(b)** what basicly currently does, **(c)** the gap, and **(d)** the
> concrete improvements basicly should own. Accepted gaps are filed as prioritized
> `br` implementation beads, linked per section. The final section rolls the seven
> dimensions into a harness-maturity view.

## Framing

**The harness matters as much as the model.** A production agent is not just a
model — it is the runtime, tools, context retrieval, identity, guardrails,
evaluators, and deployment pipeline around it. Models are *swappable*, and each has
different properties the harness must adjust to (unlike interchangeable database
versions). basicly is a custom harness built **on top of** code agents that already
supply some of these layers; the spike tests where basicly's design is sound, where
it delegates correctly, and where it has a real gap to close.

Two principles anchor the identity/guardrail split:

- **Identity bounds what an agent is allowed to reach.**
- **Guardrails bound what flows through it once it acts.**

The recurring analytical move is the **boundary question**: for each layer, is it
the code agent's job (the token-level runtime), basicly's job (the workflow runtime
and projected catalog), or the OS/trust-boundary's job? A gap is only "basicly's to
own" when it is projectable, deterministic-where-possible, and not already covered
by the layer beneath.

---

## Dimension 1 — Identity & audit trail

*Child: `basicly-zv48.1`. Principle: without identity controls an agent runs as a
shared system principal with no audit trail; agents need their own identities, role
assignments, and tamper-evident audit trails, bounded like a misbehaving employee.*

**(a) What the code agent provides.** Claude Code / Codex / Copilot all run *as the
invoking human* — headless dispatch inherits the user's OS environment and git
identity, so their commits carry the developer's `user.name`/`user.email`. Each keeps
its own session transcript (Claude Code's session JSONL, etc.) and fires hook events,
and Claude Code can add a `Co-Authored-By` trailer by convention — but none presents a
*distinct, scoped machine principal* to the harness or a tamper-evident audit at the
repo level. At the agent layer, identity is "the user, in an agent session," not "an
agent with its own bounded identity."

**(b) What basicly currently does.**

- **Guards the *absence* of identity, not per-agent identity.** `identity-guard.py`
  blocks a commit when `user.name`/`user.email` is unset or a hostname fallback
  (`.local`/`(none)`), with an optional strict allow-email regex
  `basicly.identityAllowEmail` (`.basicly/core/hooks/identity-guard.py:44-74`).
  `.scripts/setup_git_identity.py` wires per-*remote* conditional includes for humans
  — neither concept is per-agent.
- **Links every commit to a tracked issue.** `commit-msg.py` + `beads-commit-msg.py`
  require Conventional Commits + a trailing bead id that must exist in
  `.beads/issues.jsonl` (§10) — the "what/why" half of the audit trail.
- **Engine-owned tracker commits at three points**, all as `chore(beads): …` under the
  *same human git identity*: provisioning claim and fan-out claim
  (`src/basicly/loop.py:196-200`, `:304-306`), landing roll-up and ship close
  (`merge.commit_tracker_state`, `src/basicly/merge.py:89-107`; `loop.py:164-173`).
- **`external_ref`** stashes the worktree/branch binding on the issue
  (`loop.py:321-323`) — resumability, not attribution.
- **The runner drops the one attribution fact it holds.** `runner.run` invokes the
  agent CLI with inherited `os.environ` and *no* identity/actor override
  (`src/basicly/runner.py:147-170`); the loop plumbs no `--assignee`/`--actor` to `br`.
  basicly *knows which runner it dispatched* but never records it on the commit, the
  bead, or the gate.
- **`tool-usage.py`** is aggregate, token-free PostToolUse telemetry into
  `.basicly/usage/` — not identity-attributed.
- Net: the audit trail is git history (who/when via git identity) + beads linkage
  (what/why) + `git diff`/`blame` (§3.8) — all collapsed onto **one human principal**.

**(c) The gap.**

- **No per-agent identity.** Human, Claude, Codex, and Copilot actions are
  indistinguishable in history — the runner name is known at dispatch and thrown away.
- **No scoped roles.** No portable notion of what an agent-principal may reach; the
  `.claude/settings.json` deny-list is a Claude-only tool gate (dimension 2), not a role.
- **No tamper-evidence.** Commit signing isn't required, git history is rewritable, and
  `.beads/issues.jsonl` is hand-editable — nothing is append-only or verifiable.
- **Runner→attribution seam missing.** The cheapest win (stamp the dispatched agent) is
  unbuilt even though basicly owns the fact.

**(d) Recommended improvements basicly should own.**

1. **Attribute agent actions.** At landing, stamp the dispatched runner (agent + model
   when the CLI exposes it) as a commit trailer / `Co-Authored-By` and/or a `br` comment,
   so history distinguishes human vs. which agent. Cheap, high value, fact already in hand.
2. **Record runner + outcome on the gate ledger.** `br gate report` already stores
   verdicts; add the runner name so each verify result ties to the agent that produced it.
3. **Optional per-agent bot identity.** Let the headless runner dispatch under a configured
   `GIT_AUTHOR_*/GIT_COMMITTER_*` identity per agent, validated by `identity-guard`'s
   allow-email — opt-in, config-driven. Turns "misbehaving employee" into an *attributable*
   one.
4. **Document + optionally gate tamper-evidence.** Establish the trust model: an optional
   commit-signing gate for harness commits, and treat the beads JSONL git history as the
   append-only ledger.

Role *enforcement* (what a principal may reach) belongs to the code agent / OS layer
(Claude deny-list, sandbox) — mostly out of scope here; the projectable slice (a per-agent
tool-allow policy) folds into dimension 2.

**Accepted gaps → beads:** see the "Filed beads" table at the end. One item is wontfix
for basicly: portable per-agent *role enforcement* — enforcement lives at the agent/OS
trust boundary, not a git-file harness; basicly can only project policy, which dimension 2
covers.

---

## Dimension 2 — Guardrails

*Child: `basicly-zv48.2`. Principle: identity bounds what an agent can reach;
guardrails bound what flows through it once it acts — stop it confidently doing/saying
what it should not (output/content, runtime tool-call allow/deny, egress, secrets).*

**(a) What the code agent provides.** The runtime-guardrail surface largely lives *at
the code-agent layer*, not basicly. **Claude Code**: `permissions.allow`/`deny`
(tool-call allow/deny at runtime), permission modes
(`default`/`acceptEdits`/`plan`/`bypassPermissions`), and `PreToolUse`/`PostToolUse`
hooks that can block a call (exit 2). **Codex**: approval modes + a sandbox
(`read-only`/`workspace-write`) with **network disabled by default** — egress control at
the runtime. **Copilot**: tool allow/deny config + managed `.github/hooks/*.json`. So
tool-call allow/deny and egress sandboxing are enforceable at the agent layer; the
harness's job is to **project sane, consistent defaults across all three** and add what
none of them owns (content/secret guardrails tied to *basicly's* actions).

**(b) What basicly currently does.**

- **Deterministic git-stage gates** (`.basicly/core/hooks/hooks.yaml:8-52`), all
  blocking: `identity-guard`, `pre-commit`→fast `[[verify.checks]]` and
  `pre-push`→full checks (`pre-commit.py:20`, `pre-push.py:20`, config-driven via
  `check_runner.py:70-113`), `catalog-lint`, `commit-msg`, `beads-commit-msg`.
- **`protect-generated`** — the *only distributed runtime tool-call guardrail*: a Claude
  `PreToolUse` hook on `Edit|Write|MultiEdit|NotebookEdit` that blocks edits to
  basicly-generated files (exit 2, `protect-generated.py:76-85`). Explicitly **fails
  open** and self-describes as *"a guardrail against accidents, not a security boundary."*
- **`.claude/settings.json` deny-list** — blocks `rm -rf`, `git push
  --force/--no-verify/--no-gpg-sign`, `git commit --no-verify/--no-gpg-sign`, `git reset
  --hard`, `git clean -f`, `filter-branch/repo`, and `.env` read/edit
  (`.claude/settings.json:14-40`). **Critical finding: this is repo-local, not
  projected** — `.claude/settings.json` is git-tracked but *absent from the generated
  manifest*; only the `hooks` block is managed by `hooks-build`. Consumers who `basicly
  install` get the `protect-generated`/`tool-usage` agent hooks but **not** this deny-list.
- **`runner` dry-run / handoff** (`runner.py:147-170`): `dry-run` prints exact argv
  without executing; the `manual` handoff never shells out. This is *safety-by-not-guessing*
  on invocation, not on what flows through the dispatched agent — captured stdout/stderr is
  never inspected, filtered, or egress-limited.
- **`tool-usage`** is telemetry only (PostToolUse counter), non-blocking — not a guardrail.

**(c) The gap.**

- **No output/content guardrails.** Nothing inspects what the agent produces (commit
  bodies, generated content, runner stdout) for policy violations before it lands.
- **No secret redaction/scanning.** `rg` confirms zero `redact|secret-scan|detect-secrets`
  logic in `src/basicly` or the hooks; no gate scans commits/diffs/runner output for leaked
  credentials.
- **No egress/network control owned by basicly.** Egress bounding exists only if the
  underlying agent's sandbox provides it (Codex yes; Claude/Copilot as configured) — basicly
  neither projects nor requires it.
- **Runtime tool-call allow/deny is not distributed.** The strong deny-list is a dogfooded
  repo-local example, not part of the catalog; a consumer inherits none of it.
  `protect-generated` is the only projected runtime block, and it fails open.

**(d) Recommended improvements basicly should own.**

1. **Project a baseline permissions guardrail across all three agents** — a
   catalog-managed deny-list (destructive git, `--no-verify`/`--no-gpg-sign`, `.env`
   access) rendered per-target (Claude `permissions.deny`, Copilot tool-deny, Codex
   sandbox/approval defaults), managed the way hooks are — so consumers actually *get* the
   guardrail, not just the dogfooding repo.
2. **A secret-scanning gate** in the deterministic layer (a `[[verify.checks]]`-style
   pre-commit/pre-push check, e.g. gitleaks/detect-secrets wrapper) — a blocking gate on
   committed content.
3. **A runner output guardrail seam** — inspect/redact runner stdout/stderr for secrets
   before it's surfaced or logged, and optionally an egress-policy assertion (require a
   network-restricted sandbox for headless runs).
4. **Escalate `protect-generated` where it's a real boundary** — keep fail-open for
   accidents but pair it with a git-stage manifest check so a bypass at tool time is still
   caught deterministically at commit.

**Accepted gaps → beads:** all four are genuine, non-overlapping gaps (no wontfix). This
is basicly's highest-signal dimension: its guardrail posture is mechanical-gate-heavy and
content-guardrail-absent, and the strongest runtime control it has *isn't even shipped to
consumers*.

---

## Dimension 3 — Observability & fleet governance

*Child: `basicly-zv48.3`. Principle: a single view of every agent across every project —
health scoring, token usage, latency, drift detection, cross-project rollups; without it
regressions are invisible and cost uncontrolled.*

**(a) What the code agent provides.** Each code agent accounts for its *own* run in
isolation. Claude Code tracks per-session token counts and cost (`/cost`, the ccusage
ecosystem, OpenTelemetry export of tokens/cost/tool-decision events); Codex and Copilot
keep their own session/usage logs and billing dashboards. So token, latency, and per-turn
cost telemetry **does exist — but at the agent-session layer**, siloed per agent, keyed by
the agent's own session id, and not tied to basicly's unit of work (a bead / a loop track /
a worktree). None of the three offers a cross-agent, cross-project fleet view or
health/drift scoring; that aggregation is definitionally outside a single code-agent's scope.

**(b) What basicly currently does.**

- **`basicly status --json`** — a single-repo, point-in-time, read-only snapshot
  (`_status_report`, `src/basicly/cli.py:422-524`): engine vs installed catalog version,
  config **drift** (stale outputs, manifest staleness, core hand-edit drift via provenance
  hashes), per-manager hook state, technology selection, overlay counts. Never writes, always
  exits 0. Its `--json` schema is explicitly labelled "stable schema, **for fleet loops**" —
  basicly's design intent is that an *external* caller loops `status --json` over each housed
  repo; **basicly itself does no cross-repo aggregation**.
- **Tool-usage telemetry** — the `tool-usage` PostToolUse hook
  (`.basicly/core/hooks/tool-usage.py:158-213`) extracts the head token of every shell
  pipeline segment plus `skill:<name>` entries into per-entry `{count, last_used}` counters
  in the self-ignored `.basicly/usage/tool-usage.json`. `basicly usage report` joins those
  counts against the skill catalog to name never-used skills. **Token-free by design**
  (pipeline-head *counts*, not LLM tokens/latency/cost); its purpose is culling idle
  tools/skills, not agent health.
- **`br` substrate** — gate results, claims, dependency graph per repo; `br coordination`/
  `--stale-claim-hours` surface swarm/stale-claim state. Closest thing to per-work-unit
  health, but per-repo and about *tracker* state, not agent behavior/cost.

**(c) The gap.**

1. **No per-agent/per-run token, latency, or cost metric anywhere in basicly** — grep for
   `latency|duration|elapsed|tokens?_used|cost|health.?score` across `src/basicly/*.py`
   returns only the three "for fleet loops" comment strings, no implementation. The runner
   dispatches an agent headless and captures exit code + output, but records **no** timing or
   token accounting against the bead/loop it ran.
2. **"Drift" in basicly means config drift, not behavioral/regression drift** — stale
   outputs/manifest/core hand-edits at a single instant. No time-series, no baseline, no
   detection of an agent *getting worse* (regressions invisible — exactly the principle's
   warning).
3. **No fleet rollup exists as a basicly capability** — the `dev` cross-project workspace
   houses many repos, but no command aggregates across them. There's a schema contract but no
   aggregator, no health scoring, no cross-project view.
4. **The two telemetry sources are disjoint and neither is keyed to a work unit** — tool-usage
   counts are global-per-repo; status is config-state. Nothing ties "agent X ran bead Y, cost
   N tokens, took T, passed/failed gates" together.

**(d) Recommended improvements basicly should own.** basicly should own the *aggregation and
work-unit correlation* layer — the code agents own raw per-session numbers; basicly is the
only thing that knows the bead/loop/worktree and spans repos.

- **Runner run-record**: capture wall-clock duration + exit status per dispatched run and,
  where the agent exposes it (Claude Code OTEL / session-usage JSON), token+cost, writing a
  per-run record keyed by bead id into `.basicly/usage/` (same self-ignored, atomic-write
  pattern as tool-usage). This is the missing correlation and the foundation for everything
  else.
- **`basicly fleet` (or `status --fleet`) rollup**: aggregate `status --json` + per-run
  records across housed repos into one cross-project view. Read-only, JSON-first, exit-0 —
  same contract as `status`.
- **Health scoring + drift-over-time**: derive a per-repo/per-agent health signal from gate
  pass/fail rates and rework counts already in `br` + the new run records; flag regressions
  against a rolling baseline.
- **Secret hygiene**: any token/cost/output the runner persists must redact — coordinate with
  dimension 2 so run-records never log secrets or full prompts.

**Accepted gaps → beads.** Wontfix / not-basicly: **re-implementing raw per-session token
metering** — the code agents already emit this (Claude OTEL/ccusage); basicly should consume
and aggregate, not re-meter at the token level. **Cross-dimension note:** the runner
run-record overlaps the runtime-boundary seam (D5, structured run result beyond exit code)
and identity/audit (D1, attributing a run to an agent) — consolidated in the synthesis.

---

## Dimension 4 — Harness-as-first-class / model swappability & inference layer

*Child: `basicly-zv48.4`. Principle: the harness matters as much as the model; the
inference layer is one interface to swappable models, and each model has different
properties the harness must adjust to (unlike interchangeable DB versions).*

**(a) What the code agent provides.** Claude Code / Codex / Copilot each *are* the
inference layer for their model(s). Model selection (`claude --model`, `/model`, session
config), context-window management, prompt caching, the tool-call protocol, token
accounting, retries, and streaming all live inside the code-agent CLI/session. Each
adjusts to its own model's properties internally — the harness never sees a raw inference
API. Critically, the "swappable model, different properties" axis operates *within* each
agent (Opus vs. Sonnet vs. Haiku under Claude Code), not just *between* agents.

**(b) What basicly currently does.** basicly's runner is a purely **outer invocation
adapter**, not a token-level inference client — the right altitude, but minimal:

- `RunnerSpec` = `{name, kind, command, prompt_via}` and nothing else
  (`src/basicly/runner.py:52-66`). No model, version, context-window, or capability field.
- Built-in adapters are static argv templates: `claude -p {prompt}`, `codex exec {prompt}`,
  `copilot -p {prompt}`, plus the `manual` handoff (`runner.py:71-76`).
- Selection: explicit name wins; else `auto` PATH-probes claude→codex→copilot via
  `shutil.which`; else falls back to `manual` — an unknown agent's command is never guessed
  (`select_runner` `runner.py:118-144`).
- Config override/add is name/command/prompt_via only (`config.py:596-617`); `runner
  dry-run` prints exact argv; `runner list` shows PATH availability.
- Loop dispatch injects a **static agent-neutral prompt** that *points at* `AGENTS.md` +
  `br show <id>` rather than inlining them (`loop.py:228-236`). The one model-property
  adjustment anywhere in basicly is the per-**target** size cap (§7: 8000 claude/copilot,
  12000 codex), authored in `targets/*.yaml` — not derived from model properties, and living
  in projection, not the inference seam.
- Grepping `runner.py`/`config.py` for `model|context.window|max_tokens|capability|version`
  returns **nothing** — zero per-model property handling.

**(c) The gap.** basicly swaps the **agent binary**, not the **model**, and adjusts to
**neither**. The principle is essentially unmodeled:

1. **No model concept.** You can hand-encode `--model opus` inside a `[[runner.agents]]`
   command, but there is no first-class model field — no model pinning, no per-track model
   choice (cheap model for mechanical leaves, strong model for hard nodes), no record of
   which model ran.
2. **Capability detection is PATH-presence only** (`shutil.which`). A binary on PATH is
   assumed to speak the hard-coded headless flag; a version that dropped/renamed `-p` would
   be selected and fail at dispatch. No `--version`/`--help` probe.
3. **No property-adjustment seam.** The size caps that *should* be model-derived are
   target-hardcoded; nothing lets the harness react to a model's context budget or tool-call
   quirks.

**(d) Recommended improvements basicly should own.** Keep the boundary honest — basicly
must **not** become a token-level inference client. The principle maps onto basicly as
*model/agent-property awareness at the invocation and projection seams*:

- Add an optional first-class `model` field to `RunnerSpec` / `[[runner.agents]]`, rendered
  into the command template — pin a model per runner (later per-track) without hand-crafting
  argv.
- Upgrade capability detection beyond PATH: probe `--version`/`--help` to confirm the assumed
  headless flag before selecting; surface in `runner list`/`status`.
- Record runner+model provenance on each loop-landed node — the inference seam is the only
  place model identity is known (feeds D1 audit and D3 observability).
- (Modest/deferred) A per-model property table (context budget, tool-call style) the
  projection layer can consult so caps become model-derived rather than target-hardcoded.

**Accepted gaps → beads.** Wontfix-for-now: model-derived projection caps — real but low
value until a consumer runs a model whose context budget differs materially; keep the
8000/12000 target caps. **Cross-dimension note:** runner+model provenance is the same record
D1 and D3 want — one bead, three consumers.

---

## Dimension 5 — Agent runtime boundary

*Child: `basicly-zv48.5`. Principle: the agent runtime turns a model into an agent —
the orchestration loop (think→act→observe), tool-call dispatch, conversation state, and
the protocol the rest of the harness speaks to it. Most of this is the code agent's job.
This dimension's deliverable is a boundary map, not a gap hunt.*

**(a) What the code agent (agent runtime) owns.** The entire token-level runtime lives in
Claude Code / Codex / Copilot: the think→act→observe loop, tool-call selection and
dispatch, conversation/context-window state, prompt caching, retries, streaming, and the
tool-use protocol. basicly never sees a tool call, a token, or a turn — it hands a prompt
to a CLI and gets a process exit back. This is correct: there is no cross-agent token-level
protocol to standardize, so re-implementing any of it would be reinventing the agent.

**(b) What basicly (workflow runtime) owns.** basicly is a *coarse-grained, resumable,
cross-agent workflow* runtime, one altitude above the token loop:

- **The phase state machine** — intake → classify → decompose → build → verify → ship,
  with human checkpoints, a bounded rework loop (`max_rework`), and tier escalation
  (`policy.py`, `loop.py`). None of this is a model turn; it is a workflow transition.
- **Durable state is entirely in `br`, none in the harness.** `derive_phase` reconstructs
  the furthest phase purely from recorded `br` evidence — issue status, the `external_ref`
  worktree binding, gate verdicts, checkpoint/rework comment markers
  (`src/basicly/loop_state.py:106-126`; `verified = gates.can_advance and (worktree is not
  None or has_children)`). There is no side-file to corrupt, which is what makes the loop
  resumable and cross-agent (start on Claude, finish on Codex).
- **Work isolation, merge queue, gates** — worktree lifecycle, parallel-build/serial-merge,
  deterministic verify — all workflow concerns outside any single agent turn.

**(c) The seam / protocol boundary.** The **runner adapter is the entire protocol seam**
(`src/basicly/runner.py`). What crosses it is deliberately minimal:

- **In:** a static, agent-neutral prompt that *points at* the durable state rather than
  inlining it — "you are in a worktree for issue X; read `AGENTS.md`; run `br show X`;
  implement and commit; do not merge/push/close" (`loop.py:227-237`). The two things
  standardized across all agents (the projected `AGENTS.md` and the `br` tracker) *are* the
  protocol; the prompt just names them.
- **Out:** a `RunResult` = `{returncode, stdout, stderr, handoff}` (`runner.py:79-90`); the
  loop consumes only the exit code plus the last output line for its block message
  (`loop.py:215-226`).
- **What does NOT cross:** all token-level state — conversation history, tool calls, context
  — stays inside the agent. The handoff runner formalizes the seam's floor: when no CLI is
  known it shells out to nothing and defers to `AGENTS.md` + resumability.

**(d) Recommended seam-hardening.** The boundary is clean and correctly drawn — basicly is
the workflow runtime, the code agent is the token runtime, and the seam is a
tracker-anchored prompt plus a process result. The one genuine thinness is on the **out**
side: a bare exit code cannot distinguish "did the work," "partially did it," "got stuck
and needs input," or "refused." That is the *only* seam-hardening worth doing, and it is
the same structured run-record already demanded by D1/D3/D4 (a richer, self-reported run
outcome) plus the D6 structured "I don't know" signal — so this dimension files **no bead
of its own**; it ratifies `basicly-z6dh` (run-record) and `basicly-o774` (structured
needs-input) as the seam-hardening path. Boundary verdict: **sound, delegates correctly.**

**Accepted gaps → beads:** none unique to this dimension — the seam is clean; hardening
folds into `basicly-z6dh` and `basicly-o774`.

---

## Dimension 6 — Agentic context/retrieval layer

*Child: `basicly-zv48.6`. Principle: wrap retrieval in an agentic loop — plan sources,
execute queries, evaluate against the question, decide return/refine/try-another; when the
iteration budget runs out, return a structured "I don't know" instead of a confident wrong
answer.*

**(a) What the code agent provides.** The agentic retrieval loop *already exists at the
code-agent layer*. Claude Code / Codex run a plan→search→read→refine loop over the repo
(grep/glob/read, sub-agent exploration, tool-driven lookups) as their core behavior; that
*is* agentic retrieval, and it is not basicly's to re-implement. What no code agent
reliably provides is a *disciplined, enforced* "I don't know" — models default to
answering, and the stop-instead-of-guess behavior is prompt-suggested, not runtime-guaranteed.

**(b) What basicly currently does.** basicly's context contribution is **static projection**,
not a retrieval loop:

- **Render-once projection** of fragments/skills into the always-on files, scoped
  `.claude/rules/*`, and projected `SKILL.md` (`loader.py`/`planner.py`/`skills.py`); the
  catalog is a *push* the agent may or may not read, not a queryable store.
- **Progressive disclosure** is the one retrieval-shaped feature: skills project as
  discoverable `SKILL.md` loaded on demand (`skills.py`), so the agent pulls detail only
  when a task triggers it — retrievability, not a retrieval loop.
- **A soft, prose-level "I don't know"** lives in two `applies_to: [all]` fragments the
  model may ignore: `knowledge-priming` ("if no repo context covers a decision, say so and
  proceed on stated assumptions") and `decision-protocol` ("stop and ask when a needed fact
  can't be found … state exactly what's missing"). This is guidance, not an enforced contract.
- **The loop *does* own a hard block-and-resume outcome** (`_blocked` in `loop.py`), but it
  fires on runner failures/checkpoints/gates — never on the agent itself signaling "I lack
  the facts to proceed."

**(c) The gap.** Two honest halves:

1. **The agentic retrieval loop itself is *not* basicly's gap to close** — the code agent
   already does it, and a harness-level retrieval agent would duplicate (and fight) the
   code agent's own search loop. Wontfix, with reasoning.
2. **The real, basicly-shaped gap is the structured "I don't know."** Today it is soft
   prose the model can override; there is no first-class outcome by which the agent signals
   *"iteration budget spent, fact not found"* and the loop blocks-and-surfaces instead of
   accepting a confident wrong answer. basicly owns the loop's outcome protocol (D5 seam),
   so this one *is* its job.

**(d) Recommended improvements basicly should own.**

- **A structured "I don't know" / needs-input as a first-class loop outcome**
  (`basicly-o774`): let the runner signal an unresolved-fact result that maps to the loop's
  existing block-and-resume contract, turning the prose escalation policy into an enforced
  seam. This is the D5/D6 convergence.
- **Keep improving catalog *retrievability*** (the progressive-disclosure direction) rather
  than building a retrieval loop — the catalog should be the best possible *static* input to
  the code agent's own loop.
- **Explicitly out of scope (wontfix):** a harness-owned agentic retrieval agent — that is
  the code agent's job; duplicating it adds cost and conflict for no gain.

**Accepted gaps → beads:** one — `basicly-o774` (structured "I don't know", shared with
D5). The agentic-retrieval-loop half is wontfix (belongs to the code agent).

---

## Dimension 7 — Rubric-based evaluation of agent behaviors

*Child: `basicly-zv48.7`. Principle: evaluate the agent against specific use-case-tied
behaviors via rubrics — yes/no checks about what the agent should be doing — not just
generic metrics. Deliverable: a gap analysis **plus a rubric-eval design proposal.***

**(a) What the code agent provides.** No code agent ships a built-in evaluator of its *own*
task behavior against use-case rubrics; evaluation frameworks (Claude's eval tooling,
promptfoo, OpenAI evals) are *separate* products you run *around* an agent, not a runtime
self-check. So rubric evaluation is neither the code agent's runtime job nor something it
provides for free — it is a harness/pipeline concern, which is exactly where basicly sits.

**(b) What basicly currently does.** basicly has two evaluation layers, and *neither is a
behavioral rubric*:

- **Deterministic gates** — generic pass/fail on the *artifact*: tests/lint/type/build via
  `[[verify.checks]]` (`verify.py`), plus commit-msg / beads / identity / catalog-lint
  hooks. These check "is the code well-formed," not "did the agent do the right things for
  *this* use case."
- **Advisory semantic review** — `basicly catalog review` renders the always-on files and
  asks an agent to find contradictions/ambiguity/redundancy; it is a fixed, catalog-specific
  prompt that **always exits 0** (`review.py:24-34`, `build_review_prompt`), never a gate.
- **The gate ledger already models required-vs-advisory** — `gate_status` advances only when
  every `config.required_gates` entry (default `["verify"]`) passes; any other recorded gate
  is advisory and never blocks (`policy.py:72-94`). This is the exact seam a rubric layer
  would plug into.

There are **no use-case-tied yes/no behavioral rubrics** anywhere, and no way to author them.

**(c) The gap.** basicly evaluates *artifacts generically* (tests pass) but never
*behaviors specifically* (e.g. "did it add a regression test for the bug?", "did it update
the changelog on a release?", "did it point at the enforcing command instead of restating
the rule?"). The advisory review is the closest primitive but is a single hard-coded prompt
about catalog files, not an authorable, work-type-tied rubric set.

**(d) Recommended improvements + rubric-eval design proposal** (`basicly-0122`).
The gate ledger's required/advisory split *is* the wiring; the missing pieces are authoring
and evaluation. Proposed design:

- **Authoring — rubrics as a catalog source**, the same shape as fragments/skills: a
  `rubric.yaml` with `id`, `description`, `applies_to` work-types (`bug`/`feature`/…) or a
  named use case, and a list of `checks`, each a yes/no question with a `kind`:
  `deterministic` (a shell/verify command whose exit code answers it — reuse the
  `[[verify.checks]]` runner) or `judged` (an agent-answered yes/no with quoted evidence,
  reusing the `review.py` prompt-assembly + agent-agnostic runner). Lint enforces the format
  like every other catalog source.
- **Evaluation — deterministic first, judged second** (§3.3): run the deterministic checks
  as real pass/fail; dispatch the judged checks through the runner for a structured
  yes/no+evidence verdict. Selection is by the issue's work type, so a `feature` bead gets
  the feature rubric, a `bug` bead the bug rubric.
- **Wiring — report as a `br` gate, advisory by default, promotable per work-type.** Emit a
  `rubric` gate via `br gate report`. By default it is **non-required** (advisory — the
  deterministic-first, semantic-second rule; a subjective judged check must not silently
  block a merge), and a consumer can add `rubric` to `[policy] required_gates` (globally or,
  with a small extension, per work-type) to make a mature rubric blocking. This respects the
  existing block-vs-advise policy exactly and needs no new gate mechanism.

**Accepted gaps → beads:** one feature — `basicly-0122` — carrying the design above; large
enough to decompose into authoring/eval/wiring children when it enters its own loop.

---

## Synthesis — basicly harness-maturity view

**Headline.** basicly is a **strong, correctly-scoped workflow harness with a thin
runtime-governance layer.** Its thesis — *lean over the `br` substrate, project guidance
per agent, gate deterministically* — is sound, and its boundaries are drawn in the right
places: it delegates the token-level runtime and agentic retrieval to the code agent
(D5, D6) and owns the workflow loop, isolation, and deterministic gates. The Foundry
principles it scores *lowest* on are precisely the **runtime-governance** ones —
attribution, content guardrails, per-run observability, model awareness, behavioral
evaluation — because basicly today governs the *commit boundary* (post-action, deterministic)
far better than the *action boundary* (in-flight, per-agent, per-model).

**Maturity by dimension** (Strong = principle well-served / correctly delegated; Partial =
real primitive exists but incomplete; Thin = principle largely unmodeled):

| Dimension                        | Maturity | Owning layer                | Top gap basicly should own                     |
| -------------------------------- | -------- | --------------------------- | ---------------------------------------------- |
| 5 Agent runtime boundary         | Strong   | code agent (delegated)      | none — seam is clean, hardening folds into D3  |
| 6 Context/retrieval              | Partial  | code agent + basicly (soft) | structured "I don't know" as a loop outcome    |
| 1 Identity & audit               | Partial  | basicly + OS/agent          | attribute the dispatched agent/model on actions|
| 4 Model swappability             | Partial  | basicly (invocation seam)   | first-class model field + capability probe     |
| 3 Observability & fleet          | Thin     | basicly (aggregation)       | per-run run-record + cross-repo fleet rollup   |
| 7 Rubric evaluation              | Thin     | basicly (pipeline)          | authorable behavioral-rubric gate              |
| 2 Guardrails                     | Thin     | code agent + basicly        | project the deny-list + a secret-scan gate     |

**The one cross-cutting keystone.** Four dimensions (1 identity, 3 observability, 4 model,
5 runtime seam) all converge on the *same missing artifact*: a **structured runner
run-record** (`basicly-z6dh`) — who (agent), what model, how long, what outcome, keyed to
the bead. The runner is the only place basicly holds these facts, and today it discards all
but the exit code (`runner.py:79-90`). Building that one record unlocks attribution (D1),
model provenance (D4), the observability foundation (D3), and a richer seam outcome (D5).
It is the highest-leverage single change in the whole spike.

**Two framing principles, scored.** *"Identity bounds what an agent can reach"* — basicly
guards the **absence** of identity (`identity-guard`) but has **no per-agent identity**, so
every actor collapses onto one human principal (D1). *"Guardrails bound what flows through
it once it acts"* — basicly's guardrails are overwhelmingly **post-action commit gates**;
its strongest in-flight control (the deny-list) **isn't even shipped to consumers**, and it
has no content/secret/egress guardrail (D2). The identity/guardrail pair is basicly's
weakest axis and its clearest area to own.

**Recommended sequencing** (of the 13 filed beads):

1. **Foundation first:** `basicly-z6dh` (run-record) — unblocks D1/D3/D4/D5.
2. **Highest-signal safety:** `basicly-u0zg` (project the deny-list) and `basicly-yzyd`
   (secret-scan gate) — close the "guardrail exists but isn't distributed" gap.
3. **Evaluation maturity:** `basicly-0122` (rubric framework) — turns generic gates into
   use-case-tied behavioral checks.
4. **Then the P2/P3 build-outs** — attribution, model field, fleet rollup, structured
   "I don't know", redaction, capability probe, and the remaining hardening.

**What basicly should *not* build** (accepted wontfix, so scope stays honest): the
token-level inference client and agentic retrieval loop (code agent's job, D4/D6);
re-metering raw per-session tokens (consume the agent's OTEL/usage, don't re-meter, D3);
portable per-agent *role enforcement* (lives at the agent/OS trust boundary — basicly can
only project policy, D1/D2); and model-derived projection caps until a consumer actually
needs them (D4).

---

## Filed beads

Thirteen implementation beads were filed for the accepted gaps (priority scale: 1 High, 2
Medium, 3 Low). Dimension 5 files none of its own (seam is clean); dimension 6 shares
`basicly-o774` with dimension 5.

| Dim | Priority | Bead           | Title                                                            |
| --- | -------- | -------------- | ---------------------------------------------------------------- |
| 2   | 1 High   | `basicly-u0zg` | Project a baseline agent-permissions deny-list across all agents |
| 2   | 1 High   | `basicly-yzyd` | Secret-scanning deterministic gate on committed content          |
| 3   | 1 High   | `basicly-z6dh` | Runner run-record (duration/exit/agent/model, keyed by bead)     |
| 7   | 1 High   | `basicly-0122` | Rubric-based behavioral evaluation framework (advisory gate)     |
| 1   | 2 Medium | `basicly-140a` | Stamp the dispatched agent/model as attribution on commits/gates |
| 2   | 2 Medium | `basicly-3p2i` | Runner output secret-redaction plus egress-policy seam           |
| 4   | 2 Medium | `basicly-45ld` | First-class model field on runner adapters + provenance          |
| 4   | 2 Medium | `basicly-bveo` | Capability probe beyond PATH presence                            |
| 3   | 2 Medium | `basicly-h0f0` | Fleet rollup across housed repos (`status --fleet`)              |
| 6   | 2 Medium | `basicly-o774` | Structured "I don't know" / needs-input as a first-class outcome |
| 1   | 3 Low    | `basicly-smzg` | Optional per-agent bot identity + commit-signing trust model     |
| 3   | 3 Low    | `basicly-y886` | Health scoring and drift-over-time                               |
| 2   | 3 Low    | `basicly-yw28` | protect-generated git-stage manifest backstop                    |
