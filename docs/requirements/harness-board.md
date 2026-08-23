# Harness Board — a wall-display view of a running harness

Status: **design; one unit shipped.** Authored 2026-08-14 as `01-solution-design.md` on the
`harness/basicly-rn0o` branch. **Moved here and re-based against the tree on 2026-08-19**
(`basicly-rn0o.12`). The six-section shape is unchanged: the structured requirement register
(`basicly-vkh0.42.12`) does not exist yet, and this document is reformatted when it ships.

> The `factory-loop.md` sections quoted below as `[S ...]` sources were absorbed into
> `architecture.md` and the file was deleted on 2026-08-23 (`basicly-1hp91f`). The quotes
> stand as dated evidence; the live rules are the architecture's decision records.

**Why it moved.** The branch it lived on is 283 commits behind main
(`git rev-list --count harness/basicly-rn0o..main` → `283`, 2026-08-19; `basicly-rn0o.12` recorded
`248` when it was filed, and the gap widening while the record sat open is the decay it was filed
about), and nothing on main could open it — so no gate could tell that half of its measurements had
expired, and the producer half was denominated in a store that had since been deleted. A requirement
lives where the gates can see it, or it decays silently.

**Measurement corpus, so every number below is re-derivable.** The tracker figures are taken against
this repository's committed ledger at commit `3861bd7`
(`.basicly/ledger/events-0001.jsonl`, 5,890,340 bytes, 5,924 rows, 968 records). The
`.basicly/usage/` figures are taken against the operator's base checkout, because that directory is
machine-local and self-ignored — which is itself a finding, recorded in C5. Timings are Python
`perf_counter` medians over repeated in-process runs on this machine, stated with their run count.

**What re-basing changed, and nothing else was rewritten:**

| The 2026-08-14 claim | 2026-08-19 |
| --- | --- |
| Tracker source: the external store named in the superseded block | That store was deleted. The source is `.basicly/ledger/events-0001.jsonl`. |
| `observe()` costs 11 s because it spawns 345 `br` subprocesses | It costs 6.1 s and spawns **one** subprocess. The cause moved from process spawn to 93 repeated whole-log folds (C5). |
| Build from files 15 ms, a 733× reduction | **103.8 ms, a 59× reduction** (S3, C5). The 2026-08-19 revision recorded 19.1 ms and 320×; that figure excluded the log read and was refuted by C5's own table on 2026-08-20 (`basicly-ef953m`). |
| Field selection buys 98.9× | It buys **132.5×** (C6). |
| Naive pending-ask count 132, paired count 0 | Naive **140**, paired **1** (S6). |
| 44.4 h of human wait, 9% addressable | **284.5 h**, **20%** addressable — and one 7.7-day outlier carries 65% of the total (Problem). |
| Unit A is unbuilt | Unit A **shipped** (`basicly-rn0o.1`): `board_schema.py`, `board-snapshot.schema.json`, and the `board-schema` verify check. |

**This file's existence contradicts D33, and the contradiction is recorded rather than hidden.**
`factory-loop.md` §2 D33 reads *"`docs/` carries only architecture, tutorial, how-to and a
contributor guide. No new requirement or plan document is ever created as a file; a new requirement
enters as `01-solution-design.md` on a branch"*, with a `docs/` path gate named as the free
deterministic check that would make it binding. **No such gate exists** — nothing under `.scripts/`,
`.basicly/core/hooks/` or `tests/` refers to D33 **[M]**, and the positive control is that D33's own
text is findable in `factory-loop.md`, so the zero is the gate's absence and not a bad probe. The
owner decision on `basicly-rn0o.12` (2026-08-19) overrides D33 for this document, with the reason
recorded there: *a requirement lives where the gates can see it, so it cannot decay silently again*.
Whether D33 is amended or this file is the standing exception is the owner's call and not this
document's.

**Revised again 2026-08-20 (`basicly-rn0o.10`): the snapshot schema is the only interface.** The
2026-08-19 move changed where the document lives and which store its numbers are denominated in. This
revision changes what the document *says*: the producer/consumer framing and the decomposition graph
were the two things the move deliberately left alone, and both are rewritten here. Unit B stops being
*the* producer and becomes **a** producer; the schema stops being an internal wire format and becomes
a published contract with its own compatibility rule and its own distribution path; unit G moves from
last to first-class, because it is now what makes the interface real rather than a later courtesy.
Two owner decisions and one architect review drive it, and every finding either lands in this document
or is recorded here as refused with a reason — see `### Disposition of the architect review` at the
end of `## Constraints`.

**The record's own `## Scope` was stale and is corrected.** `basicly-rn0o.10` scoped
`01-solution-design.md` on the `harness/basicly-rn0o` branch. `basicly-rn0o.12` landed before it and
moved the design to this path on `main`, so that scope named a file no branch reachable from `main`
carries. The revision was made here.

Evidence marks: **[M]** measured, with the command · **[S]** sourced, cited and dated · **[D]** a
design decision. Unmarked prose carries no authority. A **[M]** in this document is measured
2026-08-19 unless it names another date.

---

## SUPERSEDED — the store this design was first denominated in

**Everything in this block is dead. It is kept because the numbers above are corrections to it, and
a correction with no antecedent is unreadable.** This is the only block in this document that names
the deleted store; a hit for it anywhere else is a defect.

The 2026-08-14 revision measured the producer against `.beads/issues.jsonl`, the export of the
external `beads`/`br` tracker: 820 rows, 3,336,549 bytes, 649 closed, 162 open, 2,301 comments at
`d50440b`. Its stated read costs — 3,336,549 bytes / 834,137 tokens whole, 33,745 bytes / 8,436
tokens field-selected, 15 ms to build — were costs of parsing that file, and its 733× argument was
against an `observe()` that spawned 345 `br` subprocesses.

That store no longer exists and neither does `br`. `git ls-tree main .beads/` prints nothing
**[M]**; `basicly.toml`'s `[tracker] mode = "owned"` records the collapsed cutover ladder, and the
one store is the append-only event log under `.basicly/ledger/`. Every figure in the paragraph above
is superseded by C5, C6 and S3, which are measured against the log.

---

## SUPERSEDED — the four-unit phased scope, and the trial that will not happen

**Everything in this block is dead as a decision and live as a rationale.** It is kept for the same
reason the block above it is kept: the scope this document now carries is a correction to it, and a
correction with no antecedent is unreadable. It is also the only record of what the two units added
here were justified on, and someone will ask.

**The 2026-08-14 owner decision, verbatim in substance.** Ship units **A, B, C and G only** — a
complete zero-process product at roughly 470,000 tokens — then *run it against the real factory for
four weeks* and only then decide D (wall mode), E (the action surface) and F (supervisor emission).
Unit H was deferred outright. The reason was OQ-A: the whole arrival mechanism rests on an assumption
about human behaviour that this repository's own data sizes at **n=5**, and D and E are where the
security surface, the authority boundary and the process argument all live. The design recommended
spending them last.

**The 2026-08-18 owner decision supersedes it.** Wall mode and the action surface are **in scope now**,
without the trial. The four-week experiment the phasing existed to run will not be run before D and E
are built.

**What that costs, stated rather than smoothed over.** OQ-A is now **overridden, not answered** — see
`## Open questions`. The n=4 in-hours arrival figure is an **accepted risk carried into the build**,
not a resolved question, and this document still refuses to put a wait-time reduction on the
acceptance criteria (`## Success`, *What success is not*). The two decisions are consistent only in
this reading: D and E are being bought for reasons the phasing did not price, and the arrival
assumption is being carried unmeasured rather than tested. `basicly-rn0o.8` is the only instrument
that can ever settle it, and it is unit H — still optional, still separately decided.

**What survives the supersession.** The *cut* does not change. A, B, C and G remain a complete
shippable product with no long-lived process, so a later decision to stop after G loses nothing and
needs no redesign. That property is why the phasing was cheap to reverse, and it is worth keeping.

---

## Problem

The owner wants an interactive web page, runnable by anyone on their own machine, that shows what
the basicly harness is doing — backlog, factory-in-flight, lane state, gates, spend, health — and
whose ultimate target is **full screen on a monitor or TV in an engineering room**, so that a room
full of people can see what is happening and jump in: interact, debug, prevent a catastrophe in
real time. It may be reused by other projects, so the producer/consumer contract matters more than
the page.

### The framing I was asked to check, and the measurement that partly refutes it

The owner's proposed argument is: `factory-loop.md` §7.4 found that the checkpoint clock measures
**rendezvous, not reading**, so a wall display — which attacks arrival latency, not comprehension —
hits exactly the quantity §7.4 identified as the real cost.

**The reasoning is directionally sound and the target quantity is right. The addressable share of
it is about 9%, not 100%.** I re-ran §7.4's analysis on today's ledger and extended it by one axis
it did not have.

§7.4's own numbers **[S** `docs/requirements/factory-loop.md` §7.4, measured 2026-08-09**]**:
`n = 129` human-answered waits, 145,640 s total, median 95 s, 17 events over 30 min carrying **85%**
of all human wait; `basicly-hxnf.2` (27,553 s) and `basicly-hxnf.3` (27,510 s) answered *in the same
second*.

The replication and extension **[M]**, over `.basicly/ledger/events-0001.jsonl`, parsing every
`[harness-wait]` comment event whose header carries `answered`, keeping `by=human` and
`delegated=false`:

```text
n = 161 human-answered        total 1,024,364 s (284.5 h)
p25 11 s   median 98 s   p75 450 s   p90 2,090 s   max 665,788 s
21 events > 30 min  ->  993,156 s  =  97% of all human wait     (§7.4 found 17 -> 85%)
```

**The 2026-08-14 revision reported 44.4 h and 83%; both moved, and the honest reading of the move is
that the distribution broke rather than grew.** Two escalations answered since carry
665,788 s (7.7 days, `basicly-u2hl`, asked Fri 22:45 UTC) and 195,322 s (2.3 days, `basicly-u2hl`,
asked Thu 09:26 UTC). The first alone is **65%** of all human wait ever recorded here. A mean over
that population describes nothing; the tail is the population.

The new axis. For each tail event I took `requested_at` — the instant the engine asked — and
classified it by local wall-clock (07:00–22:00 Mon–Fri = "someone could plausibly be in the room").
The classification is **invariant across UTC+0 to UTC+3** **[M]**, so it does not turn on guessing
the operator's timezone:

| Bucket | n | seconds | share of all human wait |
| --- | ---: | ---: | ---: |
| Tail (>30 min) **asked during office hours** — a TV in the room could have caught it | 5 | 209,849 | **20%** |
| Tail (>30 min) **asked out of hours** — nobody is in the room; a TV cannot help | 16 | 783,307 | **76%** |
| Head (≤30 min), median 64 s — a TV cannot beat a 64-second answer | 140 | 31,208 | 3% |

**So the honest ceiling on the rendezvous argument, denominated in the quantity §7.4 measured, is
~58 hours out of 284.5 across the whole project history — and 54 of those 58 are the single
195,322 s escalation.** Remove both multi-day escalations, one from each side of the ratio, and the
remainder is 14,527 s addressable out of 163,254 s — **8.9%**, against the **9.1%** the 2026-08-14
revision reported from a population less than a sixth the size. **The rise from 9% to 20% is two
events, not a trend**, and the four office-hours events behind that 14,527 s are the same four the
earlier revision found, to the second. The mass is still overnight and
weekend waits — `basicly-sco6`'s 29,012 s is eight hours, and §7.4 already records that the *next*
decision on the same issue, same human, took 81 s: *"Eight hours asleep, eighty-one seconds awake"*
**[S** `factory-loop.md` §7.4**]**. A display in a room where nobody is standing recovers none of
that.

**I am telling you this because you asked to be corrected rather than agreed with, and because a
project justified on a 284-hour number that is really a 4-hour number will be judged against the
284-hour one.** The rendezvous framing is the right *kind* of argument — it identifies a real
mechanism and it is the only one §7.4 leaves standing — but it cannot carry this project alone.

### The argument that does carry it

Three things I measured are larger than the wait number and are not measured by wait markers at all.

1. **Money burns unattended.** 431 dispatch records over 317 records; 225 carry a cost, summing
   **$1,254.26**, single largest dispatch **$36.59**, total **1,615,048,198** tokens **[M]**
   `.basicly/usage/run-records.json`. A dispatch runs a median of **566 s**, p90 **1,800 s**, max
   **3,601 s** **[M]** (`duration_s`, n=237). Multi-lane passes are the standing default, and the
   repo's own operating memory records a 300M-token grant burned for zero landings.
2. **The engine already knows it is off the rails and nobody is looking at the line that says so.**
   `basicly loop session basicly-kjc5 --json` prints, from the base checkout,
   `"spent_tokens": 177970761` against `"token_budget": 4000000` **[M]** — **44× over the declared
   budget**, in a JSON surface that exists, on a session nobody is watching. There are 12
   `[harness-overrun]` markers in the ledger **[M]**, each one a lane that blew its context ceiling
   and spawned a follow-up.

   **And the same command in a lane worktree prints `"spent_tokens": 0` [M].** `session_spend`
   reads `run_record.load_run_records(repo_root)`, and `.basicly/usage/` is machine-local *and*
   worktree-local: a fresh worktree has none of it. So the surface that reports the catastrophe is
   silent in exactly the checkout an agent runs in. The board must be produced from the base
   checkout, or its `spend` section is a zero that reads as safety. Recorded as a constraint in C5.
3. **The one number a room can act on is "is anything asking me?"** and today obtaining it costs
   **6.9–7.8 s** of wall clock, **6.1 s** of it inside `supervise.observe()` **[M]** — see C5 for
   where that time goes. Nobody polls a seven-second command, so nobody polls.

The problem, restated in terms I can defend: **the harness emits everything a room needs to prevent
an expensive mistake, and there is no surface on which a human who is not driving can see it without
paying seven seconds and knowing which of eleven commands to type.**

---

## Success

Observable, not a feeling. Every one of these is a command or a check, not an impression.

**S1 — Zero-install, zero-server artifact.** `uv run basicly board --out board.html` writes one
self-contained file; opening it at `file://` shows the current backlog, gates, spend and health with
no process running, on Windows, Linux and macOS. Verified by opening the file and reading it.

**S2 — Wall mode is fresher than the producer it renders and never claims more.**
`uv run basicly board --serve` prints a `http://127.0.0.1:<port>` URL; the page's header always
carries the snapshot's `generated_at` and a computed **age in seconds**, and shows `STALE` past
`stale_after_s`. There is no state in which the page displays a number without displaying how old it
is. Verified by stopping the producer and watching the badge flip.

**S3 — Snapshot build is affordable enough to refresh.** Building a full snapshot from files costs
**103.8 ms** (median of 21, `perf_counter`, 2026-08-20, on the tree that ships `units` and `graph`)
**[M]** against `observe()`'s **6,105 ms** (median of 3) **[M]** — a **59×** reduction — so a 15 s
refresh cadence consumes 0.7% of a core. Verified by a timing test in the suite that fails if the
build crosses 500 ms on this repo's own corpus. *The 19.1 ms and 320× this criterion carried until
2026-08-20 excluded the log read and were refuted by C5's own per-source table
(`basicly-ef953m`).*

**S4 — The contract validates with no basicly runtime, from one file a stranger can copy.**
`python3 conformance.py <snapshot>` exits 0 on a conforming document and non-zero on a broken one,
where `conformance.py` is a **single standard-library file that imports no basicly and needs no
install**. A snapshot with an unknown `schema` major exits non-zero naming the version it saw and the
version it wants.

**S4 was false as written until 2026-08-20, and the remedy is a new surface rather than a wording
change.** The claim was *"a foreign harness can be conformance-tested with no basicly runtime"*, and
it was verified with `basicly board validate` — which **is** the basicly runtime. Re-measured
2026-08-20 **[M]**: in a directory holding only a copy of `tests/fixtures/board/minimal-v1.json`,

```console
$ uv run basicly board validate snapshot.json
not-installed: .basicly/core/schemas/board-snapshot.schema.json is not installed
$ echo $?
1
```

*Positive control: the identical file inside this repository prints `harness-board/v1, ok` and exits 0
**[M]**, so the 1 is a property of the directory, not of the probe.* The schema resolves from the cwd
repository's catalog through `catalog_source`, so the contract is readable only by someone who already
installed the thing the contract exists to avoid depending on.

**[D] The remedy, decided by the owner 2026-08-20: a standalone single-file conformance script that
imports no basicly.** It was chosen over the two alternatives — a `--schema PATH` flag on
`board validate`, and a published schema file the how-to tells a foreign producer to vendor — because
it is the only one of the three that makes **both** S4 and S7 true. A flag still requires the runtime;
a vendored schema still requires a validator to run it against. Constraint C9 carries the freeze
consequence and C13 carries the placement, the boundary it must keep and the parity test that stops
it drifting from `board_schema`.

**The fail-open direction is not reversed.** The shipped `not-installed` outcome exits **non-zero**,
which is correct: a validator that cannot find its contract must not report a pass. Nothing in this
remedy may turn that into a 0.

**S5 — Every write the page can perform is a named existing CLI invocation, echoed before it runs.**
The page never writes a file, never touches the ledger, never mints an authority. Verified by a test
asserting the action table maps 1:1 onto `argv` lists whose head is `basicly`, and by a
`grep` gate that the board server module imports no writer module.

**S6 — Pending asks are derived by pairing, not by counting.** The naive parse ("count `requested`
lines") reports **140** pending asks on this repo today; the correct derivation — pair
`[harness-wait]` markers by their `id=` and treat an ask as pending only when no `answered` marker
shares its wait id — reports **1**, `basicly-b2n2#wait-ship` **[M]**. S6 is met when a regression
test pins that pair of numbers against the committed corpus. *Positive control: the same parser found
138 distinct requested wait ids and **203** distinct answered ones, so the 1 is a property of the
world today, not of the probe — and the first version of this probe returned 0 pending because it
read a comment as a mapping when the fold stores it as a string, which is why the control is stated
rather than assumed.*

**203 answered against 138 requested is not an error.** An ask the engine answers itself is written
as one `answered` marker with no `requested` line before it, so the answered set is legitimately the
larger one, and a producer that paired in the other direction would report negative work.

**S7 — Another project adopts it by emitting one file, and never runs `basicly install`.** A
directory holding a `harness-board/v1` snapshot and the two files the kit distributes —
`conformance.py` and `board.html` — gets a working board and a working conformance check, with no
basicly on the machine.

**S7 was false as written for a second, different reason, and the fix is the distribution path rather
than the code.** *"A repo with no basicly installed"* fails even with the runtime present, because the
contract must be **installed in the tree** for anything to resolve it: a repository that ran
`basicly install` is fine, a genuinely foreign one is not. So S4's falsity is *the checker is the
runtime* and S7's is *the contract is not distributed* — two failures, one remedy each, and C9 names
the surface both remedies add.

Verified by unit G's fixture: a directory under `tests/fixtures/board/foreign/` containing a
hand-written snapshot and **no basicly state at all**, checked by the distributed
`conformance.py` under a bare `python3` and rendered by the distributed page.

**S8 — The snapshot is the only interface, enforced rather than intended.** No consumer unit — the
renderer, the server, the action surface — imports a tracker, ledger, store or writer module. An
`import-linter` `forbidden` contract names every one of them and fails the build on the first import,
and the contract is a `forbidden` type rather than a tier placement because the tier stack puts
`owned_store` and `owned_write` near the *bottom*, so every module above `board_schema` can reach them
by layering alone (C11 **[M]**). Verified by that contract, plus a test asserting the renderer's only
input is a parsed snapshot document.

**S9 — The absent-section list is derived from the contract, not written down twice.** The renderer's
section inventory comes from the shipped schema's own property list, so a section added to the schema
appears as `not emitted by this producer` on an old snapshot with no renderer edit. Verified by a test
that adds a property to a copy of the schema and asserts the rendered region count follows. The
shipped schema declares **15** top-level properties, **3** required and **12** optional **[M]**, and
`basicly board validate` on the minimal fixture names all 12 absent — so "eight regions" anywhere in
this document is a layout count and never a section count.

**What success is not.** It is not "the checkpoint wait number goes down". Per the measurement in
`## Problem` the recoverable share is 20% of that number on a population of **five** events — 8.9%
once the two multi-day escalations are removed — it is confounded by everything else that changes,
and n=5. I will not put a wait-time reduction on the acceptance criteria, and I would refuse
a release note that claimed one.

---

## Consumer transcript

### Mode A — the on-demand artifact (satisfies `work-tracker.md` §4.3 requirement 10 exactly as recorded)

```console
$ uv run basicly board --out board.html
board: harness-board/v1 snapshot built in 104 ms from 4 sources
       tracker   .basicly/ledger/events-0001.jsonl   968 records, 236 active, 2727 comment events
       runs      .basicly/usage/run-records.json     431 dispatches over 317 records (machine-local)
       verify    .basicly/usage/verify-run.json      mode=fast passed=True, 20 checks
       session   .basicly/usage/supervisor.lock      absent; live lane panel renders "not supervised"
board: wrote board.html (self-contained, 214 KB) — open it with your browser
board: wrote board-snapshot.json (34 KB) — the contract, for any other consumer
$ echo $?
0
```

### Mode B — wall mode

```console
$ uv run basicly board --serve --root basicly-kjc5
board: serving harness-board/v1 on http://127.0.0.1:8787  (127.0.0.1 only; Ctrl-C to stop)
board: producer  self-refresh every 15s (no supervisor lock held on this repo)
board: actions   ENABLED for loop-answer; checkpoint-approve and lane-kill relay a confirm code
board: press Ctrl-C to stop. This process holds no lock and blocks no gate.
^C
board: stopped. 47 refreshes, 0 failed. No state was written.
```

When a supervisor **is** running, the producer line changes and the server computes nothing:

```console
board: producer  supervisor bc7cc925 (pid 41207), heartbeat 6s old, writing every 15s
```

### Mode C — version mismatch, the case that decides whether the contract is real

**This mode has shipped** (`basicly-rn0o.1`), so the transcript below is the real output, captured
2026-08-19, not a proposal:

```console
$ uv run basicly board validate tests/fixtures/board/wrong-major.json
refused - snapshot declares schema "harness-board/v2", this consumer reads harness-board/v1
A major version is a different contract. Nothing was rendered.
$ echo $?
2

$ uv run basicly board validate tests/fixtures/board/minimal-v1.json
harness-board/v1, ok
present   nothing beyond the required keys
absent    generator, repo, session, lanes, asks, gates, spend, health, backlog, units, graph, events
$ echo $?
0
```

Two differences from the 2026-08-14 proposal, both in what shipped and neither a regression: the
lines carry no `board:` prefix, and the refusal does not advise the reader to ask the producer for a
v1 alongside. The absent-section list is longer because the shipped schema carries two sections this
document did not propose, `units` and `graph`.

### Mode D — the foreign repository, which is the case that decides whether the *kit* is real

Mode C proves the contract refuses the wrong major. It does not prove a stranger can run the check,
and until 2026-08-20 nothing did. This is the transcript S4 and S7 are now written against; it is a
**proposal**, not captured output, because the script it runs does not exist yet (unit G).

```console
# a directory with no basicly, no .basicly/, no venv - just python3 and two copied files
$ ls
board.html  conformance.py  snapshot.json

$ python3 conformance.py snapshot.json
harness-board/v1, ok
present   backlog
absent    generator, repo, session, lanes, asks, gates, spend, health, units, graph, events
$ echo $?
0

$ python3 conformance.py broken.json
refused - "freshness" is required and is absent
The three required keys are schema, generated_at and freshness. Nothing else can refuse a document.
$ echo $?
2

$ python3 -c "import basicly"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'basicly'
```

**The third command is the acceptance criterion, not decoration.** A conformance kit whose check
imports the harness it is meant to be independent of proves nothing, and that is exactly the defect
this mode exists to close. `kit-boundary` is the gate that holds it (C13).

### What appears on the TV

1920×1080, dark, no chrome, no scrollbar. Rows are fixed height; the layout never reflows when a
number changes, because a reflowing wall display is unreadable from six metres.

```text
+--------------------------------------------------------------------------------------------------+
| basicly              root basicly-kjc5   L3 grant             as of 8s ago  [LIVE]  16:42 UTC     |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|   ############################################################################################   |
|   #                             N O T H I N G   I S   W A I T I N G                          #   |
|   #                     next checkpoint: ship on basicly-kjc5.57 (not yet asked)             #   |
|   ############################################################################################   |
|                                                                                                  |
|  --- when something IS waiting, this one band replaces the above and is the only red on screen ---|
|   ############################################################################################   |
|   #  2 ASKS WAITING          basicly-1th1  classify   00:04:11   "approve the plan?"        #   |
|   #                          basicly-3w44  ship       00:31:02   "merge landed; ship?"      #   |
|   ############################################################################################   |
+---------------------------------------+----------------------------------------------------------+
| LANES                    4 in flight  | SPEND                          this session               |
|                                       |                                                          |
|  basicly-kjc5.57  build   claude       |   tokens  177,970,761 / 4,000,000    [############] 4449%|
|    running 24m   18.7M tok   $13.13   |   cost   $1254.26 lifetime  $36.59 largest dispatch       |
|    [##############------]  ~11m left  |   grant   L3   budget 4,000,000   >>> OVER BUDGET <<<     |
|                                       |                                                          |
|  basicly-g7os    verify   claude       | GATES                    verify fast, 16:42:52, 20 checks|
|    running  3m    2.1M tok    $1.90   |   [OK] ruff  format  pyright  bandit  pytest  markdownlint|
|    [####----------------]             |   [OK] module-size  comment-density  tree-growth  noqa    |
|                                       |   [--] catalog-review  docs-claims   (not run this pass)  |
|  basicly-7bur    REPAIR   claude       |                                                          |
|    rework 1 of 2   scope collision    | HEALTH                        window 5, from run-records  |
|    [!!] merge bounced 4m ago          |   claude  163 runs  0.825  failure 0.178  drift -0.04     |
|                                       |   manual  194 runs  0.995  failure 0.005  drift  0.00     |
|  basicly-4t9z    ship     waiting     |                                                          |
|    blocked on checkpoint (see above)  |                                                          |
+---------------------------------------+----------------------------------------------------------+
| BACKLOG  236 active of 968          ready 180        blocked 2        in progress 4              |
|  [########################################----------------------------] 732 closed / 968         |
|  P0 ####  P1 ####################################  P2 ##################  P3 ####                |
+--------------------------------------------------------------------------------------------------+
| last 6 events   16:41 kjc5.57 dispatched build claude-opus-5 · 16:39 g7os verify PASS ·           |
|                 16:37 7bur merge BOUNCED conflict · 16:31 4t9z ship checkpoint asked · ...        |
+--------------------------------------------------------------------------------------------------+
```

**The five-second question each region answers** — the acceptance criterion for the layout, not
decoration:

| Region | The question a human answers in <5 s from six metres |
| --- | --- |
| Ask band (top, full width, the only red) | *Is anything waiting on a person right now, and for how long?* |
| Header right | *Is this screen telling me the truth, or is it frozen?* — `as of Ns` + `LIVE/STALE` |
| Lanes | *Is anything wedged, in repair, or running longer than it should?* |
| Spend | *Are we about to spend money we did not agree to?* — the 4449% bar is the catastrophe signal |
| Gates | *Is the tree green?* |
| Health | *Has an agent's failure rate moved?* |
| Backlog | *Are we making progress, or churning?* |
| Event strip | *What just changed?* — the only region that reads as prose |

**Interaction on the wall**: the display is touch/click-through. Clicking an ask expands it in place
to the full question text with two buttons; clicking a lane expands to its last run record and its
worktree branch. Nothing navigates away — a wall display with a back button is a wall display
someone has left on the wrong page.

---

## Out of scope

- **A maintained TUI.** Refused permanently **[S** `architecture.md` §8 Non-goals, *"A maintained TUI"***]**,
  with the recorded reason *"Permanent cost; generated artifacts (JSON, Mermaid/DOT, static HTML)
  are diffable in review and cost nothing to keep"* **[S** `docs/research/2026-07-26-sota-review.md:1031` **[M]** re-checked 2026-08-19**]**.
  This design is the artifact side of that sentence, not a departure from it.
- **A hosted service, multi-user auth, or any network listener beyond `127.0.0.1`.** `work-tracker.md` §15 Non-goals
  names *"a sync server or hosted service; a web application"* as non-goals. This is a local viewer
  of local files; it is not a web application in that sense and it must never become reachable off
  the loopback.
- **A second source of truth.** The board derives; it never stores. There is no board database, no
  cache that outlives a process, and deleting every board file loses nothing.
- **Writing to `.basicly/ledger/` or to any other store.** Every mutation is an `argv` list handed to
  the existing `basicly` CLI. See `## Constraints`.
- **Historical analytics, burndown, velocity, per-person metrics.** `work-tracker.md` §15 Non-goals
  excludes *"sprint, estimation, or reporting ceremony beyond what the loop consumes"*. The board
  shows *now* plus a six-event tail; anything longer belongs to `basicly usage report`.
- **Reading another machine's state.** `.basicly/usage/` is self-ignored — `.basicly/usage/.gitignore`
  line 1 is a bare `*` **[M]**, and `git ls-files .basicly/usage/` returns zero files **[M]**. It is
  also worktree-local, which C5 records as a constraint on where the producer runs. A board reading
  it shows **one operator's** dispatch history. Cross-machine aggregation is `status --fleet`'s job
  and is explicitly not attempted here.
- **A JS framework, a bundler, or a `node_modules` in the render path.** See `## Constraints`.
- **Claiming real-time.** See the next section.

---

## Constraints

### C1 — The real-time collision, re-opened explicitly

`work-tracker.md` §4.3 restated requirement 10 from real-time to on-demand, and recorded why:

> **Requirement 10 is on-demand regeneration.** A generated Mermaid/DOT graph and a static HTML board
> rebuilt on write is not real-time, and calling it that would be the same overclaim one notch smaller.
> **[S** `docs/requirements/work-tracker.md` §4.3, requirement 10**]**

That restatement is **correct and I am not asking to reverse it.** What I am asking to add is a
second, separately-named mode with a separately-named claim.

**What real-time would cost, concretely.** The producer's own liveness resolution is
`supervise.HEARTBEAT_INTERVAL_S` is `15.0` and `supervise.STALE_AFTER_S` is `60.0` **[M]**.
A consumer polling at 1 s cannot be fresher than a producer ticking at 15 s. Genuine sub-second
freshness would require the engine to push on every state transition — an event bus, a persistent
connection, and an ordering guarantee across five writers — which is the daemon the non-goals refuse
and which buys nothing, because *the thing being watched changes on the order of minutes*: a dispatch
runs a median of 566 s and a p90 of 1,800 s **[M]**, the heartbeat is 15 s, and a checkpoint sits
for a median of 98 s **[M]**.

**What it buys.** Nothing measurable. There is no state transition in this system whose value decays
in under 15 seconds.

**[D] The honest claim, and the wording I propose be frozen into the CLI's own help text:**

> The board is **as fresh as the producer that wrote its snapshot, and it always shows how old that
> snapshot is.** It is not real-time and does not claim to be. In wall mode with a live supervisor it
> refreshes on the supervisor's 15-second tick.

This is enforceable, not aspirational: the snapshot schema makes `generated_at` and
`freshness.stale_after_s` **required**, and the page has no code path that renders a value without
rendering its age. An overclaim would have to be a deliberate schema violation, not a slip. That is
the same standard `work-tracker.md` §4.3 was defending, met by construction rather than by restraint.

**What this changes in the requirement.** Requirement 10 stays as written. I propose one additional
sentence be considered at the next revision of §4.3, *not* a new document (D33): *"A live-attached
mode may render the same artifact on the supervisor's tick, provided it displays the snapshot's age
and never uses the word real-time."* Whether that sentence lands is the owner's call, and the design
works without it — Mode A alone already satisfies requirement 10 as recorded.

### C2 — The rendezvous collision, and what I concluded

`factory-loop.md` §7.4 is titled *"A rendered artifact does not fix a checkpoint, because the clock
is not measuring reading"* **[S** `factory-loop.md` §7.4**]**, and concludes *"the checkpoint clock
measures rendezvous, not reading. A renderer cannot move a quantity the instrument does not contain"*
**[S** `factory-loop.md` §7.4**]**.

**§7.4 does not refute this project, and this project does not refute §7.4.** They are about
different things and both survive:

- §7.4 refutes **rendering as a comprehension aid**. That refutation stands untouched. This design
  does not claim any checkpoint is easier to *read* on a screen than in a terminal, and I found no
  evidence it would be. §7.4's counter-datum (`wait-decompose`, 2,490 s, n=1) remains the only thing
  on the other side and n=1 is not a finding.
- The owner's rendezvous framing correctly identifies that the remaining quantity is **arrival**, and
  a wall display is an arrival intervention. **That reasoning holds.** My extension in `## Problem`
  bounds it: 20% of measured human wait, n=5, 58 hours lifetime of which 54 are one escalation.
  Directionally right, quantitatively small.

**[D] Therefore the wait number is not the justification and must not appear as an acceptance
criterion.** The justification is unattended spend and unattended failure — quantities §7.4 never
measured because it was measuring waits. I am recording this so that a later reader does not
reconstruct the project's rationale from the wrong section.

### C3 — OQ-15, and the honest answer about whether this closes it

OQ-15 **[S** `factory-loop.md` §13**]** asks whether a checkpoint's artifact takes real reading time,
notes it is unanswerable with today's instrument, and says it is *"Settled by **one field**: a third
marker written when the operator first views the checkpoint."*

The seam is already shaped for it. `policy.WaitEvent` **[M]** already carries `requested_at` and
`answered_at`; the wait id is **derived, not minted** — `policy.wait_id_for_checkpoint(issue_id,
name)` returns `f"{issue_id}#wait-{name}"` **[M]** — so any observer can name an existing wait
without creating one.
A third timestamp is one optional field on one dataclass and one marker rewrite.

**But a wall display cannot supply that field, and I want to be blunt about why.** An always-on TV
renders every checkpoint the instant it appears. If "viewed" means "rendered", then
`viewed_at == requested_at` for every event on the wall, the field measures the render loop rather
than a human, and OQ-15 would be recorded as settled by an instrument that measures nothing. That is
a worse outcome than leaving it open, because it is a false close.

**[D] The field, if it is written at all, must key on a human input event, not a render.** Concretely:
the first `pointerdown`/`keydown` on the board while `document.visibilityState === "visible"` **and**
at least one ask is pending, stamped once per wait id. On a passive TV with nobody touching it, no
field is written — and that absence is honest, not a false zero.

**[D] I am carving this out of the core scope and filing it as a separate, optional unit** (`H` in the
decomposition) that ships behind a config flag defaulting to **off**. Reasons: it is the only write
in the whole design that touches a **frozen surface** (the owned ledger format — see C6), it is the
only feature whose value depends on an unmeasured behavioural assumption, and the board is worth
building whether or not OQ-15 ever closes. If the owner wants OQ-15 closed, this is the cheapest
route to it that exists; it should still be a decision taken on its own.

### C4 — "No external database or daemon", answered head on

The non-goal is real and permanent **[S** `architecture.md` §8 Non-goals and §D-27**]**. The recorded reason is specific:

> Adopting Dolt, or any external DB/daemon — *Reintroduces exactly the unowned-binary upgrade surface
> we are removing.* **[S** `docs/research/2026-07-26-sota-review.md:1031` **[M]** re-checked 2026-08-19**]**

and

> **3.8 Everything lives in plain, git-tracked files.** No daemon, no hidden state, no network calls
> at build time. **[S** `docs/architecture/architecture.md` §21**]**

The refusal is about **an unowned binary the system depends on**, and about hidden state. Measured
against those two criteria:

| Criterion the refusal names | This design |
| --- | --- |
| Unowned binary | **None.** `http.server` is Python stdlib. New third-party runtime deps: **zero**. |
| Upgrade surface | **None.** Nothing to install, nothing pinned, nothing to migrate. |
| Hidden state | **None.** The snapshot is a plain file; the page is a plain file; deleting both loses nothing. |
| Network calls | **Loopback only**, at view time, never at build time. |
| System depends on it | **No.** Every gate, every lane and every landing behaves identically with the board absent. |

**[D] And the precedent is already set by the engine itself**: `basicly loop supervise` is an
operator-started foreground process that runs a standing loop and a **background heartbeat thread**
(`supervise.HeartbeatThread` **[M]**), and `basicly loop watch` **polls** **[M** `basicly loop
--help`, *"Poll and print newly pending decisions"***]**. The repository already sanctions
operator-started, session-scoped, foreground processes. `basicly board --serve` is that category and
nothing more: it is in the foreground, Ctrl-C ends it, it holds no lock, it survives no reboot, and
there is no supervisor, systemd unit, or auto-start anywhere in the design.

**[D] Additionally, Mode A needs no process at all.** `basicly board --out board.html` produces one
self-contained file openable at `file://`. If the owner rejects the serve mode entirely, Mode A still
delivers `work-tracker.md` §4.5's *"static HTML board emitted by a command"* **[S** `work-tracker.md` §4.5**]**
verbatim, and the design degrades to that without redesign.

### C5 — The seven-second problem, and why the producer is not `observe()`

`basicly loop session <root> --json` is the richest live surface and it already emits almost exactly
the wall payload — 21 keys, 1,417 bytes **[M]**. It takes **7.79, 7.40, 6.94 s** across three runs
**[M]**, of which `uv run` startup is **0.01–0.02 s** **[M]**; **6.1 s** (median of 3) is inside
`supervise.observe()` **[M]**.

**The cost is real but its cause has changed, and the change matters more than the number.** The
2026-08-14 revision measured 11 s and attributed it to **345 `br` subprocesses**. `br` is gone.
Profiled today with a `subprocess.run` spy and `cProfile` **[M]**, one `observe()` spawns **exactly
one** subprocess — a `git rev-parse` — and spends its time here. The figures are *cumulative seconds
under the profiler*, whose own overhead more than doubles the run (`observe()` costs 15.1 s profiled
against 6.1 s unprofiled), and they nest, so they do not sum:

```text
observe()                                  15.1 s cumulative (profiled)
  tracker.read_record            x  62     10.7 s
  policy.session_issue_ids       x   3      8.3 s
  kit events.read_log            x  93      8.1 s      <- the whole log, 93 times
  kit events.Event.from_json     x 554,280  7.0 s      <- 93 x 5,924 rows
  kit events.fold                x 156      5.7 s
```

The **call counts** are what carry the finding and they are not distorted by the profiler: 93 whole-log
reads and 554,280 event parses for one observation.

**The producer is not `observe()` because `observe()` folds the whole ledger 93 times to answer one
question.** That is not a subprocess problem and it will not be fixed by removing one; it is an
uncached repeated read, and the file-only producer's whole advantage is that it folds **once**.

**Every source the producer reads, with its size and its read cost measured 2026-08-19.** The tracker
figures are against the committed ledger at `3861bd7`; the `.basicly/usage/` figures are against the
operator's base checkout. Timings are `perf_counter` medians, run counts stated.

| Source | Size | What the producer takes | Read cost |
| --- | ---: | --- | ---: |
| `.basicly/ledger/events-0001.jsonl` | 5,890,340 B · 5,924 rows | 968 folded records, 2,727 comment events | **16.5 ms** fold (median of 5) |
| `.basicly/usage/run-records.json` | 517,521 B | 431 dispatches over 317 records | **1.25 ms** parse (median of 5) |
| `.basicly/usage/verify-run.json` | 3,178 B | last gate pass, mode and checks | **0.01 ms** parse (median of 5) |
| `.basicly/usage/supervisor.lock` | absent on both checkouts | holder id, heartbeat age | **0.002 ms** `supervise.read_holder`, no lock (median of 200) |
| **Whole snapshot build** | — | all four, plus ask pairing and status tallies | ~~19.1 ms~~ **see below** |

**The 19.1 ms in that last row was wrong, and its own table said so (`basicly-ef953m`).** A whole
build cannot cost 19.1 ms when the fold alone above it costs 16.5 ms and the fold is not even the
expensive part: the figure excluded the **log read**, which is the single largest cost in the whole
producer. Re-measured 2026-08-20 against the tree that ships `units` and `graph`
(`basicly-vhixrn`), on the ledger at `.basicly/ledger/` — **6,310,689 B · 6,167 rows · 1,010
records** — with `perf_counter`, after one warm build **[M]**:

| Step | Cost |
| --- | ---: |
| `kit.read_ledger` — the log off disk and parsed to events | **51.4 ms** (median of 7) |
| `kit.events.fold` | **13.8 ms** (median of 7) |
| `board_fields.units` — 270 field-selected rows | **8.8 ms** (median of 7) |
| `board_fields.read_markers` | **7.3 ms** (median of 7) |
| `board_fields.edge_triples` — 676 edges | **1.9 ms** (median of 7) |
| `board_fields.asks` | **0.3 ms** (median of 7) |
| **Whole snapshot build** | **103.8 ms** (median of **21**: min 88.4, p25 100.0, p75 109.6, max 115.0) |

**This decomposition adds up and the old one did not, which is the actual repair.** The six steps
sum to 83.5 ms of the 103.8; the remainder is the graph rows, the status tallies, the two usage
parses and the document assembly. A reader can now check the whole against its parts, which is
the property C5 lost when the log read was left out of it.

**The run count is 21 rather than 7 because the box was running nine concurrent lanes**, and two
earlier medians on the same tree disagreed by 26% (132.1 ms against 104.8 ms) before the sample was
widened. The tight p25–p75 band of 100.0–109.6 is what makes 103.8 reportable; a median of 7 here
is a load reading, not a cost.

103.8 ms against `observe()`'s 6,105 ms is a **59×** reduction, not the 320× this section claimed,
and against the 500 ms acceptance cap the real headroom is **4.8×** at the median and **4.3×** at
the slowest of 21 runs — not 26×. The design remains affordable at a 15 s refresh cadence, which
costs 0.7% of a core rather than the <0.2% claimed. **What is no longer true is that the cap is a
loose bound.** A runner three times slower than this box would sit at the cap, so AC 4's 500 ms is
now a band rather than the 26× margin it was chosen as, and whether the cap moves or the log read
gets cached is a decision this correction does not take.

**[D] The board's producer reads files, and calls no engine read path that re-folds per record.**
This is the direction `work-tracker.md` §4 already mandates for bulk reads: the file is the only bulk
read, and the only one a fresh clone can answer from. **[S]** The one thing files cannot supply is
the live lock holder and lane worktrees; for that the producer reads the path
`supervise.LOCK_FILE` names — `.basicly/usage/supervisor.lock` **[M]** — which is also a file.

**[D] The producer is run from the base checkout, and says so when it is not.** `.basicly/usage/` is
gitignored (`.basicly/usage/.gitignore` line 1 is a bare `*` **[M]**) and therefore worktree-local: in
a lane worktree all three usage files are absent, `spend` reads zero and `gates` reads nothing. A
board that rendered that as "no spend" would be a false zero of exactly the kind S2 exists to prevent.
The producer therefore omits a section whose source file is absent rather than emitting zeros
(unit B, AC 5), and the page renders `not emitted by this producer`.

### C6 — The wire format, and the field-selection lesson

The original caution — a JSON listing costs an order of magnitude more than a text one, because JSON
inlines every `description` and `acceptance_criteria` body — holds against the owned ledger too, and
harder. Tokens are `read_cost._text_tokens`, the same chars/4 unit the sizing governor uses, so these
numbers are comparable with a lane's scope budget **[M]**:

| Payload | bytes | ≈ tokens |
| --- | ---: | ---: |
| Whole `.basicly/ledger/events-0001.jsonl` | 5,890,340 | 1,472,207 |
| All rows re-serialised minified | 5,893,419 | 1,473,354 |
| **Active records (236), six selected fields** | **44,454** | **11,113** |
| + the 672 dependency edges touching an active record, as triples | 76,988 | 19,247 |

**132.5×, from field selection alone; minification cost 0.1% rather than buying any.** **[D] The
schema therefore selects fields, never records.** No `description`, no `acceptance_criteria`, no
comment bodies cross the wire — a marker is reduced to its parsed fields at the producer. A board
that shipped whole records would be a 5.9 MB page and a 1.47M-token read for any agent that fetched
it — 5.6× `config.DEFAULT_WORKING_SET_MAX`, the largest lane working set the band admits **[M]**.

*Edge population, stated because the earlier revision's "211 dependency edges" is not the same
quantity:* the ledger holds 1,067 edges in total — 685 `parent-child`, 319 `blocks`, 58 `related`,
5 `discovered-from` **[M]** — of which 672 touch a record that is not closed.

**The RULE is not re-opened; one number carrying it was stale, and both stale copies were in the
shipped schema rather than here — and both are now repaired.** *"Select fields, never records"* is
implemented — no property in `.basicly/core/schemas/board-snapshot.schema.json` admits a `description`,
an `acceptance_criteria` or a raw comment body, and every free-text property is length-bounded **[M]**.
The justification carried *"the whole tracker export is 3336549 B against 33745 B for the active rows
at six selected fields, 98.9x from field selection alone (measured 2026-08-14)"* — the **deleted
store's** bytes. `basicly-rn0o.2` swapped it at both sites, the top-level `description` and `units`, for
`5,890,340 B` against `44,454 B` at six selected fields, **132.5×**, measured 2026-08-19 **[M]**
re-read 2026-08-20. Nothing about the rule changed.

**The gate hazard that deferred the repair no longer exists, and the warning that announced it is
gone too (`basicly-desr1v`).** The schema's first line used to read *"prose here is indexed by
wired_or_deleted as field references (`basicly-r343`); avoid dataclass field names in descriptions
until that is fixed"*, which is why C7 below listed a schema-prose edit as a `wired-or-deleted`
hazard. `basicly-r343` had already narrowed the scan: `wired_or_deleted.schema_names` visits every
object key and only descends string values under `required`, `enum`, `const` and `$ref`, so a
`description` is never read **[M]** 2026-08-20. *Positive control on the same function: a probe
schema's key and its `enum` value are both indexed while its `description` prose is not, so the zero
belongs to the gate and not to the probe — and `record-field:basicly.run_record.CostRollup.dispatches`
still reproduces with the word `dispatches` sitting in the schema's `spend` description today.* The
first line now states what the gate does read; the hazard is a key or a permitted value repeating a
declared name, not prose.

### C7 — Stack, build steps and portability

- **Python 3.14 + `uv`; zero new runtime dependencies.** Current runtime deps are `jinja2`,
  `jsonschema`, `pyyaml`, `rich`, `ruamel.yaml` **[M]** `pyproject.toml:10-16`. The board needs
  `jinja2` (already there, and already used — `src/basicly/renderers/common.py`), `jsonschema`
  (already there, for `board validate`), `json`, `pathlib`, `http.server`, `threading` (stdlib).
  **The diff to `pyproject.toml` is empty.**
- **No JS framework, no bundler, no `node_modules` in the render path.** `package.json` carries
  exactly one devDependency, `markdownlint-cli2` **[M]**, and CI runs `setup-node` + `npm install`
  solely for it **[S** `.github/workflows/quality-gates.yml`**]**. Adding a bundler would put a
  `npm run build` in every committer's loop and in three OS matrix legs. **[D] The page is one HTML
  file with inline `<style>` and inline `<script>`, hand-written, vanilla — exactly the shape
  `site/index.html` already is** (42,767 bytes, single file, inline CSS, deployed by uploading the
  directory with **no build step** **[S** `.github/workflows/pages.yml`**]**).
- **House visual style is already committed and I am reusing it, not inventing one.**
  `site/index.html` `:root` defines the palette **[M]**: `--bg #0f1117`, `--bg-raise #171a23`,
  `--bg-sunk #0b0d12`, `--border #262b38`, `--text #d7dce6`, `--text-bright #f2f5fa`,
  `--text-dim #8b93a7`, `--indigo #818cf8`, `--amber #f59e0b`, `--orange #fb923c`, `--green #34d399`,
  plus a mono stack (`ui-monospace, "Cascadia Code", "JetBrains Mono", Consolas`) and a system sans
  stack. It already honours `prefers-reduced-motion`. The board lifts these tokens verbatim; it is
  our own file under our own licence.
- **Portability.** Every path in the snapshot is repo-relative; absolute worktree paths from
  `supervise.LaneView.worktree` / `.branch` **[M]** are passed through the existing
  `redact.redact_machine_paths` **[M]** before serialisation — reuse, not a second implementation.
  Port selection defaults to an ephemeral bind so no hostname or fixed port is ever committed.
- **Module-size ratchet.** A *new* Python module may never cross the 4,000-token cap
  (`read_cost.SCOPE_FILE_READ_CAP`, read by `.scripts/check_module_size.py` **[M]**) — the frozen
  list is closed to new entries. The decomposition below is cut into six small modules for that
  reason, not for taste.
- **Comment-density ratchet**, cap 50% (`.scripts/check_comment_density.py`'s `CAP` **[M]**), applies
  to every new module. A module with no frozen entry fails only above the cap
  (`_module_finding` returns `_over_cap` when `baseline is None` **[M]**), so for the board's new
  modules the binding rule is simply *under 50%*.
- **`wired-or-deleted` indexes the board schema's keys and its permitted values, never its prose —
  corrected 2026-08-20.** This entry read that the prose was indexed, citing the schema's own first
  line. That line was itself stale and is gone (`basicly-desr1v`); the scan reads object keys plus the
  string values under `required`, `enum`, `const` and `$ref` **[M]**, and see C6 for the positive
  control. **[D] So the hazard on a unit that widens the schema is a new *key* or a new *permitted
  value* that repeats a name declared in `src/basicly`** — which can retire that name's finding — and
  not a word in a `description`. A unit that adds either still runs `wired-or-deleted` before it
  commits rather than after.
- **`check_test_naming.py` binds forward only, so every new module owes a test module named after
  it.** *"A source unit must have a test file; a test file need not have a source unit"* **[M]**, and
  *"a derived name that is another unit's own test file does not count"* — so `tests/test_board_cli.py`
  is required by `board_cli.py` and cannot be satisfied by `tests/test_board_schema.py`. **[D] The
  decomposition below names the test module for every module it creates**, and unit C's
  `tests/test_cli_board.py` is renamed to `tests/test_board_cli.py` for exactly this reason: the old
  name derives from no source unit and therefore covers nothing.
- **`docs_claims` serialises the CLI units against the architecture document.** `_cli_subcommands` and
  `_cli_subcommands_covered` assert that every shipped subcommand, and every subcommand of a command
  *group*, appears in a table row under `## 22. The CLI surface` of
  `docs/architecture/architecture.md` **[M]** — today that section carries one board row,
  `` `basicly board validate` `` (line 1355 **[M]**). **[D] Units C, D and E therefore each declare
  `docs/architecture/architecture.md` in their scope.** That is not a formality: it serialises them
  against every architecture lane as well as against each other, and the decomposition below declares
  it so the plan gate can see the contention rather than discover it at a landing.

### C8 — Authority: the engine disposes, agents propose

> **The engine disposes, agents propose.** No model holds authority over the tracker, the schedule,
> or a required gate — at any autonomy level. **[S** `docs/architecture/architecture.md` §6 Core invariants**]**

**[D] The board is a *renderer with a keyboard*, and it holds no authority whatsoever.** Every action
is an `argv` list executed via `subprocess` against the installed `basicly` CLI, displayed to the
operator before it runs and displayed with its exit code and stdout after. The board module imports
no engine writer and calls no engine write function directly.

The complete action table — nothing else is clickable:

| Action | The existing surface that carries it | Confirmation |
| --- | --- | --- |
| Answer a queued decision | `basicly loop answer <issue> <decision-id> --answer "<text>"` | The operator types the answer. No default, no one-click. |
| Approve a checkpoint | `basicly policy checkpoint <issue> <name> --approve --confirm <CODE>` | **The code is not shown by the board.** See below. |
| Kill a lane | `basicly loop kill <issue> --confirm <CODE> --reason "<text>"` | Same. Plus a typed reason. |
| Show a lane's last run | `basicly loop session <root> --json` (read) | none — read-only |
| Copy a command | clipboard only | none — writes nothing |

**The confirm-code boundary, which is the one place this design could quietly break a gate.**
`policy.approve_checkpoint_guarded` grants approval on *"a TTY, a valid confirm code, or a covering
grant"*, and a non-interactive caller with no code gets a one-time `challenge` code it must relay to
a human **[M]**. The code is minted into `.basicly/usage/checkpoint-confirms.json`
(`policy._CONFIRM_FILE`) with a `policy.CONFIRM_TTL_SECONDS` of 900 s **[M]**.

A board that read that file, displayed the code, and offered a one-click approve would be relaying
the code **to itself**. It would satisfy the letter of the gate and defeat its purpose entirely: the
gate exists so that an automated caller cannot self-approve, and the board is an automated caller.

**[D] Therefore: the board never reads `checkpoint-confirms.json`.** When the operator clicks
*Approve*, the board runs the challenge command, shows the printed challenge — *"relay this to a
human"* — and presents an **empty input box**. The human must obtain the code from the terminal or
from whoever holds it and type it in. This is more friction than a button, deliberately: it is the
same friction a terminal operator has today, and the gate is worth exactly that friction. `loop kill`
is treated identically **[M** `basicly loop --help`, *"always gated on a human confirm code"***]**.

### C9 — The v1.0.0 freeze scope this enlarges

Five surfaces freeze at v1.0.0: **CLI commands and flags · `basicly.toml` plus the overlay contract ·
the catalog source schemas · the generated-file contract · the owned ledger format**
**[S** `docs/plan/implementation-plan.md` §7**]**.

This design adds to **four** of them — three when it was written, and a fourth once the S4/S7 remedy
landed as a decision. I am naming every addition so the freeze audit does not have to discover them:

| Frozen surface | What this adds |
| --- | --- |
| **CLI commands and flags** | One command group `basicly board` with three verbs: `board` (default, emit), `board serve`, `board validate`. Flags: `--out PATH\|-`, `--root ISSUE`, `--port N`, `--refresh SECONDS`, `--no-actions`, `--snapshot PATH`. |
| **Generated-file contract** | Two new generated artifacts: `board.html` and `board-snapshot.json`, both disposable and both regenerable from the command. Neither is committed. |
| **Owned ledger format** | **Only if unit `H` (OQ-15) ships**: one optional `viewed_at` field on the `[harness-wait]` marker payload. Additive and optional, so it is forward-compatible under the rule `work-tracker.md` §4.5 already states — *"skips unknown event kinds and unknown fields, preserving them verbatim"*. **This is the single reason `H` is optional and separately decided.** |
| `basicly.toml` | Nothing. Board settings, if any, are flags. **[D]** I am deliberately not adding a `[board]` table; "no unrequested config" is a Core Rule and flags are enough. |
| **Catalog source schemas** | **One, already shipped:** `.basicly/core/schemas/board-snapshot.schema.json` (`basicly-rn0o.1`). The 2026-08-14 revision recorded *Nothing* here and that was true when it was written. It is now the file the whole contract is, so it is the most consequential row in this table. |
| **The conformance kit's distribution path — NEW 2026-08-20** | One kit directory, `.basicly/core/kit/board/`, holding `conformance.py` and the distributed `board.html`. This is the row the owner decision on S4/S7 adds. It is a *distribution* surface rather than a code surface: what freezes is that a foreign consumer can copy those files and run them under a bare `python3`, and the file names it copies. |

**[D] The snapshot schema is frozen under its own `harness-board/vN` version, not folded into
basicly's semver.** This adopts the 2026-08-14 proposal verbatim, on the owner's decision of
2026-08-20, and the reason is unchanged: its whole purpose is to be implemented by producers that are
not basicly, and a foreign producer cannot track basicly's semver. A basicly major bump does not bump
`harness-board`, and a `harness-board` major bump does not need a basicly major bump.

**[D] Plus the one thing that proposal was missing: a named distribution path.** Freezing a contract
that only a repository which already ran `basicly install` can read freezes nothing a stranger can
hold. So the frozen unit is *the contract **and** the two files that distribute it*:

| What is frozen under `harness-board/vN` | Where it lives | How a stranger gets it |
| --- | --- | --- |
| the schema | `.basicly/core/schemas/board-snapshot.schema.json` | copied verbatim, or read in the how-to |
| the conformance check | `.basicly/core/kit/board/conformance.py` | copied verbatim; one file, stdlib only, no install |
| the consumer page | `.basicly/core/kit/board/board.html` | copied verbatim; opens at `file://` |
| the adapter contract in prose | `docs/how-to/adopt-the-board.md` | the entry point that names the three above |

The **fifth** freeze-audit consequence, and it is the one a reader will miss: the schema's own
compatibility rule is **stricter than the ledger's**, and it is written into the shipped file rather
than into this document. See `## The contract` — *"keys may be added within a major, permitted values
may never be widened within a major"* **[M]**. A freeze audit that assumed the ledger's additive-only
rule applied here would sign off a value-set widening that already-shipped consumers refuse.

### C10 — Security boundary

- Bind `127.0.0.1` explicitly, never `0.0.0.0`. A TV in an engineering room is driven by a browser on
  the same machine, or by a display attached to it — not by a LAN listener.
- The action endpoints are `POST` with an `Origin` check and a per-process token embedded in the
  served page, so a page in another tab cannot drive the board. This is the standard CSRF shape and it
  costs ~15 lines.
- The snapshot passes through `redact.redact_secrets` **[M]** and `redact.redact_machine_paths`
  before serialisation. Lane branch names and worktree paths are the known carriers of a username
  (`supervise.LaneView` **[M]**).
- `--no-actions` renders a read-only board. **[D] It is the recommended flag for an unattended wall**,
  and the reason is C8: a screen anyone in the room can touch should not be able to kill a lane.

### C11 — Module placement, and the two ratchets that decide it

**The import contract is exhaustive, so placement is a build blocker rather than a tidiness
question.** `.importlinter`'s `engine-layering` contract sets `exhaustive = True` and states it:
*"every top-level module of `basicly` must appear in exactly one tier, so a new module cannot join
the package without a maintainer deciding where it sits"* **[M]**. Siblings on one line *"may not
import each other. That is what makes a tier a tier rather than a bucket"*
**[S** `architecture.md` §34 **[M]** re-read 2026-08-20**]**. So this design owes a placement per
module, and it owes it before the first unit starts.

#### The placement table

Read the stack top-down; a higher tier may import a lower one, never the reverse.

| Module | Unit | Placement in `.importlinter` `layers` | May import | Must not import |
| --- | --- | --- | --- | --- |
| `board_cli` | C creates it; D and E extend it | a new line **immediately below `cli`** | `board_serve`, `board_snapshot`, `board_render`, `board_schema`, `supervise`, `ui` | `cli` |
| `board_serve` | D | a new line **immediately below `board_cli`**, still above `supervise` | `supervise` (the lock reader and its two cadence constants), `board_snapshot`, `board_render`, `board_actions` | `cli`, `board_cli` |
| `board_snapshot` | B | a new line **immediately below `loop \| release`** | `policy` (wait pairing), `run_record`, `tracker` / `owned_store`, `redact`, `board_schema` | **`supervise`** — see the inversion below — and `cli`, `board_cli`, `board_serve` |
| `board_render` | C | a new line **immediately above `board_schema`**, as `board_actions \| board_render` | `board_schema` and the leaf band (`redact`, `ui`, `schema`) | everything above it; it never sees engine state, only a parsed document |
| `board_actions` | E | the same new line, as `board_render`'s **sibling** | the leaf band only, plus stdlib `subprocess` | `board_render` (sibling rule), and **every writer** — C12 |

**One dependency in that table is not an import, and it is the one most likely to be got wrong.**
`board_snapshot` **depends on the marker-family declaration** — the roster of `[harness-*]` families it
must parse — and that dependency cannot be an import. The rule and the obstacle:

| | |
| --- | --- |
| **The rule** | The producer SHALL NOT carry a hand-maintained family list. A second, hand-kept roster is a second source that drifts silently, and nothing would detect the drift — the same cross-producer parity rot this revision exists to prevent, reintroduced one layer down. |
| **The obstacle, measured** | The authoritative roster is `.scripts/check_marker_families.FROZEN`, and `.scripts/` **is not an importable package** — it carries no `__init__.py` **[M]** — while its sibling gates reach *into* `basicly` via a `sys.path` insert (`check_comment_density.py` imports `basicly.read_cost` **[M]**). So the dependency runs gates → engine, and a runtime module importing a gate would invert it and put a gate script on the engine's import path. |
| **[D] The mechanism taken** | The producer declares its own set **and a test binds it**, loading the gate by file path exactly as `tests/test_check_marker_families.py` already does (a file-path load through `importlib.util`, not an import **[M]**). A test may reach a gate; a runtime module may not. Drift is then caught by a gate, which is the whole requirement. Unit B AC 9 and unit G AC 3 carry it. |

Two alternatives were considered and both are refused. **(a) The producer imports the gate** — refused on
the obstacle above: not a package, and it inverts the gates → engine direction. **(b) The roster moves
into a low-tier `src/basicly` module and the gate reads it from there** — the reuse-correct shape, and
refused *for this record* rather than on principle: it relocates a roster that `architecture.md` §32.3.2
specifies beside the alias table it feeds, and it widens unit B's scope into the gate, the architecture
document and every one of the 11 declaring modules. **If that move is ever made, unit B's AC 9 becomes an
import and this row simplifies** — recorded so the option is not lost.

Four new tier lines, five new modules. **[D] `board_page.html.j2` is a template, not a module**, and
templates are not in the contract; it sits beside `board_render` and `check_test_naming.py` has no
opinion about it.

**The inversion this table exists to prevent, and it is a real defect in the 2026-08-14
decomposition.** Unit B's acceptance criterion 1 names `.basicly/usage/supervisor.lock` as one of four
files the producer reads, and the natural way to read it is `supervise.read_holder` **[M]**. Unit F
then has the **supervisor** write a snapshot, which requires `supervise` to import the producer. Those
two together are a cycle: `supervise → board_snapshot → supervise`. It cannot be declared as an
exemption either, because the two existing exemptions are function-level imports across one tier and
this one is a genuine two-way dependency.

**[D] The producer therefore does not read the lock. It takes the live-lock facts as an argument, and
its caller supplies them.** `board_cli` (Mode A) and `board_serve` (Mode B) are both above
`supervise`, so both may call `supervise.read_holder` and pass the result down; unit F's caller *is*
the supervisor and already holds it. This costs one parameter, keeps `reuse > reinvent` — the lock is
read by the one existing reader, not by a second copy — and it is the same shape as OQ-D's narrowing:
*the consumer never chooses, the producer supplies*. Unit B's AC 1 is rewritten below to say so.

The alternative was to extract `read_holder`, `LOCK_FILE`, `HEARTBEAT_INTERVAL_S` and `STALE_AFTER_S`
into a low-tier module. **Rejected**: it moves a public surface every `supervise` caller uses, widening
unit B's scope into `supervise.py` and its callers, to buy a parameter.

#### The three parser groups, and the extraction — measured, not asserted

Units C, D and E each add a parser group to the CLI. Measured with the gates' own counters
(`check_module_size.module_tokens`, imports excluded; `check_comment_density.measure`), 2026-08-20:

| Quantity | Value |
| --- | --- |
| `cli.py` today | **53,883** tokens against a frozen **54,336** → **453** tokens of room **[M]** |
| eight comparable parser groups in `cli.py` | 207 · 256 · 258 · 394 · 457 · 507 · 738 · 1,797 tokens; **median 426** **[M]** |
| three more at the median | **1,278** tokens — **2.8× the room** |

**Three parser groups do not fit, and the arithmetic says so rather than a feeling.** One at the
median leaves 27 tokens; the largest single group in the file (`_add_loop_parser`, 1,797) would not
fit on its own.

**[D] The extraction is the board command group's grammar and dispatch, moved into
`src/basicly/board_cli.py`.** It is a nameable responsibility and it is not invented: `tracker_query`
already does exactly this, exposing `add_parsers(tracker_sub)` and a `HANDLERS` table that
`cli._add_tracker_parser` calls in one line **[M]**. `usage_report.cmd_outcomes` and
`tracker_write.cmd_write` are the same pattern for handlers. So the board follows a shipped precedent
rather than a size dodge, and after it lands **units C, D and E add zero tokens to `cli.py`**.

**Prose share, which the 2026-08-19 review left unmeasured and which the extraction turns on:**

| Subject | Prose share | Tokens |
| --- | ---: | ---: |
| `cli.py` whole | **26.3%** | 54,510 total, 14,327 prose **[M]** |
| the board block being moved (`_cmd_board_validate` + `cmd_board` + `_add_board_parser`) | **29.6%** | 304 **[M]** |
| — its handler half alone | 46.0% | 161 **[M]** |
| — its parser half alone | 11.2% | 143 **[M]** |
| `cli.py` after the move | **26.3%** | 54,205 total, 14,237 prose **[M]** |

**The rule is satisfied, and the honest reading is that it could not have failed here.** 29.6% > 26.3%,
so the extracted unit is prose-heavier than the module it leaves and the comment-density ratchet moves
in the safe direction. But the movement is **0.0 points at the gate's own one-decimal resolution**, and
the premise behind the rule does not hold for this module: `cli.py` carries **no**
`[tool.comment_density.frozen]` entry and sits 23.7 points under the 50% cap, so the only
comment-density outcome that can fail is a *new* module above 50%. `board_cli.py` arrives at 29.6%.
Both ratchets are green on arrival, and the rule was checked rather than assumed.

*The direction is worth stating because it is counter-intuitive:* every parser group in `cli.py`
measures **0.0%–10.9%** prose **[M]**, far below the file's 26.3%, so *adding* parser groups pushes
`cli.py`'s prose share **down** and *extracting* them pushes it **up**. Extracting the whole 7,288-token
argument-grammar block (5.7% prose **[M]**) would take `cli.py` to 29.5% — still 20.5 points under the
cap. **[D] That larger extraction is refused**: it is a 700-line refactor of a module no board unit
needs to restructure, and the board group's own extraction is sufficient and local.

**Size on arrival and after all three units:** `board_cli.py` starts at 304 tokens and, with three
parser groups and three thin handlers at the measured median, reaches roughly **1,600** — under half
the 4,000-token `read_cost.SCOPE_FILE_READ_CAP` **[M]**. `cli.py`'s room rises from 453 to about
**751** and then stops moving.

#### One unguarded surface, named so a unit does not miss it

`architecture.md` §34 states *"The 36 tiers group into nine bands"* and its mermaid diagram carries a
module count per band **[M]**. **Nothing binds those numbers to `.importlinter`**: no script under
`.scripts/`, no test, and `docs_claims` asserts CLI coverage and skill work types, not tier counts
**[M]** — positive control, `docs_claims`'s own assertion list is findable and names four assertions,
so the zero belongs to the absence and not to the probe. **[D] Each unit that adds a tier line updates
§34's counts by hand and says so in its commit**, and no section is renumbered — §34 is a cited surface
in this document and in the modules that cite it.

### C12 — The snapshot is the only interface, and the enforcement is structural

This is the constraint the whole 2026-08-19 owner decision reduces to, so it is stated as a rule and
given a gate rather than left as an intention.

**[D] RULE: every consumer reads the snapshot document and nothing else.** The renderer, the server and
the action surface take a parsed `harness-board/v1` document as their only input. None of them reads
`.basicly/ledger/`, `.basicly/usage/`, a tracker module, a store module or a writer. A consumer that
reaches past the snapshot is a consumer that only works against basicly's producer, which is the
parity rot `basicly-rn0o.13` is filed on.

**Tier placement alone cannot deliver this, and it is worth knowing why before someone assumes it
does.** In `.importlinter`'s stack, `owned_store`, `owned_write`, `mirror` and `tracker_argv` sit near
the **bottom** **[M]**, so every module placed above `board_schema` may import them by layering alone.
**[D] The enforcement is therefore a second contract of `type = forbidden`**, naming
`board_render`, `board_serve` and `board_actions` as sources and the tracker/store/writer modules as
forbidden targets. `import-linter` is already a dev dependency **[M]** `pyproject.toml:29`, so this
adds a contract, not a tool.

**The one permitted exception, and it is not a consumer.** `board_snapshot` — the reference producer —
reads all of it. That is its entire job, and it is why it sits above `policy` and `run_record` in C11
while every consumer sits below `board_schema`.

### C13 — The conformance kit is a distributed surface, and it has a stricter contract than `src/`

The S4/S7 remedy is a single-file check that imports no basicly. **[D] It ships as a kit**, at
`.basicly/core/kit/board/`, because the kit already *is* this contract, written down and gated:

> The portable half of this harness. Everything under here is deployed **into a consumer repository**
> and runs there, so it is written to a stricter contract than `src/basicly/`: the engine imports the
> kit, and the kit imports nothing.
> **[S** `.basicly/core/kit/README.md` **[M]** read 2026-08-20**]**

Three properties come free with that choice, and each one is a requirement the remedy would otherwise
have to invent:

| What the remedy needs | What the kit already guarantees |
| --- | --- |
| imports no basicly | *"A kit module imports the standard library and its own siblings, nothing else. `kit-boundary` enforces the first half"* **[M]**, and `kit-boundary.py` scans the whole `.basicly/core/kit` tree, so a new directory is gated on arrival with no gate edit **[M]** |
| runs on a stranger's Python | *"Parseable by an interpreter older than this repo's 3.14 floor: no syntax newer than 3.9, and **one exception class per handler**"* **[M]** — note this inverts the `python-guidelines` form used in `src/` |
| a named distribution path | a kit is *deployed into a consumer repository*; a per-kit `README.md` and a row in the kit table are the declared entry points **[M]** |

**[D] `jsonschema` is not available to it**, because it is third party and the kit contract forbids it.
So `conformance.py` implements the ruling in the standard library: the three required keys, the
`harness-board/vN` major check, the per-section verdict, and the absent-section inventory. That is a
**second implementation of a ruling `board_schema` already makes**, and the reuse rule is honoured by
binding them rather than by sharing code:

**[D] A test asserts `conformance.py` and `board_schema` agree on every fixture under
`tests/fixtures/board/`, verdict and exit code**, and it is a required criterion of unit G rather than
a nicety. Two implementations of one contract that nothing compares is the parity rot this whole
revision is about, one level down — and it is the same defect `basicly-rn0o.13` records for producers.

**[D] `kit-deployment` needs no entry.** The kit README's rule is *"a kit with no such requirements
needs no entry"* **[M]**, and the board kit imposes nothing on a host repository: it is copied, not
installed.

### Disposition of the architect review

Every finding from the four `[architect-review N of 4]` comments on `basicly-rn0o.10`, and where it
landed. Nothing is silently dropped; two are refused, with the reason.

| Finding | Disposition | Where |
| --- | --- | --- |
| The four-cell matrix uses "harness" in two readings and the design never distinguishes them | **Adopted, reading (b)** — the driver of the loop — on the owner decision of 2026-08-20 | `## The contract` → *The four harness-and-tracker combinations* |
| `TRACKER_MODES` is a one-member tuple and `config.py` refuses an unrecognised mode, so every external-tracker cell needs a foreign producer under either reading | **Adopted as a measured fact**, re-verified 2026-08-20 | same section, the fact box |
| The dispatch ledger holds zero copilot records, so spend and health fidelity for either copilot cell is unmeasured, and a family with no usage format falls back to an estimate the schema cannot mark | **Adopted as a declared limit**, with a named remedy that is v1-compatible | same section, *Declared limit* |
| S4 is false: `basicly board validate` **is** the basicly runtime | **Adopted**, re-measured independently 2026-08-20 | S4 |
| S7 is false: the contract must be installed in the tree | **Adopted**, and distinguished from S4's failure | S7 |
| Pick one of three remedies; whichever is chosen adds a surface to the v1.0.0 freeze audit | **Adopted**: the standalone script, on the owner decision | S4, C9, C13 |
| The fail-open direction must not be undone | **Adopted as a rule** | S4, last paragraph |
| C9's `harness-board/vN` freeze proposal is correct, adopt verbatim plus a named distribution path | **Adopted verbatim, plus the distribution table** | C9 |
| The design must carry a placement table, one row per new module, with its tier and what it may import | **Adopted** | C11 |
| `cli.py` 453 · `supervise.py` 1,503 · `policy.py` 226 tokens of room; three parser groups will not fit | **Adopted and re-measured**; the arithmetic is stated | C11 |
| THE TRAP: do not split a module to move a number; extract along a nameable responsibility | **Adopted**, and the precedent it follows is named (`tracker_query.add_parsers`) | C11 |
| Per-module prose share was not measured and is owed | **Measured and reported**, with the finding that the rule cannot bind on `cli.py` | C11 |
| `docs_claims` puts `architecture.md` in units C, D and E's scope, serialising them against the architecture lanes | **Adopted**; the scope lines now declare it | C7, and each unit's **Scope** |
| C7 hazard: the board schema's prose is indexed by `wired-or-deleted` as field references | **Adopted** | C7 |
| C7 hazard: test naming binds forward only, so every new board module needs a test module named after it | **Adopted**, and unit C's `test_cli_board.py` is renamed for it | C7 |
| OQ-E answered — the ledger is live and is the only store | **Closed** | OQ-E |
| OQ-B moot — wall mode serves over HTTP | **Closed as moot** | OQ-B |
| OQ-D narrows — the consumer never chooses the root, the producer supplies it | **Narrowed and closed**; the same rule resolves the C11 inversion | OQ-D, C11 |
| OQ-A is **overridden, not answered**, and the design must say so plainly | **Adopted, stated twice** — once as a superseded decision, once as an accepted risk | `## SUPERSEDED — the four-unit phased scope`, OQ-A |
| OQ-C still open and now blocks unit B | **Settled by measurement**, twice, by paths sharing no step; the one disagreement is dispositioned | OQ-C |
| The producer must not carry a hand-maintained family list; bind it to the roster's own source and put the dependency in the placement table | **Adopted**, with the mechanism corrected from an import to a test-time binding — `.scripts/` is not an importable package **[M]** | C11 placement table, unit B AC 9 |
| The conformance kit should assert the roster agreement too | **REFUSED**, with the reason: it would put a basicly-internal fact on the producer-neutral surface | the refusals below, item 0 |
| OQ-F still open and now load-bearing | **Settled as a design statement**, with the rejected alternatives | OQ-F |
| C2 must not be re-opened | **Honoured** — untouched | C2 |
| The RULE in C6 must not be re-opened; only its number is stale | **Honoured**: the rule is untouched, and the stale copy is located in the *schema file* and recorded as a defect for the next unit that opens it | C6 |
| C8 must not be re-opened | **Honoured** — untouched | C8 |
| C10's shape must not be re-opened | **Honoured** — untouched | C10 |
| The shipped schema's open strings on phase, status, type and edge kind are more producer-neutral than this document's prose; reading the prose as the contract generates false findings | **Adopted**, and generalised: the shipped file is the contract, this document's sketch is illustrative | `## The contract`, first paragraph |
| OQ-G's answer — the dependency graph stays off the wall | **Honoured** — untouched | OQ-G |
| The rename-atomic JSON transport, with SQLite refused | **Honoured** — untouched | `## The contract`, transport |

**Three things this revision refused, and why.**

0. **Putting the marker-family agreement assertion into the conformance kit.** It was asked for
   alongside the producer binding, and it is refused because it would contradict the acceptance
   criterion the whole revision is built on. Marker families are a **basicly-internal parse concern**:
   a foreign producer has none, the shipped schema has no `artifacts` section, and `events[].kind`,
   `units[].type` and `units[].phase` are open strings with no `enum` **[M]**. A kit that asserted a
   basicly roster would be a producer-neutral surface carrying a basicly-specific fact, which is
   exactly the coupling `## The contract` refuses. **The assertion belongs to unit B's test (AC 9),
   where the parser it constrains lives.** The kit's own parity obligation is the different one it
   already carries: `conformance.py` agrees with `board_schema` on every fixture (unit G AC 3).

1. **Extracting `cli.py`'s whole argument grammar.** It would move 7,288 tokens along a genuinely
   nameable responsibility and it is still refused: no board unit needs `cli.py` restructured, the
   board group's own extraction is sufficient, and a 700-line move touching every command group would
   serialise the board units against every CLI lane in the tree. Recorded in C11 rather than dropped.
2. **Extracting `supervise`'s lock reader to a low tier.** It resolves the C11 inversion too, and it is
   refused because it relocates a public surface every `supervise` caller uses in order to save one
   function parameter. Recorded in C11 with the alternative that was taken.

---

## Open questions

Things I could not establish. These are not guesses dressed as design.

- **OQ-A — Does anyone actually stand in front of it? — OVERRIDDEN 2026-08-18, NOT ANSWERED.** This
  wording is deliberate and it is the most important line in this section. The entire arrival mechanism
  assumes a person in the room during working hours. Measured 2026-08-19, **5 tail events** (20% of
  wait, or 8.9% once the two multi-day escalations are removed from both sides of the ratio) were even
  *asked* during plausible office hours **[M]**; remove those two escalations and the four office-hours
  events behind the residual 14,527 s are the same four the 2026-08-14 revision found, to the second —
  so the effective n is **4**. Whether a display changes behaviour at n=4 is not answerable from this
  repository's data and no other source was found.

  **What would have unblocked it:** ship Mode A + Mode B read-only, run four weeks against the real
  factory, compare the office-hours tail before and after. That was the 2026-08-14 decision and it is
  the experiment this question is *for*.

  **What happened instead:** the owner decided wall mode (D) and the action surface (E) on 2026-08-18
  without the trial. **[D] The arrival assumption at n=4 is therefore an accepted risk carried into the
  build, and it is recorded as that rather than as a closed question.** Three consequences follow and
  all three are live:

  1. **The question stays open.** No measurement answered it. A later reader must not read D and E
     having shipped as evidence that it was settled.
  2. **`## Success` still refuses a wait-time claim**, and that refusal is now doing real work: it is
     the only thing standing between an unmeasured assumption and a release note that claims a
     reduction. A release note claiming one would still be refused.
  3. **The instrument that could settle it is unit H**, via `basicly-rn0o.8`'s view events — and H is
     still optional and separately decided, because a passive wall would close OQ-15 falsely (C3). So
     the question that justifies D and E can only be answered by the unit that is not in scope. That
     is the shape of the risk, stated plainly.

  `## SUPERSEDED — the four-unit phased scope` carries the decision history. This entry carries the
  evidence, so that "what were D and E justified on?" has an answer at the place the question will be
  asked.
- **OQ-B — Does `<script src="./board-data.js">` reload under `file://`? — MOOT, closed 2026-08-20.**
  The question existed because a `file://` mode that auto-refreshed without a server would have been
  strictly better than Mode B for a solo operator. Wall mode is now in scope and **serves over HTTP**
  (unit D, `127.0.0.1` only), so the auto-refreshing path exists and does not depend on `file://`
  reload semantics. Mode A continues to inline, so neither mode depends on the answer. **It is closed
  as moot rather than answered:** nobody ran the four-browser trial, and if a `file://` auto-refresh is
  ever wanted the question returns unchanged.
- **OQ-C — Which marker families does the board's parser have to know? — SETTLED 2026-08-20 by
  measurement. It blocks unit B, so it is answered here with a set and not with a direction.**

  The 2026-08-19 answer was ten families with three at zero, and it was measured by a hand probe over
  raw text. That probe was the wrong instrument: it cannot tell a marker from a bead description
  quoting a marker. **The right instrument already exists** —
  `.scripts/check_marker_families.py` reconciles exactly the two populations this question needs, and
  states its own discriminator: *"a family counts only where it **leads** a comment body, which is
  where a writer puts it"* **[M]**. Both counts below are taken with that script's own functions
  (`declared_families`, `logged_families`) against `.basicly/ledger/events-0001.jsonl`, **2,784 comment
  events**, 2026-08-20.

  **The *set* is the answer; the per-family counts below are a dated sample and the design does not
  depend on them.** The store gained 13 comment events while this revision was being written, so every
  count in the next two blocks moved and the set did not. Unit B binds to the set (AC 9); nothing binds
  to a count.

  **Count 1 — declared by the engine: 11 families**, one producing module each **[M]**:

  ```text
  harness-artifact       artifact_record.py     harness-policy    policy.py
  harness-classification integrity.py           harness-retro     retrospective.py
  harness-cost           run_record.py          harness-review    lens_review.py
  harness-decision       decision_marker.py     harness-run       run_record.py
  harness-info           supervise.py           harness-sizing    decompose.py
                                                harness-wait      policy.py
  ```

  **Count 2 — observed leading a comment body: 12 families** **[M]**:

  ```text
  harness-policy 1091   harness-run 398   harness-wait 350   harness-cost 214
  harness-decision 176  harness-info 96   harness-artifact 61
  harness-classification 35   harness-sizing 35   harness-overrun 12
  harness-review 4      harness-retro 2
  ```

  **The reconciliation, which is the answer.**

  | | Count | The disagreement |
  | --- | ---: | --- |
  | declared by code | 11 | — |
  | observed in the ledger | 12 | — |
  | declared but never observed | **0** | every family the engine declares has rows |
  | observed but not declared | **1** | **`[harness-overrun]`, 12 rows, no producer anywhere in `src/`** |

  **[D] The authoritative figure is `11 declared, 1 retired, 12 frozen` — never "12 families".** That
  is the gate's own summary line, and the split is the part a producer needs: a parser that treats the
  retired family as live waits for rows nothing writes any more, and a parser that omits it renders 12
  real rows as nothing. **Which the board does: it parses all 12 and writes none of them.** The
  live/retired distinction governs *writing*, and the board never writes a marker (C8), so for a
  read-only consumer the correct set is the frozen 12 with no live/retired branch in the parse at all.
  The distinction still has to be *recorded*, because a later reader will otherwise "tidy" the retired
  entry out of the producer's set and silently lose the history.

  **Two denominators that are easy to swap, and one of them was swapped in review.** The gate's
  summary counts **rows**, and `rows = sum(census.rows.values())` **[M]** — the total marker-led
  comment bodies across **all 12 families**, not the retired family's history and not the number of
  comment events scanned:

  | Figure | Value | What it counts |
  | --- | ---: | --- |
  | comment events read | **2,797** **[M]** | the population scanned, marker-led or not |
  | rows, the gate's summary figure | **2,479** **[M]** | marker-led bodies summed over all 12 families |
  | `[harness-overrun]` rows | **12** **[M]** | the retired family's whole history |

  **So "ignoring the retired family drops 2,479 rows of history" would be wrong by a factor of 206.6
  **[M]**. It drops **12**.** The gate's row total is a store-wide figure, and it is not stable enough
  to put in a criterion: this document measured **2,474** earlier the same day and **2,479** a few
  landings later, from the same instrument on the same checkout **[M]**, because the ledger is
  git-tracked and gains rows on most landings **[M]**. Treat it as a health indicator for the probe —
  a sudden fall means the probe broke — and never as a per-family quantity. Stated because the
  confusion is cheap to make, it was made once in review, and it was on its way into an acceptance
  criterion.

  **`[harness-overrun]` is the whole finding, and it is not a defect — it is a retirement.** The gate
  froze it deliberately and records why: *"`[harness-overrun]` carries 12 rows in this repository's log
  and has no producer anywhere in `src/`; the string survives only in two negative test assertions. A
  list derived from the live constants drops it, and those 12 rows then resolve to nothing"* **[M]**.
  **[D] So the board's parser set is the gate's frozen literal — 11 live plus 1 retired = 12 — and not
  the declared list**, because a producer built from the declared list renders 12 real rows as nothing.
  `.scripts/check_marker_families.py` reports `11 declared, 1 retired (12 frozen)` and exits 0
  **[M]**, so the two lists already agree and unit B has a single source to bind to.

  **The three "zero" families from the 2026-08-19 answer were two different mistakes, and both matter
  to unit B's parser.**
  - `[harness-review]` and `[harness-retro]` are **no longer zero** — 4 and 2 rows. The earlier zero
    was correct when it was taken and expired; a producer written against it would drop them.
  - `[harness-side]` was **never a marker at all**. The gate's own history records it as *"a phrase
    from a `commit.py` sentence rather than a marker"* **[M]**, and the ledger confirms it: the one
    occurrence of the string sits inside a `created` event's description — a bead *about* that very
    mistake (`basicly-vkh0.37`) **[M]**.
  - The raw-text probe also finds `[harness-estimate]` and `[harness-conflict]`, one occurrence each.
    Both sit inside `created` events describing markers a future unit *proposes* **[M]**
    (`basicly-kjc5.48`, `basicly-m4zv.5`). **[D] Neither is in the set.** A producer keyed on raw text
    would have grown two panels for markers that do not exist — which is precisely the population
    error the leading-marker discriminator exists to prevent, and it is why unit B binds to the gate.

  *Positive control: the probe returned 12 non-empty families over 2,784 comment events and the two
  lists agree at 11, so a zero for `[harness-side]` is a property of the corpus and not of the probe.
  Re-run at the end of the same session over 2,797 comment events: still 12 families, still 11
  declared, `[harness-overrun]` still 12 rows **[M]** — the set is stable under the drift that moved
  every count.*

  **[D] What the board does about a family with no sample: nothing, by construction.** The board
  renders sections of the *snapshot*, not families of the *ledger*. A family the producer has never
  parsed contributes no key, an absent section renders `not emitted by this producer`, and no panel is
  ever coded against a family. The open half of the 2026-08-19 question — *what should the board do
  about the empty ones?* — dissolves once the interface is the snapshot rather than the ledger, which
  is the whole point of this revision. **The obligation lands on unit B instead**, as an acceptance
  criterion: bind the parser's family set to `check_marker_families.FROZEN` so a thirteenth family
  cannot appear in the log without the producer's own gate saying so.

  **Second derivation, by a path sharing no step, and the one disagreement it produced.** A raw-text
  occurrence count over `src/` **and** `.scripts/` — `grep -rho '\[harness-[a-z-]*\]'` — returns
  **12** families code-side, against the AST probe's **11** **[M]**. The disagreement is
  `[harness-overrun]`, and it is instructive rather than a rounding error:

  | | Where its occurrences are | Is it a producer? |
  | --- | --- | --- |
  | `src/` | **zero occurrences** **[M]** | no |
  | `.scripts/check_marker_families.py:11` | the gate's own **docstring**, explaining why the family is frozen | no |
  | `.scripts/check_marker_families.py:112` | the gate's **frozen literal** | no |
  | `tests/` | three assertions, two of them negative **[M]** | no |

  **The raw grep counted the census as a member of the census.** Its twelfth family comes entirely
  from the file whose job is to hold the inventory, so the two `12`s agree by a route worth stating:
  the ledger has 12 because 12 rows exist, and the grep has 12 because the frozen literal is
  maintained against those rows. The agreement is real and it is *derived from* the same fact, not
  independent of it — which is why the AST count over `src/` alone (**11**) is the honest
  "declared by code" figure and the frozen literal (**12**) is the honest "must be parsed" figure.

  **A second reason the raw grep is the wrong instrument: occurrences are not write sites.**
  `[harness-classification]` has 4 occurrences of the literal and **one** definition of it —
  `integrity.CLASSIFICATION_MARKER` at `integrity.py:52`, placed there rather than in its writer
  *"because two tiers that read it back may not import"* it **[M]**. Two of the other three are
  docstring prose at `classify.py:20` and `:82`, and the fourth is the gate's own literal **[M]**.
  *A third shape the occurrence count misses entirely:* `classify.py:39` **re-exports** the constant
  as `CLASSIFICATION_MARKER` **[M]**, so it is a real reference that the literal grep cannot see and
  the AST probe correctly does not credit as a declaration. `[harness-review]` is the simpler shape:
  4 occurrences, the definition at `lens_review.py:33`, with `lens_review.py:16` and
  `retrospective.py:37` as prose **[M]**. The AST probe reports one producing module per family
  because it excludes bare string statements, which is the discriminator an occurrence count lacks —
  and this correction was itself found by a gate, `docs-citations`, which refused the first draft of
  this paragraph for citing `classify.py:20` while naming a symbol that lives at `:39`.

  **Which population this question is about, stated because two overlapping ones exist.** *Marker
  family* and *artifact kind* are different populations that intersect: `classification` is produced
  today as a **comment marker** (`integrity.CLASSIFICATION_MARKER`) rather than as a typed artifact
  through `handoff.record` **[M]**. **[D] OQ-C is about marker families only, and only inside the
  producer.** The snapshot touches **neither** population as a closed set: the shipped schema has no
  `artifacts` section, and `events[].kind`, `units[].type` and `units[].phase` are all **open
  strings** with no `enum` **[M]**. So a change to either roster can never break a consumer — it can
  only change what unit B is able to parse, which is why AC 7 binds the producer to the frozen
  literal and why nothing in the schema needs to know about it.
- **OQ-D — Which root does an unattended wall display show? — NARROWED and CLOSED 2026-08-20.** The
  question was framed as a consumer problem and it is not one. **[D] The consumer never chooses the
  root; the producer supplies it.** `session.root` is a field of the snapshot, so the page renders
  whatever root the document names and has no selection logic, no fallback ladder and no "current
  session" concept to invent. What is left is a *producer* question with a three-line answer: the
  reference producer takes `--root` when given one, otherwise the root named by a fresh supervisor
  lock, otherwise it omits the `session` section entirely rather than guessing — and an omitted
  section renders `not emitted by this producer`, which is the honest state for "nothing is being
  supervised".

  The same narrowing resolves the layering inversion in C11: the producer does not read the lock
  either, its caller does. So the rule is one rule at two levels — *the layer above supplies the fact
  the layer below cannot honestly derive.*
- **OQ-E — RESOLVED 2026-08-19. `.basicly/ledger/events-0001.jsonl` is the live and only source.**
  The 2026-08-14 revision could not tell a frozen import from a live log and declined to guess. It
  is a live log: `basicly.toml` declares `[tracker] mode = "owned"`, the cutover ladder has collapsed
  to that one rung, and the log has grown from 3,775 rows over 643 records to **5,924 rows over 968
  records** **[M]**, with this record's own claim event in it. The open part of the question is
  answered too: it *is* a better event source than parsing comment markers for anything the event
  kinds already carry — `created`, `status`, `edge`, `field`, `gate`, `artifact` — and the marker
  parse is needed only for the payloads still written as comment text (asks, spend, dispatch).

  **One trap the board must not walk into.** In a linked worktree `.basicly/ledger/redirect` points
  at the base checkout **[M]**, so the tracker a lane reads is the base repository's. A producer that
  resolved the ledger by path rather than through the kit's `ledger_dir` would read a stale copy of
  the log in every worktree.
- **OQ-F — What is the wall's idle state? — SETTLED 2026-08-20 as a design statement.** It became
  load-bearing when wall mode entered scope, so it is answered rather than carried. The problem is
  real: 90% of the time nothing is waiting, and a screen that is calm 90% of the time gets ignored,
  which destroys the arrival mechanism the whole thing rests on.

  **[D] The idle state is the same layout, and the ask band is replaced by a *watch band* that is never
  empty.** The band carries exactly one line, chosen by the first rule that applies:

  | Priority | Condition | The line | Colour |
  | ---: | --- | --- | --- |
  | 1 | the snapshot's age exceeds `freshness.stale_after_s` | `STALE 74s — this screen is not being refreshed` | amber |
  | 2 | an ask is pending | `2 ASKS WAITING …` (unchanged) | **the only red on screen** |
  | 3 | a lane is over its token budget, or over the p90 dispatch elapsed | `SPEND OVER BUDGET` / `LANE RUNNING LONG` | amber |
  | 4 | lanes running, none of the above | `NEXT CHECKPOINT: ship on basicly-kjc5.57 — build running 24m` | dim |
  | 5 | no lanes | `IDLE — 180 READY, NEXT UP basicly-rn0o.2` | dim |

  **Rule 1 outranks the ask, and that inversion is the load-bearing part.** The failure an idle state
  must not have is being indistinguishable from a broken producer. A calm screen and a dead screen look
  identical, and a room that has once mistaken one for the other will read every calm screen as dead.
  So staleness pre-empts even a waiting ask: a stale screen cannot honestly claim to know whether
  anything is waiting.

  Every field is already in the snapshot — `freshness`, `asks`, `session`, `lanes`, `backlog` — so this
  adds no producer work, no new key and no schema change. It keeps the fixed-height no-reflow rule
  (one line, five spellings) and it keeps red meaning exactly one thing.

  **The three alternatives, and why each was rejected.**
  - **Ambient motion** — rejected on three counts, any one sufficient: it is burn-in risk on a display
    left on for weeks, which is the same reason this design already drops sssf's aurora washes; it
    fights `prefers-reduced-motion`, which the house palette already honours **[M]**; and motion that
    carries no information trains the room to ignore motion, which is the one channel the ask band
    needs to keep.
  - **Burn-in-safe dimming** — rejected as the exact failure mode above. A dimmed screen is
    indistinguishable from a dead one, so it reintroduces the false zero S2 exists to prevent, and it
    makes the transition *into* an ask fight a dark-adapted viewer.
  - **A rotating "what shipped today"** — rejected on two counts: it is historical reporting, which
    `## Out of scope` refuses by citing `work-tracker.md` §15's exclusion of *"reporting ceremony
    beyond what the loop consumes"*; and a rotating panel makes the screen's content depend on *when*
    you looked, so the header's `as of Ns` no longer describes what is on it.

  **The honest limit, stated rather than buried.** Whether rule 5 is enough to keep a room *looking* is
  OQ-A, which is overridden and not answered. OQ-F settles what the screen *shows*; it does not claim
  that what it shows works. `basicly-rn0o.8`'s view events are the only instrument that could tell,
  and they are unit H.
- **OQ-G — Do 685 `parent-child` + 319 `blocks` edges — 672 of them touching one of the 236 active
  records — render legibly at six metres?** **[M]** on the edge counts; unmeasured on the
  legibility, and the counts have roughly quintupled since the question was first asked. I have deliberately **left the
  dependency graph off the TV layout** for that reason and put it in the click-through detail only.
  `work-tracker.md` §4.5 already warns that *"What agents handle well is a small, explicit,
  labelled edge set — not traversal of a large one"* **[S]**, and I suspect humans at six metres are
  worse than agents here, not better.

---
---

## The contract: `harness-board/v1`

This is the reusable part. The page is replaceable; this is not. Since 2026-08-19 it is also **the only
interface**: every consumer reads a snapshot document and nothing else (C12), and basicly's producer is
one implementation of the contract rather than the contract itself.

**READ THIS BEFORE TREATING ANY PROSE BELOW AS THE CONTRACT.** The contract is the shipped file,
`.basicly/core/schemas/board-snapshot.schema.json` (`basicly-rn0o.1`). Everything in this section is a
**sketch of it**, kept because it is readable and because the criteria in `## Decomposition` were
accepted against it. Where the two differ, **the file wins** — and they do differ, in three ways that
each generate a false finding if the prose is read as authoritative:

| This document's prose | The shipped schema |
| --- | --- |
| The compatibility rule is the ledger's additive-only rule | It is **stricter** — see the box below. The schema states so explicitly: *"COMPATIBILITY RULE, and it is not the ledger's"* **[M]** |
| `phase`, `status`, `type` and edge kind read as closed vocabularies | They are **open strings** that name this project's values as *examples* **[M]**. The shipped file is more producer-neutral than this sketch, which is the right direction and is not a defect to be "fixed" back |
| `freshness.source` is one of three values | It is one of **four**: `supervisor-tick`, `self-refresh`, `state-change`, `one-shot` **[M]** |

**Transport [D]**: a file at a path the consumer is told, default
`.basicly/usage/board/snapshot.json`. Written temp-then-rename so a reader sees the old file or the
new one, never a partial. In serve mode the identical bytes are also `GET /snapshot.json`. **There is
no other transport** — no socket, no stream, no database. A producer that can write a file can drive
this board.

**Versioning and compatibility rule [D] — as the shipped schema states it, which is *not* the
ledger's rule.** The 2026-08-14 prose said *"deliberately the same rule `work-tracker.md` §4.5 already
fixes for the ledger rather than a second one"*. That was wrong, and the shipped file corrects it with
the reason **[M]**:

> **Keys may be added within a major; permitted values may never be widened within a major.** An
> undeclared key is counted and reported, so adding one is compatible; a value outside a closed set is
> refused, so widening a set, loosening a pattern or raising a length bound produces documents that an
> already-shipped consumer of the same major refuses. The ledger's additive-only rule does not transfer
> whole, because **every ledger reader is ours and a board's readers are not.**

- `schema` is `harness-board/vN`. **N changes only on a break**, and *a new permitted value is a
  break*. The sets here were widened once, before any release carried the file — it landed 2026-08-16,
  after the v0.9.0 tag of 2026-08-14 — so from the first release that carries it, a new permitted value
  takes a new major **[M]**.
- **Frozen under its own version, not basicly's semver** (C9). A basicly major does not bump
  `harness-board`, and the reverse holds too.
- A consumer meeting a **different major** refuses to render and names both versions (transcript
  Mode C). It does not guess.
- **Adding a key is compatible.** A consumer skips unknown keys and **reports their count**; it never
  errors on them and never silently drops them.
- **Exactly three keys are required: `schema`, `generated_at`, `freshness`** **[M]**. Every other
  section is optional — 12 of them — and an absent section renders `not emitted by this producer`,
  which is what makes the contract adoptable incrementally. *(The 2026-08-14 prose said "only `meta` is
  required"; there is no `meta` property in the shipped schema.)*
- **The ruling is per section, not per document** (`basicly-rn0o.11`, closed). Only the three required
  keys can refuse a document; a violation inside an optional section withholds **that section** and
  names its violations while the conforming sections still draw. So a length bound costs a panel, never
  the screen — which is what stops a foreign producer's first honest attempt from blanking a wall.

**Transport [D]**: a file at a path the consumer is told, default
`.basicly/usage/board/snapshot.json`. Written temp-then-rename so a reader sees the old file or the
new one, never a partial. In serve mode the identical bytes are also `GET /snapshot.json`. **There is
no other transport** — no socket, no stream, no database. A producer that can write a file can drive
this board.

**The minimum conformant snapshot** — this is the whole barrier to entry for a foreign harness:

```json
{
  "schema": "harness-board/v1",
  "generated_at": "2026-08-14T16:42:52Z",
  "freshness": { "source": "one-shot", "cadence_s": null, "stale_after_s": 60 }
}
```

**The full shape.** Field-selected, never record-shaped (C6: 132.5× **[M]**). No `description`, no
`acceptance_criteria`, no raw comment body ever crosses this wire.

```jsonc
{
  "schema": "harness-board/v1",
  "generated_at": "2026-08-14T16:42:52Z",           // REQUIRED, RFC3339 UTC
  "freshness": {                                     // REQUIRED
    "source": "supervisor-tick",                     // supervisor-tick | self-refresh | state-change | one-shot
    "cadence_s": 15,                                 // null when one-shot
    "stale_after_s": 60
  },
  "generator": { "tool": "basicly", "version": "0.9.0" },
  "repo":    { "name": "basicly", "branch": "main", "head": "256f8d0", "dirty": false },

  "session": {                                       // the live factory, or null
    "root": "basicly-kjc5", "root_status": "open", "supervised": true,
    "holder": { "id": "bc7cc925", "heartbeat_age_s": 6, "stale": false },
    "grant_level": "L3", "token_budget": 4000000, "spent_tokens": 177970761,
    "human_wait_s": 30856, "delegated_wait_s": 237, "dispatch_s": 16074.8
  },

  "lanes": [                                         // one per in-flight unit
    { "id": "basicly-kjc5.57", "phase": "build", "status": "in_progress",
      "agent": "claude", "model": "claude-opus-5", "live": true,
      "started_at": "2026-08-14T16:18:00Z", "elapsed_s": 1440,
      "tokens": 18794333, "cost_usd": 13.13,
      "context_used": 209041, "context_window": 1000000,   // BOTH or the bar is not drawn
      "rework_attempt": 0, "rework_allowance": 2,
      "branch": "<redacted>", "note": null }
  ],

  "asks": [                                          // pending only; PAIRED, not counted (S6)
    { "wait_id": "basicly-1th1#wait-classify", "issue": "basicly-1th1",
      "kind": "checkpoint", "subject": "classify",
      "requested_at": "2026-08-14T16:38:41Z", "waiting_s": 251,
      "question": "approve the plan?",
      "actions": ["checkpoint-approve"] }            // names entries in the closed action table
  ],

  "gates":  { "mode": "fast", "recorded_at": "2026-08-14T16:42:52Z", "passed": true,
              "checks": [ { "name": "ruff", "status": "pass" } ] },

  "spend":  { "lifetime_usd": 1254.26, "largest_dispatch_usd": 36.59,
              "input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0,   // billed vs moved, never summed
              "scope": "machine-local" },                        // honesty flag: one operator only

  "health": [ { "agent": "claude", "runs": 163, "score": 0.825,
                "failure_rate": 0.178, "drift": -0.04 } ],

  "backlog": { "total": 968, "active": 236, "ready": 180, "blocked": 2, "in_progress": 3,
               "closed": 732, "by_priority": { "P0": 4, "P1": 96, "P2": 58, "P3": 13 } },

  "events": [ { "at": "2026-08-14T16:41:02Z", "issue": "basicly-kjc5.57",
                "kind": "dispatched", "text": "build claude-opus-5" } ]   // bounded, last 6
}
```

**Notes that are part of the contract, not commentary:**

- `spend.scope` is **required whenever `spend` is present**, and `"machine-local"` is the honest value
  for basicly today, because `.basicly/usage/` is self-ignored (`.basicly/usage/.gitignore` line 1 is
  a bare `*`, and `git ls-files .basicly/usage/` returns zero files **[M]**). The page renders that
  word. A board that showed a team's spend when it holds one
  operator's would be the overclaim `work-tracker.md` §4.3 is guarding against, in the money domain.
- `context_used` and `context_window` are a **pair**; a producer that knows only one emits neither,
  and the consumer draws no bar (unit C, AC 7).
- `asks[].actions` may only name entries in the closed action table of C8. A producer cannot invent an
  action, because the consumer has no mechanism to execute one it does not already know.
- Everything under `lanes[].branch` and any path-shaped string is redacted at the producer, not the
  consumer.

### The adapter contract a foreign producer satisfies

**What a foreign harness must do to adopt it**: write the minimum snapshot above, then add whichever
sections it can populate. Nothing else. That is the whole contract, and these six clauses are all of
it — they are what unit G's how-to states and what unit G's conformance kit checks.

1. **Write three keys.** `schema`, `generated_at`, `freshness`. A four-line file is a conforming
   snapshot. Every other section is optional and an absent one renders as absent, never as zero.
2. **Declare only what you know.** A section you cannot populate is **omitted**, not filled with
   zeros. This is a rule, not a courtesy: a zero that means "unknown" is the false-zero failure S2
   exists to prevent, and it is the one way a conforming producer can still lie.
3. **Never widen a value set.** Adding a key is compatible; a value outside a closed set is refused
   by an already-shipped consumer of the same major. If your vocabulary does not fit, check whether
   the property is an open string — `phase`, `status`, `type` and edge kind are **[M]** — before
   assuming you need a new major.
4. **Redact at the producer, never at the consumer.** Branch names and any path-shaped string are the
   known carriers of a username. The consumer has no redaction pass and must not need one.
5. **Write temp-then-rename.** A reader must see the old document or the new one, never a partial.
   There is no other transport.
6. **Name only actions the consumer already knows.** `asks[].actions` may only name entries in C8's
   closed action table. A producer cannot invent an action, because the consumer has no mechanism to
   execute one it does not have.

**How it is proved, and this is the part that changed on 2026-08-20.** `python3 conformance.py <file>`
— one standard-library file, no basicly, no install (S4, C13). `basicly board validate <file>` remains
available *inside* a basicly repository and is the same ruling; a unit G test asserts the two agree on
every fixture, verdict and exit code, because two implementations of one contract that nothing compares
is parity rot one level down.

### The four harness-and-tracker combinations, and who supplies the snapshot

The board must work across harnesses **and** across trackers, in every combination. The 2026-08-19
owner decision names four cells, and **"harness" in that matrix means the *driver of the loop***, not
the dispatch target inside one basicly engine (owner decision 2026-08-20, reading (b)).

**Why the reading matters, because it is the difference between a contract and a config flag.** Under
the other reading — harness as the dispatch target, where claude and copilot are runner adapters over
one agent-neutral basicly loop — the producer is basicly in all four cells, the harness axis changes
only *values*, the matrix is two cells rather than four, and *"basicly ships one reference producer,
not the only one"* means nothing. Reading (b) is the one under which the matrix is four cells and the
reusable-contract framing has a reason.

**The fact that halves the matrix under *either* reading, measured 2026-08-20:** `TRACKER_MODES` in
`owned_store.py:44` is a **one-member tuple** — `TRACKER_MODES = (MODE_OWNED,)` **[M]** — and
`config.py:854` refuses an unrecognised mode rather than defaulting to one **[M]**. So the engine has
exactly one tracker mode and **there is no external-tracker reader to configure**. Every
external-tracker cell needs a foreign producer, and no flag, table or adapter inside basicly can change
that.

| Loop driver | Tracker | Which component supplies the snapshot |
| --- | --- | --- |
| **claude**, driven by `basicly loop` | the basicly owned ledger | **Unit B**, the reference producer — invoked by `basicly board` for Mode A, or by **unit F** on the supervisor's tick for wall mode. This is the only cell basicly serves end to end. |
| **copilot**, driven by `basicly loop` | the basicly owned ledger | **Unit B / unit F, unchanged.** The producer reads the ledger, not the runner, so the driver is invisible to it. This cell needs no new code — and it is the cell that shows the harness axis is not a producer axis once the tracker is ours. |
| **claude coding agent**, no basicly present | its own external tracker | **A foreign producer**, written by whoever owns that tracker, conforming to the adapter contract above and proved by unit G's kit. Not unit B: there is no external-tracker reader in the engine to point it at. |
| **copilot coding agent**, no basicly present | its own external tracker | **A foreign producer**, same as the row above. This is the cell the whole revision exists for, and the cell in which basicly ships **no** producer at all — only the schema, the conformance script, the page and the how-to. |

**Read the table as two halves, not four cells.** The tracker axis decides *who produces*: ours →
unit B, theirs → a foreign producer. The harness axis decides *nothing about production* and only
changes values inside `lanes[].agent` and `health[].agent`. That is the honest shape of the four cells
once `TRACKER_MODES` is measured, and it is why unit G is first-class: two of four cells are served by
the kit alone.

**Declared limit — the two copilot cells' spend and health are unmeasured, and this is not a
footnote.** Counted 2026-08-20 over the ledger's `[harness-run]` markers: **398 dispatch records —
264 `claude`, 134 `manual`, 0 `copilot`, 0 `codex`** **[M]**. *Positive control: the probe parsed all
398 payloads and found two families, so the zero for copilot is a property of the corpus and not of the
probe.* Consequences:

- **No copilot usage format has ever been exercised here**, so the fidelity of `spend` and `health` for
  either copilot cell is unknown rather than good or bad.
- A family with no usage format falls back to a **transcript estimate**, and the schema has **no field
  that marks a value as an estimate**. A rendered `$13.13` derived from a transcript is
  indistinguishable from a billed `$13.13`, on the one panel this project's justification rests on
  (`## Problem`, *money burns unattended*).
- **[D] So the rule is clause 2 of the adapter contract, applied to itself: a producer that can only
  estimate `spend` or `health` omits the section.** `not emitted by this producer` is honest; an
  unmarked estimate on the spend panel is the overclaim in the money domain that C1 refuses in the
  freshness domain.
- **[D] The named remedy, and it is v1-compatible so it needs no major:** an optional `estimated`
  boolean on `spend` and on `health[]`. Adding a key is permitted within a major (rule above); widening
  a value set is not. Whoever first has a copilot corpus to measure adds it — it is not built
  speculatively here, and it is not needed until a copilot cell exists.

---
---

## Decomposition

Cut to pass this repo's plan gate. Every child carries the five fields
`plan_gate.gate_plan` enforces — `acceptance`, `scope`, `depends_on` (declared, possibly empty),
`budget_tokens`, `integrity` — plus the sixth a **proposed** plan owes, `demonstration` **[M]**.
Titles are unique; the declared graph is acyclic. Integrity levels are `L1|L2|L3`
(`plan_gate.INTEGRITY_LEVELS` **[M]**).

Acceptance criteria are in EARS, per the `decompose-plan` skill
**[S** `.basicly/core/skills/decompose-plan/skill.yaml`**]**. Cut **vertically**: every child below
names a runnable demonstration through the consumer surface, which is what that skill says the sixth
field exists to catch.

Token budgets are stated as a scope read cost + build factor, in the same chars/4 unit
`read_cost._text_tokens` uses. They are forecasts, and the skill's own advice applies: *declare the
scope honestly even though it costs you*.

**Sizing constraint that shaped the cut**: a new Python module may never cross the 4,000-token
module-size cap **[S** `.scripts/check_module_size.py`**]**, so this is small modules rather than two
large ones. **Revised 2026-08-20:** the cut is now **five new modules** — `board_snapshot`,
`board_cli`, `board_serve`, `board_render`, `board_actions` — and the constraint that shaped it is no
longer only the cap. It is **C11**: the import contract is exhaustive, so each module carries a
declared tier and a declared may-import set, and `cli.py`'s measured **453** tokens of room against
three parser groups at a measured **median 426** each is what puts the board grammar in `board_cli.py`
rather than in `cli.py`. Each unit below carries a **Placement** line for that reason. Every new module
also owes `tests/test_<module>.py`, because `check_test_naming.py` binds forward only (C7).

---

### A — `harness-board/v1` snapshot schema and validator — **SHIPPED**

**Closed as `basicly-rn0o.1`.** What landed: `src/basicly/board_schema.py`,
`.basicly/core/schemas/board-snapshot.schema.json`, `tests/test_board_schema.py`, the
`basicly board validate` verb, three fixtures under `tests/fixtures/board/`, and a `board-schema`
`[[verify.checks]]` entry that runs the validator against `minimal-v1.json` on every `fast` and
`full` pass **[M]**. The shipped schema requires exactly `schema`, `generated_at` and `freshness`, as
criterion 1 below asks, and carries two optional sections this design did not propose: `units` and
`graph` **[M]**. The criteria are kept as written so the next unit has the contract they were
accepted under.

**Integrity** L3 — it defines a surface intended to be implemented by foreign producers, which is
the L3 consumer category (*"CLI, `basicly.toml` schema, catalog source schemas, generated-file
contract, ledger format"* **[S** `factory-loop.md` §4**]**).
**Scope** `src/basicly/board_schema.py`, `.basicly/core/schemas/board-snapshot.schema.json`,
`tests/test_board_schema.py`
**depends_on** `[]`
**budget_tokens** 60,000

#### Acceptance criteria (EARS)

1. *Ubiquitous* — The schema SHALL require exactly three top-level keys: `schema`, `generated_at`,
   `freshness`; all other sections SHALL be optional.
2. *Ubiquitous* — `freshness` SHALL require `source`, `cadence_s` and `stale_after_s`.
3. *Event-driven* — WHEN `validate` is given a snapshot whose `schema` major differs from the
   consumer's, it SHALL exit 2 and name both versions.
4. *Event-driven* — WHEN `validate` is given a snapshot carrying keys the schema does not define, it
   SHALL exit 0 and report their count, never dropping them silently (the tolerance direction
   `work-tracker.md` §4.5 already fixes).
5. *State-driven* — WHILE a section is absent, validation SHALL report it as `absent` rather than
   as an error.
6. *Ubiquitous* — No schema property SHALL admit a bead `description`, `acceptance_criteria`, or a
   raw comment body (the 132.5× field-selection rule, C6).

**Demonstration** `uv run basicly board validate tests/fixtures/board/minimal-v1.json` prints
`harness-board/v1, ok` and exits 0; `... /wrong-major.json` exits 2.

---

### B — The file-only snapshot producer — **basicly's reference producer, not *the* producer**

**Renamed in substance by the 2026-08-19 owner decision.** This unit was written as *the* producer and
is now **one** implementation of the contract: the one basicly ships, for the two cells where the
tracker is the owned ledger. The two external-tracker cells are served by a foreign producer and unit G
(see `## The contract` → *The four harness-and-tracker combinations*). Nothing about its criteria
loosens; what changes is that it is no longer allowed to be the definition of correct.

**Integrity** L2. **Scope** `src/basicly/board_snapshot.py`, `tests/test_board_snapshot.py`,
`tests/test_board_snapshot_lock.py` (AC 12's aspect split — the producer's own sections and the lock
fact crossing the boundary do not fit one module under the 4,000-token cap **[M]**),
`.basicly/core/schemas/board-snapshot.schema.json` (the C6 stale-number repair only)
**depends_on** `["A — harness-board/v1 snapshot schema and validator"]`
**budget_tokens** 140,000
**Placement** a new `.importlinter` tier line immediately below `loop | release`; may import `policy`,
`run_record`, `tracker`/`owned_store`, `redact`, `board_schema`; **may not import `supervise`** (C11).

#### Acceptance criteria (EARS)

1. *Ubiquitous* — The producer SHALL build a complete snapshot by reading only files:
   `.basicly/ledger/events-0001.jsonl` (resolved through the kit's `ledger_dir`, so a worktree
   redirect is honoured), `.basicly/usage/run-records.json` and `.basicly/usage/verify-run.json`. It
   SHALL spawn **zero** subprocesses, and it SHALL fold the ledger exactly once per snapshot (C5:
   `observe()` folds it 93 times).
2. *Ubiquitous* — The live-lock facts — holder id, heartbeat age, staleness, and the session root —
   SHALL be **supplied by the caller as an argument**, never read by this module. The producer SHALL
   NOT import `supervise`. *(C11: `supervise` imports this module in unit F, so a
   `supervise.read_holder` call here is the cycle `supervise → board_snapshot → supervise`. Every
   caller — `board_cli`, `board_serve`, and the supervisor itself — sits above `supervise` or **is**
   it, so each already holds the fact. Same rule as OQ-D: the layer above supplies the fact the layer
   below cannot honestly derive.)*
3. *State-driven* — WHILE no live-lock facts are supplied, the `session` section SHALL be omitted
   rather than emitted with nulls or a guessed root (OQ-D).
4. *Ubiquitous* — Building a snapshot on this repo's committed corpus SHALL complete in under 500 ms
   (re-measured 2026-08-20 at **103.8 ms**, median of 21 **[M]** — see C5; the cap is **4.8×** that,
   so it still fails on a regression rather than on noise, but it is a band and no longer a loose
   bound. The 19.1 ms this criterion was written against excluded the log read, `basicly-ef953m`).
5. *Ubiquitous* — An ask SHALL be reported pending only when no `[harness-wait]` marker sharing its
   `id=` carries `answered`. The test SHALL pin **1** pending against a naive **140**, with the
   answered-marker control at **203** distinct answered wait ids **[M]**, so a parser that silently
   matches nothing cannot pass it — and it SHALL pin them against a **frozen fixture corpus committed
   under `tests/fixtures/board/`, not against the live ledger.** *(The live ledger is git-tracked and
   grows on most landings: 980 records / 2,474 marker rows became 983 / 2,479 inside a single
   session, measured on one checkout **[M]**. A test pinning an exact count against it is red on the next landing, which is a flaky
   gate rather than a regression detector. The same applies to AC 4's timing cap, which is why that cap
   is 4.8× the measurement rather than a tight band.)*
6. *Ubiquitous* — Every string reaching the snapshot SHALL pass `redact.redact_secrets` and
   `redact.redact_machine_paths`; no absolute path or username SHALL appear in the output.
7. *State-driven* — WHILE `.basicly/usage/run-records.json` is absent, the `spend` and `health`
   sections SHALL be omitted and the rest SHALL build.
8. *Unwanted* — IF a `[harness-*]` marker is malformed, THEN the producer SHALL skip it and continue,
   matching the existing best-effort parser contract (`policy._parse_wait_event` returns `None`
   rather than raising **[M]**).
9. *Ubiquitous* — The parser's marker-family set SHALL equal `.scripts/check_marker_families.FROZEN` —
   the authoritative roster, reported by its own gate as **11 declared, 1 retired, 12 frozen** **[M]** —
   and NOT the 11 families the engine currently declares (OQ-C). The producer SHALL parse all 12,
   including the retired `[harness-overrun]`, and SHALL branch on live-versus-retired nowhere: the
   distinction governs writing, and this producer writes no marker.
   **The binding SHALL be a test, not an import.** `.scripts/` is not an importable package — no
   `__init__.py` **[M]** — and its sibling gates import *into* `basicly`, so a runtime import here
   would invert the gates → engine direction and put a gate script on the engine's import path. The
   test SHALL load the gate by file path, as `tests/test_check_marker_families.py` already does, and
   assert set equality, so a thirteenth family cannot enter the log without this producer's own gate
   naming it (C11).
   *(Why neither shortcut works: a list derived from the live constants drops `[harness-overrun]` and
   renders its **12** rows as nothing; a list taken from a raw text grep picks up `[harness-side]`,
   `[harness-estimate]` and `[harness-conflict]`, none of which is a marker — all three occur only
   inside record descriptions **[M]**.)*
10. *Ubiquitous* — A section whose values would be an **estimate** SHALL be omitted rather than emitted,
   because the schema has no field marking a value as estimated. *(Adapter contract clause 2, and the
   declared limit for the copilot cells: 0 of 398 dispatch records are copilot **[M]**, so a
   transcript-estimated `spend` would render indistinguishably from a billed one.)*
11. *Ubiquitous* — Any new or edited **key** or **permitted value** in
   `.basicly/core/schemas/board-snapshot.schema.json` SHALL avoid repeating a name declared in
   `src/basicly`, and this unit SHALL run `wired-or-deleted` before committing (C7 hazard, corrected
   2026-08-20 — a `description` is not read by that scan, so prose is not the hazard **[M]**). The
   stale field-selection figure recorded in C6 was repaired by `basicly-rn0o.2` at both of its sites,
   and the schema's own stale first-line warning by `basicly-desr1v`; both are closed and neither is
   this unit's to repeat.
12. *Ubiquitous* — `supervise.read_holder` SHALL be the **only** parser of
   `.basicly/usage/supervisor.lock` in the tree, and a test SHALL assert that the `session.holder`
   heartbeat age this producer emits **equals** that reader's `age_s` on one fixture lock, with
   `stale` derived from `supervise.STALE_AFTER_S`. The fixture lock SHALL carry a payload field the
   reader ignores, so an age taken from the payload rather than from `st_mtime` fails the assertion
   rather than passing by coincidence. `read_holder`'s recorded invariant — *"a corrupt payload still
   reports the heartbeat age (staleness is mtime-only by design), with the identity fields None"* —
   SHALL be asserted **through the producer**, so a crashed supervisor renders an age and no holder id.
   *(AC 2 makes the import direction one-way and `lint-imports` proves it, but nothing structural
   proves the two ends still agree once the fact is copied across the boundary by hand. A second age
   disagrees about whether a supervisor is alive silently, and only after a crash — the one moment a
   wall display is worth reading: basicly-rn0o.14.)*

**Demonstration** `uv run basicly board --out - | uv run basicly board validate -` exits 0 and
prints the section inventory.

---

### C — `basicly board`: the on-demand artifact (Mode A)

**Integrity** L3 — adds a CLI command, a frozen surface (C9).
**Scope** `src/basicly/board_render.py`, `src/basicly/board_page.html.j2`,
`src/basicly/board_cli.py` (**created here**, and it is where the `board` grammar and dispatch move to),
`src/basicly/cli.py` (the board block **out**, two registration lines in — C11),
`.basicly/core/kit/board/board.html` (the distributed page — the kit directory is unit G's, this file
is C's, and C9's distribution table names both),
`.importlinter` (three new tier lines), `docs/architecture/architecture.md` (the §22 CLI row, and §34's
tier counts), `tests/test_board_render.py`, `tests/test_board_cli.py`
**depends_on** `["B — The file-only snapshot producer", "G — Adoption seam: the foreign-producer conformance kit"]`
**budget_tokens** 210,000
**Placement** `board_render` on a new tier line immediately above `board_schema`, as
`board_actions | board_render`; `board_cli` on a new tier line immediately below `cli` (C11).

**Two scope changes from the 2026-08-14 cut, both forced by a gate rather than by taste.**
`tests/test_cli_board.py` becomes `tests/test_board_cli.py` because `check_test_naming.py` derives
coverage from the module name and the old spelling covers no source unit (C7). And
`docs/architecture/architecture.md` enters scope because `docs_claims` asserts every subcommand appears
in a §22 table row (C7) — which serialises this unit against every architecture lane, not only against
D and E.

#### Acceptance criteria (EARS)

1. *Event-driven* — WHEN `basicly board --out board.html` runs, it SHALL write one self-contained
   HTML file with the snapshot inlined, referencing no external URL, no CDN and no local asset.
2. *Ubiquitous* — The page SHALL render the eight regions of the wall layout and SHALL display
   `generated_at` and a computed age in every one of them.
3. *Ubiquitous* — The page SHALL use only the CSS custom properties already defined in
   `site/index.html` `:root`, and SHALL honour `prefers-reduced-motion` as that file does.
4. *Ubiquitous* — The rendered page SHALL contain no `<script src>` and no `<link href>` to any
   origin.
5. *Event-driven* — WHEN a section is absent from the snapshot, its region SHALL render
   `not emitted by this producer` rather than an empty box or a zero.
6. *Ubiquitous* — `basicly board --help` SHALL carry the C1 freshness wording verbatim and SHALL NOT
   contain the string `real-time`. A test SHALL assert that absence.
7. *State-driven* — WHILE either the numerator or the denominator of a proportional bar is absent or
   unmeasured, the page SHALL render the raw number and SHALL NOT render a bar. *(Adopted from
   `sssf/…/SessionTrace.vue:127-134`; the reason it matters here is that this repo has already shipped
   a wrong `context_window` constant, and a bar drawn against a wrong ceiling is worse than no bar.)*
8. *Ubiquitous* — Every state SHALL be encoded on at least two non-colour channels — a glyph and a
   border style — in addition to colour.
9. *Ubiquitous* — The renderer's section inventory SHALL be derived from the shipped schema's own
   property list, never written out a second time. A test SHALL add a property to a copy of the schema
   and assert the rendered region count follows (S9). The shipped schema declares 15 top-level
   properties, 3 required and **12** optional **[M]**; "eight regions" in this document is a layout
   count and never a section count.
10. *Ubiquitous* — The renderer's only input SHALL be a parsed snapshot document. It SHALL NOT read
   `.basicly/ledger/`, `.basicly/usage/`, or import a tracker, store or writer module; the
   `import-linter` `forbidden` contract of C12 SHALL name it. A foreign fixture from unit G — three
   required keys, no basicly state — SHALL render a complete page naming all 12 optional sections
   absent, with no error. *(These two criteria were unit G's AC 1 and AC 3 in the 2026-08-14 cut. They
   are assertions about the renderer, so they move to the renderer, which is what lets G depend on A
   alone and become first-class.)*
11. *Ubiquitous* — The `board` command group's grammar and dispatch SHALL live in
   `src/basicly/board_cli.py`, following `tracker_query.add_parsers` **[M]**, and `cli.py` SHALL retain
   only the registration call and the dispatch-table entry. `cli.py` SHALL be **smaller** after this
   unit than before it: 53,883 tokens today, ~53,585 after, against a frozen 54,336 **[M]**. A test
   SHALL assert `basicly board --help` and `basicly board validate` behave identically across the move,
   because a grammar extraction that changes the surface is not an extraction.
12. *Ubiquitous* — The page SHALL also be published to the kit as
   `.basicly/core/kit/board/board.html`, self-contained and openable by a consumer that fetches its
   snapshot beside it — the second of the two files C9's distribution table freezes. A test SHALL
   assert it references no external origin and is byte-identical to the page the renderer emits from
   the same template.
13. *Ubiquitous* — `.importlinter`'s new tier lines SHALL be added in the same commit as the modules
   they place — `exhaustive = True` fails the build otherwise **[M]** — and `architecture.md` §34's
   tier and per-band module counts SHALL be updated by hand, because **no gate binds them to
   `.importlinter`** (C11 **[M]**). **No architecture section SHALL be renumbered.**

**Demonstration** `uv run basicly board --out /tmp/b.html && python -c "import pathlib,sys;
t=pathlib.Path('/tmp/b.html').read_text(); sys.exit(0 if 'harness-board/v1' in t and 'src=' not in t
else 1)"` exits 0; then open it in a browser and read it.

---

### D — `basicly board serve`: wall mode, read-only (Mode B)

**Integrity** L3. **Scope** `src/basicly/board_serve.py`,
`src/basicly/board_cli.py` (the `board serve` parser group — **not `cli.py`**, C11),
`.importlinter` (one new tier line), `docs/architecture/architecture.md` (the §22 CLI row, §34 counts),
`tests/test_board_serve.py`
**depends_on** `["C — basicly board: the on-demand artifact (Mode A)"]`
**budget_tokens** 170,000
**Placement** `board_serve` on a new tier line immediately below `board_cli` and above
`supervise | usage_report` — it is the layer that may read the supervisor lock and hand the facts down
to the producer (C11).

**This unit adds zero tokens to `cli.py`**, because unit C moved the grammar to `board_cli.py`. It
still declares `docs/architecture/architecture.md`, because `docs_claims` requires the new
`board serve` verb to appear in a §22 row (C7) — that is the contention that serialises D against C and
E and against every architecture lane.

#### Acceptance criteria (EARS)

1. *Event-driven* — WHEN `board serve` starts, it SHALL bind `127.0.0.1` only and SHALL print the
   bound URL. A test SHALL assert the bind address is never `0.0.0.0` or a hostname.
2. *State-driven* — WHILE a supervisor lock exists with a heartbeat younger than
   `supervise.STALE_AFTER_S` (60.0 **[M]**), the server SHALL serve the supervisor-written snapshot
   and SHALL NOT compute one.
3. *State-driven* — WHILE no supervisor lock is fresh, the server SHALL self-refresh at `--refresh`
   seconds, default 15 to match `supervise.HEARTBEAT_INTERVAL_S` (15.0 **[M]**), with never more than
   one refresh in flight.
4. *Event-driven* — WHEN the snapshot's age exceeds `stale_after_s`, the page SHALL display `STALE`
   and the age, and SHALL NOT hide or freeze the last known values.
5. *Ubiquitous* — The process SHALL acquire no lock, write to no path under `.basicly/ledger/`, and
   exit cleanly on SIGINT reporting refresh count and failures.
6. *Ubiquitous* — In this unit the server SHALL expose GET only; any POST SHALL return 405.

**Demonstration** `uv run basicly board serve --port 0 &` then
`curl -s http://127.0.0.1:$PORT/snapshot.json | uv run basicly board validate -` exits 0, and
`curl -s -X POST http://127.0.0.1:$PORT/action` returns 405.

---

### E — The action surface, behind existing engine commands

**Integrity** L3 — it touches the anti-autopilot boundary (C8).
**Scope** `src/basicly/board_actions.py`, `src/basicly/board_serve.py`,
`src/basicly/board_cli.py` (the `--no-actions` flag — **not `cli.py`**, C11),
`.importlinter` (`board_actions` joins `board_render`'s tier line, plus the C12 `forbidden` contract),
`docs/architecture/architecture.md` (the §22 flag row, §34 counts), `tests/test_board_actions.py`
**depends_on** `["D — basicly board serve: wall mode, read-only (Mode B)"]`
**budget_tokens** 200,000
**Placement** `board_actions` as `board_render`'s **sibling** on the tier line above `board_schema`.
Siblings may not import each other and neither needs the other; the placement is what makes "the action
surface cannot reach the renderer or the engine" structural rather than reviewed (C11, C12).

#### Acceptance criteria (EARS)

1. *Ubiquitous* — Every action SHALL be an `argv` list whose head is the `basicly` executable, taken
   from a closed table of exactly three entries: `loop answer`, `policy checkpoint --approve`,
   `loop kill`. A test SHALL assert the table's length and contents.
2. *Ubiquitous* — `board_actions` SHALL import no engine module that writes; an import-linter contract
   SHALL enforce it (`import-linter` is already a dev dependency **[M]** `pyproject.toml:29`). It SHALL
   be a `type = forbidden` contract, **not** a tier placement: `owned_store`, `owned_write`, `mirror`
   and `tracker_argv` sit near the *bottom* of the layer stack **[M]**, so every module above
   `board_schema` may reach them by layering alone (C12). A test SHALL assert the contract fails on a
   deliberately added writer import, because a contract nothing has ever seen fail is a contract nobody
   knows is wired.
3. *Unwanted* — IF any code path reads `.basicly/usage/checkpoint-confirms.json`, THEN a test SHALL
   fail. The board SHALL never display a confirm code (C8).
4. *Event-driven* — WHEN an approve or kill action is submitted without a confirm code typed by the
   operator, the server SHALL refuse it and SHALL NOT invoke the CLI.
5. *Event-driven* — WHEN a POST arrives whose `Origin` is not the served origin or whose per-process
   token does not match, the server SHALL return 403 and invoke nothing.
6. *Ubiquitous* — Every invocation SHALL be echoed to the server's stdout before it runs and its exit
   code and stdout echoed after, so the terminal is a complete audit log of what the wall did.
7. *Feature-gated* — WHERE `--no-actions` is given, the action routes SHALL not be registered at all.

**Demonstration** `uv run basicly board serve --port 0` then submit an approve with an empty code and
observe 400 with no subprocess spawned (asserted via a `subprocess.run` spy in the test), then submit
with a code obtained from `basicly policy checkpoint <issue> ship --approve` in a terminal and observe
the real approval land.

---

### F — Supervisor-side snapshot emission

**Integrity** L2. **Scope** `src/basicly/supervise.py` (the tick's report hook only),
`tests/test_supervise_board.py`
**depends_on** `["B — The file-only snapshot producer"]`
**budget_tokens** 90,000
**Placement** no new module. `supervise` sits above `board_snapshot` in the C11 stack, so
`supervise → board_snapshot` is a downward import and legal. **The direction is why unit B may not
import `supervise`** — this unit is the reason that constraint exists, and it is the caller that
supplies the live-lock facts unit B's AC 1a requires, since it already holds them.

#### Acceptance criteria (EARS)

1. *State-driven* — WHILE a supervisor pass is running, it SHALL write a snapshot on each heartbeat
   tick to `.basicly/usage/board/snapshot.json`.
2. *Ubiquitous* — The write SHALL be atomic (temp + rename), so a reader never observes a partial file.
3. *Ubiquitous* — Snapshot emission SHALL add no more than 50 ms to a tick, measured.
4. *Unwanted* — IF snapshot emission raises, THEN the supervisor SHALL log one line and continue; a
   board failure SHALL never fail a pass or a landing.
5. *Ubiquitous* — The supervisor's four coverage lines (`band:`, `spend:`, `health:`, `drift:`,
   printed by `supervise._report_coverage` **[M]**) SHALL be carried into the snapshot as structured
   fields, not as the free-text strings they are printed as.

**Demonstration** run `uv run basicly loop supervise <root>` in a scratch fixture repo and assert
`.basicly/usage/board/snapshot.json` mtime advances at least twice within 40 s while
`board validate` stays green on every version.

---

### G — Adoption seam: the conformance kit — **FIRST-CLASS, second in the build order**

**Promoted from last to second by the 2026-08-19 owner decision, and it is not a courtesy promotion.**
When unit B was *the* producer, G documented an adoption path nobody was on. Now the schema is the only
interface, and G is what makes that interface **real**: it is the only unit that serves two of the four
harness-and-tracker cells, and it is the only unit that makes S4 and S7 true. A contract nobody outside
this repository can read or check is not a contract; it is an internal wire format with an aspiration
attached.

**It now depends on A alone**, so it can start the moment the schema exists and it runs in parallel with
B. That is possible because its two rendering criteria moved to unit C, where the renderer is — see C's
AC 10. What is left here is the contract's *distribution* and its *proof*, which need no page and no
producer.

**Integrity** L3 — it defines a distributed surface intended to be implemented and run by parties that
are not basicly, which is the L3 consumer category, and it adds a row to the v1.0.0 freeze audit (C9).
**Scope** `.basicly/core/kit/board/conformance.py`, `.basicly/core/kit/board/README.md`,
`.basicly/core/kit/README.md` (one table row), `docs/how-to/adopt-the-board.md`,
`tests/fixtures/board/foreign/**`, `tests/test_board_foreign.py`
**depends_on** `["A — harness-board/v1 snapshot schema and validator"]`
**budget_tokens** 130,000
**Placement** no `src/basicly` module, so no tier line and no `check_test_naming` obligation — that gate
scopes to `src/basicly` **[M]**. The kit's own contract binds instead (C13), and `kit-boundary` already
scans the whole `.basicly/core/kit` tree, so the new directory is gated on arrival with no gate edit
**[M]**.

#### Acceptance criteria (EARS)

1. *Ubiquitous* — `conformance.py` SHALL be a **single file importing only the Python standard
   library**. `kit-boundary` SHALL pass on it, and a test SHALL assert that in the fixture directory
   `python3 -c "import basicly"` fails while `python3 conformance.py <snapshot>` exits 0 — the two
   halves of S4, asserted together, because either alone proves nothing.
2. *Ubiquitous — THE POSITIVE CONTROL, and it is a criterion because a check written only against
   the failing side passes when it always fails.* The suite SHALL assert **exit 0 on a conforming
   document** as well as non-zero on a broken one. *This is measured and it holds: a hand-written
   126-byte document — `schema`, `generated_at`, and `freshness` carrying `source`, `cadence_s` and
   `stale_after_s` — prints `harness-board/v1, ok` and exits 0 **[M]** 2026-08-20.*

   **The trap the control exists to catch, observed rather than imagined.** An independent verifier on
   2026-08-20 reported being unable to construct a conforming document by hand and recorded exit 0 as
   unproven. The cause is that **the minimum is not "three keys"**: `freshness` is an object with three
   *required members* **[M]**, so the real floor is six values, and a document with `"freshness": {}`
   exits 1 naming all three **[M]**. **[D] So the how-to states the floor as the JSON block, never as
   a key count**, and AC 5's byte-identical assertion between the how-to's example and the fixture is
   what keeps that promise honest.
3. *Ubiquitous* — `conformance.py` SHALL agree with `board_schema` on **every** fixture under
   `tests/fixtures/board/`, on both the verdict and the exit code. A test SHALL assert the agreement
   fixture by fixture rather than in aggregate. *(Two implementations of one contract that nothing
   compares is parity rot one level below the producer parity `basicly-rn0o.13` records. `jsonschema` is
   third party and the kit contract forbids it, so a second implementation is unavoidable — binding it
   is not.)*
4. *Unwanted* — IF `conformance.py` cannot reach a verdict — an unreadable file, unparseable JSON, an
   absent argument — THEN it SHALL exit **non-zero** and say what it could not answer. It SHALL NOT
   exit 0. *(The fail-open direction the shipped `not-installed` outcome already takes correctly, and
   the one thing the S4 remedy must not reverse.)*
5. *Ubiquitous* — The minimum conformant snapshot SHALL be under 400 bytes and SHALL be reproduced
   verbatim in the how-to, and a test SHALL assert the how-to's copy and the fixture are byte-identical
   after whitespace normalisation, so the published example cannot drift from the checked one.
6. *Ubiquitous* — The how-to SHALL state the **six adapter-contract clauses** verbatim from
   `## The contract`, and SHALL carry a *"Where it can still fail"* section naming real defects —
   including the declared limit that a producer with no usage format must omit `spend` and `health`
   rather than estimate them.
7. *Ubiquitous* — `conformance.py` SHALL satisfy the kit's portability contract: no syntax newer than
   Python 3.9 and **one exception class per handler**, which inverts the form `python-guidelines`
   prescribes for `src/` **[M]** `.basicly/core/kit/README.md`. `.basicly/core/kit/board/README.md`
   SHALL state which of the kit's two failure modes this kit takes — *fail closed on a question* — and
   the kit table in `.basicly/core/kit/README.md` SHALL gain its row.
8. *Ubiquitous* — The how-to SHALL live under `docs/how-to/`, which D33 permits
   **[S** `factory-loop.md` §2, D33 — and see the note under this document's title**]**; no requirement
   or plan document SHALL be created.
9. *Ubiquitous* — `kit-deployment` SHALL NOT gain an entry. The board kit imposes nothing on a host
   repository — it is copied, not installed — and the kit README's rule is *"a kit with no such
   requirements needs no entry"* **[M]**.

**Demonstration** in a scratch directory holding **only** `conformance.py` and a hand-written
`snapshot.json`, with no `.basicly/`, no virtualenv and no basicly on `PYTHONPATH`:
`python3 conformance.py snapshot.json` prints `harness-board/v1, ok` and the absent-section inventory
and exits 0; `python3 conformance.py /dev/null` exits non-zero naming what it could not answer; and
`python3 -c "import basicly"` raises `ModuleNotFoundError`. That is transcript Mode D, run.

---

### H — OPTIONAL, separately decided: `viewed_at` and OQ-15

**Integrity** L3 — it writes to the owned ledger format, a frozen surface (C9).
**Scope** `src/basicly/policy.py` (`WaitEvent` + marker writer), `src/basicly/board_actions.py`,
`tests/test_policy_wait_view.py`
**depends_on** `["E — The action surface, behind existing engine commands"]`
**budget_tokens** 120,000
**Ship only if the owner decides OQ-15 is worth a frozen-surface change. The board is complete
without it.**

#### Acceptance criteria (EARS)

1. *Event-driven* — WHEN the operator's first `pointerdown` or `keydown` occurs on the board WHILE
   `document.visibilityState === "visible"` and at least one ask is pending, the board SHALL record
   `viewed_at` once per wait id.
2. *Unwanted* — IF no human input event occurs, THEN no `viewed_at` SHALL be written. A rendered
   checkpoint on an untouched wall SHALL produce **no** field (C3: a render is not a view).
3. *Ubiquitous* — `viewed_at` SHALL be an optional field on the existing `[harness-wait]` JSON payload;
   a reader that does not know it SHALL be unaffected. A test SHALL parse a `viewed_at`-bearing marker
   with the *current* parser and assert an unchanged `WaitEvent`.
4. *Feature-gated* — WHERE the feature flag is unset (the default), no code path SHALL write the field.
5. *Ubiquitous* — After a recording session, `basicly usage report` (or an equivalent read) SHALL be
   able to split `requested_at → viewed_at` from `viewed_at → answered_at`, which is precisely what
   OQ-15 asks for.

**Demonstration** with the flag set, approve a checkpoint through the board, then
`uv run basicly tracker show <issue> --json | jq -r '.comments[]' | grep 'harness-wait' | tail -1`
shows a payload carrying all three timestamps; with the flag unset, the same run shows two.

---

### Graph and ordering

**Rewritten 2026-08-19/20. G moves from a leaf hanging off C to the second unit in the order**, because
it is now what makes the interface real rather than a later courtesy, and its two rendering criteria
moved to C (C's AC 10) so that it depends on A alone.

```text
A (SHIPPED) ──┬──> G ──┐
              │        ├──> C ──> D ──> E ──> H (optional)
              └──> B ──┤
                       └──> F
```

Acyclic. **A publishes the schema; G publishes the schema's distribution and its proof; B is one
producer of it; C, D, E and F are the consumers and the second producer.** Stated as a table, because
"which unit publishes and which consume" is an acceptance criterion of this revision:

| Unit | Role against the contract | Reads |
| --- | --- | --- |
| **A** — SHIPPED | **publishes the contract** — the schema file and the ruling | a snapshot document |
| **G** | **publishes the contract's distribution** — the standalone check, the how-to, the foreign corpus | a snapshot document, with no basicly present |
| **B** | **a producer** — basicly's reference one, for the two owned-ledger cells | the ledger and the usage files; the only unit that may |
| **C** | **consumer** — the page | the snapshot **only** (C12) |
| **D** | **consumer** — the server | the snapshot only; plus the supervisor lock, which it hands *down* to B |
| **E** | **consumer** — the action surface | the snapshot only; writes nothing, ever (C8) |
| **F** | **a second producer path** — the same producer B builds, invoked on the supervisor's tick | the ledger, through B |
| **H** | optional, separately decided | — |

**Declared scope overlaps, because overlap is what decides serialisation and the plan gate wants it
declared rather than absent.** The 2026-08-14 graph declared two; there are **five**, and two of them
are new findings from the architect review:

| Overlapping path | Units | Consequence |
| --- | --- | --- |
| `src/basicly/board_cli.py` | C, D, E | C creates it, D and E extend it. Replaces the 2026-08-14 overlap on `src/basicly/cli.py`, and it is why `cli.py`'s 453 tokens of room stops being the binding constraint (C11). |
| **`docs/architecture/architecture.md`** | C, D, E | **NEW.** `docs_claims` requires every subcommand in a §22 table row **[M]**, so each CLI unit edits the architecture document — which serialises them against **every architecture lane in the tree**, not only against each other. This is the contention the 2026-08-14 graph did not see. |
| **`.importlinter`** | B, C, D, E | **NEW.** `exhaustive = True` means each new module needs its tier line in the same commit **[M]**. Four units touch one file with no merge-friendly structure. |
| `src/basicly/board_serve.py` | D, E | unchanged from 2026-08-14. |
| `tests/fixtures/board/foreign/**` | G, C | G creates the corpus, C's AC 10 asserts against it. G→C in the graph, so it is ordered, not concurrent. |

C→D and D→E stay sequential for that reason, and **B and G are the only pair that may run
concurrently** — their scopes are disjoint and both depend only on A.

**Total forecast** 1,180,000 tokens across A–G, of which A's 60,000 is spent — **1,120,000
remaining**; 1,240,000 including H. That is 130,000 above the 2026-08-14 forecast: +20,000 on C for the
`board_cli` extraction and the kit page, +50,000 on G for the conformance script and the parity test.
Reported, not defended: per the `decompose-plan` skill, a large forecast is **reported, never refused**,
and the author decides. For calibration, this repository's measured lane mean is far larger than these
children, so the cut is on the conservative side.

**The recommended-first-release paragraph that stood here is superseded and is not repeated.** It
recommended shipping A + B + C + G, running four weeks, and only then deciding D/E/F/H. The owner
decided D and E on 2026-08-18 without the trial. `## SUPERSEDED — the four-unit phased scope` carries
the decision and OQ-A carries the accepted risk; neither is deleted, because the question of what D and
E were justified on has to have an answer.

**What survives it, and it is worth keeping.** A + B + C + G is still a complete, shippable,
zero-process product that satisfies `work-tracker.md` §4.3 requirement 10 and §4.5 exactly as recorded
and adds no process anywhere. So a later decision to stop after G loses nothing and needs no redesign —
and after this revision that cut is **stronger** than it was, because G no longer depends on C: the
contract, its distribution and its proof all land before the first page is rendered.

---
---

## Library and licence findings

**Dating, because this section was not re-measured on 2026-08-19.** Every claim below about the two
reference repositories — `super-simple-software-factory` and `fusion-harness` — was measured
**2026-08-14** against checkouts that are not present in this tree, and re-verifying them was out of
this move's scope. Treat each `file:line` in the "Reference repos" subsections as *sourced on
2026-08-14*, not as current. The claims about **this** repository's own stack are re-measured
2026-08-19 and marked **[M]** as usual.

## The stack, each with its licence and a reason

Licences read before the recommendation was written, per `.claude/rules/external-review.md`.

| Component | Licence | Already present? | Reason |
| --- | --- | --- | --- |
| Python 3.14 stdlib — `http.server`, `socketserver`, `json`, `pathlib`, `threading` | PSF License Agreement | bundled | The whole serve mode. No install, no pin, no upgrade surface — which is exactly what the `no external DB/daemon` refusal is protecting (C4). |
| `jinja2` | BSD-3-Clause | **yes** — `pyproject.toml:11`, used at `src/basicly/renderers/common.py` | Renders the page template. Reuse over reinvent: the repo already templates with it. |
| `jsonschema` | MIT | **yes** — `pyproject.toml:12` | `board validate`. The schema is the contract; validating it with a hand-rolled checker would be the reinvention this repo's Core Rules forbid. |
| `rich` | MIT | **yes** — `pyproject.toml:14` | The CLI's own output already routes through `src/basicly/ui.py`. The board's terminal lines use it; nothing new. |
| **Front end** | — | — | **Nothing.** One HTML file, inline `<style>`, inline `<script>`, vanilla ES2020. |
| Fonts | — | — | **System stacks only**, copied from `site/index.html`'s `--mono` and `--sans`. No webfont, no `@fontsource` package, no network fetch. |
| Icons | — | — | **Unicode glyphs only** (`● ◐ ○ ✗ ▲ ◆`). No icon library, and it doubles as the redundant non-colour channel (see below). |

**Net new dependency on the engine's critical path: zero.** The diff to `pyproject.toml` is empty and
the diff to `package.json` is empty.

### Libraries I considered and rejected, with the reason

- **Vue / React / Svelte + Vite** — this is what `super-simple-software-factory` uses (Vue 3.5 + Vite 7
  - Bun) **[S]**. Rejected: it puts a bundler in the render path, and therefore an
  `npm install && npm run build` into every committer's loop and all three OS legs of
  `quality-gates.yml`. `package.json` currently carries exactly **one** devDependency
  **[M]**; that number is worth defending.
- **d3 / Chart.js / ECharts** — rejected. The board has one bar shape and one sparkline; both are
  ~20 lines of CSS. A charting library for two marks is the speculative abstraction the Core Rules
  refuse.
- **htmx / Alpine.js** — rejected. Both are tiny and MIT, but the page has one interaction pattern
  (fetch JSON, re-render) and vendoring anything at all creates an attribution obligation in every
  downstream repo `basicly install` touches.
- **SQLite as the transport** (sssf's design) — rejected *for us*, and the reason is specific: our
  operating memory records that the tracker **corrupts its WAL under our own five-lane fan-out**. A
  second WAL file polled by a browser while a supervisor compacts is a failure mode we have already
  paid for. A rename-atomic JSON snapshot has no such mode.

## Reference repos: licence, and what I propose we take

**Both are MIT.** `super-simple-software-factory/LICENSE:1` and `fusion-harness/LICENSE:1`, both the
verbatim MIT text, both `Copyright (c) 2026 IndyDevDan` (`LICENSE:3` in each) **[S**, verified with a
positive control: `fd -H -i -t f 'licen[cs]e|notice|copying|copyright'` returned exactly those two
files, while the control `fd -H -i -t f 'readme'` returned both READMEs**]**. No `NOTICE`, no
`COPYING`, no per-directory licences, no SPDX headers in any source file. The only manifest in either
tree, `sssf/.claude/skills/sssf/apps/visualizer/package.json`, has **no `license` field** and is
`"private": true`.

So expression is legally takeable with attribution. **[D] I recommend we take the ideas and
re-author the expression**, for three non-legal reasons: the volume is small (a status chip is ~40
lines), the palette is *their* brand identity, and a vendored MIT fragment inside our projected
catalog would need attribution plumbing in every repo `basicly install` touches. The licence-plumbing
cost exceeds the 200 lines it saves.

**Two things MIT does not cover and we must not take at all:**

- `sssf/.claude/skills/sssf/apps/visualizer/public/models/{claude,gemini,kimi,openai,zai}.png` — these
  are the actual **Anthropic burst mark** and **Google Gemini spark** and their peers **[S**, opened
  and viewed**]**. MIT conveys copyright, not trademark. If we ever want model icons, they come from
  each vendor's own brand-assets page under that vendor's terms.
- `fh/images/hero.png` — a stock/AI photo composite carrying the Pi, OpenAI and Anthropic logos, with
  unknown photograph provenance.

Also out of bounds on judgement rather than law: `sssf/public/logo.svg` and the gradient wordmark
(their brand), and `.claude/skills/sssf/SKILL.md` (a named competing product's skill text — copying it
into our catalog would be legal and wrong).

### Ideas worth taking — free regardless of licence, since a licence restricts expression, not facts

Each of these I am folding into the design above, with the `file:line` that is the evidence rather
than the README that is the claim:

1. **"A bar against an unknown ceiling is decoration, not data."**
   `sssf/…/SessionTrace.vue:127-134` renders its context-occupancy bar **only when both numerator and
   denominator are measured**, and shows one decimal below 1% so a real small value never reads as
   empty (`:136-139`). **[D] Adopted as a hard rule for the board**, and it matters more for us than
   for them: this repo's own telemetry refuted a declared `context_window` of 200000 against a
   recorded occupancy of 223221, and the ceiling fired at 1/5 its intended point. A bar drawn against
   a wrong constant is worse than no bar. Added to unit **C** as an acceptance criterion.
2. **Poll; do not build a socket.** `sssf/server/index.ts:6-7` states in code — not in the README —
   *"There is no ingest endpoint and no websocket. The data path is agents → sqlite → web ui, and the
   UI gets there by polling."* Their client is three `setInterval(tick, 500)` sites
   (`SessionsList.vue:33`, `SessionTrace.vue:84`, `SessionCard.vue:65`), each guarded by an
   `inflight` flag so a slow response cannot stack. `rg 'EventSource|WebSocket'` over their tree
   returns nothing. This is **independent corroboration** of C4's direction, arrived at by a project
   with no stake in our non-goals: their diagram lists *"✗ no websocket · ✗ no ingest endpoint · ✗ no
   backfill or dedup · ✗ no replay path"* as **features**. **[D] Adopted**, including the `inflight`
   guard.
3. **Poll only what is live.** `SessionCard.vue:55, 70-77` — a card polls while running, self-stops on
   the transition out, and does one final drain first. **[D] Adopted**: the board polls at
   `--refresh` while a supervisor is live and backs off to 60 s when nothing is in flight, so an idle
   wall costs nothing.
4. **The readonly reader that survives a schema migration.** `sssf/server/db.ts:97-124` probes
   `PRAGMA table_info` per column, substitutes `NULL AS <col>` for columns an older producer never
   created, and re-probes until it latches. **[D] Adopted as the *shape* of the tolerance rule in unit
   A**: a consumer meeting an absent section reports `absent`, never errors — which is also exactly
   what `work-tracker.md` §4.5 already mandates for our own ledger. Two independent sources, same
   rule.
5. **Separate *billed* from *moved*.** `sssf/shared/types.ts:232-243` models usage as `{read, written}`
   rather than `total_tokens`, because the headline double-counts cached re-reads, and each chip
   carries a tooltip saying so (`StatChip.vue:24-39`). **[D] Adopted**: our run records already carry
   `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` separately on 277 of
   432 entries **[M]**, and
   the spend panel must not silently sum them into one number.
6. **Fixed-size cards with `+N more` overflow**, never fewer than three lanes shown, *"so a
   five-agent chain still reads as a chain rather than as a pair and a count"* (`SessionCard.vue:171-186`).
   **[D] Adopted** — it is why the lane column in the ASCII layout is fixed-height.
7. **State encoded on three redundant channels, never colour alone.** sssf uses colour + glyph +
   border-style/motion (`StatusChip.vue:69-72` gives queued a **dashed** border;
   `SessionCard.vue:316-319` gives running a glow). **[D] Adopted** — this is an accessibility floor,
   not a flourish, and it is why the board uses Unicode glyphs rather than an icon font.
8. **A "Where it can still fail" section on the consumer surface.** Both READMEs have one and both
   name real defects. **[D] Adopted** for the `docs/how-to/adopt-the-board.md` in unit G. It is
   cheap credibility and it matches this repo's own rule that a README is a claim.
9. **Ask the renderer for its width; never guess it** — `fh/fusion-harness.ts:363-412`, with vertical
   stacking below 100 columns. **[D] Adopted** as the responsive rule: the board reflows to a single
   column below 1280px so a laptop shows the same board without a second layout.

### Visual style — what to inherit and what to keep as ours

The owner likes their look. Concretely, what that look **is**:

- **sssf**: dark-only, near-black navy `#06080f`, panels `#0d1119`/`#131a26`, borders `#232c3d`, two
  fixed radial "aurora" washes (violet 9% top-left, cyan 7% bottom-right, `style.css:37-42`) with
  `background-attachment: fixed` so content glides over them. A squarish techno sans for UI voice,
  mono reserved **strictly** for data — ids, times, code (`style.css:18-21`). An explicit readability
  floor: nothing below 16px (`style.css:45-47`). Low density on purpose — fixed 460×420 cards that
  truncate rather than resize, so fifty runs read as a uniform wall. Status colours
  green `#4ade80` / red `#ff6f67` / blue `#6cb6ff` / amber `#e8b64a`.
- **fh**: brighter neon-terminal, mono-first with letterspaced uppercase titles, and — the genuinely
  distinctive idea — colour coded by **role** rather than by status (ARCHITECT violet `#a78bfa` ◆,
  BUILDER amber `#fbbf24` ▲, FUSION cyan `#22d3ee`), mapped in code at `fusion-harness.ts:108-124`.

**[D] What I propose we inherit: the *grammar*, not the palette.** Dark-only; mono strictly for data
and sans for voice; a hard minimum type size; fixed-height cards that truncate rather than reflow;
three redundant state channels. Those are facts about how to build a readable wall display, and
learning a fact is on the free side of the licence line.

**[D] What I propose we keep as ours: the palette and the identity.** `site/index.html` `:root` already
defines a committed, shipped, house dark palette **[M]** — `#0f1117` / `#171a23` / `#262b38` /
`#d7dce6` / indigo `#818cf8` / amber `#f59e0b` / orange `#fb923c` / green `#34d399` — with a mono and
a sans stack and a `prefers-reduced-motion` block already in place. It is a very close cousin of
sssf's, it is under our own licence, and using it means the board looks like `basicly` rather than
like someone else's product. Two deliberate departures for the TV: **raise the minimum type size well
above sssf's 16px floor** (the six-metre constraint is not the laptop constraint), and **drop the
aurora washes** — a gradient that never changes is burn-in risk on a display left on for weeks.

**One thing worth stealing outright from `fh` for our lanes**: role-coloured rather than
status-coloured lanes. Our lanes carry a *phase* (`build`, `verify`, `ship`, `repair`) which is
exactly the same kind of axis, and colouring by phase makes "three lanes are all stuck in verify"
legible in one glance in a way that four green boxes is not.

### One warning I am carrying forward from their design

Their 500 ms cadence is safe against one local SQLite file and one Bun process. Ours would not be:
this repo's operating memory records the tracker **corrupting its WAL under our own five-lane
fan-out**, and a browser polling a file a supervisor is compacting is that failure mode with a new
reader attached. **[D] This is a second, independent reason the board's transport is a
rename-atomic JSON snapshot and not a live read of the ledger or of any database** — the producer
writes to a temp path and renames, so a reader observes either the old file or the new one and never
a partial. Recorded as acceptance criterion 2 on unit **F**.
