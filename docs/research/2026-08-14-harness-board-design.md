# solution-design: `harness-board` — a wall-display view of a running harness

Author: planning session, 2026-08-14. Repository under design: `basicly`, main, clean.
**Nothing in the repository was modified to produce this document** (`git status --short` empty at
start and at finish).

**Measurement window caveat, stated because it moves two numbers.** Main advanced from `d50440b` to
`256f8d0` during this session — four commits by a **concurrent session**, not by me (`d50440b` is an
ancestor of `256f8d0`; I made no commits and no edits). The tracker measurements below were taken at
`d50440b`: `.beads/issues.jsonl` was 820 rows / 3,336,549 bytes / 649 closed / 162 open. At
`256f8d0` it is 821 rows / 3,340,211 bytes / 650 closed / 163 open, with the comment count unchanged
at 2,301 **[M]**. A one-row drift over four commits does not move any ratio in this document — the
98.9× field-selection figure, the 733× build-cost figure and the wait analysis are all robust to it —
but a reader re-running a command and seeing 821 should know why.

Evidence marks: **[M]** measured by me here, with the command · **[S]** sourced, cited and dated ·
**[D]** a design decision I am taking. Unmarked prose carries no authority.

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

§7.4's own numbers **[S** `docs/requirements/factory-loop.md:708-713`, measured 2026-08-09**]**:
`n = 129` human-answered waits, 145,640 s total, median 95 s, 17 events over 30 min carrying **85%**
of all human wait; `basicly-hxnf.2` (27,553 s) and `basicly-hxnf.3` (27,510 s) answered *in the same
second*.

My replication and extension **[M]**, over `.beads/issues.jsonl`, parsing every `[harness-wait]`
marker whose header carries `answered`, keeping `by=human` and `delegated=false`:

```text
n = 154 human-answered        total 159,809 s (44.4 h)
p25 10 s   median 91 s   p75 391 s   p90 2,026 s   max 29,012 s
19 events > 30 min  ->  132,046 s  =  83% of all human wait     (§7.4 found 17 -> 85%; replicated)
```

The new axis. For each tail event I took `requested_at` — the instant the engine asked — and
classified it by local wall-clock (07:00–22:00 Mon–Fri = "someone could plausibly be in the room").
The classification is **invariant across UTC+0 to UTC+3** **[M]**, so it does not turn on guessing
the operator's timezone:

| Bucket | n | seconds | share of all human wait |
| --- | ---: | ---: | ---: |
| Tail (>30 min) **asked during office hours** — a TV in the room could have caught it | 4 | 14,527 | **9%** |
| Tail (>30 min) **asked out of hours** — nobody is in the room; a TV cannot help | 15 | 117,519 | **74%** |
| Head (≤30 min), median 60 s — a TV cannot beat a 60-second answer | 135 | 27,763 | 17% |

**So the honest ceiling on the rendezvous argument, denominated in the quantity §7.4 measured, is
~4 hours out of 44.4 across the whole project history.** The mass of the tail is overnight and
weekend waits — `basicly-sco6`'s 29,012 s is eight hours, and §7.4 already records that the *next*
decision on the same issue, same human, took 81 s: *"Eight hours asleep, eighty-one seconds awake"*
**[S** `factory-loop.md:723-724`**]**. A display in a room where nobody is standing recovers none of
that.

**I am telling you this because you asked to be corrected rather than agreed with, and because a
project justified on a 44-hour number that is really a 4-hour number will be judged against the
44-hour one.** The rendezvous framing is the right *kind* of argument — it identifies a real
mechanism and it is the only one §7.4 leaves standing — but it cannot carry this project alone.

### The argument that does carry it

Three things I measured are larger than the wait number and are not measured by wait markers at all.

1. **Money burns unattended.** 357 dispatch records over 292 beads; 153 carry a cost, summing
   **$953.82**, single largest dispatch **$24.64**, total **1,245,746,430** tokens **[M]**
   `.basicly/usage/run-records.json`. A dispatch runs ~1,800 s **[M]** (`duration_s` on the sampled
   `[harness-run]` marker). Multi-lane passes are the standing default, and the repo's own operating
   memory records a 300M-token grant burned for zero landings.
2. **The engine already knows it is off the rails and nobody is looking at the line that says so.**
   `basicly loop session basicly-kjc5 --json` prints, right now, `"spent_tokens": 177970761` against
   `"token_budget": 4000000` **[M]** — **44× over the declared budget**, in a JSON surface that
   exists, on a session nobody is watching. There are also 12 `[harness-overrun]` markers in the
   tracker **[M]**, each one a lane that blew its context ceiling and spawned a follow-up.
3. **The one number a room can act on is "is anything asking me?"** and today obtaining it costs
   **10.5–11.6 seconds** of wall clock **[M]**, because `supervise.observe()` spawns **345 `br`
   subprocesses** for one observation, 91 of them `br comments list` **[M]** (profiled by
   monkeypatching `subprocess.run`). Nobody polls an 11-second command, so nobody polls.

The problem, restated in terms I can defend: **the harness emits everything a room needs to prevent
an expensive mistake, and there is no surface on which a human who is not driving can see it without
paying eleven seconds and knowing which of eleven commands to type.**

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
**15 ms** measured today **[M]** against `observe()`'s 11,000 ms — a **733×** reduction — so a 15 s
refresh cadence consumes <0.1% of a core. Verified by a timing test in the suite that fails if the
build crosses 500 ms on this repo's own corpus.

**S4 — The contract validates independently of the page.**
`uv run basicly board --out - | uv run basicly board validate -` exits 0, and a snapshot with an
unknown `schema` major exits non-zero naming the version it saw and the version it wants. A foreign
harness can be conformance-tested with no basicly runtime.

**S5 — Every write the page can perform is a named existing CLI invocation, echoed before it runs.**
The page never writes a file, never touches `.beads/`, never mints an authority. Verified by a test
asserting the action table maps 1:1 onto `argv` lists whose head is `basicly`, and by a
`grep` gate that the board server module imports no writer module.

**S6 — Pending asks are derived by pairing, not by counting.** The naive parse ("count `requested`
lines") reports **132** pending asks on this repo today; the correct derivation — pair `[harness-wait]`
markers by their `id=` and treat an ask as pending only when no `answered` marker shares its wait id
— reports **0** **[M]**. S6 is met when a regression test pins that pair of numbers against the
committed corpus. *Positive control for the zero: 186 answered markers were found by the same
parser, so the 0 is a property of the world today, not of the probe.*

**S7 — Another project adopts it by emitting one file.** A repo with no basicly installed that writes
a `harness-board/v1` snapshot to a path, and serves the shipped `board.html` beside it, gets a
working board. Verified by a fixture repo in `tests/` containing a hand-written snapshot and no
basicly state, rendered and asserted.

**What success is not.** It is not "the checkpoint wait number goes down". Per the measurement in
`## Problem` the recoverable share is ~9% of that number, it is confounded by everything else that
changes, and n=4. I will not put a wait-time reduction on the acceptance criteria, and I would refuse
a release note that claimed one.

---

## Consumer transcript

### Mode A — the on-demand artifact (satisfies `work-tracker.md` §4.3 requirement 10 exactly as recorded)

```console
$ uv run basicly board --out board.html
board: harness-board/v1 snapshot built in 18 ms from 3 sources
       tracker   .beads/issues.jsonl                 820 records, 171 active, 2301 comments
       runs      .basicly/usage/run-records.json     357 records over 292 beads (machine-local)
       verify    .basicly/usage/verify-run.json      mode=fast passed=True, 20 checks
       session   (no supervisor lock; live lane panel will render "not supervised")
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

```console
$ uv run basicly board validate ./foreign-snapshot.json
board: refused - snapshot declares schema "harness-board/v2", this consumer reads v1
       A major version is a different contract. Upgrade the consumer, or ask the producer
       to emit v1 alongside. Nothing was rendered.
$ echo $?
2

$ uv run basicly board validate ./partial-snapshot.json
board: harness-board/v1, ok
       present   meta, backlog, gates
       absent    session, lanes, asks, spend, health
       Those five panels will render "not emitted by this producer". That is a
       supported snapshot: only meta is required.
$ echo $?
0
```

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
|    running 24m   18.7M tok   $13.13   |   cost    $953.82 lifetime   $24.64 largest dispatch      |
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
| BACKLOG  171 active of 820          ready 122        blocked 2        in progress 4              |
|  [########################################----------------------------] 649 closed / 820         |
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

- **A maintained TUI.** Refused permanently **[S** `architecture.md:1869`, `implementation-plan.md:70`**]**,
  with the recorded reason *"Permanent cost; generated artifacts (JSON, Mermaid/DOT, static HTML)
  are diffable in review and cost nothing to keep"* **[S** `docs/research/2026-07-26-sota-review.md:1031`**]**.
  This design is the artifact side of that sentence, not a departure from it.
- **A hosted service, multi-user auth, or any network listener beyond `127.0.0.1`.** `work-tracker.md:1261-1266`
  names *"a sync server or hosted service; a web application"* as non-goals. This is a local viewer
  of local files; it is not a web application in that sense and it must never become reachable off
  the loopback.
- **A second source of truth.** The board derives; it never stores. There is no board database, no
  cache that outlives a process, and deleting every board file loses nothing.
- **Writing to `.beads/` or to any ledger.** Every mutation is an `argv` list handed to the existing
  `basicly` CLI. See `## Constraints`.
- **Historical analytics, burndown, velocity, per-person metrics.** `work-tracker.md:1261-1266`
  excludes *"sprint, estimation, or reporting ceremony beyond what the loop consumes"*. The board
  shows *now* plus a six-event tail; anything longer belongs to `basicly usage report`.
- **Reading another machine's state.** `.basicly/usage/` is self-ignored — `.basicly/usage/.gitignore`
  line 1 is a bare `*` **[M]**, and `git ls-files .basicly/usage/` returns zero files. A board reading
  it shows **one operator's** dispatch history. Cross-machine aggregation is `status --fleet`'s job
  and is explicitly not attempted here.
- **A JS framework, a bundler, or a `node_modules` in the render path.** See `## Constraints`.
- **Claiming real-time.** See the next section.

---

## Constraints

### C1 — The real-time collision, re-opened explicitly

`work-tracker.md:487-488` restated requirement 10 from real-time to on-demand, and recorded why:

> **Requirement 10 is on-demand regeneration.** A generated Mermaid/DOT graph and a static HTML board
> rebuilt on write is not real-time, and calling it that would be the same overclaim one notch smaller.
> **[S** `docs/requirements/work-tracker.md:487-488`**]**

That restatement is **correct and I am not asking to reverse it.** What I am asking to add is a
second, separately-named mode with a separately-named claim.

**What real-time would cost, concretely.** The producer's own liveness resolution is
`HEARTBEAT_INTERVAL_S = 15.0` and `STALE_AFTER_S = 60.0` **[S** `src/basicly/supervise.py:106-107`**]**.
A consumer polling at 1 s cannot be fresher than a producer ticking at 15 s. Genuine sub-second
freshness would require the engine to push on every state transition — an event bus, a persistent
connection, and an ordering guarantee across five writers — which is the daemon the non-goals refuse
and which buys nothing, because *the thing being watched changes on the order of minutes*: a dispatch
runs ~1,800 s **[M]**, the heartbeat is 15 s, and a checkpoint sits for a median of 91 s **[M]**.

**What it buys.** Nothing measurable. There is no state transition in this system whose value decays
in under 15 seconds.

**[D] The honest claim, and the wording I propose be frozen into the CLI's own help text:**

> The board is **as fresh as the producer that wrote its snapshot, and it always shows how old that
> snapshot is.** It is not real-time and does not claim to be. In wall mode with a live supervisor it
> refreshes on the supervisor's 15-second tick.

This is enforceable, not aspirational: the snapshot schema makes `generated_at` and
`freshness.stale_after_s` **required**, and the page has no code path that renders a value without
rendering its age. An overclaim would have to be a deliberate schema violation, not a slip. That is
the same standard `work-tracker.md:487` was defending, met by construction rather than by restraint.

**What this changes in the requirement.** Requirement 10 stays as written. I propose one additional
sentence be considered at the next revision of §4.3, *not* a new document (D33): *"A live-attached
mode may render the same artifact on the supervisor's tick, provided it displays the snapshot's age
and never uses the word real-time."* Whether that sentence lands is the owner's call, and the design
works without it — Mode A alone already satisfies requirement 10 as recorded.

### C2 — The rendezvous collision, and what I concluded

`factory-loop.md` §7.4 is titled *"A rendered artifact does not fix a checkpoint, because the clock
is not measuring reading"* **[S** `factory-loop.md:702`**]**, and concludes *"the checkpoint clock
measures rendezvous, not reading. A renderer cannot move a quantity the instrument does not contain"*
**[S** `factory-loop.md:728-729`**]**.

**§7.4 does not refute this project, and this project does not refute §7.4.** They are about
different things and both survive:

- §7.4 refutes **rendering as a comprehension aid**. That refutation stands untouched. This design
  does not claim any checkpoint is easier to *read* on a screen than in a terminal, and I found no
  evidence it would be. §7.4's counter-datum (`wait-decompose`, 2,490 s, n=1) remains the only thing
  on the other side and n=1 is not a finding.
- The owner's rendezvous framing correctly identifies that the remaining quantity is **arrival**, and
  a wall display is an arrival intervention. **That reasoning holds.** My extension in `## Problem`
  bounds it: ~9% of measured human wait, n=4, ~4 hours lifetime. Directionally right, quantitatively
  small.

**[D] Therefore the wait number is not the justification and must not appear as an acceptance
criterion.** The justification is unattended spend and unattended failure — quantities §7.4 never
measured because it was measuring waits. I am recording this so that a later reader does not
reconstruct the project's rationale from the wrong section.

### C3 — OQ-15, and the honest answer about whether this closes it

OQ-15 **[S** `factory-loop.md:1289`**]** asks whether a checkpoint's artifact takes real reading time,
notes it is unanswerable with today's instrument, and says it is *"Settled by **one field**: a third
marker written when the operator first views the checkpoint."*

The seam is already shaped for it. `WaitEvent` **[S** `policy.WaitEvent`**]** already
carries `requested_at` and `answered_at`; the wait id is **derived, not minted** —
`wait_id_for_checkpoint(issue_id, name)` returns `f"{issue_id}#wait-{name}"`
**[S** `policy.py:2365-2371`**]** — so any observer can name an existing wait without creating one.
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

The non-goal is real and permanent **[S** `implementation-plan.md:69-71`; `architecture.md:1869`,
`:1978-1981`**]**. The recorded reason is specific:

> Adopting Dolt, or any external DB/daemon — *Reintroduces exactly the unowned-binary upgrade surface
> we are removing.* **[S** `docs/research/2026-07-26-sota-review.md:1031`**]**

and

> **3.8 Everything lives in plain, git-tracked files.** No daemon, no hidden state, no network calls
> at build time. **[S** `docs/architecture/architecture.md:302-304`**]**

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
**[S** `supervise.HeartbeatThread`**]**, and `basicly loop watch` **polls** **[S** `cli.py` `loop watch`
subcommand, *"Poll and print newly pending decisions"***]**. The repository already sanctions
operator-started, session-scoped, foreground processes. `basicly board --serve` is that category and
nothing more: it is in the foreground, Ctrl-C ends it, it holds no lock, it survives no reboot, and
there is no supervisor, systemd unit, or auto-start anywhere in the design.

**[D] Additionally, Mode A needs no process at all.** `basicly board --out board.html` produces one
self-contained file openable at `file://`. If the owner rejects the serve mode entirely, Mode A still
delivers `work-tracker.md` §4.5's *"static HTML board emitted by a command"* **[S** `work-tracker.md:568-571`**]**
verbatim, and the design degrades to that without redesign.

### C5 — The 11-second problem, and why the producer is not `observe()`

`basicly loop session <root> --json` is the richest live surface and it already emits almost exactly
the wall payload — 18 keys, ~450 bytes **[M]**. It takes **10.5, 11.5, 11.6 s** across three runs
**[M]**, of which `uv run` startup is **0.22 s** **[M]**; the remaining 11 s is inside
`supervise.observe()`, which spawns **345 `br` subprocesses** — 91 × `br comments list`, ~250 ×
`br show` **[M]**.

Building the same board from files instead — `.beads/issues.jsonl` (3.3 MB, 820 rows),
`run-records.json`, `verify-run.json` — costs **15 ms** total **[M]**: 12 ms to parse the tracker
export, 1 ms to derive active rows, open asks and cost markers, 2 ms for the other two files. That is
a **733×** reduction and it is the whole reason this design is affordable.

**[D] The board's producer reads files, never `br`.** This is not a shortcut; it is the direction
`work-tracker.md:219-223` already mandates: *"`list --json` caps its result set and drops closed
records, so the file is the only bulk read — and the only one a fresh clone can answer from with no
br invocation at all (D10)."* **[S]** The one thing files cannot supply is the live lock holder and
lane worktrees; for that the producer reads `.basicly/usage/supervisor.lock`
**[S** `supervise.LOCK_FILE`**]** directly, which is also a file.

### C6 — The wire format, and the field-selection lesson

The measured caution — `br ready --format json` costs 22.8× `--format text` in bytes, because JSON
inlines every `description` and `acceptance_criteria` body — is confirmed here and generalises. On
this repo's tracker **[M]**:

| Payload | bytes | ≈ tokens |
| --- | ---: | ---: |
| Whole `.beads/issues.jsonl` | 3,336,549 | 834,137 |
| All rows re-serialised minified | 3,339,617 | 834,904 |
| **Active rows (171), six selected fields** | **33,745** | **8,436** |
| + all 211 dependency edges as triples | 43,538 | 10,884 |

**98.9×, from field selection alone; minification bought 0.1%.** **[D] The schema therefore selects
fields, never records.** No `description`, no `acceptance_criteria`, no comment bodies cross the wire
— a marker is reduced to its parsed fields at the producer. A board that shipped whole bead rows
would be a 3 MB page and a 834k-token read for any agent that fetched it.

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
  `site/index.html:31-48` defines the palette **[M]**: `--bg #0f1117`, `--bg-raise #171a23`,
  `--bg-sunk #0b0d12`, `--border #262b38`, `--text #d7dce6`, `--text-bright #f2f5fa`,
  `--text-dim #8b93a7`, `--indigo #818cf8`, `--amber #f59e0b`, `--orange #fb923c`, `--green #34d399`,
  plus a mono stack (`ui-monospace, "Cascadia Code", "JetBrains Mono", Consolas`) and a system sans
  stack. It already honours `prefers-reduced-motion`. The board lifts these tokens verbatim; it is
  our own file under our own licence.
- **Portability.** Every path in the snapshot is repo-relative; absolute worktree paths from
  `LaneView.worktree` / `.branch` **[S** `supervise.LaneView`**]** are passed through the existing
  `redact.redact_machine_paths()` **[S** `redact.redact_machine_paths`**]** before serialisation — reuse,
  not a second implementation. Port selection defaults to an ephemeral bind so no hostname or fixed
  port is ever committed.
- **Module-size ratchet.** A *new* Python module may never cross the 4,000-token cap
  **[S** `.scripts/check_module_size.py:1,19-26`**]** — the frozen list is closed to new entries. The
  decomposition below is cut into six small modules for that reason, not for taste.
- **Comment-density ratchet**, cap 50% **[S** `.scripts/check_comment_density.py:57`**]**, applies to
  every new module.

### C8 — Authority: the engine disposes, agents propose

> **The engine disposes, agents propose.** No model holds authority over the tracker, the schedule,
> or a required gate — at any autonomy level. **[S** `docs/plan/implementation-plan.md:50-51`**]**

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
`policy.approve_checkpoint` grants approval on *"a TTY, a valid confirm code, or a covering grant"*,
and a non-interactive caller with no code *"gets a one-time `challenge` code it must relay to a
human"* **[S** `src/basicly/policy.py:1299-1312`**]**. The code is minted into
`.basicly/usage/checkpoint-confirms.json` **[S** `policy._CONFIRM_FILE`, `policy.CONFIRM_TTL_SECONDS`**]** with a 900 s TTL.

A board that read that file, displayed the code, and offered a one-click approve would be relaying
the code **to itself**. It would satisfy the letter of the gate and defeat its purpose entirely: the
gate exists so that an automated caller cannot self-approve, and the board is an automated caller.

**[D] Therefore: the board never reads `checkpoint-confirms.json`.** When the operator clicks
*Approve*, the board runs the challenge command, shows the printed challenge — *"relay this to a
human"* — and presents an **empty input box**. The human must obtain the code from the terminal or
from whoever holds it and type it in. This is more friction than a button, deliberately: it is the
same friction a terminal operator has today, and the gate is worth exactly that friction. `loop kill`
is treated identically **[S** `cli._add_loop_parser`, *"always gated on a human confirm code"***]**.

### C9 — The v1.0.0 freeze scope this enlarges

Five surfaces freeze at v1.0.0: **CLI commands and flags · `basicly.toml` plus the overlay contract ·
the catalog source schemas · the generated-file contract · the owned ledger format**
**[S** `docs/plan/implementation-plan.md:64-67, :473`**]**.

This design adds to **three** of them, and I am naming every addition so the freeze audit does not
have to discover them:

| Frozen surface | What this adds |
| --- | --- |
| **CLI commands and flags** | One command group `basicly board` with three verbs: `board` (default, emit), `board serve`, `board validate`. Flags: `--out PATH\|-`, `--root ISSUE`, `--port N`, `--refresh SECONDS`, `--no-actions`, `--snapshot PATH`. |
| **Generated-file contract** | Two new generated artifacts: `board.html` and `board-snapshot.json`, both disposable and both regenerable from the command. Neither is committed. |
| **Owned ledger format** | **Only if unit `H` (OQ-15) ships**: one optional `viewed_at` field on the `[harness-wait]` marker payload. Additive and optional, so it is forward-compatible under the rule `work-tracker.md:525-532` already states — *"skips unknown event kinds and unknown fields, preserving them verbatim"*. **This is the single reason `H` is optional and separately decided.** |
| `basicly.toml` | Nothing. Board settings, if any, are flags. **[D]** I am deliberately not adding a `[board]` table; "no unrequested config" is a Core Rule and flags are enough. |
| Catalog source schemas | Nothing. |

The **snapshot schema itself** is a sixth contract, and I am proposing it be frozen under its own
`harness-board/vN` version rather than folded into any of the five — because its whole purpose is to
be implemented by producers that are not basicly, and a foreign producer cannot track basicly's semver.

### C10 — Security boundary

- Bind `127.0.0.1` explicitly, never `0.0.0.0`. A TV in an engineering room is driven by a browser on
  the same machine, or by a display attached to it — not by a LAN listener.
- The action endpoints are `POST` with an `Origin` check and a per-process token embedded in the
  served page, so a page in another tab cannot drive the board. This is the standard CSRF shape and it
  costs ~15 lines.
- The snapshot passes through `redact.redact_secrets()` **[S** `redact.redact_secrets`**]** and
  `redact_machine_paths()` before serialisation. Lane branch names and worktree paths are the known
  carriers of a username **[S** `supervise.LaneView`**]**.
- `--no-actions` renders a read-only board. **[D] It is the recommended flag for an unattended wall**,
  and the reason is C8: a screen anyone in the room can touch should not be able to kill a lane.

---

## Open questions

Things I could not establish. These are not guesses dressed as design.

- **OQ-A — Does anyone actually stand in front of it?** The entire arrival mechanism assumes a person
  in the room during working hours. I measured that only **4 tail events** (9% of wait) were even
  *asked* during plausible office hours **[M]**. Whether a display changes behaviour at n=4 is not
  answerable from this repo's data and I could not find any other source. **What would unblock it:**
  ship Mode A + Mode B read-only, run for four weeks, and compare the office-hours tail before and
  after. That is a cheap experiment and it is the honest first release.
- **OQ-B — Does `<script src="./board-data.js">` reload under `file://` in current Chrome, Edge,
  Firefox and Safari?** Mode A sidesteps it by inlining, so nothing in the design depends on the
  answer — but a `file://` mode that *auto-refreshed* without a server would be strictly better than
  Mode B for a solo operator, and I did not test it. **What would unblock it:** one manual trial per
  browser. I did not run it because I could not run it on all four platforms and a result on one is
  not the claim.
- **OQ-C — Is `[harness-side]` a real marker family?** `work-tracker.md:201` lists twelve families
  including `[harness-side]`. My count of `src/basicly/` finds twelve, but the set differs: it
  contains `[harness-retro]` (**[S** `src/basicly/retrospective.py:38`**]**) and **not**
  `[harness-side]`, whose only occurrence anywhere in `src/`, `docs/` or `tests/` is that one line in
  `work-tracker.md` itself **[M]**. Either the doc has a typo or a family was removed and the doc was
  not. The board's parser must know which. Also: `[harness-review]` and `[harness-retro]` have **0**
  occurrences in `.beads/issues.jsonl` **[M]** — they are written to some other sink or not yet
  exercised, and the board cannot render what it has never seen a sample of. *Positive control: the
  same parser found 962 `[harness-policy]` and 318 `[harness-wait]`.*
- **OQ-D — Which root does an unattended wall display show?** `loop session` requires a root issue.
  A TV cannot be told which one. Options: the root of the live supervisor lock (works only while
  supervising); the most recently updated epic; a `--root` pinned at launch. I could not find an
  existing "current session root" concept in the engine to reuse and I will not invent one.
  **What would unblock it:** the owner saying whether the wall is pinned to one epic or should follow
  the supervisor.
- **OQ-E — Is `.basicly/ledger/events-0001.jsonl` a live source?** It holds 3,775 rows over 643
  records, every row `imported_from: beads-export` with `spend_micros: 0`, last written 2026-08-08.
  It looks like a frozen import snapshot rather than a live log. If it is live, it is a better event
  source than parsing comment markers; if it is frozen, the board must not read it. I could not tell
  from the code path alone.
- **OQ-F — What is the wall's idle state?** The mock shows a `NOTHING IS WAITING` band. On a real TV,
  90% of the time nothing is waiting, and a screen that is calm 90% of the time gets ignored — which
  destroys the arrival mechanism the whole thing rests on. I do not know the right answer (ambient
  motion? burn-in-safe dimming? a rotating "what shipped today"?) and I would rather ask than guess.
- **OQ-G — Do 121 `parent-child` + 83 `blocks` edges over 171 active rows render legibly at six
  metres?** **[M]** on the edge counts; unmeasured on the legibility. I have deliberately **left the
  dependency graph off the TV layout** for that reason and put it in the click-through detail only.
  `work-tracker.md:559-566` already warns that *"What agents handle well is a small, explicit,
  labelled edge set — not traversal of a large one"* **[S]**, and I suspect humans at six metres are
  worse than agents here, not better.

---
---

## The contract: `harness-board/v1`

This is the reusable part. The page is replaceable; this is not.

**Transport [D]**: a file at a path the consumer is told, default
`.basicly/usage/board/snapshot.json`. Written temp-then-rename so a reader sees the old file or the
new one, never a partial. In serve mode the identical bytes are also `GET /snapshot.json`. **There is
no other transport** — no socket, no stream, no database. A producer that can write a file can drive
this board.

**Versioning rule [D]**, deliberately the same rule `work-tracker.md:525-532` already fixes for the
ledger rather than a second one: *never change a key's meaning, never reuse a key name, only add keys
and optional sections.*

- `schema` is `harness-board/vN`. **N changes only on a break.**
- Within a major: additive only. A consumer **skips unknown keys and reports their count**; it never
  errors on them and never silently drops them.
- A consumer meeting a **different major** refuses to render and names both versions (transcript
  Mode C). It does not guess.
- **Only `meta` is required.** Every other section is optional, and an absent section renders
  `not emitted by this producer` — which is what makes the contract adoptable incrementally.

**The minimum conformant snapshot** — this is the whole barrier to entry for a foreign harness:

```json
{
  "schema": "harness-board/v1",
  "generated_at": "2026-08-14T16:42:52Z",
  "freshness": { "source": "one-shot", "cadence_s": null, "stale_after_s": 60 }
}
```

**The full shape.** Field-selected, never record-shaped (C6: 98.9× **[M]**). No `description`, no
`acceptance_criteria`, no raw comment body ever crosses this wire.

```jsonc
{
  "schema": "harness-board/v1",
  "generated_at": "2026-08-14T16:42:52Z",           // REQUIRED, RFC3339 UTC
  "freshness": {                                     // REQUIRED
    "source": "supervisor-tick",                     // supervisor-tick | self-refresh | one-shot
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

  "spend":  { "lifetime_usd": 953.82, "largest_dispatch_usd": 24.64,
              "input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0,   // billed vs moved, never summed
              "scope": "machine-local" },                        // honesty flag: one operator only

  "health": [ { "agent": "claude", "runs": 163, "score": 0.825,
                "failure_rate": 0.178, "drift": -0.04 } ],

  "backlog": { "total": 821, "active": 171, "ready": 122, "blocked": 2, "in_progress": 3,
               "closed": 650, "by_priority": { "P0": 4, "P1": 96, "P2": 58, "P3": 13 } },

  "events": [ { "at": "2026-08-14T16:41:02Z", "issue": "basicly-kjc5.57",
                "kind": "dispatched", "text": "build claude-opus-5" } ]   // bounded, last 6
}
```

**Notes that are part of the contract, not commentary:**

- `spend.scope` is **required whenever `spend` is present**, and `"machine-local"` is the honest value
  for basicly today, because `.basicly/usage/` is self-ignored (`.basicly/usage/.gitignore` line 1 is
  a bare `*` **[M]**). The page renders that word. A board that showed a team's spend when it holds one
  operator's would be the overclaim `work-tracker.md:487` is guarding against, in the money domain.
- `context_used` and `context_window` are a **pair**; a producer that knows only one emits neither,
  and the consumer draws no bar (unit C, AC 7).
- `asks[].actions` may only name entries in the closed action table of C8. A producer cannot invent an
  action, because the consumer has no mechanism to execute one it does not already know.
- Everything under `lanes[].branch` and any path-shaped string is redacted at the producer, not the
  consumer.

**What a foreign harness must do to adopt it**: write that minimum snapshot, then add whichever
sections it can populate. Nothing else. Verified by `basicly board validate <file>` and by unit G's
fixture, which contains a hand-written snapshot and no basicly state at all.

---
---

## Decomposition

Cut to pass this repo's plan gate. Every child carries the five fields
`gate_plan` enforces — `acceptance`, `scope`, `depends_on` (declared, possibly empty),
`budget_tokens`, `integrity` — plus the sixth a **proposed** plan owes, `demonstration`
**[S** `src/basicly/plan_gate.py:10-33, 289-334`**]**. Titles are unique; the declared graph is
acyclic. Integrity levels are `L1|L2|L3` **[S** `plan_gate.py:72`**]**.

Acceptance criteria are in EARS, per the `decompose-plan` skill
**[S** `.basicly/core/skills/decompose-plan/skill.yaml`**]**. Cut **vertically**: every child below
names a runnable demonstration through the consumer surface, which is what that skill says the sixth
field exists to catch.

Token budgets are stated as a scope read cost + build factor, in the same chars/4 unit
`read_cost._text_tokens` uses. They are forecasts, and the skill's own advice applies: *declare the
scope honestly even though it costs you*.

**Sizing constraint that shaped the cut**: a new Python module may never cross the 4,000-token
module-size cap **[S** `.scripts/check_module_size.py`**]**, so this is six small modules, not two
large ones.

---

### A — `harness-board/v1` snapshot schema and validator

**Integrity** L3 — it defines a surface intended to be implemented by foreign producers, which is
the L3 consumer category (*"CLI, `basicly.toml` schema, catalog source schemas, generated-file
contract, ledger format"* **[S** `factory-loop.md:211`**]**).
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
   `work-tracker.md:525-532` already fixes).
5. *State-driven* — WHILE a section is absent, validation SHALL report it as `absent` rather than
   as an error.
6. *Ubiquitous* — No schema property SHALL admit a bead `description`, `acceptance_criteria`, or a
   raw comment body (the 98.9× field-selection rule, C6).

**Demonstration** `uv run basicly board validate tests/fixtures/board/minimal-v1.json` prints
`harness-board/v1, ok` and exits 0; `... /wrong-major.json` exits 2.

---

### B — The file-only snapshot producer

**Integrity** L2. **Scope** `src/basicly/board_snapshot.py`, `tests/test_board_snapshot.py`
**depends_on** `["A — harness-board/v1 snapshot schema and validator"]`
**budget_tokens** 140,000

#### Acceptance criteria (EARS)

1. *Ubiquitous* — The producer SHALL build a complete snapshot by reading only files:
   `.beads/issues.jsonl`, `.basicly/usage/run-records.json`, `.basicly/usage/verify-run.json`,
   `.basicly/usage/supervisor.lock`. It SHALL spawn **zero** subprocesses.
2. *Ubiquitous* — Building a snapshot on this repo's committed corpus SHALL complete in under 500 ms
   (measured today at 15 ms **[M]**; the cap is 33× headroom, so it fails on a regression, not on noise).
3. *Ubiquitous* — An ask SHALL be reported pending only when no `[harness-wait]` marker sharing its
   `id=` carries `answered`. On the committed corpus this SHALL yield **0** pending asks where the
   naive count yields **132** **[M]**.
4. *Ubiquitous* — Every string reaching the snapshot SHALL pass `redact.redact_secrets` and
   `redact.redact_machine_paths`; no absolute path or username SHALL appear in the output.
5. *State-driven* — WHILE `.basicly/usage/run-records.json` is absent, the `spend` and `health`
   sections SHALL be omitted and the rest SHALL build.
6. *Unwanted* — IF a `[harness-*]` marker is malformed, THEN the producer SHALL skip it and continue,
   matching the existing best-effort parser contract **[S** `policy.py:2366-2372`**]**.

**Demonstration** `uv run basicly board --out - | uv run basicly board validate -` exits 0 and
prints the section inventory.

---

### C — `basicly board`: the on-demand artifact (Mode A)

**Integrity** L3 — adds a CLI command, a frozen surface (C9).
**Scope** `src/basicly/board_render.py`, `src/basicly/board_page.html.j2`,
`src/basicly/cli.py` (the `board` parser only), `tests/test_board_render.py`, `tests/test_cli_board.py`
**depends_on** `["B — The file-only snapshot producer"]`
**budget_tokens** 190,000

#### Acceptance criteria (EARS)

1. *Event-driven* — WHEN `basicly board --out board.html` runs, it SHALL write one self-contained
   HTML file with the snapshot inlined, referencing no external URL, no CDN and no local asset.
2. *Ubiquitous* — The page SHALL render the eight regions of the wall layout and SHALL display
   `generated_at` and a computed age in every one of them.
3. *Ubiquitous* — The page SHALL use only the CSS custom properties already defined in
   `site/index.html:31-48`, and SHALL honour `prefers-reduced-motion` as that file does.
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

**Demonstration** `uv run basicly board --out /tmp/b.html && python -c "import pathlib,sys;
t=pathlib.Path('/tmp/b.html').read_text(); sys.exit(0 if 'harness-board/v1' in t and 'src=' not in t
else 1)"` exits 0; then open it in a browser and read it.

---

### D — `basicly board serve`: wall mode, read-only (Mode B)

**Integrity** L3. **Scope** `src/basicly/board_serve.py`, `src/basicly/cli.py` (`board serve` parser),
`tests/test_board_serve.py`
**depends_on** `["C — basicly board: the on-demand artifact (Mode A)"]`
**budget_tokens** 170,000

#### Acceptance criteria (EARS)

1. *Event-driven* — WHEN `board serve` starts, it SHALL bind `127.0.0.1` only and SHALL print the
   bound URL. A test SHALL assert the bind address is never `0.0.0.0` or a hostname.
2. *State-driven* — WHILE a supervisor lock exists with a heartbeat younger than `STALE_AFTER_S`
   **[S** `supervise.STALE_AFTER_S`**]**, the server SHALL serve the supervisor-written snapshot and SHALL NOT
   compute one.
3. *State-driven* — WHILE no supervisor lock is fresh, the server SHALL self-refresh at `--refresh`
   seconds, default 15 to match `HEARTBEAT_INTERVAL_S` **[S** `supervise.HEARTBEAT_INTERVAL_S`**]**, with never more
   than one refresh in flight.
4. *Event-driven* — WHEN the snapshot's age exceeds `stale_after_s`, the page SHALL display `STALE`
   and the age, and SHALL NOT hide or freeze the last known values.
5. *Ubiquitous* — The process SHALL acquire no lock, write to no path under `.beads/`, and exit
   cleanly on SIGINT reporting refresh count and failures.
6. *Ubiquitous* — In this unit the server SHALL expose GET only; any POST SHALL return 405.

**Demonstration** `uv run basicly board serve --port 0 &` then
`curl -s http://127.0.0.1:$PORT/snapshot.json | uv run basicly board validate -` exits 0, and
`curl -s -X POST http://127.0.0.1:$PORT/action` returns 405.

---

### E — The action surface, behind existing engine commands

**Integrity** L3 — it touches the anti-autopilot boundary (C8).
**Scope** `src/basicly/board_actions.py`, `src/basicly/board_serve.py`, `tests/test_board_actions.py`
**depends_on** `["D — basicly board serve: wall mode, read-only (Mode B)"]`
**budget_tokens** 200,000

#### Acceptance criteria (EARS)

1. *Ubiquitous* — Every action SHALL be an `argv` list whose head is the `basicly` executable, taken
   from a closed table of exactly three entries: `loop answer`, `policy checkpoint --approve`,
   `loop kill`. A test SHALL assert the table's length and contents.
2. *Ubiquitous* — `board_actions` SHALL import no engine module that writes; an import-linter contract
   SHALL enforce it (`import-linter` is already a dev dependency **[M]** `pyproject.toml:29`).
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

#### Acceptance criteria (EARS)

1. *State-driven* — WHILE a supervisor pass is running, it SHALL write a snapshot on each heartbeat
   tick to `.basicly/usage/board/snapshot.json`.
2. *Ubiquitous* — The write SHALL be atomic (temp + rename), so a reader never observes a partial file.
3. *Ubiquitous* — Snapshot emission SHALL add no more than 50 ms to a tick, measured.
4. *Unwanted* — IF snapshot emission raises, THEN the supervisor SHALL log one line and continue; a
   board failure SHALL never fail a pass or a landing.
5. *Ubiquitous* — The supervisor's four coverage lines (`band:`, `spend:`, `health:`, `drift:`
   **[S** `supervise.py:1417-1421`**]**) SHALL be carried into the snapshot as structured fields, not
   as the free-text strings they are printed as.

**Demonstration** run `uv run basicly loop supervise <root>` in a scratch fixture repo and assert
`.basicly/usage/board/snapshot.json` mtime advances at least twice within 40 s while
`board validate` stays green on every version.

---

### G — Adoption seam: the foreign-producer conformance kit

**Integrity** L2. **Scope** `docs/how-to/adopt-the-board.md`, `tests/fixtures/board/foreign/**`,
`tests/test_board_foreign.py`
**depends_on** `["C — basicly board: the on-demand artifact (Mode A)"]`
**budget_tokens** 80,000

#### Acceptance criteria (EARS)

1. *Ubiquitous* — A fixture directory containing **only** a hand-written `snapshot.json` and no
   basicly state SHALL render a complete page.
2. *Ubiquitous* — The minimum conformant snapshot SHALL be under 400 bytes and SHALL be reproduced
   verbatim in the how-to.
3. *Event-driven* — WHEN a foreign snapshot omits every optional section, the page SHALL render eight
   `not emitted by this producer` regions and no error.
4. *Ubiquitous* — The how-to SHALL live under `docs/how-to/`, which D33 permits
   **[S** `factory-loop.md:115`**]**; no requirement or plan document SHALL be created.

**Demonstration**
`uv run basicly board --snapshot tests/fixtures/board/foreign/minimal.json --out /tmp/f.html` renders,
and the page opened in a browser shows the eight absent-section notices.

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
`jq -r '.comments[].text' .beads/issues.jsonl | grep 'harness-wait' | tail -1` shows a payload
carrying all three timestamps; with the flag unset, the same run shows two.

---

### Graph and ordering

```text
A ──> B ──> C ──> D ──> E ──> H (optional)
       └──> F
            C ──> G
```

Acyclic. Scope globs are disjoint except `src/basicly/cli.py` (C, D) and
`src/basicly/board_serve.py` (D, E), both declared — the plan gate wants overlap **declared**, not
absent, because overlap is what decides serialisation. C→D and D→E are sequential for that reason.

**Total forecast** 1,050,000 tokens across A–G; 1,170,000 including H. Reported, not defended: per
the `decompose-plan` skill, a large forecast is **reported, never refused**, and the author decides.
For calibration, this repo's measured lane mean is far larger than these children, so the cut is on
the conservative side.

**A first release that is honest about OQ-A.** A + B + C + G is a complete, shippable, zero-process
product that satisfies `work-tracker.md` §4.3 requirement 10 and §4.5 exactly as recorded, adds no
process anywhere, and costs ~470,000 tokens. **[D] I recommend shipping that first, running the wall
on it via a browser auto-refresh for four weeks, and only then deciding D/E/F/H.** The reason is
OQ-A: the entire arrival mechanism rests on an assumption about human behaviour that this repo's data
sizes at n=4, and D/E are where the security surface, the authority boundary and the process
argument all live. Spending them before the assumption is tested is the shape of mistake the repo's
own memory records as *"burned a 300M grant for zero landings."*

---
---

## Library and licence findings

## The stack, each with its licence and a reason

Licences read before the recommendation was written, per `.claude/rules/external-review.md`.

| Component | Licence | Already present? | Reason |
| --- | --- | --- | --- |
| Python 3.14 stdlib — `http.server`, `socketserver`, `json`, `pathlib`, `threading` | PSF License Agreement | bundled | The whole serve mode. No install, no pin, no upgrade surface — which is exactly what the `no external DB/daemon` refusal is protecting (C4). |
| `jinja2` | BSD-3-Clause | **yes** — `pyproject.toml:11`, used at `src/basicly/renderers/common.py` | Renders the page template. Reuse over reinvent: the repo already templates with it. |
| `jsonschema` | MIT | **yes** — `pyproject.toml:12` | `board validate`. The schema is the contract; validating it with a hand-rolled checker would be the reinvention this repo's Core Rules forbid. |
| `rich` | MIT | **yes** — `pyproject.toml:14` | The CLI's own output already routes through `src/basicly/ui.py`. The board's terminal lines use it; nothing new. |
| **Front end** | — | — | **Nothing.** One HTML file, inline `<style>`, inline `<script>`, vanilla ES2020. |
| Fonts | — | — | **System stacks only**, copied from `site/index.html:44-47`. No webfont, no `@fontsource` package, no network fetch. |
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
   what `work-tracker.md:525-532` already mandates for our own ledger. Two independent sources, same
   rule.
5. **Separate *billed* from *moved*.** `sssf/shared/types.ts:232-243` models usage as `{read, written}`
   rather than `total_tokens`, because the headline double-counts cached re-reads, and each chip
   carries a tooltip saying so (`StatChip.vue:24-39`). **[D] Adopted**: our run records already carry
   `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` separately **[M]**, and
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

**[D] What I propose we keep as ours: the palette and the identity.** `site/index.html:31-48` already
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
rename-atomic JSON snapshot and not a live read of `.beads/` or of any database** — the producer
writes to a temp path and renames, so a reader observes either the old file or the new one and never
a partial. Recorded as acceptance criterion 2 on unit **F**.
