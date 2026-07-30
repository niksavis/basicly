# Work Tracker — Owning the Harness's Core Dependency

Status: **initialization — information gathering, not a build plan.** Opened 2026-07-25; updated
2026-07-26 from the state-of-the-art review
([`research/2026-07-26-sota-review.md`](../research/2026-07-26-sota-review.md) §2.10); updated
2026-07-28 with the cross-repo topology decision in §8.1. No schema is
frozen and no implementation starts from this document; its job is to record why we must own this
component, what our own usage already tells us it must do, and what we still need to measure
before committing to a design. The decision point is named in §7.

**Read §7 first if you are about to start work.** It carries a licence correction — `beads_rust`
is *not* MIT, and a clean-room boundary now applies to this component. The 2026-07-26 additions
are §5.1 (three risks in the JSONL import path), §9.4's collision budget, §9.6 (provenance labels
on graph edges), and §16 (why we decline the versioned-database alternative upstream chose). The
2026-07-28 change is §8: the shared exchange is replaced by a per-repo mesh, with the reasoning
and the reversal condition recorded in §8.1.

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
| **Cross-repo** | One writer per repo ledger and no shared artifact; foreign work is *offered* by a self-write in the announcer's ledger and taken by a self-write in the target's, with read-only access across the boundary (§8) |
| **Fast** | The loop makes many reads per advance, so per-read cost multiplies. Measured baseline and targets in §10 — an in-process read is ~175× cheaper than one external CLI call |
| **Upgradable** | Every event carries a schema version; unknown fields are preserved on read-modify-write, never dropped. A newer writer's events stay readable by an older reader |
| **Maintainable** | Owned by the same toolchain as the rest of `basicly`; no second language, no separate release train |
| **Auditable** | Every state change attributable and reconstructible from the ledger *itself*, not only from git history — a squash or shallow clone must not destroy the trail |
| **Visualizes work** | Dependency graph, ready set, and progress viewable without a bespoke TUI to maintain |
| **Prioritizes work** | A ranked ready set the loop can consume deterministically, with total ordering and stable tie-breaks (D9) |

### 2.1 Requirements carried forward from defects we have already paid for

The requirements above are what we want. These six are what we have already been
*billed* for — each is a `br` defect that cost real sessions, and the repo rule is that a
dependency's defect is **requirements input for our own replacement** and the proof must become a
committed gate, never a fix applied outside this repo (`basicly-vkh0.6`).

Every one is pinned by a test in `tests/test_tracker_requirements.py`, named for its id. Those
tests assert the *harness's* defence against the defective input — never that `br` still
misbehaves, which would pin us to a bug and break on the version that fixes it. When the
replacement lands, the module runs against it unchanged.

| Id | Defect | What it cost | Requirement on the replacement |
| --- | --- | --- | --- |
| **R1.** Clock | `br` validates `updated_at >= created_at` and refuses its own write when the host clock steps backwards | Flaky landings, and a rework attempt spent on `basicly-m4zv.9`; invisible to the re-run test because a clock step persists and so reproduces | **A timestamp is evidence, never a constraint.** Nothing branches on wall-clock order; total order comes from the single writer's sequence numbers (§9.5) |
| **R2.** Field spelling | One dependency edge is spelled `id`/`dependency_type` by `show --json` and `depends_on_id`/`type` by the `create`/`dep add` echo | Silent empty graphs — reading one spelling returns *no* dependencies rather than an error, degrading every landing order to the caller's (`basicly-kjc5.10`) | **Exactly one spelling per field**, in every command's output. One reader (`br.dependency_edge`) contains the damage until then |
| **R3.** Validation templates | Lint templates are compiled into the binary; a `chore` is never asked for acceptance criteria, and only the description *body* is inspected | The rule "every bead needs acceptance criteria" had to move into the harness's own gate (`basicly-kjc5.36`) | **Validation rules are configuration, not code**, and apply per work type without a rebuild |
| **R4.** Single-line field | `--acceptance-criteria` accepts one line only, and exists only on `update` — so filing a bead is always two calls | Structured criteria are flattened; the harness carries them in the description body instead | **A text field accepts newlines**, and every field settable on update is settable on create |
| **R5.** Id shape | `--slug` mints ids like `basicly-fix-the-thing`, whose internal hyphens read as a prefix boundary | Broke our own `beads-commit-msg` gate (`basicly-jms0`); the standing rule is now "never `--slug`" | **An id is opaque and never re-parsed** — a short root plus a dotted child counter, with no separator that any consumer needs to interpret (§9.4) |
| **R6.** Path leak | The export wrote `source_repo_path` on 328 of 332 records | Published two users' home-directory layouts into a committed, distributed file (`basicly-vkh0.5`) | **No committed artifact carries a host path**, username or hostname; portability is a property of the format, not of a scrubbing pass |

R1, R5 and R6 are already settled in the design (§9.5, §9.4, §12). R2, R3 and R4 are constraints on
the command layer that has not been written yet, and this table is where they are recorded so it
cannot be written without them.

## 3. What our own usage already tells us

The harness's tracker surface is **narrow and enumerable** — this is the central finding, and the
reason owning it is tractable. The list below is a *manual read* of the engine's call sites and is
therefore a lower bound: §6's telemetry replaces it with measurement before anything is frozen.

### 3.0 Measured, 2026-07-30 — the manual read was close, and `bv` is not needed at all

`basicly-vkh0.2` landed the reduction, so this is no longer a lower bound but a count. Regenerate
both numbers with `basicly usage tracker --refresh-surface`; the report reads
`.basicly/ledger/tracker-usage.jsonl` (measurement) against
`.basicly/ledger/tracker-surface.json` (the full surface, generated from `br --help` — a sanctioned
clean-room input per §7, never from source).

Over **1568 recorded invocations** against **br 0.2.16**:

| Question | Answer |
| --- | --- |
| `br` surfaces exercised | **17 of 87** |
| `br` surfaces never used | **70** — 12 of them group namespaces, so **58 real operations** |
| `bv` surfaces exercised | **0 of 141** |
| Read : write calls | **1210 : 358**, about **3.4:1** |
| Unclassified calls | **0** |

Three consequences for the design:

1. **The replacement needs no `bv` equivalent.** Not one of its 141 flags has ever been invoked
   programmatically — it is a TUI a human opens, so it reads the ledger rather than being part of
   it. This is the single largest scope reduction available and it was invisible to the manual read,
   which listed `bv` alongside `br` throughout.
2. **Reads dominate 3.4:1**, which supports §10's derived-snapshot direction — but note the ratio is
   flattered by `comments list` alone (714 calls, 45% of everything). §10's cache decision should be
   driven by that one surface, not by the aggregate.
3. **The engine/interactive split is real and lopsided.** `create`, `ready`, `list`, `dep list` and
   `dep remove` are **interactive-only** — a human at a prompt, never a harness phase. By §6 those
   may be served later or never, which moves five more surfaces out of the hard requirement.

Two honest limits on the sample. It covers many sessions on **one machine**, so an
interactive-only classification reflects how *this* operator works. And `where --json` is a genuine
engine surface (`worktree.py` `_probe_redirect`) that shows as never-used because its only recorded
observation was destroyed while promoting this sample; the next worktree provisioning re-records it.
Neither affects the `bv` result, which is the load-bearing one.

- **Records**: `create`, `update` (type, external-ref, acceptance-criteria, description, status),
  `show --json`, `list --json`, `close`, `delete --hard`
- **Structure**: `dep add`, parent-child links, `ready`, `blocked`, `scheduler` (ranked)
- **Evidence**: `comments add`, `comments list --json`, `gate report`, `gate list --robot`
- **Validation**: `lint --json` (per-type template sections)
- **Plumbing**: `where --json`, `config`, JSONL export/import, `.beads/redirect`

Semantics we depend on, which any replacement must preserve:

1. **Content-derived ids** and idempotent re-writes (the decision-queue pattern).
2. **Comment markers as durable, attributable evidence** — eight families today
   (`[harness-policy]`, `[harness-decision]`, `[harness-info]`, `[harness-run]`,
   `[harness-sizing]`, `[harness-overrun]`, `[harness-cost]`, `[harness-wait]`). Comments are
   exported, so they are the shared ledger (D11) — and the only carrier of cost history, since
   run-records live in the self-ignored `.basicly/usage/` (`basicly-kjc5.50`).
3. **A creation timestamp on every comment.** The wait meter (`basicly-kjc5.51`) derives how long
   a track sat blocked on a human from the interval between two markers, so a replacement that
   drops (or rewrites) `created_at` silently destroys that measurement rather than failing.
4. **A committed JSONL export plus a three-way merge baseline** (`beads.base.jsonl`) — git is the
   transport and the audit log. The export's own `comments` array is read *directly* for
   whole-tracker questions (calibration, cost per landed package): `list --json` caps its result
   set and drops closed records, so the file is the only bulk read — and the only one a fresh
   clone can answer from with no br invocation at all (D10).
5. **Prefix-anchored commit scanning** for the commit-message gate.
6. **A dependency graph** with parent-child and blocking edges, and derivation of ready/blocked
   from it.
7. **Compaction** (`compaction_level`, `original_size`) — present in the schema but dormant
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
cross-repo work moves as offers recorded by each participant in its *own* ledger (§8), so no
component ever writes across a repo boundary and there is no shared artifact to coordinate on.

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

### 5.1 Three risks the 2026-07-26 review found in step 1

Upstream `gastownhall/beads` has moved to **Dolt** as its storage backend and, in doing so, has
explicitly demoted the format our import plan reads. Its own docs: *"The local Dolt database is
the source of truth … `.beads/issues.jsonl` is an export. It exists for viewers, interchange,
migration, and backup. It is **not** the canonical cross-machine sync channel."* Three
consequences, none fatal, all better known now than at cutover:

- **The JSONL path is a second-class citizen upstream and will drift.** Our importer targets a
  format whose owner has deprioritised it. Pin the import against a known-good export and treat
  format drift as expected, not exceptional.
- **`import` is upsert-only.** Upstream states it *"cannot infer that records absent from an
  export were deleted, pruned, or simply never exported."* So a JSONL snapshot **cannot express
  deletion**, and our importer must treat tombstones as a first-class concern rather than
  discovering the gap during the flip. §13's "unknown event kind is preserved and skipped" rule
  handles forward compatibility; this is the different problem of *absence*, and absence in an
  upsert-only format is ambiguous by construction.
- **A one-shot import is the only import.** Because step 1 cannot round-trip deletions, the
  shadow-mode differential in step 2 must compare against the **live tracker**, not against a
  re-import of its export. Comparing two derivatives of the same lossy snapshot would agree with
  itself and prove nothing.

## 6. What we must measure first

We should not design a schema from memory of our own usage. `basicly-vkh0.1` extends the
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

**Correction, 2026-07-26: the licence claim that stood here was wrong.** This section previously
read "Reading beads_rust and bv sources for reference is explicitly sanctioned while they are
MIT". `beads_rust/LICENSE` is titled **"MIT License (with OpenAI/Anthropic Rider)"**. The rider
grants no rights to Anthropic, OpenAI, their affiliates, or anyone "acting directly or indirectly
on behalf of, for the benefit of, or under the direction of" them, and it names "benchmarking,
testing, analyzing, indexing" as restricted use. Full text and analysis in
[`research/references.md`](../research/references.md) §2.

**A clean-room boundary therefore applies to this work.** The replacement tracker must not be
derived from `beads_rust` source. Its sanctioned inputs are:

- **our own ledger's observable data** — which is what §§9–10 are already built on;
- **`br`'s documented CLI contract** — the interface we already consume;
- **[`gastownhall/beads`](https://github.com/gastownhall/beads)**, the genuine-MIT upstream
  original, which covers the same conceptual ground and is the better reference anyway (§16).

This is a supply-chain finding as much as a legal one, and it strengthens §1 rather than
complicating it: a dependency whose licence can be amended with a rider aimed at a class of users
is exactly the unowned-critical-path risk this document was opened about. Not legal advice — if
implementation proceeds, confirm the boundary with someone qualified. Until then the conservative
line costs nothing, because the MIT original is available.

## 8. Cross-repo work offers — announce in your own ledger, never write across a boundary

Settled 2026-07-25 by the owner; **topology revised 2026-07-28** — see the decision entry in §8.1,
which is where the reasoning lives. Several repos are worked at once, and work is routinely
*discovered* in the wrong repo: a bug for one repo surfaces while working in a different one. The
resolution is Kanban pull semantics, not delivery.

**Each repo's ledger has exactly one writer: that repo — and there is no shared artifact that more
than one repo writes.** Every write in the system is a self-write. That is what makes the
concurrency story trivial rather than distributed (§9.3), and it holds without exception.

Work moves as **offers**, not assignments, in three self-writes:

1. **Announce** — the discovering repo creates a record *in its own ledger*, typed as an offer,
   naming the target repo and carrying the whole payload (summary, context, provenance). That is
   its only write. It does not create a record in the target.
2. **Intake** — the target reads its peers' committed ledgers, finds offers addressed to itself,
   and creates a native record *in its own ledger* that copies the offer payload verbatim and
   records the offer id as provenance.
3. **Reconcile** — the announcer reads the target's committed ledger, observes a record whose
   provenance names its offer, and retires the offer *in its own ledger*.

Reading a peer is ordinary git — `git show <default-branch>:<ledger-path>` against a peer
checkout — so it needs no coordination, no write access, and no daemon. Peer discovery is the
mechanism `basicly status --fleet` already ships (`fleet.py`): the basicly-installed repos under a
workspace root, read-only, with an unreadable peer captured as an error entry rather than failing
the sweep.

- **Consumers poll at their own cadence.** A repo checks its peers when *it* is stable enough to
  take work. The claim is a record in the claimant's own ledger; nothing is written anywhere else.
- Design work brainstormed in a workspace repo (design docs not yet ready for any repo to
  implement) is **decomposed where the design lives** and its children announced the same way.
  That repo is a peer like every other, not a hub.
- Event kinds are append-only and total: `announced`, `claimed`, `declined`, `superseded`. An
  offer's state is a fold over its events — now over the union of the ledgers involved rather than
  over one shared file — so nothing is mutated and history is the audit trail.

Why offers rather than tasks: an announcement carries no authority. The receiving repo decides
whether the work fits its own priorities, and a repo that never pulls simply has a growing offer
list rather than a corrupted backlog. That is the same engine-disposes/agents-propose stance
(D2) applied across repo boundaries — the announcer proposes, the owner disposes.

Idempotence: an offer id is stable, and the taken record names it as provenance, so a double-take
is detectable and a re-poll is free. Provenance runs both ways — the taken record names its offer,
and the announcer's `claimed` event names the repo and record that took it.

### 8.1 Decision — a per-repo mesh, not a shared exchange (2026-07-28)

This section previously specified an **exchange**: an append-only offer log hosted in a designated
workspace repo, written by every participant. Revisiting the topology against the requirement in §2
that this harness be *installable into repos it does not own* showed the exchange to be the weaker
of the two shapes, for the reasons below. Recorded as a decision entry rather than a silent
rewrite, because §8 was marked settled and the reasoning is the part worth keeping.

**Adopted: the mesh.** In descending weight:

1. **The exchange imposes a deployment precondition on a distribution.** This harness is
   *installed into repos it does not own* (`basicly install`). Under the exchange, announcing or
   taking work requires a designated workspace repo that is cloned, writable, and pushable — so a
   consumer who installs into two repos and has no workspace repo cannot use cross-repo work at all
   until they create one and grant write access to it. The mesh requires only that peers can *read*
   each other. For a distribution whose proposition is "install it and the capability appears",
   that difference is the whole argument, and it is invisible in the design until someone hits a
   permission error.
2. **Peer discovery is already built here.** `basicly status --fleet` (`fleet.py`) already
   enumerates the basicly-installed repos under a workspace root, reads each one read-only, and
   captures a repo whose snapshot raises as an `error` entry rather than failing the rollup — which
   is exactly the failure behaviour cross-repo reads need. The mesh reuses a shipped mechanism; the
   exchange introduces a new privileged artifact. Reuse-before-reinventing decides this on its own.
3. **It removes the design's only multi-writer artifact.** §9.3's claim previously carried a
   carve-out: the exchange had many writers and was conflict-free *by argument*. Under the mesh,
   "one writer per ledger" is unconditional. A property with no exception is one fewer thing that
   can quietly stop being true.
4. **It narrows the trust boundary.** §11 already treats offers as untrusted input. In the mesh
   that input arrives from an explicit, enumerable peer set, read-only, and we never write to an
   artifact a foreign agent also writes. The injection surface is the same in kind and smaller in
   extent.
5. **There is no bootstrap ordering.** You cannot announce before the exchange repo exists; the
   mesh has no such step.

**The objection that had to be answered first.** §2 requires a state change be reconstructible
from the ledger *itself*. With an exchange, one offer's whole history sits in one file; in a mesh
it spans two. The answer is that **each side records the whole offer**: the announcer's record
carries the full payload, and the taker copies that payload verbatim alongside the offer id. So
neither ledger needs the other in order to be *understood* — only in order to be *reconciled*.
That satisfies the requirement as written, which is about not depending on git history; it was
never a requirement that one file hold both halves of a two-party interaction. The mesh is in fact
the more available of the two: if a participant repo becomes unreachable, the exchange loses that
offer's later events as well, whereas each mesh ledger still fully explains what its own repo did.

**The cost the mesh adds, which the exchange did not have.** Offers now sit in the announcer's own
ledger, so that ledger holds records describing work the announcer will never do. An offer must
therefore be excluded from that repo's ready set, from the scheduler's ranked set (§9.2), and from
phase derivation — otherwise the announcer's own loop will try to dispatch foreign work. So an
offer is a **distinct record kind carrying its own exclusion**, not an ordinary record with a type
the loop is trusted to skip, and §14 carries the property test. The exchange avoided this by
keeping foreign work outside every work ledger; that is a real advantage it had, bought back here
for the price of one explicit filter that is cheap to test.

**Two behaviours to document rather than fix.** Convergence takes two reads — intake, then
reconcile — so the announcer's view of an offer is stale until it reads the target. That is
consistent with "the owner disposes", and no worse than an exchange, which also converges
asynchronously. And reconciliation is **per-machine**: a machine that holds the announcer but not
the target can announce yet cannot observe the take, so the offer reads open locally. That is
staleness, not a stuck offer, and the command must say so in its output rather than leaving a
reader to infer it.

**Peer schema skew is a non-issue by direction.** A peer may run a newer version and write offer
fields we do not understand. Because we only ever *read* a peer and copy its payload verbatim into
our own record, §13's forward-compatibility rule applies with none of the round-trip risk — we
cannot truncate a peer's unknown fields, because we never write back to it.

**What would reverse this.** A requirement that one artifact hold both halves of an offer's
history without reading two repos — an audit obligation satisfiable only from a single file.
Nothing in §2 asks for that today; if something does later, the exchange is the better answer on
that axis and this entry is where to start.

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
  anything. **Landed 2026-07-30 (`basicly-vkh0.3`)**: each `[harness-run]` marker carries
  `scheduler_rank`, `scheduler_fallback_rank`, `scheduler_score` and `scheduler_policy` — the
  schema version, without which a score is an uninterpretable integer — read once per pass so
  every lane is explained against the same answer rather than a blend of several.

  Building it surfaced a fact the plan had not accounted for: **`br scheduler` recommends only
  *unclaimed* work, and a provisioned lane is claimed**, so for most dispatched lanes br has no
  rank at all and the supervisor orders them by adoption. A marker therefore also carries
  `dispatch_rank`, the lane's position in the order the pass actually dispatched. That is the
  field that satisfies "reconstructible" in the ordinary case; the `scheduler_*` fields are null
  when br had no opinion, which is deliberately distinguishable from unrecorded.
- **When we own it**: the ranking function must be **pure**, and it must drop `created_at`.
  Age-based ordering makes dispatch order clock-dependent for an unchanged graph, which D9
  forbids for anything outliving the pass. Our ordering: unblocked only, then priority, then
  **descending dependent count** (unblock the most work first — the critical path), then id as
  the final deterministic tie-break. Every term is a pure function of the graph.

### 9.3 Concurrency — single writer per ledger

Answered by §8, and after the 2026-07-28 topology decision the answer carries no exception: one
writer per repo ledger, no shared artifact, every write a self-write. The supervisor is already a
singleton per repo (D1), and cross-repo work moves as offers each participant records in its own
ledger, so two repos never contend for one file and cross-repo access is read-only in both
directions.

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

  Two refinements from upstream's design, adopted 2026-07-26. First, **state the collision
  budget rather than saying "collision-checked"**: upstream sizes id length from the birthday
  paradox, `P(collision) ≈ 1 - e^(-n²/2N)` where `N = 36^length`, against a declared maximum
  probability, and scales 4 → 5 → 6 characters as the ledger grows. Adopting an explicit target
  turns a hand-wave into a specified, testable property; **adaptive length is safe because
  existing ids never change** — only newly minted ones get longer. Second, a correction worth
  recording: upstream's ids are widely described as "content-based" but are actually derived from
  title **plus creation timestamp plus a random salt**, so they are effectively opaque. That
  validates the split below rather than contradicting it — and it is a good reminder to read the
  data rather than the marketing.
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

### 9.6 Provenance — every edge says how it got there

Added 2026-07-26 from the review. Today a dependency edge is just an edge. An edge a human
asserted during decomposition, an edge Dana proposed from a scope-glob overlap, and an edge the
merge queue inferred after a conflict are **indistinguishable in the graph** — yet only the first
should be trusted, unexamined, to gate a landing.

`graphify` solves the same problem with a label on every derived edge, and the vocabulary
transfers cleanly:

| Label | Meaning here | Disposition |
| --- | --- | --- |
| `EXTRACTED` | explicitly asserted by a human, or mechanically derived from a fact in the repo (an import, a shared file in two scope globs) | trusted; may gate a landing |
| `INFERRED` | proposed by an agent or deduced from a second-order signal (a bounce, a co-occurrence) | usable, but visible as a proposal |
| `AMBIGUOUS` | the derivation is uncertain | **routes a decision item**; never silently gates anything |

Three reasons this belongs in the schema rather than in a convention:

- **It is D11 applied to structure.** We already require evidence to be attributable; an edge is
  evidence about the shape of the work, and it is currently the only kind that carries no
  attribution.
- **It gives `AMBIGUOUS` a disposition path that already exists.** The decision queue is exactly
  where an uncertain machine judgment belongs, and the engine-disposes/agents-propose split (D2)
  already governs it.
- **It makes the coupling-edge feedback loop honest.** When a merge conflict adds a coupling edge
  because "the decomposition missed a coupling", that edge is an inference from one observation.
  Recording it as `INFERRED` keeps a later reader from mistaking it for a declared dependency, and
  makes "how often are our inferred couplings right?" a question the ledger can answer.

The label is a property of the *event that created the edge*, not of the edge, which falls out of
§4's model for free: the fold carries the strongest label any event asserted, and a human
confirming an `INFERRED` edge is a new event promoting it to `EXTRACTED` rather than a mutation.

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

**A peer's ledger is untrusted input.** Offers are written by *other repos' agents* and read by
ours, which makes §8 a trust boundary rather than a convenience. The mesh (§8.1) keeps that
boundary narrow — the peer set is explicit and enumerable, access across it is read-only, and no
foreign agent writes to an artifact we also write — but it does not remove it. Two rules:

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
- **Offers never enter their own repo's work graph** — an announced offer is absent from the ready
  set, from the scheduler's ranked output, and from phase derivation, no matter what state its
  events put it in. This is the one property the mesh (§8.1) adds and the exchange did not need,
  and it must be asserted rather than assumed, because the failure mode is a repo dispatching work
  it announced for somebody else.

## 15. Non-goals

Naming these is how §1's scope argument stays honest. We are not building: a general-purpose issue
tracker; multi-user authentication or authorization; a sync server or hosted service; a web
application; sprint, estimation, or reporting ceremony beyond what the loop consumes; a maintained
TUI (§4); real-time collaboration; or import from third-party trackers beyond the one-off beads
import in §5. Each of those is how a tool like this becomes unmaintainable, and none of them is
required by the loop.

## 16. The rejected alternative: a versioned database

Added 2026-07-26. Upstream `gastownhall/beads` answered the same requirements with **Dolt** — a
version-controlled SQL database with cell-level merge, native branching, and sync over Dolt
remotes under `refs/dolt/data`. It is a serious, coherent answer, and recording why we decline it
is more useful than pretending we never saw it.

**What it does better than an append-only log.** Cell-level merge is genuinely stronger than
line-level for concurrent edits to one record; SQL gives ad-hoc query for free, where we would
hand-roll every projection; branching issue history independently of source branches is elegant;
and multi-writer concurrency is solved rather than avoided.

**Why we still decline it.** Every advantage is bought with the thing §1 exists to remove:

- **It reintroduces the unowned binary.** Embedded mode ships inside their binary; server mode
  needs a `dolt` install. Either way the storage engine is somebody else's release train sitting
  in our critical path — the exact dependency shape we are eliminating, restored under a better
  brand.
- **Their upgrade procedure is the strongest evidence for our position.** Crossing a schema
  migration on a remote-backed database requires *"exactly one designated clone"* to run
  `bd migrate` and `bd dolt push` while *"other clones install the new binary and run
  `bd bootstrap`."* That is a coordinated, human-sequenced, multi-machine ritual, in our critical
  path, triggered by someone else's schema decision. §1 predicted this cost from first principles;
  it is now observed.
- **It contradicts the "lives in the repo" requirement.** A separate ref namespace synced by a
  separate push is not state that travels with a clone; a fresh clone needs `bd bootstrap` before
  the tracker works.
- **The multi-writer capability solves a problem §8 dissolved.** One writer per repo ledger, and
  cross-repo work as offers, means we never need distributed write coordination. Buying a
  distributed database to solve a problem the architecture removed is the expensive way to be
  wrong.

**What we take from it.** The collision-budget maths (§9.4), the `remember`/`prime` split (a
tracker-backed memory with an assembly command, which we have the storage half of and not the
assembly half), and the honest observation that their compaction feature exists because real users
have a growth problem — §9.1 declines it because *git plus the ship-time rollup already bound our
growth*, which is a trade-off we chose, not a mistake they made.

**The fork is genuine.** They made the database more authoritative and the export secondary; we
make the log authoritative and everything else a disposable projection. Both are internally
consistent. Ours is the one that needs no second binary, no daemon, and no bootstrap step — and
those, not merge semantics, are the requirements in §2.
