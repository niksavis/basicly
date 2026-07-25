# Work Tracker — Owning the Harness's Core Dependency

Status: **initialization — information gathering, not a build plan.** Opened 2026-07-25. No
schema is frozen and no implementation starts from this document; its job is to record why we
must own this component, what our own usage already tells us it must do, and what we still need
to measure before committing to a design. The decision point is named in §7.

## 1. Why this is not optional

The tracker is not a peripheral integration — it *is* the harness's state. `br` is the single
source of truth for every phase derivation, gate, checkpoint, dependency edge, decision item and
run record; the engine deliberately keeps no side-state. Two consequences follow:

- **Every guarantee the harness makes is downstream of the tracker.** Resumability, the
  engine-disposes/agents-propose split (D2), determinism of engine steps (D9), and the shared
  evidence ledger (D11) are all expressed as tracker reads and writes. A behavior change in the
  tracker is a behavior change in the harness.
- **The dependency is unowned.** `beads_rust` and `bv` are MIT-licensed and excellent today.
  Licenses change, maintainers move on, release cadences diverge, and a breaking change upstream
  lands in *our* critical path. We already carry version-floor knowledge (`.beads/redirect`
  needs ≥ 0.2.16) and version-specific workarounds. That is the shape of a dependency that will
  eventually cost more than it saves.

Owning it is therefore a strategic requirement, not a preference. The counter-argument —
"don't rebuild a working tool" — is answered by scope: we do not need a general-purpose issue
tracker. We need the subset the harness actually calls, which §3 shows is small and already
known.

## 2. Requirements

Stated by the owner, plus what the harness's own use has demonstrated:

| Requirement | What it means concretely here |
| --- | --- |
| **Lives in the repo** | State is committed, diffable, and travels with a clone; no server, no daemon, no external DB |
| **Cross-repo** | One writer per repo ledger; foreign work moves as *offers* through an append-only exchange in `development`, pulled never pushed (§8) |
| **Fast** | Sub-100 ms for the reads the loop makes per advance (phase derivation, gates, ready set) — the loop calls the tracker many times per step |
| **Upgradable** | Schema evolution without a migration ceremony; unknown fields tolerated, never dropped |
| **Maintainable** | Owned by the same toolchain as the rest of `basicly`; no second language, no separate release train |
| **Auditable** | Every state change attributable and reconstructible from history alone |
| **Visualizes work** | Dependency graph, ready set, and progress viewable without a bespoke TUI to maintain |
| **Prioritizes work** | A ranked ready set the loop can consume deterministically, with total ordering and stable tie-breaks (D9) |

## 3. What our own usage already tells us

The harness's tracker surface is **narrow and enumerable** — this is the central finding, and the
reason owning it is tractable. Observed calls across the engine:

- **Records**: `create`, `update` (type, external-ref, acceptance-criteria, description, status),
  `show --json`, `list --json`, `close`, `delete --hard`
- **Structure**: `dep add`, parent-child links, `ready`, `blocked`, `scheduler` (ranked)
- **Evidence**: `comments add`, `comments list --json`, `gate report`, `gate list --robot`
- **Validation**: `lint --json` (per-type template sections)
- **Plumbing**: `where --json`, `config`, JSONL export/import, `.beads/redirect`

Semantics we depend on, which any replacement must preserve:

1. **Content-derived ids** and idempotent re-writes (the decision-queue pattern).
2. **Comment markers as durable, attributable evidence** — four families today
   (`[harness-policy]`, `[harness-decision]`, `[harness-info]`, `[harness-run]`). Comments are
   exported, so they are the shared ledger (D11).
3. **A committed JSONL export plus a three-way merge baseline** (`beads.base.jsonl`) — git is the
   transport and the audit log.
4. **Prefix-anchored commit scanning** for the commit-message gate.
5. **A dependency graph** with parent-child and blocking edges, and derivation of ready/blocked
   from it.
6. **Compaction** (`compaction_level`, `original_size`) — present in the schema but dormant
   here (every record is level 0), and §9.1 declines to reimplement it: it discards evidence to
   solve a size problem git already solves.

Friction we have already hit — each one is a requirement in disguise:

- `lint` templates are not configurable per type, so "acceptance criteria required for every work
  type" had to move into the harness gate (`basicly-kjc5.36`).
- `show --json` spells dependency fields two ways depending on the command that emitted them
  (`id`/`dependency_type` vs `depends_on_id`/`type`) — a silent empty-graph bug (`kjc5.10`).
- `--slug` ids break the commit gate because an internal hyphen reads as a prefix boundary.
- `--acceptance-criteria` takes a single line only.
- Ephemeral records are not linted, so they cannot be used to probe validation.
- Deleting probe records leaves tombstones that the loop then commits.
- No validation or vocabulary for `assignee`; unset on every record (`kjc5.38`).

## 4. Proposed stack (to be confirmed by §7, not yet decided)

**Pure Python inside the `basicly` package. No new runtime dependency, no second binary.**

Reasoning: adopting Rust or Go would reintroduce precisely what we are removing — an external
binary with its own release cadence, platform builds, and upgrade surface. The harness is already
Python 3.14 + `uv`, ships as a wheel, and every consumer already has it. A tracker that ships in
that wheel is upgraded by `basicly install`, tested by the same suite, and gated by the same
hooks.

**Storage inverts beads' model:** the committed append-only JSONL *is* the source of truth, and a
local index (SQLite via the stdlib, or simply an in-process read) is a **derived, disposable
cache** rebuilt from the ledger. Beads treats the DB as authoritative and the JSONL as an export;
for our constraints the inverse is strictly better —

- git becomes the audit log for free, with real attribution and no separate history store;
- merges are line-oriented and append-only, so two machines rarely touch the same line;
- there is no "the DB and the export disagree" failure mode, which is the class of bug
  `.beads/redirect` exists to avoid;
- a corrupt cache is deleted, not repaired.

**Visualization without a TUI.** A maintained TUI is a permanent cost. Prefer generated
artifacts: a `--json` CLI surface for machines, a Mermaid or DOT dependency graph and a static
HTML board emitted by a command, both viewable in any browser or markdown renderer and diffable
in review. A Textual TUI stays possible later; it is not a first deliverable.

**Cross-repo shape:** each repo owns its ledger under its own prefix and is its only writer;
cross-repo work moves as offers through the exchange in §8, so no component ever writes across a
repo boundary.

## 5. Migration and coexistence

A cutover must never be a big bang, because the harness's own development depends on the tracker
working the whole time.

1. **Import** the existing beads JSONL — it is already the format we would read.
2. **Shadow mode**: the new tracker reads the same ledger and answers the same queries
   read-only; a differential test asserts identical verdicts for phase derivation, ready set,
   and gate status across the live tracker's whole history.
3. **Dual-write** for one release, with the old tracker still authoritative.
4. **Flip** the source of truth once the differential test is clean and the telemetry (§6) shows
   no unimplemented surface in use.

## 6. What we must measure first

We should not design a schema from memory of our own usage. `basicly-kjc5.53` extends the
existing tool-usage telemetry from binary-level counting to **subcommand and flag level** for `br`
and `bv`, written to a **committed** ledger so it accumulates across machines and team members
(the current `tool-usage.json` is in the self-ignored usage dir and so tells us only about one
machine).

What the telemetry must answer:

- Which subcommands and flags are actually used, at what frequency, from which call sites.
- Which are used only by *humans* interactively versus by the *engine* — the engine's set is the
  hard requirement; the human's set can be thinner at first.
- Read/write ratio and latency per surface, so the cache design is driven by measurement rather
  than assumption.
- Which surfaces are never used, and can simply not exist.

## 7. Decision point

Revisit when the telemetry has covered a full factory session including a supervised multi-lane
run (`basicly-kjc5.22`). At that point this document is upgraded from initialization to a design
with: a frozen surface list, the ledger schema, the cache decision, and a component breakdown
sized by D8. Until then, no implementation and no schema freeze.

Reading beads_rust and bv sources for reference is explicitly sanctioned while they are MIT —
their id derivation, merge baseline, and ready-set ranking are the parts worth studying, and
their gaps (§3) are the parts worth not copying.

## 8. Cross-repo work exchange — announce, never push

Settled 2026-07-25 by the owner. Several repos are worked at once, and work is routinely
*discovered* in the wrong repo: a bug for `basicly` surfaces while working in `terminal`. The
resolution is Kanban pull semantics, not delivery.

**Each repo's ledger has exactly one writer: that repo.** No repo ever writes into another
repo's tracker. Cross-repo coordination therefore never needs a cross-tracker write, which is
what makes the concurrency story trivial rather than distributed (see §9).

The `development` workspace hosts an **exchange**: an append-only log of *offers*, not
assignments.

- A repo that discovers foreign work **announces** it — an event naming the target repo, the
  summary, and whatever context exists. It does not create a bead in the target.
- Design work brainstormed in `development` (design docs that are not yet ready for any repo to
  implement) is **decomposed in `development`** and its children announced the same way, so the
  exchange is the single place work becomes available.
- Consumers **poll at their own cadence**. A repo checks the exchange when *it* is stable enough
  to take work, pulls an item by creating a bead in its own tracker, and records the offer id as
  provenance. The claim is written back to the **exchange**, never to another tracker.
- Event kinds are append-only and total: `announced`, `claimed`, `declined`, `superseded`. An
  offer's state is a fold over its events, so nothing is mutated and history is the audit trail.

Why offers rather than tasks: an announcement carries no authority. The receiving repo decides
whether the work fits its own priorities, and a repo that never pulls simply has a growing offer
list rather than a corrupted backlog. That is the same engine-disposes/agents-propose stance
(D2) applied across repo boundaries — the announcer proposes, the owner disposes.

Idempotence: an offer id is stable, and the pulled bead records `offer: <exchange-id>`, so a
double-pull is detectable and a re-poll is free. Provenance runs both ways — the bead names its
offer, the offer's `claimed` event names the repo and bead that took it.

## 9. The open questions, answered

Researched against the live tracker 2026-07-25 rather than reasoned from memory.

### 9.1 Compaction — decline it

**Evidence:** every one of our 330 records carries `compaction_level: 0` and
`original_size: 0` — beads' compaction has never run here. The ledger is 761 KB raw, while
git packs *the entire history of the repo* to 543 KB. Git's delta plus zlib already compresses
a ledger of near-identical JSON records better than any record-shrinking scheme, and it does so
losslessly.

So compaction solves a problem we do not have, at a cost that is fatal to D11: it discards
evidence. **Our tracker will not implement lossy compaction.** Growth is bounded three ways
instead — git compression, the ship-time rollup (`basicly-kjc5.50`) which summarises a package
so its cost survives independently of the detail, and, if a single record's line becomes
unwieldy, moving *detail* to a sibling append-only file while the record keeps the rollup.

The early warning to watch is **maximum line length**, not total size: each issue is one line, so
appending a comment rewrites that whole line. Our largest record is already 45 KB against a
median far below that — the `basicly-kjc5` epic, thick with comments. Per-dispatch markers land
on leaf beads rather than the epic, which keeps the distribution flat, but the metric is worth
a check in the surface report.

### 9.2 Ranking — record it now, own it purely later

I previously called `br scheduler`'s ranking opaque. That was wrong, and the correction matters:
it emits `schema: br.scheduler.v1` with a per-item `score`, a `fallback_rank`, and an explicit
`fallback_policy` of `priority ASC, created_at ASC, id ASC`, plus "if scoring evidence is tied or
incomplete, preserve fallback rank". It is versioned and explainable.

Two consequences:

- **Now**: the cheap half of D9's requirement is to *record* the score and rank into the
  dispatch marker, which makes a pass's dispatch order reconstructible without replacing
  anything (`basicly-vkh0.3`).
- **When we own it**: the ranking function must be **pure**, and it must drop `created_at`.
  Age-based ordering makes dispatch order clock-dependent for an unchanged graph, which D9
  forbids for anything outliving the pass. Our ordering: unblocked only, then priority, then
  **descending dependent count** (unblock the most work first — the critical path), then id as
  the final deterministic tie-break. Every term is a pure function of the graph.

### 9.3 Concurrency — single writer per ledger

Answered by §8. One writer per repo ledger; the supervisor is already a singleton per repo (D1),
and cross-repo work moves as offers rather than writes. The exchange itself has many writers but
is conflict-free by construction: every event is its own line with a unique id, so concurrent
appends from different repos touch different lines and merge cleanly.

Within one repo, a second *interactive* writer is permitted: appends are line-oriented, and only
the derived index needs a lock — and the index is disposable, so a lost update to it is repaired
by a rebuild rather than reconciled.

### 9.4 Identity — opaque record ids, content-derived evidence ids

The distinction our own usage has already taught us:

- **Records are mutable** — titles, descriptions and criteria are edited constantly. An id
  derived from content would either drift or lie. So a record id is **opaque and stable**: a
  short random root token, collision-checked, plus a dotted monotonic child suffix
  (`<prefix>-<root>.<n>`), which is what we already read comfortably and which sorts naturally.
  Ids are never reused, and a delete leaves a tombstone.
- **Evidence is immutable** — a decision, a found-info record, a dispatch marker is a fact about
  a moment. Those ids **are** content-derived, which is what makes re-recording idempotent
  (`decisions.decision_id_for`, `run_record.marker_id`).

And no slugs in ids. `br create --slug` embeds hyphens that read as a prefix boundary, which
breaks the commit-message gate — a shipped defect we worked around rather than a hypothetical.
