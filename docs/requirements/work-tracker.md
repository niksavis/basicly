# Work Tracker — Owning the Harness's Core Dependency

Status: **partially built against an unfrozen design.** Opened 2026-07-25; updated 2026-07-26 from
the state-of-the-art review
([`research/2026-07-26-sota-review.md`](../research/2026-07-26-sota-review.md) §2.10); updated
2026-07-28 with the cross-repo topology decision in §8.1; **status corrected 2026-08-14.**

**This header said "initialization — no implementation starts from this document" for three weeks
after implementation started, and that is the finding, not a typo** [M 2026-08-14]:

```text
.basicly/core/kit/tracker/    events 50k · snapshot 42k · differential 44k · fsck 34k
                              migrate 30k · provenance 29k · ids 18k · scheduler 17k
src/basicly/owned_store.py    TRACKER_MODES = (external, dual, owned)
landed                        the import ran (b97a653) · ranking owned (vkh0.20)
                              harness markers native (s5li)
```

So the document's job has changed under it. **Still true**: no schema is frozen, the surface list is
not declared, and the cache decision is unmade — which is why §7's gate on those three has not
lifted. **No longer true**: that nothing has been built. Code was written to this document's
reasoning without the document being promoted to the design §7 requires, which means the built half
is specified only by its own tests. Reconciling the two is `basicly-vkh0`'s work and it is named in
§7.

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
| **Fast** | The loop makes many reads per advance, so per-read cost multiplies. Measured baseline and targets in §10 — a single-record in-process read is ~15× cheaper than the median external CLI call, and a full fold ~1.9×. Modest, not decisive: speed is a benefit here, never the argument (§10) |
| **Upgradable** | Every event carries a schema version; unknown fields are preserved on read-modify-write, never dropped. A newer writer's events stay readable by an older reader |
| **Maintainable** | Owned by the same toolchain as the rest of `basicly`; no second language, no separate release train |
| **Auditable** | Every state change attributable and reconstructible from the ledger *itself*, not only from git history — a squash or shallow clone must not destroy the trail |
| **Visualizes work** | Dependency graph, ready set, and progress viewable without a bespoke TUI to maintain |
| **Prioritizes work** | A ranked ready set the loop can consume deterministically, with total ordering and stable tie-breaks (D9) |

### 2.1 Requirements carried forward from defects we have already paid for

The requirements above are what we want. These nine are what we have already been
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
| **R7.** Concurrency | Under the engine's own five-lane fan-out the storage layer tore its WAL: four of five lane dispatches died in the pre-flight read, each on a bead it had not been assigned, and `br` marked the failure `retryable: false` | Three lanes recovered on the dispatch rework; `basicly-tcmy.11` reached the rework cap without an agent ever starting and was parked, and the session's L3 grant halted with 43.4M of 60M tokens unspent (`basicly-vkh0.10`) | **N concurrent readers and one writer never corrupt shared state**, and a contention failure that *is* reported is marked retryable so the caller backs off (§9.3) |
| **R8.** Lock scope | Every mutating command serialises behind one `.beads/.write.lock`, and *fails the command* when it cannot take it before the timeout rather than queueing behind it. The engine's lanes share one `.beads` through `redirect`, so every lane's gate contends with every other lane's writes and with whatever else drives the tracker at that moment | Two transient gate failures in one session, 2026-07-30 — a landing's `pytest` gate and a `pre-push` hook, each passing unchanged on the next attempt. A landing flake is not free: it spends a rework attempt against a cap of 2, so a second unlucky landing escalates to a human for a defect that does not exist (`basicly-m4zv.14`) | **Contention waits; a wait that gives up says so.** One writer per ledger, with the lock scoped to the ledger it protects and never to the machine or the home directory (§9.3), and a lock-acquisition failure reported as retryable so the caller backs off instead of the gate failing |

| **R9.** Destructive flush | A mutating command auto-flushed a **426-record database over a 612-record committed export**, deleting 187 records — 47 of them open — and reported success. `br sync --status` on that same checkout already said `JSONL is newer (import recommended)`: the condition was detected and the write allowed anyway. Measured refinement, 2026-08-06: that status is computed from **timestamps** and fires on a healthy checkout where the content is byte-identical, so it cannot be the guard | Recovered only because the export is committed — the database was the corrupt side. Nothing in the tracker layer noticed; three positive-control tests asserting a gate is not measuring an empty set were the only detection (`basicly-b2n2`) | **A publish never shrinks the artifact silently.** A write that would emit fewer records than the file it overwrites reports the shrink and requires explicit intent, and the comparison is on **content, not timestamps**. With the log authoritative and every other file derived (§4), the disagreeing-stores state cannot arise at all — but the derived snapshot still needs the shrink guard |

**R6 argued itself again on 2026-08-09, and the measurement is the argument.** `br`
re-populates `source_repo_path` on **every write** — around 715 records — so a single
session of ordinary work required the scrub to be run by hand **fifteen times**, once
before nearly every commit, each time after `tracker-path-scan` had already blocked the
commit. `basicly-vkh0.5` is closed and the gate does bind; what closed was the *mopping*,
not the leak. That is precisely the distinction R6's requirement column draws —
portability as a property of the format rather than of a scrubbing pass — and the cost of
not having it is now a measured per-session figure rather than an argument.

**R6's other half — the username — was open until 2026-08-15 and it was wider than the
bead that named it** (`basicly-r166`, closed). The owned ledger carried the OS username on
every event, and the committed export carried it on **813 of 876 records**, because br
writes `created_by` on every record it mints and `migrate._plan_record` copies every
non-structural field into the payload verbatim. Inside the ledger it sat on `asserted_by`
(3,176), `created_by` (736), `assignee` (56) and 4 free-text values, not only on `actor`.

Three parts, and the middle one is the exception this design otherwise forbids:

- **Stop writing it.** `redact.redact_committed` is paths **then** identity, on the four
  ledger and export write sites. The order is load-bearing: the placeholder holds
  characters the path rules' tail class excludes, so identity first would leave the
  directory layout published. Identity is deliberately not a `MACHINE_PATH_RULES` entry —
  a username is not a shape, only the running machine knows the string.
- **Scrub what was already published.** `br.scrub_ledger` rewrote **4,812 of 5,081** events
  and `scrub_export` **811** records. This is the one place the design **edits a line rather
  than appending one** (§4.4), because an append cannot un-publish a string; §4.2 reserves
  that for an explicit owner decision and this was one. An event id covers kind plus payload
  and the generation folded into it is not written on the line, so it is derived as the
  occurrence count of an identical `(record, kind, payload)` — a derivation that reproduced
  **all 5,081** stored ids before it was used to write one, and the function refuses the
  whole rewrite if any id fails to re-mint.
- **Gate it.** `tracker-path-scan` now covers `.basicly/ledger/events-*.jsonl` beside
  `.beads/*.jsonl` and reports a `machine-username` rule built per run from
  `getpass.getuser()`. Positive control against the kept pre-scrub ledger: **4,812 findings
  before, 0 after**. Its limit is stated rather than implied — it cannot see a *teammate's*
  username already in the file, which is `[[privacy.denied]]`'s job.

What survives deliberately is the git handle: the rule is word-bounded, and that handle is
the `git+https://github.com/...` install URL the distribution ships.

R1, R5, R6, R7, R8 and R9 are already settled in the design (§9.5, §9.4, §12, §9.3, §9.3, §4). R2, R3 and
R4 are constraints on the command layer that has not been written yet, and this table is where they
are recorded so it cannot be written without them.

R8 is the one entry whose defect **did not reproduce** when it was probed. `~/.beads/` does not
exist on the machine that filed it, and on br 0.2.16 the suite passed 2119 tests under `-n auto`
while 1297 concurrent external `br init` runs were driven against the same host (2026-08-01) — so
the "machine-global lock" the incidents were originally attributed to is not what br does now, and
`basicly.toml`'s comment saying so was retracted rather than left to mislead. It is carried anyway,
because the requirement is a property we want from the replacement and not a bug report about br,
and because the cost is already paid: the containment, if the contention returns, is the signature
entry in `verify.DEPENDENCY_DEFECT_SIGNATURES`, which routes a lock-acquisition failure to
`merge.VERIFY_UNRELIABLE` — bounded by `policy.MAX_UNRELIABLE_GATE_EVENTS` and charged to no
lane's rework budget.

R7 is the one whose gate could not be pointed at `br`. The other six are properties of a *response*,
so the harness's defence against the bad input is directly assertable; this one is a property of a
*store under concurrent load*, and `br` fails it by construction. So the gate
(`test_r7_concurrent_readers_never_observe_a_torn_write_of_the_shared_export`) is aimed at the store
this repo already owns — the committed JSONL export, rewritten by `br.scrub_export` on the commit
path while every lane reads it through `.beads/redirect`. Writing it found our own instance of the
same defect: the scrub truncated the file before rewriting it, and `br.export_records` skips a line
it cannot parse rather than raising, so a reader caught in that window got a **partial issue set
with no error at all** — a silent wrong answer where `br` at least raised. Both halves are now
fixed, the write is atomic, and the gate runs four real reader processes against a live writer with
no retry anywhere in the path, so it cannot pass by giving a reader a second chance. When the
replacement lands it inherits the gate unchanged: that is the property, not an implementation note.

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

**One honest limit on the sample**: it covers many sessions on **one machine**, so an
interactive-only classification reflects how *this* operator works. It does not affect the `bv`
result, which is the load-bearing one.

**And one correction, because the first version of this section named the wrong cause.** It said
`where --json` — a genuine engine surface, called by `worktree.py` `_probe_redirect` on every
provisioning — read as never-used because a single observation was lost while promoting, and that
the next provisioning would re-record it. Three provisionings later the count was still zero, which
is what exposed the real defect: **the spool did not follow `.beads/redirect`**, so a call made with
a worktree as its repo root spooled *inside the worktree* and the loop deleted it at teardown. Every
engine tracker call from a lane was being discarded — not uniform sampling loss, but precisely the
traffic the harness generates while doing work. Fixed in `basicly-vkh0.8`
(`tracker_usage.ledger_root`, mirroring `br.beads_dir`, in both the package and the hook).

The lesson generalises past this bug and belongs with the freeze: **a never-used entry is the only
finding in this report that a measurement error can fabricate**, because absence of a record and
absence of a surface look identical. So every never-used entry that a call site contradicts must be
chased to a cause, never explained away — the `bv` result above is trustworthy for the opposite
reason, that no `bv` call site exists to contradict it.

- **Records**: `create`, `update` (type, external-ref, acceptance-criteria, description, status),
  `show --json`, `list --json`, `close`, `delete --hard`
- **Structure**: `dep add`, parent-child links, `ready`, `blocked`, `scheduler` (ranked)
- **Evidence**: `comments add`, `comments list --json`, `gate report`, `gate list --robot`
- **Validation**: `lint --json` (per-type template sections)
- **Plumbing**: `where --json`, `config`, JSONL export/import, `.beads/redirect`

Semantics we depend on, which any replacement must preserve:

1. **Content-derived ids** and idempotent re-writes (the decision-queue pattern).
2. **Comment markers as durable, attributable evidence** — **twelve** families, counted from
   `src/basicly/` on 2026-08-14: `[harness-artifact]`, `[harness-classification]`,
   `[harness-cost]`, `[harness-decision]`, `[harness-info]`, `[harness-overrun]`,
   `[harness-policy]`, `[harness-review]`, `[harness-run]`, `[harness-side]`,
   `[harness-sizing]`, `[harness-wait]`. Comments are exported, so they are the shared ledger
   (D11) — and the only carrier of cost history, since run-records live in the self-ignored
   `.basicly/usage/` (`basicly-kjc5.50`).

   **This line read eight until 2026-08-14, and four families had already shipped.** Nothing
   binds the list. That matters more here than a stale number usually would: a family is a wire
   format the replacement must read, so a list that undercounts them under-specifies the
   migration, which is this document's whole job.

   **The correction undercounted too, and that is the useful part.** The first attempt at this
   line said ten, from reading two recent landings rather than counting the tree. A search then
   returned every family in `src/basicly/` and the answer was twelve. Two probes before it had
   returned ripgrep's own help text, because `-oh` parses as `-o -h`. Count this list with a
   command, never from what you remember shipping.
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

## 3.1 What other projects tell us — and which half of it is verified

Literature research across four tracker lineages, 2026-08-06. The `external-review` rule
binds here: a README is a claim, and this section separates what was **checked against
docs, source or an issue tracker** from what was only **read**. Nothing below has been
reproduced by us, and none of it may be cited as settled until it is.

**Verified against a primary source, and load-bearing for our design:**

- **Our own ancestor documents our pathology.** `beads`' author publishes that merge
  conflicts are common, that a repo should be kept under 200–500 issues via `bd cleanup`,
  and that agents bulk-reading the JSONL fail past ~25k tokens; its issue #534 records
  "--no-db mode completely broken". A maintainer describing his own failure modes is the
  strongest evidence available, and our WAL corruption under five-lane fan-out is that
  lineage's signature defect rather than our bad luck.
- **Structure must be edges the system owns.** GitHub shipped hierarchy *inside markdown*
  as tasklists, then retired the feature in April 2025 and replaced it with first-class
  sub-issue edges. Prose-embedded structure fails.
- **One mutable shared document corrupts itself.** Task Master's single `tasks.json` has
  its own tooling failing to parse its own output (#786). This is a warning aimed directly
  at our derived snapshot, and it is why validation-on-write is not optional.
- **Fossil is the one long-lived proof point** for canonical-log-plus-materialised-query:
  event-sourced ticket changes with per-repo SQL projection, running sqlite.org for 18
  years. It is the shape §4 adopts, and explicitly *not* beads' writable-cache-plus-export.
- **Configurable workflow is the most-cited reason teams leave Jira**, and Linear's
  counter-position is five fixed statuses with no custom workflows. Fossil's own bug-theory
  document made the same argument in 2010. Three independent sources, one direction.
- **Spec-driven tooling conflates two products.** A cited benchmark records 2,577 markdown
  lines generated for 689 lines of code, and Spec Kit's own discussions say it "creates the
  illusion of work". The lesson we take: *work state* is small, structural and must be
  reliable; *generated planning prose* is large and disposable. Our tracker stores the
  first and points at the second.

**Read but NOT verified — treat as hypothesis, and check before building on any of it:**

- **`git-bug`'s merge story.** Operation-based CRDT with Lamport clocks and
  deterministic merge is described in its *design document only*; its behaviour under real
  concurrent load was not observed, and its adoption stalled for reasons we did not
  establish. Our §4.1 sequence deliberately takes the weakest useful piece of this idea
  rather than the machinery.
- **The in-repo-file tracker graveyard.** Two independent post-mortems agree the causes were
  UUID/file proliferation, unsolved merge semantics, and no web UI for non-committers. **Half
  of those do not apply to us** — one operator, agents, no casual reporters — so this is
  weaker evidence against a git-native design than it first appears. Say which half you are
  relying on.
- **Field and nesting telemetry.** No published study isolates used from unused tracker
  fields, and no telemetry on JQL ad-hoc versus saved-filter usage was found. Our own
  measurements (`assignee` on 76/604, `notes` once, eight distinct type values) are the only
  hard numbers we have, and they are ours alone.
- **Vendor sufficiency claims are marketing.** Linear's "covers 90% of needs" is docs-adjacent
  advertising, not a measurement.

**What would change the design.** If lanes ever write tracker state *from worktrees* and
merge through git, rather than serialising through the base checkout, then union-merge
becomes load-bearing and §4 should flip to per-item append-only operation files —
`git-bug`'s shape, minus the Lamport clocks, since commit order suffices for one repo.
Today `.beads/redirect` routes every lane's writes to one checkout, which is precisely what
makes the simpler design valid. **That redirect is therefore a design dependency, not an
implementation detail**; if it goes, re-open this decision.

## 4. Proposed stack (to be confirmed by §7, not yet decided)

**Pure Python inside the `basicly` package. No new runtime dependency, no second binary.**

Reasoning: adopting Rust or Go would reintroduce precisely what we are removing — an external
binary with its own release cadence, platform builds, and upgrade surface. The harness is already
Python 3.14 + `uv`, ships as a wheel, and every consumer already has it. A tracker that ships in
that wheel is upgraded by `basicly install`, tested by the same suite, and gated by the same
hooks.

**It ships as a kit** (owner, 2026-08-05). "Inside the package" is where it *lives*, not what it
*depends on*: the tracker is scripts plus data files in the repo, consumable with **zero `basicly`
imports and nothing on `PATH`** — the same unit of delivery as the tier-injection kit
([`.basicly/core/kit/tier/README.md`](../../.basicly/core/kit/tier/README.md)), proven in this shape at
`.basicly/core/kit/`. So another harness can adopt it the way we adopt `br` today, minus the
binary.

Three reasons this is a requirement rather than a nicety, and the first is the load-bearing one:

1. **It turns `1.0.0`'s consumer criterion into a test instead of a claim.** "No external binary in
   the critical path" (`basicly-ctdz`) is provable by driving the kit under `env -i` with `-S -I`,
   which is how the tier kit is checked — *not by asserting it*. A tracker that merely lives in our
   package can only be argued about.
2. **It de-risks the migration.** The engine reaches `br` through 76 `run_br` references across 14
   files and **17 distinct subcommands**, with no single read choke point — `show --json` is parsed
   at 12 sites under four different absence contracts (`basicly-tcmy.14`). A kit can be built and
   tested standalone and then swapped behind that one seam, rather than as 18 simultaneous parser
   rewrites.
3. **The data outlives the tool.** A work ledger is the longest-lived artifact the harness owns. If
   `basicly` is ever abandoned the ledger and its scripts must stay usable, which is a property no
   in-package-only design has.

**The dependency direction is one-way and gated: the engine imports the kit; the kit imports
nothing.** This is the amendment that makes the boundary safe rather than merely stated, because a
boundary with no shared code invites the defect this repo keeps paying for — two copies that
disagree. `session_issue_ids` had a second copy in `loop_state` that followed a narrower walk and
disagreed **by 14 beads on a real root** (`basicly-tcmy.30`); the context ceiling had two
implementations that reached opposite conclusions about a bead's fate (`basicly-7kxq`). The
direction is gated by `.basicly/core/hooks/kit-boundary.py`, wired as a `[[verify.checks]]` entry
in `--mode full` (what CI runs) and as a `pre-commit` hook that ships to consumers with the kit —
so it is a CI failure and a commit failure, not an intention.

**Corrected 2026-08-06 (`basicly-vkh0.16`).** This paragraph previously named `lint-imports` as
the enforcement, on the grounds that it is already a live `[[verify.checks]]` entry. That was
**unenforceable, not merely unimplemented**, and the distinction matters: import-linter analyses a
single `root_package`, declared as `basicly` in `.importlinter` with containers `basicly` and
`basicly.renderers`. The kit is flat modules with no `__init__.py`, outside that package and not on
`sys.path`, so the tool never opens a kit file — no contract that could have been added there would
have reached it. Measured rather than reasoned: `tests/test_kit_boundary.py::test_import_linter_cannot_see_a_kit_violation`
seeds `import basicly.config` into a kit beside a staged copy of the package and records
`lint-imports` reporting `2 kept, 0 broken` while the new gate fails on the same line. A fail-open
gate is indistinguishable from a pass, which is the exact shape `basicly-tcmy.2` rewrote the
`.importlinter` contracts to escape — this document had recreated it one section later.

What the kit boundary forbids, stated so it is not rediscovered: the kit may not read `basicly`'s
config loader, its logging, its session state or its policy module. It reads its own committed data
and takes everything else as arguments. The tier kit's §6 rules apply unchanged — it is not a
security boundary, it never calls the network or an LLM, and it never guesses.

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
index exists to amortise. §10 measures where that starts to matter, and **§4.6 is the one field that
answers the common query without an index at all**.

**The derived snapshot is gitignored, not committed** (settled 2026-08-06 under review). Calling a
file derived and then committing it recreates the dual-store failure this whole design exists to
escape: two branches each rebuild it, any record changed on both sides is a same-line conflict git
cannot union-merge, and until someone rebuilds, the repo holds two sources of truth that disagree —
which kills requirement 1 in §4.3. It is regenerated by a post-merge/post-checkout hook and lazily
on a stale read. **Its first line carries the id and count of the last folded event**, so any reader
can detect staleness against the log; without that, a crash between append and rename serves stale
state forever with nothing to notice it.

Rotation for §4.3's requirement 8 is by period (`events-YYYY.jsonl`), **archived and never pruned**,
because requirement 6 folds the whole history. `rebuild` therefore globs `events-*.jsonl` **by
contract, not by convention**. Rotation alone does not reduce steady-state fold cost — the same
events are parsed however many files hold them — so the scaling answer is a **derived checkpoint
snapshot at each rotation boundary**: steady state becomes checkpoint plus current file, while the
full-history fold stays available.

### 4.1 Ordering — the per-item sequence

Every event carries a **per-item integer sequence number**. The writer reads the item's current max
and writes max+1; ties break by event id. The fold **sorts into that canonical order before
folding**, which is what makes "the fold is a deterministic function of the event *set*" true rather
than aspirational.

This is necessary and it is not a CRDT. Idempotency by event id handles union-merge **duplication**
and does nothing about **ordering** — and status is inherently ordered: `open→in_progress→done` and
a `done→open` reopen fold to different states depending on sequence. `merge=union` concatenates
conflicting hunks in arbitrary side-order and guarantees neither ordering nor dedup, so a
genuinely commutative fold over raw events *would* be a CRDT, which §15 rejects. One integer field
and one sort rule is a degenerate Lamport clock without the subsystem, and it is the whole cost.

**A timestamp may not be the sort key.** §9.5 makes time evidence rather than constraint for a
reason: one skewed clock would resurrect a `done` item.

Two branches incrementing the same item's sequence concurrently produce a **visible,
fsck-reportable fork**, which is strictly better than silent misordering — a conflict you can see
beats a state you cannot explain.

### 4.2 Secrets, size, and the committed-ledger trust boundary

Agents paste command output, so a token, an `.env` fragment or an absolute path **will** eventually
be written. Every property that makes this design good makes a leak permanent: append-only, delete
is a tombstone, archives are never pruned, and true removal means a history rewrite this repo
requires explicit confirmation for.

Nearer and more concrete: this repo's own rules forbid committed machine-specific paths, so a
harness worktree path landing in a gate event would trip that gate and **wedge tracker writes
entirely** — the tracker would be unable to commit its own state.

The control is prevention, inside the validation that already runs on every write: a **redaction
pass** (secret patterns, absolute paths rewritten repo-relative) and a **per-event size cap**. The
cap does double duty — §4.4 needs it as the interleave bound. Comments are already 45% of tracker
traffic, so agent verbosity is the growth driver and the cap is the only thing bounding it.

**The cap truncates; it never refuses, and it never conceals that it truncated.** Those are the two
wrong answers. Refusing an oversized write loses the event — the fact that a gate ran, along with its
output — and quietly clipping the payload makes a cut comment indistinguishable from a short one, so
a reader cannot tell whether it is looking at the evidence or at a fragment. So an oversized payload
is stored cut to the cap, carrying `truncated: true` and `original_length` beside it. The reader
learns both that evidence was dropped **and how much**, which is the growth bound §9.1 leaves open.
Four rules make it safe:

- **Only free-text payloads truncate.** Never a field the fold reads — ids, `seq`, `kind`, status,
  provenance, or §4.6's totals. Truncating one of those would make a derived value depend on the cap,
  which breaks the fold determinism §14 asserts.
- **Redact, then truncate, then measure.** Redaction can *lengthen* text (a matched pattern becomes a
  placeholder), and a cut through the middle of a secret can defeat the pattern that would have
  caught it. `original_length` is therefore the length of the redacted payload, not of what the agent
  pasted — which is the honest number anyway, since the raw bytes were never ours to keep.
- **Cut on a character boundary, and name the unit.** A byte-sliced UTF-8 payload stops being
  decodable and takes the whole line down with it. `original_length` is in **bytes** and the field
  says so; a length whose unit a reader has to guess is worse than no length.
- **Truncation is a write-time property of the event, not a later rewrite.** It happens once, before
  the event is authoritative, and nothing revisits it. That is the whole difference between this and
  the compaction §9.1 declines.

### 4.3 The ten requirements, and the weakest link in each

Stated by the owner 2026-08-05. Two are restated rather than claimed, because overclaiming a
capability on a consumer-facing surface is what the Quality Gate rule forbids.

| # | Requirement | Weakest link |
| --- | --- | --- |
| 1 | Single source of truth for where implementation stands | Fails if the snapshot is ever committed (§4) |
| 2 | Resume at the correct place in a new session | The claim race — see §4.5 |
| 3 | A team can organise work with it | Ordering across machines; needs §4.1's sequence |
| 4 | Work is transparent | Committed plain text, diffable in review |
| 5 | Reconstruct work history for analysis | Needs `field` events, not only status |
| 6 | Greenfield reimplementation from history | **Restated** — see below |
| 7 | Recover when defective | `rebuild`; repairs only by appending |
| 8 | Partial archival at size | Needs the rotation checkpoint, not rotation alone |
| 9 | Work reports | Fold over events |
| 10 | Visualisation | **Restated** — on-demand, not real-time |

**Requirement 6 is spec, sequencing and rationale reconstruction — not the software.** A ledger can
faithfully rebuild *what was decided, in what order, and why*, and with the commit sha recorded per
landed item it can point at what was built. Literal reimplementation would need every interface
decision articulated in events, which no write-time gate can verify. To make even the honest version
real, **decisions and acceptance criteria must be first-class events carrying their reasoning**, not
prose buried in a description field.

**Requirement 10 is on-demand regeneration.** A generated Mermaid/DOT graph and a static HTML board
rebuilt on write is not real-time, and calling it that would be the same overclaim one notch smaller.

### 4.4 Concurrency and resilience

Single writer per ledger, one lock, snapshot published write-temp-then-atomic-rename, events by
plain append, writes only from the base checkout. Contention is reported as **retryable** so callers
back off — R7 and R8 discharged.

- **Torn line.** Before appending, check the last byte is `\n` and write one first if not, or the
  next append concatenates onto a partial line and corrupts a **good** event. The fold tolerates
  exactly one unparseable *trailing* line silently; interior garbage is an fsck finding, quarantined
  by line number and **never edited**.
- **`O_APPEND` is not the guarantee — the lock is.** POSIX makes the offset update atomic per
  `write()` on a regular file, but Python's buffered writer flushes in ~8 KiB chunks, so one logical
  line larger than that becomes several syscalls a concurrent appender can interleave between; and
  `O_APPEND` is not atomic on NFS at all. §4.2's size cap bounds the exposure.
- **No `fsync` per event.** The push is the durability boundary. Stated explicitly so nobody later
  adds one and destroys the millisecond-scale lock hold that makes single-writer viable.
- **An orphaned lock must not wedge every lane.** The lock file carries a pid and a timestamp and is
  stolen when the pid is dead or the lock outlives a multiple of the expected hold. `O_CREAT|O_EXCL`
  plus that steal rule, because **`fcntl.flock` does not exist on Windows** and §12 commits to three
  platforms.
- **Encoding and line endings.** Declare `events*.jsonl -text` in `.gitattributes` or a Windows
  `autocrlf` checkout rewrites the ledger in place, and pass `encoding="utf-8"` to **every** `open()`
  — on Python 3.14 the default is still locale-dependent, so an unmarked open on a cp1252 host
  corrupts on the first non-ASCII comment.
- **`fsck` repairs only by appending corrective events**, never by editing lines, or it quietly
  becomes an editor and the log stops being the truth.

### 4.5 Claiming, and forward compatibility

With concurrent lanes, `ready` and the status write must be **one locked read-check-write inside the
kit, not two CLI calls**, or two lanes take the same item. Every event carries an opaque `actor`
string — wanted anyway for requirement 5 — which is **not** assignee-as-person modelling; a lane
claim is a lease.

Forward compatibility has to be tolerant in the right direction, which the first draft had backwards:

- The fold **skips unknown event kinds and unknown fields, preserving them verbatim** on any rewrite.
- `fsck` **warns** on unknown rather than erroring, or an old reader hitting a newer ledger reports
  false corruption.
- One `format_version` event. A reader below it **still reads but refuses to write**.
- The limit no rule fixes: a new kind that semantically supersedes an old one makes old readers
  silently wrong. The discipline is therefore **never change a kind's meaning, never reuse a kind
  name, only add kinds and optional fields.**

**The line encoding is open, and §7 still gates it.** Three candidates, raised 2026-08-05 when the
owner asked whether a line-oriented format can serve a *graph* at all. The framing that resolves it:
a ledger and a graph are not competing formats but different layers — the ledger is the write shape,
a graph is a read projection — so the question is only what each layer's lines look like.

| | **A. Event-object JSONL** | **B. Fact-per-line quad log** | **C. Hybrid** |
| --- | --- | --- | --- |
| Line | `{seq, ts, kind, record, …, prov}` | `<seq> <subj> <pred> <obj> <prov>` | A is truth, B is the derived graph |
| A graph? | No — edges implicit, found by folding | **Yes** — every line is an edge | Yes, in the projection |
| Agent token cost | High; keys repeat per line | Lowest | Low on the read path |
| Graph query | Fold, then traverse | `rg` over the log answers directly | `rg` over the projection |
| Prose (1523 comments today) | Natural | Awkward; needs content-addressed blobs (§9.4) | Natural |
| Parse safety | High | Needs a strict encoder | High |

**B is closer to this design than it looks**: it is the shape of N-Triples/N-Quads, whose fourth
element exists precisely to carry provenance — which §9.6 already requires per *event*. In B the
ledger and the graph are one artifact. Its real cost is prose, which §9.4's content-derived evidence
ids can absorb.

**Recommendation, not a decision: ship C.** JSON objects for the authoritative log (safe,
extensible, and the shape our run-record and usage ledgers already use), plus a derived edge list in
B's shape and a derived record snapshot. It costs nothing architecturally because derivatives are
already mandated disposable, and it leaves the door open to collapsing to pure B if measurement ever
shows the fold is the bottleneck.

**The measurement that should govern this, taken 2026-08-05**: the current tracker is 603 records /
2,112,691 bytes, median line 2,605 B, and a full open-read-parse costs **5.8–7.6 ms** while a full
serialize-rewrite-rename costs **5.5–5.8 ms**. Two conclusions. The machine does not care which
format we pick at this scale — so **do not choose on parse speed**. And reading the whole ledger is
on the order of half a million tokens, so *no* on-disk format saves an agent that reads all of it:
the dominant variable is whether a **scoped view** exists (one bead plus its edges plus its open
blockers), which is a command rather than a format. What agents handle well is a small, explicit,
labelled edge set — not traversal of a large one.

**Visualization without a TUI.** A maintained TUI is a permanent cost. Prefer generated
artifacts: a `--json` CLI surface for machines, a Mermaid or DOT dependency graph and a static
HTML board emitted by a command, both viewable in any browser or markdown renderer and diffable
in review. A Textual TUI stays possible later; it is not a first deliverable.

**Cross-repo shape:** each repo owns its ledger under its own prefix and is its only writer;
cross-repo work moves as offers recorded by each participant in its *own* ledger (§8), so no
component ever writes across a repo boundary and there is no shared artifact to coordinate on.

### 4.6 The running aggregate — the tail answers the common query, the fold stays the authority

**Every event carries the value of the item's running aggregates as they hold immediately after
that event.** One field — call it `totals` — and the overwhelmingly common query, *what is this
item's spend, how many attempts has it had, how many events does it carry*, is answered by reading
the item's last event instead of folding its history. That is the mechanism that makes §10's
deferral of the index defensible rather than a hand-wave, and it costs one field per line.

Absorbed as a **concept** from the only production append-only journal in the 2026-07-26 review, and
designed here from first principles: that project's source is permanently out of bounds as an
implementation reference on a licence question the owner declined to litigate
(the review's Appendix A §2.3), so what follows owes it the idea and nothing else — no port, no
snippet, no line ranges. This is the clean-room boundary Appendix A §2.1 already imposes on the
tracker work, applied to a second source.

Four rules, and the first is what keeps a denormalized total from recreating the dual-store defect
§4 exists to escape:

- **The fold is the authority; a carried total is a cache that happens to live in the log.** Any
  reader that must be *right* folds. `fsck` (§13) recomputes the fold and reports every event whose
  carried totals disagree with it, which is what makes the denormalization checkable instead of a
  second source of truth — and a disagreement is a **finding, never a repair in place** (§4.4).
- **One accumulator, called from both sides.** The writer computes the totals by calling the *fold's*
  accumulator over `(predecessor totals, this event)`, never a hand-written increment. Two copies
  that disagree is the defect this repo keeps paying for — `session_issue_ids` disagreed by 14 beads
  (`basicly-tcmy.30`), the context ceiling disagreed about a bead's whole fate (`basicly-7kxq`) — and
  a denormalized aggregate is exactly the shape that invites a third.
- **Only pure functions of the events qualify, and only per item.** A carried value must be a pure
  function of the events up to and including its own: counts, sums, and the last status. Never a wall
  clock (§9.5), never anything read from outside the log. Per *item* rather than per ledger, because
  the writer already reads the item's max sequence to assign the next one (§4.1) — so the
  predecessor's totals arrive in a read it is making anyway — while a ledger-wide counter would put
  every item behind one number and fork on every branch.
- **The totals are trustworthy exactly when the item's sequence chain is unforked.** Two branches
  appending to one item both compute from the same predecessor, so after a union merge the tail
  carries totals that omit the other side. This needs no new detector: it is the same visible,
  fsck-reportable fork §4.1 already produces, and the rule is that a forked item's carried totals
  are **void until a fold restates them**. A cache with a known invalidation condition is safe; one
  without is the hand-wave.

**What the tail read actually costs, stated rather than implied.** Whole-ledger totals are the last
line of the current file. A single item's totals are a **reverse scan that stops at that item's first
hit** — cheap in the ordinary case, and bounded in the worst by rotation, because §4's checkpoint at
each rotation boundary carries every item's totals as of that boundary. That last part is a
**requirement on the checkpoint**, stated here rather than assumed, because the bound depends on it:
without it the reverse scan for a long-idle item walks the whole archive. So the bound is "current
file, then one checkpoint", never "the whole history". That is the claim the deferral in §10 rests
on, and it narrows the index's trigger: the index earns its place when a **cross-item** query cannot
be served this way, not merely when some fold got slow.

**The second payoff is evidential.** Because the total is recorded at the moment of the write, *what
did this item's spend say when this dispatch marker was written* is answerable without folding
anything — which is what a spend-accuracy check needs and what a snapshot holding only the present
cannot give it.

## 5. Migration and coexistence

A cutover must never be a big bang, because the harness's own development depends on the tracker
working the whole time.

1. **Import** the existing beads JSONL — it is already the format we would read. **Ran once,
   2026-08-07 (`b97a653`): 643 records as 3,775 events, every one carrying provenance
   `EXTRACTED`.** `migrate.import_snapshot` had no caller, no `main()` and no CLI surface, so it
   was a **one-shot that could not be repeated** — it had drifted 24 records behind the export by
   the following day and 200 behind by 2026-08-14.

   **Fixed 2026-08-14 (`basicly-vkh0.23`): `basicly tracker import [--dry-run]`.** The dry run
   reports how far behind the ledger is and writes nothing; the real run brings it up to the
   export and reports what it added. It **refuses a ledger that already holds a post-flip
   record**, and the dry run reports that same refusal rather than a count, because a preview
   saying "would add 200" for a run that will refuse is worse than no preview. No `actor` is
   recorded — and `basicly-r166` is **closed**: the OS username is out of both committed stores
   (R6 below).

   **Run 2026-08-15 (`basicly-u4xu`), in the order that bead's do-not-re-import rule requires**:
   import while still `external`, declare the residual baseline, then flip. The ledger holds
   **5,081 events over 873 records** against an 876-record export; the residual 3 are beads filed
   by hand through `br` directly, which `basicly-vkh0.24` covers and which are deliberately left
   as that bead's own demonstration.

   The entry point is the CLI rather than a kit `main()`, on a measurement rather than a
   preference — `migrate.py` has three tokens of size headroom and none on density. So §4's
   promise that the kit is consumable with **zero basicly imports and nothing on PATH** still has
   no entry point of its own, and that is a named gap rather than a closed one.
2. **Shadow mode**: the new tracker reads the same ledger and answers the same queries
   read-only; a differential test asserts identical verdicts for phase derivation, ready set,
   and gate status.

   **Not "across the whole history", and that clause was the deadlock** (`basicly-c357`,
   landed 2026-08-14). Step 2 proves **the dual write agrees**, not that history agrees.
   `vkh0.23` was right that a consumer needs a re-runnable import and `u4xu` was right that
   closing this repo's historical gap by re-importing would leave the owned side tracking
   the external one — both hold, because they are about different records. The run is now
   judged on records created after the flip, and the pre-existing delta is **declared**:

   - A record the **ledger** holds is classified by the marker its own producer wrote —
     `migrate.py` stamps every extracted event with `imported_from`, so no flip point has to
     be kept in step with the tree. All 643 carry it [M 2026-08-14].
   - A record the **reference** holds and the ledger does not has no ledger event to
     classify, so `basicly tracker shadow --declare-history` captures that set once, at the
     flip, into a committed sidecar. A **second declaration is refused**: re-declaring after
     the dual write has begun would absorb a genuine failure into history, which is the same
     shape `u4xu` refuses re-importing for, one artifact over.
   - **An empty in-scope population is inconclusive, never clean.** Scoping leaves it empty
     until the flip happens, so the run still refuses to license step 3 — measured today at
     0 in scope, 643 imported, 375 disagreements excused as history and 200 undeclared.
   - A **refused reference voids the run whatever the scoping says.** The boundary decides
     which records are judged, never whether the reference was the live tracker.

   **Ran 2026-08-15 on `dual`, with an empty declared baseline** — so nothing is excused by
   construction and every disagreement from here is a real finding rather than an argument
   about what predates the rule. Current verdict: `clean: no`, `conclusive: yes`, and **the
   whole of the gap is one query** — **372 disagreements, every one `query='gates'`**, zero on
   records, phase or ready. The owned side reports `missing` for every gate because gate
   *history* was never imported; that is what the one-shot `br gate list --robot` dump exists
   to close, and until it runs the differential cannot license step 4 however healthy the
   record comparison is.
3. **Dual-write** for one release, with the old tracker still authoritative. **Active since
   2026-08-15**: every accepted write also lands in the owned ledger through `br._mirror_write`,
   which **raises** on any failure rather than logging, so a write surface with no translator
   stops the work instead of silently diverging. Two defects in it are open and both were found
   by using it — `basicly-e2mz.23`, the mirror failing **open** when the tracker-mode reader is
   unregistered, and `basicly-e2mz.24`, a translator that refuses *after* br has taken the write.
4. **Flip** the source of truth once the differential test is clean and the telemetry (§6) shows
   no unimplemented surface in use. Not dispatchable: it needs post-flip records to exist plus
   the gate-history import above, and neither can be brought forward by dispatching anything.
5. **Carry the harness markers natively**, which is the step that actually removes `br` from
   the engine rather than merely making it non-authoritative. Landed 2026-08-07
   (`basicly-s5li`): `comments` was 26 of the engine's 55 `_run_br` call sites and 45% of all
   recorded tracker traffic (§3.0), and 1646 of the live tracker's 1834 comments — **89%** —
   are `[harness-*]` markers using a beads comment purely as transport. In `owned` those
   families are written and read as `comment` events through `br.add_comment` /
   `br.read_comments` / `br.all_comment_texts`, and no `br` is spawned for them at all. The
   188 human comments are deliberately untouched: a human writing prose runs `br` directly and
   the engine never spawns that, so removing the engine's dependency does not require removing
   theirs (§15). Two `comments list` spawns remain at their own call site — `decompose`'s
   sizing markers and `supervise`'s found-info records — each internally consistent, and
   retiring them is `basicly-wpc8`'s.

   **This is the point of no return for the comment query.** After it, the marker families no
   longer reach the external tracker, so `br comments list` run by hand does not show them and
   the step-2 differential's comment comparison diverges by construction. Step 2 is therefore
   run on `dual`, before this — which is the order §5 already gives.

   **The order was not followed** [M, 2026-08-08]. Step 5 landed on 2026-08-07 while the repo
   was — and still is — `mode = "external"`, with steps 2, 3 and 4 unrun. It does not bite yet,
   precisely because `external` still routes the markers to `br`: the divergence this paragraph
   warns about only appears at `owned`. What it costs is an ordering constraint that is now
   binding rather than advisory — **the step-2 differential must be run on `dual` and never on
   `owned`**, and a run that finds comment divergence at `owned` is measuring this, not a
   defect.

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

**The trigger fired and the upgrade did not happen** [M 2026-08-14]. `basicly-kjc5.22` is
**closed**, so the condition above was met — and instead of the promotion it gates, eight kit
modules landed against the reasoning in §§4–4.6 with no frozen surface, no declared schema and no
cache decision. The paragraph above is left standing because the wrong outcome is the more useful
record: **a gate written as prose is not a gate.** Nothing read `kjc5.22`'s status, nothing refused
a commit under `kit/tracker/`, and the condition was discharged by a bead closing somewhere else
entirely. Compare the gates that did bind over the same period — `tracker-path-scan`, the module-size
ratchet, `kit-boundary.py` — each of which is a script wired to a hook.

**What the promotion still owes, and the order it runs in.** The three deliverables above are
unchanged, and they are now *reverse-engineering* work over eight built modules rather than design
ahead of code: enumerate the surface the kit already exposes, declare the event schema `events.py`
already writes, and decide the cache question §10 defers on the fold cost `snapshot.py` already
pays. It sequences with the cutover — `basicly-c357` (scope the shadow differential),
`basicly-vkh0.23` (give the import an entry point), `basicly-u4xu` (flip to dual) — because the
differential in §5 step 2 is what would falsify a schema declared from a read.

**Until it runs, `kit/tracker/` is outside the scope of any architectural audit of this repo.** Not
because it is exempt, but because an audit needs a specification to judge against, and this section
is the record that one does not exist yet.

**Correction, 2026-07-26: the licence claim that stood here was wrong.** This section previously
read "Reading beads_rust and bv sources for reference is explicitly sanctioned while they are
MIT". `beads_rust/LICENSE` is titled **"MIT License (with OpenAI/Anthropic Rider)"**. The rider
grants no rights to Anthropic, OpenAI, their affiliates, or anyone "acting directly or indirectly
on behalf of, for the benefit of, or under the direction of" them, and it names "benchmarking,
testing, analyzing, indexing" as restricted use. Full text and analysis in
[the review's Appendix A](../research/2026-07-26-sota-review.md) §2.

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
evidence. **Our tracker will not implement lossy compaction.** Growth is bounded four ways
instead — git compression, the ship-time rollup (`basicly-kjc5.50`) which summarises a package so
its cost survives independently of the detail, the event log itself, which bounds each write
by the size of the change rather than by the record's accumulated history (§10), and honest
truncation.

**Honest truncation is the fourth bound, and the one the other three leave out (§4.2).** Git
compression, the rollup and the per-change write all bound growth *given* bounded events; none of them
bounds a single pasted payload, so an agent that pastes a 5 MB test log puts it in every clone of the
repo — compressed, but not removable, since true removal from an append-only log is the history
rewrite §4.2 requires explicit confirmation for. The per-event cap makes that ceiling explicit, and the
recorded
`original_length` is what keeps the cap from being the lossy compaction this section rejects. The
distinction is the whole point: compaction discards evidence *after* the fact and leaves the record
looking whole, while truncation drops it at the boundary and **says on the record that it did, and by
how much**. "We kept the first N bytes of a 5 MB payload" is a checkable statement, and it tells a
reader that the rest exists elsewhere — in the run's own output, in the branch it came from — rather
than nowhere. "We summarised this" tells them neither.

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

  **Landed 2026-08-07 (`basicly-vkh0.20`)**, as `kit/tracker/scheduler.py` behind
  `br.read_ranking` — the ranking's own seam, the shape `read_record` has for a record. It
  emits `schema: basicly.scheduler.v1` and the sort above, so a marker recorded under the
  owned scorer is distinguishable from one recorded under `br.scheduler.v1`. Two decisions the
  ordering above did not settle, both made in the module and testable there: the dependent
  count is over **blocking edges to still-live dependents** only, since a `related` dependent
  was never waiting and a closed one is work already done; and the score packs both terms into
  one integer that `explain()` decodes, so a recorded score stays readable without the graph
  that produced it. Age-freedom is structural rather than disciplinary — the ranking's input
  type carries no timestamp, though the ledger it is folded from does.

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

That lock is **scoped to the ledger it protects** — never to the machine, the home directory or any
other path two unrelated repos could share (R8). Scope is what decides who contends: a lock one
level too wide turns every unrelated process on the host into a competitor for a record it will
never touch, and the failure that produces is a *gate* failing rather than a write waiting. Which
is the second half of the rule: **a wait that gives up is a retryable failure and says so**, the
same property R7 asks for and for the same reason — the caller must be able to tell "try again" from
"your work is wrong" without reading prose.

**Readers are the part this section used to leave implicit, and it is what R7 was billed for.**
"Single writer" bounds the *writers*; it says nothing about the N lane processes reading the ledger
while that writer works, which is the load the engine actually generates. Three rules, each one a
line item from `basicly-vkh0.10`:

- **A reader never observes a partial write.** Publishing is a rename, so every read sees one whole
  version of the file — the old one or the new one, never the seam between them. This is not
  advice: the temp-file-then-rename above *is* the mechanism, and the requirement is that nothing
  writes to a shared path any other way.
- **The temp name is per-writer.** A fixed temp suffix on the destination is not concurrency-safe —
  two writers share one temp path, and each can publish the other's half-written bytes. `br.scrub_export`
  uses a pid-scoped name for exactly this reason, which is also why it does not route through
  `projection.atomic_write_text` (whose callers are single-writer projection targets).
- **A contention failure is retryable, and says so.** `br` reported its torn WAL as
  `retryable: false`, and that one field is what cost the run: the supervisor believed it, charged
  the lane's bounded dispatch budget for the store's hiccup, and parked a lane that had never
  started an agent. The replacement's error type carries retryability as a *property of the cause*,
  and the harness's containment (`supervise.TRACKER_GATE`) keeps such a loss off the lane's rework
  counter regardless.

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

**This rule was an assertion until the 2026-07-26 review measured the alternative.** The one
production append-only journal in that reference set carries **no sequence numbers** and mints its
event ids from the wall clock plus a random component, so its only total order is the order its lines
happen to sit in the file. In its own committed **6,467-event** fixture, **44.5% of events share a
millisecond** with another event — a measured property of published data, recorded at
the review's Appendix A §2.3 and `research/2026-07-26-sota-review.md`, and usable independently of
the licence question that puts that project's source out of bounds (§4.6).

Three things follow, and the first is the number's actual force. At that collision rate a millisecond
timestamp **is not an ordering at all for nearly half the log** — a reader that sorts by it gets an
arbitrary permutation inside every collided group, and those groups hold 44.5% of events. Second, the
order that does exist there is **unrecorded**: it survives as file position, which a union merge, a
rebuild, or any sort destroys silently, and silently is the operative word — nothing in the data says
the order was lost. Third, we would sit in the same regime or a worse one, because the engine writes
in **bursts**: a multi-lane pass appends for several lanes inside the same few milliseconds, which is
precisely the shape that produces collisions. §4.1's one integer field buys the ordering their design
leaves to chance, which is why that field is not over-engineering.

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

Re-measured 2026-08-07 against the live 2.30 MB / 642-record ledger, from this repo's own
committed call ledger (1,420 recorded engine calls to `br`, 50.4 s in total):

| Operation | Cost |
| --- | --- |
| One external CLI read (`br show --json`), **median** | **14.2 ms** |
| …the same call at **p95** | **110.3 ms** |
| Full ledger parse in-process (Python, 2.30 MB) | **7.4 ms** |
| Single record, scan + parse in-process | **0.94 ms** |

**This section previously cited 113 ms as the cost of a CLI read and concluded an in-process
read was "~175× cheaper". Both numbers were wrong, and the correction matters because speed is
one of the stated arguments for owning this** (`basicly-rxc1`). 113 ms is not the typical call;
it is approximately the **p95**. The median is 14.2 ms. And the 175× ratio compared that p95
against a *single-record* read of a ledger a third of today's size — the slow end of one
distribution against the fast end of another.

Held to one comparison at a time: a full fold is **~1.9× cheaper** than the median CLI call
(14.2 vs 7.4 ms), and a single-record read is ~15× cheaper (14.2 vs 0.94 ms). A fold is
O(events) while a spawn is roughly constant, so the fold ratio *narrows* as the ledger grows
unless §4.6's carried aggregate keeps the common query off the fold — which is exactly what it
is for.

**The performance argument is therefore real but modest, and it is not why this is being
built.** The arguments that carry the release are untouched and sufficient on their own:
ownership of the harness's own state (§1), the licence rider restricting a class of users, and
the twelve paid-for defects carried as R1–R8. Correcting the number removes a bad reason for a
good decision rather than the decision.

**Where the naive design breaks.** Record cost is ~2.3 KB, and parsing is linear at ~4 ms/MB.
Extrapolating: 10k records ≈ 23 MB ≈ ~90 ms for a full fold, which is the point at which
re-folding per query stops being free and the derived index earns its place. Below roughly 2k
records a plain in-process fold beats the external CLI by two orders of magnitude with no index at
all — so **the index is deliberately deferred**, not designed now. That is the measurement the
cache decision waits on (§7), and the rule is: build the index when a measured fold exceeds the
loop's per-advance budget, not before.

**What makes that deferral defensible rather than optimistic is §4.6.** Without a carried aggregate,
"defer the index" means every current-value query re-folds and the 10k-record cliff above is the whole
answer. With one, the common query never folds at all — it reads the item's last event — so the fold
cost above is the cost of the *checkable* path and of cross-item reports, not of ordinary reads. The
trigger for building an index narrows accordingly: a **cross-item** query the tail cannot serve.

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
- **Nothing machine-specific in anything the kit writes or installs**, which is the rule the tier
  kit paid for and §4's kit requirement inherits. `basicly-dukb` shipped an installer that wrote an
  interpreter path and a repository path into a *tracked* file, leaking a username into a commit and
  breaking every teammate. Two things generalise from it. First, the committed rendering must use a
  host-substituted placeholder plus `uv run --no-project --no-python-downloads python` — `uv`
  because every committer already needs it for the projected git hooks, and because Windows ships no
  `python3.exe` from the python.org installer (the name hits an App Execution Alias that opens the
  Microsoft Store, a worse failure than a clean one). Second, **where neither a portable nor a
  machine-local rendering is possible, refuse** — falling back to the absolute one reinstates the
  bug. Second-guessing this from memory is what cost `dukb`: the test that pinned the defect
  installed into a bare `tmp_path` while running from basicly's own checkout, so the kit was never
  inside the repository being written to. A kit test fixture must be *a repository containing the
  kit*.

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

**One rejection is worth naming rather than listing, because it is the plausible one: LLM-based
monitoring of the ledger.** The production journal §9.5 cites watches its own runs with *sentinels* —
injected model calls, with a per-million-token cost model attached to them. We decline that, and not
on taste. It puts a **paid third-party service in the tracker's runtime path**, which is the exact
thing `basicly-ctdz` forbids: the test there is whether we can absorb a component's breaking change
on our own schedule, and a hosted model endpoint answers no — ids are deprecated, prices change, and
availability is somebody else's operational decision. It also contradicts the kit boundary, which
states that the kit never calls the network or an LLM (§4). And it is nondeterministic where every
other part of this design is deterministic: a monitor whose verdict on the *same* log can differ
between two runs cannot be a thing a gate reads. Finally the condition it exists to catch — a lane
that has stopped making progress — is **already covered deterministically** by `StallWatchdog`
(`src/basicly/runner.py:2546`), on a monotonic clock as §9.5 requires. A cheaper deterministic check
that already exists beats a paid probabilistic one that does not.

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
