# Steering Surfaces — Which Mechanism Carries Which Guidance

Status: **design, not yet decomposed.** Opened 2026-07-26 out of the state-of-the-art review
([`research/2026-07-26-sota-review.md`](../research/2026-07-26-sota-review.md) §§6.2, 6.3, 6.8). This
document is about the **guidance delivery layer** — the projection targets, not the catalog's
content and not the loop.

It exists because the review surfaced one finding serious enough to reframe the whole layer: **our
always-on baseline may be past the size at which agents stop attending to it, and we would have no
way of knowing.** Everything else here is either a consequence of that or a cheaper way to avoid
it.

## 1. The seven surfaces, and what each is actually for

First-party guidance now enumerates seven distinct steering mechanisms with materially different
properties. We use three of them. The table is the field's, condensed; the right-hand column is
ours.

| Surface | Loaded | Survives compaction | Context cost | Authority | Our use |
| --- | --- | --- | --- | --- | --- |
| Root instruction file (`CLAUDE.md`/`AGENTS.md`/`copilot-instructions.md`) | session start | memoized, re-read | **high** — every line, every turn | moderate | **yes** — the always-on baseline |
| Subdirectory instruction file | on-demand when that subtree is touched | lost until touched again | low | moderate | no |
| **Rules with `paths:` / `applyTo:`** | when a matching file is touched | re-injected | **medium, low if scoped** | moderate | **built, unused — §3** |
| Skills | name+description at start; body on invoke | re-injected to a budget | low | low-moderate | **yes** — on-demand catalog |
| Subagents | name+description at start; body never enters the parent | only the final message returns | low — isolated context | moderate | **yes** — factory dispatch |
| Hooks | on lifecycle events | **bypass compaction entirely** | low — config lives outside context | **high (deterministic)** | **yes** — the gate floor |
| Output styles / system-prompt append | session start | never compacted | high / moderate | highest / moderate | no |

Three properties of this table drive everything below.

**Authority is not uniform, and hooks win.** The decision rule is first-party and blunt: *"the model
choosing to run a formatter is different from the formatter running automatically."* An "every time
X, always do Y" line in an always-on file is a *worse* implementation of a hook. We already believe
this — "hooks are the deterministic floor" — but we have never audited the baseline for lines that
are really hooks in disguise.

**Cost is paid per turn, not per session.** A 5,000-token instruction file costs 5,000 tokens
before the user types anything, every turn, in every consumer repo that installs us. For a
distribution, that cost is multiplied by our install base, which makes it the one place where our
own frugality is somebody else's bill.

**Compaction behaviour differs, and it is the trap.** A root file is memoized and re-read; a
subdirectory file is **lost until that directory is touched again**. That single asymmetry decides
§3's mechanism choice, and it is why path-scoped *rules* are the right answer rather than nested
instruction files.

## 2. The always-on budget is probably a cliff, not a slope

### 2.1 What we know and what we assume

We manage the baseline against a soft character cap — 9000 for Claude and Copilot, 12000 for Codex —
and we treat refilling it as a budgeting problem: trim here, scope out there. (Measured 2026-07-26:
7209 / 7343 / 8484 chars for Claude / Copilot / Codex. The "roughly 1000 characters of headroom" this
section used to cite was never right.) That framing assumes **adherence degrades smoothly with
size**.

The field's consistent claim is that it does not. The reported thresholds: rules start dropping past
roughly 80 lines; whole blocks are ignored past roughly 200 lines; adherence to dense rules
collapses past roughly 500 words. Our baseline is on the order of 1300 words of dense rules.

These numbers are **medium-confidence** — consistent across independent practitioner write-ups, no
published primary experiment (see `references.md` §4). Treat them as an order of magnitude, not a
constant. But the order of magnitude is the point:

**If the thresholds are even roughly right, we are not managing a budget. We are operating past a
cliff, and every cap increase made it worse.** The soft cap was raised from 8000 to 9000 characters
to make room for more always-on content; under this model that decision bought less adherence, not
more coverage.

**We do not know which it is, and that is the actual finding.** Nothing we ship measures which
baseline rules bind.

### 2.2 The cheap test — run 2026-07-26, and the cliff is not where we feared

The design was: open a fresh session per agent family and ask the agent to summarise the rules in
the always-on file; anything it cannot recall is not doing work. A **Tier-3 recall case** under
[`catalog-efficacy-design.md`](catalog-efficacy-design.md), with the no-guidance control being the
same cell with the baseline absent.

**It has run** (`basicly-agzx.1`). Harness: [`.scripts/recall_eval.py`](../../.scripts/recall_eval.py),
with the rule inventory and match anchors in
[`.scripts/recall_rules.toml`](../../.scripts/recall_rules.toml). Twelve dispatches — 2 families ×
2 arms × 3 reps — each in a fresh throwaway repo containing only that arm's guidance, with the
isolation read back before dispatch and every read, write and shell tool denied so the answer can
only come from context loaded at session start.

| Family | Rules | Baseline recall | Control base rate | Lift attributable to the file |
| --- | --- | --- | --- | --- |
| claude (`.claude/CLAUDE.md`, 7209 chars) | 53 | **98%** (52.0/53) | 17% | **+81 pp** |
| copilot (`copilot-instructions.md`, 7343 chars) | 54 | **93%** (50.3/54) | 6% | **+87 pp** |

**The strong form of the cliff hypothesis is refuted.** At the size the baseline has actually
reached, the rules are not being dropped, ignored, or skimmed past: asked directly, both families
reproduce essentially the entire file. The large lift over control is what makes this a statement
about *our file* rather than about model priors — a guidance-free agent volunteers "never commit
secrets" and "keep diffs small" unprompted, and those are precisely the rules with a non-zero
control rate. So the ~500-word threshold, read as "past this size the model stops seeing the
content", does not describe us at 1086–1303 words.

**What this does and does not license.** Two limits are structural, not caveats to wave off:

- **This is `mechanism confirmed`, never `outcome improved`** (§4.1 of `catalog-efficacy-design.md`).
  It measures whether a rule is *retrievable* when the agent is asked for it. It says nothing about
  whether the rule *binds* while the agent is doing unrelated work under pressure — which is the
  operational claim, and the one the field's threshold is really about. By that document's own rule,
  a case with no hidden objective check **may not be cited as evidence of quality**, and this case
  has none.
- **It is an upper bound.** The prompt is a direct retrieval cue, the single most favourable
  condition recall can be measured under. No real session ever asks "list your rules".

The honest reading is therefore narrow and still decisive: **the content is not invisible.** The
argument "the baseline is past the cliff, therefore the tokens are wasted" is no longer available —
and that was the argument for treating Phase 4 as urgent surgery. What remains open is adherence,
which needs a hidden-criterion case where the rule is never mentioned and compliance is scored from
the work product.

**Consequences, recorded so they are not re-litigated:**

1. **The cap freeze is lifted for lowering and stays for raising.** Trimming and scoping are now
   ordinary housekeeping justified on cost, not rescue operations. Raising the cap still has no
   evidence behind it, and adherence is exactly the question raising it would prejudge.
2. **Phase 4 is routine tidying, not urgent surgery** — the plan's stated fork resolves to its P2
   branch. Its cost argument survives untouched: the baseline is billed per turn to every consumer,
   and §3's scoped tier still removes that cost for conditional content.
3. **One reproducible miss is worth more than the aggregate.** `project-overview.1` — the *purpose*
   statement — scored 0/3 in both families' baseline arms, and it is the only line in the file that
   is descriptive rather than imperative. Asked for rules, both models silently drop the prose. That
   is a finding about *shape*, and it supports §2.4's three-tier block on independent grounds:
   content not phrased as a constraint is not retrieved as one. The prompt did ask for imperative
   rules, so the effect is partly cued — but nothing in a real session cues it either.
4. **Codex is unmeasured**, and it is the arm that matters most: `AGENTS.md` is the largest baseline
   (8484 chars) and the only one that *grows* when a fragment is scoped (§3). The `codex` CLI was
   absent from the measuring machine, so the gap is declared rather than interpolated from the other
   two.

**Re-run it after any baseline change.** Phase 4's exit criterion depends on comparing against these
numbers, which is why the scorer is deterministic and committed rather than judged: a scorer that
drifts between runs cannot support a before/after claim. The inventory is derived from the live file
and refuses to score when a rule's text has changed under its anchors, so the comparison cannot
quietly become unlike-for-unlike.

**Method limits to respect when citing this.** Three reps per cell, one machine, one CLI build per
family. The scorer is lexical, so a paraphrase can miss — `project-overview.1` is one — and that
bias applies to both arms, leaving the contrast sound while making each absolute figure a slight
underestimate. Globally installed plugins and user-level settings were present on the measuring
machine and are a declared confound; there was no user-level `CLAUDE.md`, which is the one that
would have mattered.

### 2.3 Two rules for what stays

Independent of the measurement, two findings from the review apply immediately because they are
about content rather than size.

**A rule needs a reason.** *"A rule with a reason generalises to similar situations. A rule without
a reason gets ignored the moment context shifts."* Our baseline is largely reasoned already, which
is a real strength worth protecting: the temptation when trimming is to cut the *reasons* because
they are the longest part of each line. That is exactly backwards — cutting the reason keeps the
tokens that do the least work.

**One example beats three paragraphs.** The largest-sample finding in the review (2,500+
repositories): *"one real code snippet showing your style beats three paragraphs describing it."*
Our baseline is nearly all prose. Where a convention has a canonical example, the example is both
shorter and more binding.

### 2.4 The boundary triad

The 2,500-repo study found the most effective instruction files organise constraints as **always do
/ ask first / never do**, and that *"never commit secrets"* was the single most common useful
constraint.

We have all three tiers, spread across Core Rules, Require Explicit Confirmation, and Secure Coding.
The content is right; the *shape* is not, and the shape is what makes a constraint retrievable
under pressure. A model scanning for "am I allowed to do this?" benefits from one place answering
it in three tiers.

**Proposed:** the baseline's constraint content is projected as an explicit three-tier block. This
is a projection-template change, not new content, and it should be micro-tested (form-matching:
this is a *retrieval* problem, so a structural fix is indicated, not more prose).

## 3. Path-scoped rules — the tier we built and never used

> **Corrected 2026-07-26.** This section previously claimed the path-scoped tier was *missing*
> and planned the projection work to add it. That was wrong, and the error mattered: it sized a
> content problem as an engine problem. **The projection exists and is wired; no fragment uses
> it.** What follows is the corrected account.

### 3.1 The mechanism exists in both platforms — and in our engine

Claude Code supports `.claude/rules/*.md` with a `paths:` glob list; guidance loads only when a
matching file is touched, and is re-injected on compaction. Copilot supports `*.instructions.md`
with `applyTo:` glob frontmatter, comma-separable, with a sharp gotcha: **omit `applyTo` entirely
and the file does nothing automatically.**

We already project into that mechanism. A fragment may declare `scope: paths: [...]`
(`Fragment.scope_paths` / `is_scoped`), the planner routes on it (`has_scope`,
`exclude_scoped`), the `claude` target declares a `scoped_rules` output at
`.claude/rules/{fragment_id}.md`, and `rule_md.j2` renders it.

**And the directory is empty, because zero fragments declare a scope.** So the catalog does have
two *populated* tiers where the platforms offer three — but the third is not missing, it is
unused. The remaining work is **authoring** (which fragments earn a scope) plus one deterministic
check (§3.4), not a new projection target.

### 3.2 Why this is the right relief valve for §2

The always-on baseline currently carries guidance that is only sometimes relevant. Cross-platform
subprocess discipline matters when touching Python that shells out. Test-isolation discipline
matters when touching tests. Catalog-authoring rules matter when touching `.basicly/`. Each of these
pays full always-on price to be relevant occasionally.

Moving them to path-scoped rules is better on **both** axes at once, which is rare:

- **Cheaper** — out of the baseline, so not paid on turns where it is irrelevant.
- **More binding when it matters** — a rule injected because you just opened a test file competes
  with far less than the same rule sitting in position 40 of a 1300-word block.

That second point is the important one. This is not only a cost optimisation; under §2.1's model it
is an **adherence** improvement for exactly the guidance most likely to be currently ignored.

**But the saving is not uniform across families, and that must not be glossed.** Scoping a
fragment does three different things depending on the target:

| Target | What a scoped fragment does | Baseline cost |
| --- | --- | --- |
| `claude` | becomes a real conditional rule in `.claude/rules/`, loaded on a glob match and re-injected on compaction | **removed** from the baseline |
| `codex` | stays **inlined** in `AGENTS.md`: nested `AGENTS.md` scoping is directory-based while our scopes are globs, so a per-directory offload cannot express `**/*.py` faithfully and inlining is the correctness-preserving choice | **unchanged** — which is why the codex cap carries a documented allowance |
| `copilot` | is **not** twinned into `.github/instructions/` on purpose: VS Code loads both roots without dedup, so a twin double-loads. Single-sourced to `.claude/rules/` instead | **removed**, and github.com-side Copilot no longer sees it at all |

Two consequences for how this work should be planned. First, §2's headline relief — shrinking the
always-on baseline — **lands on Claude and Copilot but not on Codex**, so a measurement that
averages across families will understate it and a claim of "we cut the baseline" needs the family
named. Second, scoping a fragment is a *deliberate removal* from the github.com Copilot surface,
which is a guarantee change, not a refactor: it belongs in §4's capability-tier table and should be
a conscious choice per fragment rather than a side effect of tidying.

### 3.3 What must not move

A rule earns its always-on slot only if it is **unconditional** — it binds regardless of which file
is open. Concretely, these stay:

- The confirmation triad (§2.4) — a destructive action can be attempted from any context.
- Gate-integrity rules ("never bypass a gate to force success") — the temptation is context-
  independent.
- The decision protocol and the harness-loop pointer — they govern *whether to start*, before any
  file is touched.
- Secret handling — always.

The test is mechanical: **if you can write a glob for it, it is a path-scoped rule.** If the
predicate is "the agent is about to do something", it is always-on or it is a hook.

### 3.4 What is actually left to build

The projection and its per-family routing are done (§3.1), and `basicly check` already gates
generated-file drift for the scoped output like any other. So the engine work reduces to **one
missing deterministic check**:

**A scope whose globs match nothing.** This is the silent-failure mode Copilot's
missing-`applyTo` gotcha exemplifies, and it is the one failure the existing gates cannot see: the
fragment is well-formed, it projects cleanly, `check` is green, and the rule never loads. **A rule
that matches nothing is worse than no rule** — it looks like coverage in review and delivers
nothing at runtime. Deterministic, cheap, belongs in CI.

Two smaller notes, both cheap and both easy to forget:

- **The check must run against the consumer's tree, not ours.** A scope like `**/*.py` matches here
  and matches nothing in a docs-only consumer repo, so "matches nothing" is a warning at
  projection time and an error only where the technology is selected — otherwise the gate punishes
  a consumer for not having Python.
- **`catalog_verify` already special-cases scoped fragments** when comparing for duplication (two
  fragments with identical bodies are only a duplicate if their scopes match too). That logic is
  written and untested against real scoped content, because there is none — expect it to be the
  first thing that breaks when scopes appear.

## 4. Capability tiers per agent family

We project to three families as if the delivered guarantee were the same. It is not. `ponytail`'s
20-host adapter table classifies each host explicitly, and the classification carries real
information:

| Tier | What the host supports | What we can promise |
| --- | --- | --- |
| **instruction-tier** | a rules file only | always-on guidance; **no** on-demand skills, no hooks, no enforcement |
| **skill-tier** | rules + skills | always-on + on-demand; still no deterministic floor |
| **plugin-tier** | rules + skills + hooks/commands | the full harness, including enforcement |

Their thin-adapter rule is the discipline that keeps this from becoming twenty hand-maintained
variants: *"when a host supports skills or hooks, point it at the existing `skills/` and `hooks/`
files. When a host only supports project instructions, keep its copied rule text aligned."*

**Why this matters more for us than for them.** Our central claim is *enforcement*, and enforcement
is a plugin-tier capability. On an instruction-tier host, the harness degrades to advice — and we
currently do not say so anywhere. A consumer installing into such a host gets a projection that
looks complete and silently lacks the deterministic floor that makes the rest trustworthy.

**Proposed:** each projection target declares its capability tier, `basicly install` reports the
tier it just installed and what is unavailable at that tier, and the docs state the tier per
family. This is honesty about a guarantee, which for an enterprise consumer is the guarantee.

## 5. `effort` as a budget signal

`gsd-core` declares `effort:` per skill — `max` for heavy orchestrators, `low` for status queries —
as a budget signal orthogonal to model tier: *how much thinking should this cost*, not *which model
runs it*.

We have model tiers on roster personas and nothing on catalog entries, so a status query and a
decomposition carry the same implied budget.

One trap recorded from their experience, because it is non-obvious and cost them a revert: they
**removed** `context: fork` from spawning orchestrators, because *"a forked subagent context does
not have the `Agent` tool"* — isolating an orchestrator breaks the orchestration. **Context
isolation must come from the children, never from forking the parent.** Our supervisor is code
rather than an agent, so we are structurally immune, but the general form applies to any future
"run this in isolation" flag: check what the isolated context loses before isolating.

**Proposed, weakly:** an `effort` hint on catalog entries where the disparity is large. Flagged as
low-confidence — it is a per-family capability with no portable semantics, so it may be better as a
runner-config concern than a catalog field. Worth deciding, not worth guessing.

## 6. The ceremony threshold, and the primitive below it

### 6.1 The gap

Our rule is "drive non-trivial work through the harness loop." `gsd-core` prices its own loop
honestly — *"the phase loop introduces real friction … for a small, well-understood change, that
overhead is not justified"* — writes the threshold down, and ships two named primitives below it.

We have no named primitive below the loop and no written threshold, which produces two failures
pulling in opposite directions:

- **Ceremony on trivial work.** A typo fix nominally requires intake, classify with structured
  acceptance criteria, decompose, a worktree, gates, and a checkpoint. The real cost of that is
  high enough that it will not happen, which leads to:
- **An unenforceable rule.** "Non-trivial" is the agent's judgment call, so the loop is bypassed
  by whatever definition the current session happens to hold. A rule nobody can check is not a
  floor.

### 6.2 A written threshold

Adapted from `gsd-core`'s, whose predicate is properly observable: *"if the task could be fully
specified in a single, short prompt and completed in one agent turn without further clarification,
skip the phase loop. If the task requires research, involves files you have not read recently, or
depends on decisions that are not yet settled, the phase loop protects you."*

Ours needs one addition their framing lacks, because our loop's value is partly in the gates rather
than the planning: **work that touches a trust boundary, a gate, or the catalog goes through the
loop regardless of size.** A one-line change to a hook is small and consequential, and size is the
wrong predicate for it.

**Proposed threshold — the loop is required when any of these holds:**

- the work needs research, or touches files not already read in this session;
- it depends on a decision not yet settled;
- it spans more than one component, or more than one commit;
- it touches a trust boundary, a gate, a hook, CI, or the catalog;
- it is a bug whose root cause is not yet known.

Below all of those, a **named lightweight path** applies — still committed, still gated by hooks
(they are non-negotiable and cost nothing), still tracked, but with no decomposition, no worktree,
and no checkpoint.

Two things this must not become. It must not be a **loophole** — the trust-boundary clause exists to
stop "it's only one line" reasoning about consequential changes. And it must not be **an escape from
the hooks**, which apply to every commit regardless of path; the lightweight path skips *ceremony*,
never *enforcement*. That distinction is the whole reason we can afford to offer it at all, and it
is a place where our architecture is genuinely better than `gsd-core`'s: their quick path skips
their gates because their gates are prose, and ours are hooks.

### 6.3 Why this belongs in this document

Because the threshold is a **steering** decision: it determines which guidance surface a unit of
work is routed through. Putting it here keeps [`factory-design.md`](factory-design.md) about the
factory and keeps the routing question in one place.

## 7. Smaller adopted items

- **Refer to work by name, not by bare id.** *"A wall of `#42, #43, #44` is illegible; names read
  at a glance."* Our engine output and projected guidance are dense with bare bead ids. The id
  rides *inside* the name; it does not stand in for it. A cheap, immediate legibility win in every
  human-facing surface.
- **Commands early.** The largest-sample finding on instruction-file ordering: put executable
  commands in an early section, because agents reference them constantly. Our `## Commands` section
  sits well down the file.
- **The deliberate-shortcut convention.** A marker comment naming **the ceiling and the upgrade
  trigger** (`# basicly: global lock, per-account locks if throughput matters`), harvested
  mechanically into a ledger, with any marker lacking a trigger tagged `no-trigger` — *"those are
  the ones that silently rot."* This gives our "no dead code, no silent error swallowing" rule a
  sanctioned way to record a *deliberate* limitation, which it currently lacks: today the only
  options are fix it or say nothing.
- **A `prime`-shaped assembly command.** Upstream beads' `bd prime` prints workflow context plus
  persistent memories as one command. We have the storage half (found-info records) and not the
  assembly half. Related: their instruction *"do not create MEMORY.md files"* — a tracker-backed
  memory beats a markdown file for the same reason our ledger beats a plan file.

## 8. Explicitly not proposed

- **Output styles.** They *replace* the default system prompt unless `keep-coding-instructions` is
  set, which would silently discard first-party defaults about scoping changes, security, and
  verification habits — the exact disciplines we are trying to add to, not replace. High cost,
  highest authority, and the failure mode is invisible.
- **System-prompt append.** Per-invocation and not projectable into a repo, so it cannot be part of
  a distribution. Also self-limiting: *"the more instructions you provide this way, the less
  strictly Claude will follow them."*
- **Subdirectory instruction files as the scoping mechanism.** They are lost on compaction until the
  subtree is touched again; path-scoped rules are re-injected. Same intent, better mechanism (§1).
- **Raising the always-on cap again.** Blocked on §2.2.

## 9. Preconditions before implementation

1. **Run §2.2's recall test** across all three families. It is one session and it decides whether
   §2 is a cliff or a slope — and therefore how urgent §3 is.
2. **Audit the baseline against three questions** before moving anything: *is this really a hook?*
   (§1) · *can I write a glob for it?* (§3.3) · *does it change behaviour versus the default at
   all?* (the no-op test). Expect all three to fire.
3. ~~Confirm per-family capability for path-scoped rules before designing the projection.~~
   **Done (2026-07-26), and it answered differently than expected**: the projection is already
   built and wired, and the per-family behaviour is §3.2's table. What replaces this precondition
   is narrower — **decide, per fragment, whether losing the github.com Copilot surface is
   acceptable**, since scoping is a guarantee change there rather than a refactor (§3.2).
4. **§6.2's threshold needs owner sign-off**, not derivation. It is a policy choice about how much
   ceremony this repo wants, and it is the one item in this document that is not a technical
   question.
