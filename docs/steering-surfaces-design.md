# Steering Surfaces — Which Mechanism Carries Which Guidance

Status: **design, not yet decomposed.** Opened 2026-07-26 out of the state-of-the-art review
([`research/2026-07-26-sota-review.md`](research/2026-07-26-sota-review.md) §§6.2, 6.3, 6.8). This
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
| **Rules with `paths:` / `applyTo:`** | when a matching file is touched | re-injected | **medium, low if scoped** | moderate | **no — §3** |
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

We manage the baseline against a soft character cap, currently ~9000 characters with roughly 1000
characters of headroom, and we treat refilling it as a budgeting problem — trim here, scope out
there. That framing assumes **adherence degrades smoothly with size**.

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

### 2.2 The cheap test, first

Before any redesign, one measurement, adopted from the field and costing a single session: **open a
fresh session and ask the agent to summarise the rules in the always-on file.** Anything it cannot
recall is not doing work.

Run it per agent family, since our baseline is projected to three of them and there is no reason to
assume the same adherence profile. Formalised, this is a **Tier-3 recall case** under
[`catalog-efficacy-design.md`](catalog-efficacy-design.md), with the no-guidance control being the
same session with the baseline absent.

**Decided: no further change to the always-on cap — in either direction — until this is run.**
Raising it and lowering it are both guesses right now, and one of them has already been made.

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

## 3. Path-scoped rules — the tier we are missing

### 3.1 The mechanism exists in both platforms

Claude Code supports `.claude/rules/*.md` with a `paths:` glob list; guidance loads only when a
matching file is touched, and is re-injected on compaction. Copilot supports `*.instructions.md`
with `applyTo:` glob frontmatter, comma-separable, with a sharp gotcha: **omit `applyTo` entirely
and the file does nothing automatically.**

Our catalog has **two** guidance tiers — always-on fragments and on-demand skills — where the
platforms offer **three**. The missing middle is *conditional, automatic, scoped*.

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

### 3.4 Projection consequences

This adds a third projection target with a per-family capability question, so it needs the same
gating treatment as the existing two: `basicly check` must verify the path-scoped tier projects
correctly per family, and `skills-check`'s sibling for rules must catch a rule whose globs match
nothing — the silent-failure mode Copilot's missing-`applyTo` gotcha exemplifies.

**A rule that matches nothing is worse than no rule**: it looks like coverage in review and delivers
nothing at runtime. That is a deterministic check and it belongs in CI.

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
3. **Confirm per-family capability for path-scoped rules** before designing the projection, since
   the tier table in §4 is exactly the kind of claim that goes stale.
4. **§6.2's threshold needs owner sign-off**, not derivation. It is a policy choice about how much
   ceremony this repo wants, and it is the one item in this document that is not a technical
   question.
