# basicly capability status

**This is not reference material.** It moved out of
[`architecture.md`](architecture.md) because a status row changes on every landing, and a
specification must not go stale on a schedule it does not control.

The view is **derived**. A functional section of the architecture document describes every
`shipped` row, and every other row names what is missing. The view carries **no date**. The
project does not run to a schedule, so status is the only honest axis. `docs/plan/` holds
the **order** in which the unshipped rows get built.

**The vocabulary is the one closed set defined in architecture §2.** Nothing here may use
a seventh word.

| Status | Means | Evidence required to claim it |
| --- | --- | --- |
| `shipped` | Running code, and a real call path reaches it | Exercised on this repository's own development, and described in a functional section of the architecture document |
| `partial` | Code exists and nothing reaches it, or it covers only part of what it claims | A caller search with a positive control behind it |
| `building` | Sequenced into a phase being worked now | An open work package with written exit criteria |
| `designed` | Decided, sequenced behind a later phase, and **nothing is built** | A decision record. **Not** evidence that anything enforces it |
| `researching` | The deliverable is a number rather than a capability | A specified measurement whose result is allowed to cancel the work |
| `deferred` | Deliberately not built | Nobody has asked for it, and the reason is recorded |

## Guidance

| Capability | Status | Note |
| --- | --- | --- |
| One catalog projected to three agent families: instructions, skills, subagents, permissions | shipped | |
| Projection drift gate run by CI | shipped | |
| Path-scoped rules tier | shipped | Engine built; four fragments and one skill glob use it. Cost falls for two families and rises for the one that inlines |
| Invocation axis per entry | shipped | Declared on skill sources; not yet on fragments |
| Deterministic lexical routing evals with a ratcheting rank-1 floor | shipped | |
| Both skill roots written by every skills command | partial | `basicly install` writes both. A bare `skills-build` or `skills-check` writes one, and needs `--all-default-roots` for the second. Architecture §14 marks the fix as a target |
| An eval case file per catalog entry, enforced as a structural failure | building | Model-invoked skills carry one by convention; fragments carry none |
| Relieve the always-on baseline by scoping what is conditional | building | Authoring work, not engine work |
| Tutorial and how-to layer | shipped | The tutorial was executed end to end on a fresh repository before it was written |
| Whether an individual entry changes behaviour, and which baseline rules bind while an agent works | researching | Recall is measured; adherence is open. **The largest gap in the system** |
| Behavioural efficacy evals with control arms, hidden checks and a safety tier | designed | No arms, no hidden checks, no safety tier exist in code |
| Cursor as a target; a native Codex scoped-rules renderer | deferred | For Codex there is currently no mechanism to project *to* |

## Gates

| Capability | Status | Note |
| --- | --- | --- |
| Git hook floor across three stages | shipped | |
| Agent hooks for two families | shipped | Four hooks: three on claude spanning two event types, one on copilot |
| Verify pipeline with three modes | shipped | |
| Ratchets: module size, comment density, suppression debt, corpus drift, stale citations | shipped | Tree growth reports rather than blocks, because it has no firing history |
| Severity required on judged output, and a lint refusing a pre-judging reviewer bundle | shipped | |
| Rework convergence detection from the open-finding set rather than the count | shipped | |
| A release gate refusing to ship a declared capability nothing has exercised | shipped | Derives the inventory from the configured checks, and fails closed with no ledger at all |
| A gate that runs the demonstration a unit declared | shipped | `demonstration_proof` runs it: advisory at the decompose advance, **blocking at the ship advance**. It rebuilds a pytest argv from an allowlist; a non-pytest demonstration is still admitted |
| The plan gate running the demonstration it admits | deferred | Not `designed`: this is a deliberate separation, not sequenced work. The plan gate judges the field's form and `demonstration_proof` runs the command, at the decompose advance and at the ship advance. Architecture §36.4 holds the reason. No open work item proposes moving the run into the gate [measured 2026-08-16, a `plan[_ ]gate` search over every tracker record: 12 open matches, none of them proposing it, against a positive control of 11 closed matches] |
| Every gate classified by type | building | The gates the engine names by constant are typed; the rest are classified in prose because they have nothing to key on |
| Enforcement at the tool-call boundary, not only at the commit boundary | designed | Engine work before it is catalog work: the host event vocabulary is barely mapped |
| `basicly install` reporting the capability tier it actually delivered | building | On a host with no plugin tier the projection degrades to advice, and we say so nowhere |
| A check that code citations of `architecture §N` resolve | designed | The citation ratchet runs document to code. Nothing runs code to document, which is why a stale `§` citation sits in the tree with every gate green. Architecture §3 carries the probe that counts them |
| A typed event vocabulary: `note` for prose, first-class kinds for machine state | designed | One kind carries both today: `comment` is the largest kind and holds close to half the log, while the `gate` kind is the smallest. The target set is **eighteen** kinds, sized by partitioning the measured population rather than by proposal — routing 2,540 `comment` rows through the thirteen first drafted left 585 of them, 23%, with nowhere to go. Architecture §32.3 carries the vocabulary, §32.3.1 the measured partition and its command, §32.3.2 the reader's alias, §32.8 the migration, and D-34 the decision including why `record` is unavailable |
| A single definition of the closed event-kind set | designed | Four modules each declare part of it and they disagree. `events.KNOWN_KINDS` looks like the authority, omits two of the six kinds actually in the log, and has no consumer anywhere including the suite. Architecture §32.8 carries the table; this blocks the vocabulary row above |
| A `unknown_kinds` signal that separates delegation from corruption | designed | 959 events, 17.9% of the log, are reported as unknown kinds while both are deliberately folded by a sibling module. `fsck` names the ambiguity in its own warning text. Architecture §32.8 |
| The owned tracker as the only store | building | Architecture §32 specifies it; §37 is the account of the external binary still carrying part of it |
| A mermaid parse check on every committed block | designed | See [`backlog.md`](backlog.md) |

## The loop

| Capability | Status | Note |
| --- | --- | --- |
| Single-track loop driven identically by any supported agent | shipped | |
| Worktree isolation per unit of work | shipped | |
| Parallel lanes: supervisor, lane mini-loop, serial landing | shipped | |
| Autonomy grants with a spend ceiling, decision queue, confined decider | shipped | Two of the five decision kinds are delegable: `needs-input` and `escalation` |
| Release automation up to the annotated tag | shipped | |
| Scope sized by the material a lane actually reads | shipped | |
| Measured context occupancy recorded beside the forecast on every dispatch | shipped | |
| VALIDATE as a rung with its own gate, a validator plus a reviewer per lens | shipped | |
| Hold and Kill as writes an operator's answer actually carries out | shipped | |
| A named role per judgment step | shipped | All seven reachable; **the declared tier is inert at spawn**, and no supervised pass has yet recorded a role on an argv |
| RETROSPECTIVE on a computed special cause | shipped | |
| An improvement controller driving a codebase property to a set point | shipped | Has run live and filed one issue; manual-dispatch caller only, by decision |
| A schema-validated handoff artifact at each state boundary | building | Three of eight kinds have a producer, two of those a consumer. The rest refuse nothing |
| Tier injection, so a declared tier reaches the spawn | building | The tier is declared on every agent source and validated by lint, and it reaches no spawn. The hook that would rewrite a spawn is `.basicly/core/kit/tier/install_hook.py` and is not installed. On claude the installer declines with a nonzero exit, and across repeated probes no tool-boundary hook fired for an agent spawn there — that host's documented hook contract is approve-or-deny, not rewrite, which is the requirement architecture §17 sets |
| Per-model spend and wall-clock forecast enforced at pass admission | building | The current forecast models working set, not turn count, and that is now measured rather than suspected |
| A supervised multi-lane run with zero human interventions caused by an engine defect | building | |
| The judged-output contract: a reviewer structurally incapable of seeing the producer's conclusion, a review base recorded before dispatch, re-review scoped to the fix range, late rounds escalating a tier | designed | **Deterministic engine code, not a persona**, which is why it survived the routing landing |
| Cost per landed unit | researching | The instrument the tier claims rest on |

## The work graph

| Capability | Status | Note |
| --- | --- | --- |
| Issues, dependencies, gate results, checkpoints and evidence in a tracked graph | shipped | |
| Phase derived from tracker state, so resume is a read rather than a replay | shipped | |
| Atomic publish of the shared export, and a store error charged to the store rather than to the lane's rework budget | shipped | |
| The scheduler score and rank recorded behind each dispatch | shipped | |
| A pure, age-free ranking function owned in-process | shipped | |
| Harness comment markers native to the owned store | shipped | Landed ahead of the steps before it, which is why the differential must run on dual |
| A repeatable ledger import a fresh consumer can run | shipped | Refuses a post-flip ledger |
| A seam-routed surface for a human tracker write, so both stores move together | shipped | Closes the last bypass route the differential can see |
| No committed artifact carries a host path, username or hostname | shipped | Redaction at both write seams; the secret-rule mirror is kept in step by convention only |
| The owned append-only event log as the source of truth | building | Steps 1 to 3 of the cutover have run; the flip waits on the remaining bypasses and on five unported operations. Architecture §37.3 |
| A consistency check and rebuild, so "the log is the truth" is checkable | partial | **Built with tests and reached by nothing** [re-measured 2026-08-17: no engine module and no `[[verify.checks]]` entry reaches the kit's `fsck` or `rebuild`, against a positive control of five engine modules reaching the kit's `events`]. The over-advertising half of this note was **false and is withdrawn**: `README.md` and `site/index.html` both mark it `◐ partial` against a legend matching this table |
| Provenance on every edge: extracted, inferred, ambiguous | partial | **It has a caller and the earlier "no caller" note was false** [measured 2026-08-17 over the ledger: 962 of 962 edge events carry a `provenance` label — 924 `EXTRACTED` from the import and 38 `dual-write` written live by the mirror, which `edge_adoption` promotes to `EXTRACTED`]. What makes it partial is the vocabulary, not the wiring: `INFERRED` and `AMBIGUOUS` have never been written, and `dual-write` is a fourth label absent from `provenance.LABELS`, so by design it ranks below every known label and can never gate |
| Cross-repo work offers as self-writes in each repository's own ledger | deferred | |

## How this view stays current

A row changes state in the change that lands the behaviour, never in a later cleanup pass.
The same change updates the two rendered copies, on the README and on the landing page.
**Nothing gates that rule.** A stale row here is therefore possible, and the functional
sections of the architecture document stay the place where a `shipped` claim has to be
true.

Architecture decision **D-30** proposes the fix: one source, rendered into all three
surfaces by a `docs-claims` generated block. It is `proposed`, not accepted, and not built.
