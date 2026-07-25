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
| **Cross-repo** | A workspace of independent repos, each owning its ledger; foreign references by qualified id, read-only aggregation across repos |
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
6. **Compaction** (`compaction_level`, `original_size`) — records shrink over time, which
   threatens long-lived evidence (D11 §3).

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

**Cross-repo shape:** each repo owns its ledger under its own prefix; cross-repo references are
qualified ids (`<prefix>-<id>`), which the workspace already does by convention; an aggregator
reads N ledgers read-only and never writes across a repo boundary.

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

## 8. Open questions

1. **Compaction** — do we need it at all if the ledger is append-only and git-compressed? If
   evidence must never be lost (D11), compaction may be a misfeature we should decline to copy.
2. **Ranking** — `br scheduler`'s ranking is currently an unpinned external input to dispatch
   order (D9 flags this). Owning the tracker means owning the ranking function; it must be pure,
   documented, and stable under equal inputs.
3. **Concurrency** — one writer per repo is the harness's model today (the supervisor is a
   singleton). Is a second interactive writer supported, and if so with what locking?
4. **Identity** — content-derived ids are good for idempotence but make renaming impossible. Is
   the id a hash, a monotonic counter per prefix, or both?
