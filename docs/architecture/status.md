# basicly capability status

**This is not reference material.** It moved out of
[`architecture.md`](architecture.md) because a status row changes on every landing, and a
specification must not go stale on a schedule it does not control.

**Every table below is generated.** The one source is [`status.yaml`](status.yaml); edit a
row there and run `uv run python .scripts/docs_claims.py --fix`. Editing a table here fails
the `docs-claims` gate on the next commit, which is what makes this the only file in the
repository that grades a capability.

The view is **derived**. A functional section of the architecture document describes every
`shipped` row, and every other row names what is missing. The view carries **no date**. The
project does not run to a schedule, so status is the only honest axis. `docs/plan/` holds
the **order** in which the unshipped rows get built.

**The vocabulary is the one closed set defined in architecture §2**, which also states the
evidence each state requires. It is not restated here: the renderer reads that table, and a
`status` outside it is refused rather than reviewed.

<!-- docs-claims:begin status-view -->

## Guidance

| Capability | Status | Note |
| --- | --- | --- |
| One catalog projected to three agent families: instructions, skills, subagents, permissions | shipped |  |
| Projection drift gate run by CI | shipped |  |
| Path-scoped rules tier | shipped | Engine built; four fragments and one skill glob use it. Cost falls for two families and rises for the one that inlines |
| Invocation axis per entry | shipped | Declared on skill sources; not yet on fragments |
| Deterministic lexical routing evals with a ratcheting rank-1 floor | shipped |  |
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
| Git hook floor across three stages | shipped |  |
| Agent hooks for two families | shipped | Four hooks: three on claude spanning two event types, one on copilot |
| Verify pipeline with three modes | shipped |  |
| Ratchets: module size, comment density, suppression debt, corpus drift, stale citations | shipped | Tree growth reports rather than blocks, because it has no firing history |
| Severity required on judged output, and a lint refusing a pre-judging reviewer bundle | shipped |  |
| Rework convergence detection from the open-finding set rather than the count | shipped |  |
| A release gate refusing to ship a declared capability nothing has exercised | shipped | Derives the inventory from the configured checks, and fails closed with no ledger at all |
| A gate that runs the demonstration a unit declared | shipped | `demonstration_proof` runs it: advisory at the decompose advance, **blocking at the ship advance**. It rebuilds a pytest argv from an allowlist; a non-pytest demonstration is still admitted |
| The plan gate running the demonstration it admits | deferred | Not `designed`: this is a deliberate separation, not sequenced work. The plan gate judges the field's form and `demonstration_proof` runs the command, at the decompose advance and at the ship advance. Architecture §36.4 holds the reason. No open work item proposes moving the run into the gate [measured 2026-08-16, a `plan[_ ]gate` search over every tracker record: 12 open matches, none of them proposing it, against a positive control of 11 closed matches] |
| Every gate classified by type | building | The gates the engine names by constant are typed; the rest are classified in prose because they have nothing to key on |
| Enforcement at the tool-call boundary, not only at the commit boundary | designed | Engine work before it is catalog work: the host event vocabulary is barely mapped |
| `basicly install` reporting the capability tier it actually delivered | building | On a host with no plugin tier the projection degrades to advice, and we say so nowhere |
| A check that code citations of `architecture §N` resolve | designed | The citation ratchet runs document to code. Nothing runs code to document, which is why a stale `§` citation sits in the tree with every gate green. Architecture §3 carries the probe that counts them |
| A typed event vocabulary: `note` for prose, first-class kinds for machine state | partial | **Ten of the eighteen kinds exist and one of the three new ones now has a reader.** `artifact` is the live transport for a handoff artifact: `tracker.read_artifacts` folds it and `artifact_record.recorded` consumes it, over 10 events on this ledger [measured 2026-08-21]. `note` carries prose and only the kit's own writer records it, so the log holds none; `checkpoint` has no writer at all and folds into named state that only `snapshot.record_to_dict` reads. `comment` is a permanent alias of `note` — 2,821 of 6,273 events — and the write seam still emits it, so a marker body is still parsed as prose rather than selected by kind. The target set is **eighteen** kinds, sized by partitioning the measured population rather than by proposal — routing 2,540 `comment` rows through the thirteen first drafted left 585 of them, 23%, with nowhere to go. Architecture §32.3 carries the vocabulary, §32.3.1 the measured partition and its command, §32.3.2 the reader's alias, §32.8 the migration, and D-34 the decision including why `record` is unavailable |
| A single definition of the closed event-kind set | shipped | `events.KNOWN_KINDS` is an explicit twelve-member frozenset and every sibling takes its kind from it — the ten built kinds of the eighteen, plus `comment` as the permanent alias and `edge_retracted` beside `edge` [measured 2026-08-21]. It was **six** partial definitions, not the four first recorded — `baseline.py` and `provenance.py` were missed. `baseline.py` keeps its own spelling deliberately, with the reason at the declaration. Two tests bind it, one over this repository's own log and one over every sibling's AST, both proven against five mutations. Architecture §32.8 |
| A `unknown_kinds` signal that separates delegation from corruption | shipped | `classify_kind` answers applied, delegated or unknown, `DELEGATED_KINDS` names the folding function per kind, and `fsck` warns only on the third case — moving 1,015 events, 18.09% of the log, out of the false-unknown population. The closed set is checked to be exactly applied plus delegated, and disjoint. Architecture §32.8 |
| The owned tracker as the only store | shipped | The external binary is removed: no `br.py`, every write goes through the engine seam into `.basicly/ledger/`, and `ledger-fsck` gates the log on every commit. Architecture §32 |
| A mermaid render check on every committed block | shipped | `.scripts/check_mermaid.py` renders every block through `render_mermaid.mjs` (mermaid pinned) and runs as the `mermaid` verify check |

## The loop

| Capability | Status | Note |
| --- | --- | --- |
| Single-track loop driven identically by any supported agent | shipped |  |
| Worktree isolation per unit of work | shipped |  |
| Parallel lanes: supervisor, lane mini-loop, serial landing | shipped |  |
| Autonomy grants with a spend ceiling, decision queue, confined decider | shipped | Two of the five decision kinds are delegable: `needs-input` and `escalation` |
| Release automation up to the annotated tag | shipped |  |
| Scope sized by the material a lane actually reads | shipped |  |
| Measured context occupancy recorded beside the forecast on every dispatch | shipped |  |
| VALIDATE as a rung with its own gate, a validator plus a reviewer per lens | shipped |  |
| Hold and Kill as writes an operator's answer actually carries out | shipped |  |
| A named role per judgment step | shipped | All seven reachable; **the declared tier is inert at spawn**, and no supervised pass has yet recorded a role on an argv |
| RETROSPECTIVE on a computed special cause | shipped |  |
| An improvement controller driving a codebase property to a set point | shipped | Has run live and filed one issue; manual-dispatch caller only, by decision |
| A schema-validated handoff artifact at each state boundary | building | Three of eight kinds have a producer, two of those a consumer. The rest refuse nothing, and four of those - `classification`, `change-shape`, `verification-evidence` and `validation-transcript` - are tracked by no work item. Architecture §33 carries the per-kind measurement |
| Tier injection, so a declared tier reaches the spawn | building | The tier is declared on every agent source and validated by lint, and it reaches no spawn. The hook that would rewrite a spawn is `.basicly/core/kit/tier/install_hook.py` and is not installed. On claude the installer declines with a nonzero exit, and across repeated probes no tool-boundary hook fired for an agent spawn there — that host's documented hook contract is approve-or-deny, not rewrite, which is the requirement architecture §17 sets |
| Per-model spend and wall-clock forecast enforced at pass admission | building | The current forecast models working set, not turn count, and that is now measured rather than suspected |
| A supervised multi-lane run with zero human interventions caused by an engine defect | building |  |
| The judged-output contract: a reviewer structurally incapable of seeing the producer's conclusion, a review base recorded before dispatch, re-review scoped to the fix range, late rounds escalating a tier | designed | **Deterministic engine code, not a persona**, which is why it survived the routing landing |
| Cost per landed unit | researching | The instrument the tier claims rest on |

## The board

| Capability | Status | Note |
| --- | --- | --- |
| A published snapshot contract a foreign harness can conform to | shipped | `.basicly/core/schemas/board-snapshot.schema.json`, deliberately not strict, so a consumer meeting an undeclared key counts and reports it rather than erroring. Carries no figure it cannot re-derive |
| A file-only producer that folds the log once and spawns nothing | shipped | Four modules — bounds, row reducers, `.basicly/usage/` sections, assembly — with the fold count and the subprocess count both pinned by spies, because "reads only files" is a claim one convenient import breaks |
| Omit-never-estimate, so an absent source is absent rather than zero | shipped | The schema has no field marking a value as estimated, so a guess would render identically to a billed figure |
| A command that emits a snapshot | designed | **Nothing calls the producer.** `basicly board` has one verb, `validate`, and `build_document` has no caller outside tests, so no snapshot can be produced today |
| A rendered page a human can open | designed | No renderer and no template exist. This is what makes the board unusable rather than merely unfinished, and it is the next unit |
| A conformance kit so another project can adopt the board | designed |  |
| Live modes — a snapshot on the supervisor tick, and a read-only wall view | designed | Sequenced behind the page |

## The work graph

| Capability | Status | Note |
| --- | --- | --- |
| Issues, dependencies, gate results, checkpoints and evidence in a tracked graph | shipped |  |
| Phase derived from tracker state, so resume is a read rather than a replay | shipped |  |
| Atomic publish of the shared export, and a store error charged to the store rather than to the lane's rework budget | shipped |  |
| The scheduler score and rank recorded behind each dispatch | shipped |  |
| A pure, age-free ranking function owned in-process | shipped |  |
| Harness comment markers native to the owned store | shipped | Landed ahead of the steps before it, which is why the differential must run on dual |
| A repeatable ledger import a fresh consumer can run | shipped | Refuses a post-flip ledger |
| A seam-routed surface for a human tracker write, so both stores move together | shipped | Closes the last bypass route the differential can see |
| No committed artifact carries a host path, username or hostname | partial | The path half is shipped. **The identity half covers the running committer only, and both stores carry a second person's** [measured 2026-08-17: the export holds one on 83 of 924 records and an address on 56; the owned log holds one on 211 of 5,616 lines and an address on 56, and all 263 identity-carrying events carry the import's own marker against a positive control of zero live writes]. The pre-commit floor is green over both, correctly, because it builds its rule from the running user. Architecture §32.7.1. The secret-rule mirror is kept in step by convention only |
| The owned append-only event log as the source of truth | shipped | The flip has happened [measured 2026-08-20: `.beads/` is absent, no engine module spawns the external binary, and `basicly.toml` declares one store]. What remains is surface cleanup rather than cutover: the `br`-shaped argv vocabulary is still the write seam's input language, which is why `mirror.py` translates it |
| A consistency check and rebuild, so "the log is the truth" is checkable | shipped | The "reached by nothing" note is **withdrawn as false** [measured 2026-08-20: `ledger-fsck` is a `[[verify.checks]]` entry declared in `basicly.d/basicly-t10ipy.toml` and runs on every `verify --mode full`, reporting 6,271 events over 1,022 records]. `rebuild` is still reached by tests only, which is the honest remainder |
| Provenance on every edge: extracted, inferred, ambiguous | partial | The vocabulary collision is closed [measured 2026-08-20: 1,065 edges fold and `gating_edges` now returns all 1,065, up from 932]. The engine writes `engine` and `dual-write` into the same key this module reads as evidence strength — two axes, one name — and both are now recognised as declared provenance and counted apart in `EdgeFold.writer_labels`. What keeps this `partial` is only that `INFERRED` and `AMBIGUOUS` have still never been written by anything |
| Cross-repo work offers as self-writes in each repository's own ledger | deferred |  |

<!-- docs-claims:end status-view -->

## How this view stays current

A row changes state in the change that lands the behaviour, never in a later cleanup pass.
That rule is now gated for this file: the table and its source cannot disagree, and a
capability cannot be graded twice, because `docs-claims` refuses both.

Two hand-maintained copies remain, on the README roadmap and on the landing page, and
**nothing gates those two**. Rendering them from `status.yaml` is the rest of D-30 and is
not built; until it is, a stale row is possible there and never here.

Architecture decision **D-30** carries the argument and the state of the work.
