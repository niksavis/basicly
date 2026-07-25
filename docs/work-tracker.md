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
| **Fast** | The loop makes many reads per advance, so per-read cost multiplies. Measured baseline and targets in §10 — an in-process read is ~175× cheaper than one external CLI call |
| **Upgradable** | Every event carries a schema version; unknown fields are preserved on read-modify-write, never dropped. A newer writer's events stay readable by an older reader |
| **Maintainable** | Owned by the same toolchain as the rest of `basicly`; no second language, no separate release train |
| **Auditable** | Every state change attributable and reconstructible from the ledger *itself*, not only from git history — a squash or shallow clone must not destroy the trail |
| **Visualizes work** | Dependency graph, ready set, and progress viewable without a bespoke TUI to maintain |
| **Prioritizes work** | A ranked ready set the loop can consume deterministically, with total ordering and stable tie-breaks (D9) |

## 3. What our own usage already tells us

The harness's tracker surface is **narrow and enumerable** — this is the central finding, and the
reason owning it is tractable. The list below is a *manual read* of the engine's call sites and is
therefore a lower bound: §6's telemetry replaces it with measurement before anything is frozen.

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

**Storage: an append-only event log is the truth; every other file is derived.**

This corrects an error in the first draft of this document, which claimed append-only properties
while proposing to reuse beads' format. Those are two different designs and only one of them has
the properties we want:

- **Line-per-record snapshot** (what `issues.jsonl` is): a record is one line, rewritten in place
  on every change. Measured: appending one comment rewrites that record's whole line. It is *not*
  append-only, concurrent edits to one record conflict, and the file alone cannot answer "how did
  this get here" — only git can.
- **Append-only event log** (what we will build): every change is a new line; a record's state is
  a fold over its events. Conflicts are rare by construction because two writers append different
  lines. History is *in the data*, so auditability does not depend on git history surviving —
  which matters, because a squash, a rebase, or a shallow clone destroys a git-only audit trail.

So: the **event log is authoritative**, and both the record snapshot and any index are **derived
and disposable**, rebuilt by folding the log. Beads treats a DB as authoritative and the JSONL as
an export; we treat the log as authoritative and everything else as a projection. That removes the
"DB disagrees with export" class of bug that `.beads/redirect` exists to work around, and it makes
a corrupt derivative something you delete rather than repair.

The cost is honest: a fold is O(events) and a naive reader re-folds per query, which is what the
index exists to amortise. §10 measures where that starts to matter.

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
sized by D8. Until then, no implementation of the tracker itself and no schema freeze. Work that
improves the *current* tracker's use — recording the scheduler score (`basicly-vkh0.3`), stopping
the path leak (`basicly-vkh0.5`) — is not blocked by this and lands against the existing tracker.

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
instead — git compression, the ship-time rollup (`basicly-kjc5.50`) which summarises a package so
its cost survives independently of the detail, and the event log itself, which bounds each write
by the size of the change rather than by the record's accumulated history (§10).

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

Within one repo a second *interactive* writer is permitted, and the event log is what makes that
cheap: an append is one line, so two writers do not contend for a record. Only the derived index
needs a lock, and because it is disposable a lost update to it is repaired by a rebuild rather
than reconciled. The lock must be portable (§12) — the atomic write-then-rename the harness
already uses, not `fcntl`.

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

### 9.5 Time — a timestamp is evidence, never a constraint

Ordering comes from the log, not from the clock. Each appended event carries a **sequence
number**, monotonic per ledger and assigned by the single writer (§9.3); the fold reads events in
sequence order and nothing else. Two events with equal or out-of-order timestamps are a normal
occurrence, not a conflict to resolve.

A wall-clock timestamp goes on an event as **evidence** — "when did this happen, roughly" — and
nothing branches on it. Specifically:

- **A write is never refused because of timestamp ordering.** Beads validates `updated_at >=
  created_at` and hard-errors when the machine's clock steps backwards between two writes, which
  an unconverged NTP resync does routinely. That turns a host's clock into a source of tracker
  failures during a long run, in the middle of a landing or a close. We record what the clock
  said and move on.
- **No derived value is a function of a timestamp.** Ranking already drops `created_at` for a
  different reason (§9.2); the same rule holds for staleness, dedup and idempotence, which key on
  sequence and content (§9.4), never on time.
- **Durations are measured on a monotonic clock.** Anything the tracker times itself uses
  `perf_counter`/`monotonic`; the wall clock is only ever *recorded*. The engine already obeys
  this, and a guard test in `tests/test_runner.py` fails on any new wall-clock interval, listing
  the two cross-process exemptions with their reasons.

The general form: **the ledger must be totally ordered by something we assign, so that a
misbehaving host clock degrades the quality of our evidence and never the correctness of our
state.**

## 10. Speed and scaling — measured, not assumed

Measured 2026-07-25 against the live 749 KB / 332-record ledger:

| Operation | Cost |
| --- | --- |
| One external CLI read (`br show --json`) | **113 ms** |
| Full ledger parse in-process (Python, 749 KB) | **3.0 ms** |
| Single record, scan + parse in-process | **0.64 ms** |

The decisive number is the ratio: **an in-process read is ~175× cheaper than one CLI
invocation**, because process spawn dominates everything the tracker actually does. A `loop
advance` makes many tracker reads, so today's per-advance cost is mostly `N × 113 ms` of process
startup. That is the strongest performance argument for owning this, and it does not depend on
our implementation being clever — merely on it being in-process.

**Where the naive design breaks.** Record cost is ~2.3 KB, and parsing is linear at ~4 ms/MB.
Extrapolating: 10k records ≈ 23 MB ≈ ~90 ms for a full fold, which is the point at which
re-folding per query stops being free and the derived index earns its place. Below roughly 2k
records a plain in-process fold beats the external CLI by two orders of magnitude with no index at
all — so **the index is deliberately deferred**, not designed now. That is the measurement the
cache decision waits on (§7), and the rule is: build the index when a measured fold exceeds the
loop's per-advance budget, not before.

**Line-length skew is the other scaling axis.** Median record is 1,942 B, p95 is 4,285 B, and the
maximum is **45,296 B** — the `basicly-kjc5` epic, 23× the median, thick with comments. Under a
line-per-record snapshot every append rewrites the whole line, so the skew is a write-amplification
and merge-conflict hotspot, not just a size curiosity. The event log makes this a non-issue by
construction: an event's size is bounded by the change, not by the record's accumulated history.

## 11. Security and trust boundaries

Barely considered in the first draft. Three boundaries matter.

**Committed means published.** The ledger travels to every clone and, for this repo, to every
consumer of the distribution. So nothing may enter it that we would not publish: no prompts (we
store a digest — `run_record` never sees the raw prompt), no credentials, no file contents, no
machine paths. The existing `secret-scan` hook is the floor, and the tracker's own writes must pass
it like any other content.

This is not theoretical: the review found `source_repo_path` publishing **two users' home
directory layouts across 328 records** (`basicly-vkh0.5`). The requirement for our own tracker
follows directly — **a record is path-free**, and provenance is the repo's identity (prefix, or
remote URL), never a filesystem location.

**The exchange is untrusted input.** Offers in `development` are written by *other repos' agents*
and read by ours, which makes §8 a trust boundary rather than a convenience. Two rules:

- **Offers are data, never instructions.** An offer's text reaches an agent's context, so a
  malicious or merely confused announcement is a prompt-injection vector. Offer content is embedded
  as a JSON literal — the pattern `decisions.decider_prompt` already uses so agent-authored text
  cannot impersonate prompt structure — and never interpolated as prose directives.
- **Pulling is a disposal, not an execution.** Announcing grants no authority (§8), so an offer can
  never cause work to start; a human or the engine decides. That property is what keeps the
  injection surface bounded to "wasted a decision" rather than "ran something".

Validate on read: schema, event kind, size caps, and a rejected event is *quarantined and
reported*, never silently skipped — a silently dropped event is indistinguishable from a lost one.

**Some state must stay machine-local.** Sharing it would be a defect, not a feature: the
supervisor lock (a shared lock breaks takeover), one-time confirm codes (a shared code lets another
machine consume a challenge), worktree sentinels, and high-volume hook telemetry. Anything the
tracker writes is published by default, so the boundary is drawn explicitly rather than by habit.

Subprocess discipline stays as it is: `br` is invoked with an argv list, never a shell string, so
no tracker content can reach a shell.

## 12. Portability

- **No absolute paths, ever** — in any field, including provenance (`basicly-vkh0.5`). A path is
  machine-specific by definition and the ledger is shared.
- **LF and UTF-8 explicitly.** `.gitattributes` already normalises with `* text=auto eol=lf`, but a
  data ledger should not rely on heuristics: mark it explicitly, write `\n` and UTF-8 without
  depending on platform defaults, and read tolerantly (a stray CR must not corrupt a fold).
- **No POSIX-only locking.** The index lock must work on Windows, so no bare `fcntl`; the atomic
  `tmp-write-then-rename` pattern the harness already uses for usage files is portable and is the
  intended mechanism.
- **No new runtime dependency**, which is most of §4's argument: a pure-Python tracker inherits the
  platform matrix `basicly` already tests rather than adding its own.

## 13. Failure modes and recovery

An append-only log fails differently from a database, and the differences are the design's payoff:

| Failure | Recovery |
| --- | --- |
| Torn write (crash mid-append) | Last line is unparseable → quarantine it and report; the fold before it is intact |
| Corrupt derived index | Delete and rebuild from the log; never repaired in place |
| Bad merge (both sides appended) | Both event sets survive; the fold is order-independent for distinct events |
| Bad merge (same record edited) | Two events, both retained; conflict resolution is a *later event*, not a lost one |
| Unknown event kind (newer writer) | Preserved verbatim and skipped by the fold, with a warning — forward compatibility |

Two commands this implies, and they are requirements rather than nice-to-haves: a **`verify`/fsck**
that folds the whole log and reports anything unparseable, unknown, or referentially broken; and a
**`rebuild`** that regenerates every derivative from the log alone. Without them "the log is the
truth" is a claim nobody can check.

## 14. Testability

The properties worth asserting, beyond the differential test in §5:

- **Fold determinism** — folding the same log twice yields byte-identical derivatives.
- **Order independence** — folding a shuffled log of distinct events yields the same state, which
  is what makes concurrent appends safe.
- **Idempotent replay** — re-appending an event with an existing id changes nothing.
- **Round-trip** — every field survives read-modify-write, including fields the current version
  does not understand (the upgradability requirement, tested rather than asserted).
- **Property-based generation** over event sequences, because the interesting bugs are in
  interleavings a hand-written case will not find.

## 15. Non-goals

Naming these is how §1's scope argument stays honest. We are not building: a general-purpose issue
tracker; multi-user authentication or authorization; a sync server or hosted service; a web
application; sprint, estimation, or reporting ceremony beyond what the loop consumes; a maintained
TUI (§4); real-time collaboration; or import from third-party trackers beyond the one-off beads
import in §5. Each of those is how a tool like this becomes unmaintainable, and none of them is
required by the loop.
