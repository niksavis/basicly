# Tutorial — from `basicly install` to your first shipped bead

This is a walkthrough, not a reference. Follow it top to bottom on a **scratch
git repo** and you will end with one unit of work filed, built in its own
worktree, merged, and closed by the harness — one sitting, no agent spend.
Every command and quoted output below was checked against a fresh repo using
basicly 0.28.0 from a locally built release wheel in a non-interactive shell. Bead ids, commit hashes and paths are normalized
for readability. `...` marks omitted output.

When you want to look something up rather than learn the shape, stop here and
use the [how-to guides](../how-to/) instead; the authoritative reference is
[`docs/architecture/architecture.md`](../architecture/architecture.md).

## What you will have at the end

- A tracked issue (a *bead*) that went `open → closed` through the loop.
- A `harness/<bead-id>` branch that was created, built on, merged, and deleted.
- Six commits in your base branch: two of them yours, plus the merge and the
  three tracker commits the engine makes at claim, landing, and close.

## Before you start

| You need | Why | Check |
| --- | --- | --- |
| A git repo with at least one commit | The loop branches from a base commit | `git log --oneline` |
| [uv](https://docs.astral.sh/uv/) on `PATH`, Python 3.14+ | The CLI and every projected git hook run through it | `uv --version` |
| Node and local `markdownlint-cli2` | The default markdown gate refuses missing tools | `node --version` |

Do this on a throwaway repo the first time. The loop rewrites hooks, adds
generated files, and makes commits; you want to see all of that somewhere you
do not mind.

Every command below is written as bare `basicly`. Run it as the pinned form so
you always get the version you chose:

```sh
uvx --from https://github.com/niksavis/basicly/releases/download/v0.28.0/basicly-0.28.0-py3-none-any.whl basicly <args>
```

`uvx` is one of three ways to reach the same verb, not the command itself:
`uv run basicly install` works inside a checkout of basicly itself, and a bare
`basicly install` works once the executable is on `PATH` (put it there with
`uv tool install`). Install syncs the catalog into your repo; it never puts
`basicly` on `PATH`.

## Step 1 — install the harness

Keep `node_modules/` out of Git before committing the install output. If the repository
does not already ignore it, approve this ignore-file change, then run:

```sh
printf 'node_modules/\n' >> .gitignore
```

Prepare the markdown gate in the scratch repository first. If the repository already
locks `markdownlint-cli2`, run `npm ci`. Otherwise, approve this dependency addition for
your scratch repository, then run:

```sh
npm install --save-dev markdownlint-cli2
```

The enabled hook reads repository-owned Markdown inputs. Keep an existing
`.markdownlint-cli2.jsonc`, `.markdownlint-cli2.yaml`, `.markdownlint-cli2.cjs` or
`.markdownlint-cli2.mjs` and its input and rule choices. A style-only `.markdownlint.json`
configuration sets rules but does not select CLI2 inputs. Installation does not create
an input or style policy for your repository.

For this throwaway walkthrough, choose `README.md` and `NOTES.md` explicitly. If no CLI2
configuration exists, create `.markdownlint-cli2.jsonc` with:

```json
{"globs": ["README.md", "NOTES.md"]}
```

This example keeps every default rule enabled and selects only those two consumer files.
It does not claim to lint generated files or every Markdown file in another repository.
If a configuration already exists, keep it and ensure the walkthrough's files are included
in the inputs you choose.

Start with a clean `README.md`. Keep an existing file; only create one when it is absent:

```sh
test -e README.md || printf '# Scratch repo\n' > README.md
```

Commit the configuration and package files with the install output. The default install
refuses missing Node or markdownlint before writing files. See
[markdown gate prerequisites](../how-to/upgrade-and-check-drift.md#markdown-gate-prerequisites)
if the repository owner chooses an explicit opt-out.

From the repo root:

```sh
uvx --from https://github.com/niksavis/basicly/releases/download/v0.28.0/basicly-0.28.0-py3-none-any.whl basicly install
```

It ends with:

```text
basicly install complete: repo converged. Re-run the same command to upgrade.
```

Look at what appeared before moving on — this is the whole distribution:

```sh
basicly status
```

```text
engine: basicly ...
repo: consumer
catalog: installed by basicly ... at ... (matches engine)
drift: generated files up to date
Hooks
┏━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ manager ┃ specs ┃ projection ┃ activation ┃
┡━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ git     │ 15    │ in sync    │ installed  │
│ claude  │ 9     │ in sync    │ active     │
│ copilot │ 2     │ in sync    │ active     │
└─────────┴───────┴────────────┴────────────┘
agent hooks: active on claude, copilot; the git hooks stay the commit-time floor
technologies: all (no selection recorded)
overlays: 2 fragment(s), 0 agent(s)
```

`repo: consumer` means this repo *uses* the catalog rather than authoring it.
`git 15 … installed` means fifteen git hooks are now live — which is what the
next step is about.

## Step 2 — check that the first commit can pass

The gates are active from now on, including on the install output itself. Run the catalog
check that the commit will run:

```sh
basicly catalog lint
```

```text
...
catalog lint: routing: rank-1 rate 56/60 = 93.3% (no floor declared)
catalog lint: OK
```

The rate measures how often the right skill ranks first for a test prompt. A fresh install
declares no floor, so nothing blocks the commit. When you add your own skills, you can
declare a floor a little under the rate that your own output shows, so that a skill whose
description collides with another one fails the commit:

```toml
[catalog]
rank1_floor = 0.85
```

## Step 3 — file the bead you are about to work on

File and claim a record before committing the install output. The `tracker-claim`
hook requires a named record you hold in progress for that code commit. Once the ledger
holds a record, `tracker-commit-msg` also refuses a commit that names none:

```text
ERROR: Commit message does not reference a tracked issue id.
```

A bead has to pass the **Definition of Ready** gate before the loop will build it.
Ask for the shape before filing the record:

```sh
basicly policy scaffold --type task
```

```text
## Trigger

TODO: state a complete trigger with a situation/persona, motivation/goal and outcome/benefit; each part must contain text without placeholders. Use either voice - a situation, 'When <situation>, I want to <motivation>, so I can <outcome>.', or a persona, 'As a <persona>, I want <goal>, so that <benefit>.'. A persona is never required: where a situation triggers the work and no person wants it, inventing a persona is the defect

## Scope

- TODO: one entry per line in exactly this form: - `src/basicly/cli.py` — an entry that is not a backticked glob parses to nothing.
also pass: --acceptance '- Given <state> when <action> then <result>' --requirements '- <the standard the result must obey>'
```

The body goes in the description. The acceptance criteria and the requirements are
fields, so they go in `--acceptance` and `--requirements`; a description that holds
either heading is refused. Fill it all in and file it. A `## Trigger` is owed by every work
type, and a persona is never required — where a situation triggers the work and
nobody in particular wants it, inventing a persona is the defect. A `## Scope`
entry must be a **backticked glob alone on its line** — a bare path parses to
nothing, and everything downstream (sizing, parallel grouping) reads it.

A record with no `--parent` is a *root*, and a root id needs a namespace. Set it once in
the ledger before you file the first root, or pass `--parent <id>` to hang the record off
one you already have. `create` refuses rather than guessing, because a guessed prefix
mints an id no later read finds again:

```sh
python3 .basicly/core/kit/tracker/cli.py config .basicly/ledger set prefix myrepo
```

```text
{
  "schema": "basicly.tracker.config.v1",
  "set": {
    "name": "prefix",
    "source": "ledger file",
    "value": "myrepo"
  }
}
```

The prefix has one home, `.basicly/ledger/template.json`. The engine and the tracker kit
both read it. An older repository that set `[tracker] prefix` in `basicly.toml` keeps
minting under it, and the next `basicly install` moves it into the ledger. Now file the
record:

```sh
basicly tracker write -- create "Add a getting started note" -t task -p 2 -d '## Trigger

When a reader opens the repo, I want a one line getting started note, so I can run the harness without reading the whole tree.

## Scope

- `NOTES.md`
' \
  --acceptance '- Given a reader who opens the repo when they read NOTES.md then a one line getting started note is present' \
  --requirements '- One line of Markdown in NOTES.md'
```

```text
created: myrepo-2i3r
myrepo-2i3r is not ready: it owes ## Confirmation Plan; claim refuses it
...
```

The record is filed, but the claim refuses it until someone has reviewed it. Tasks, bugs
and chores can submit a compact review containing only `checks`. This walkthrough uses
the supported full review to show how to retain a real discussion: six INVEST rationales,
conversation references and a check for each criterion. Write the agreement on the card
first:

```sh
python3 .basicly/core/kit/tracker/cli.py comment .basicly/ledger myrepo-2i3r 'Agreed: one line in NOTES.md that points a reader at basicly status.'
python3 .basicly/core/kit/tracker/cli.py show .basicly/ledger myrepo-2i3r
```

`show` lists the comment with its sequence number in `comment_log`:

```text
...
  "comment_log": [
    {
      ...
      "seq": 3,
      "text": "Agreed: one line in NOTES.md that points a reader at basicly status.",
      ...
    }
  ],
...
```

Then record the review. `conversation` names that sequence number, and each check repeats
an acceptance criterion word for word, with the command that proves it and the result you
expect:

```sh
python3 .basicly/core/kit/tracker/cli.py review .basicly/ledger myrepo-2i3r --evidence '{"invest": {"independent": "No other record touches NOTES.md.", "negotiable": "The wording of the note is open.", "valuable": "A reader finds the first command without reading the tree.", "estimable": "One line in one file.", "small": "One commit.", "testable": "grep counts the line."}, "conversation": [3], "checks": [{"criterion": "Given a reader who opens the repo when they read NOTES.md then a one line getting started note is present", "command": ["grep", "-c", "basicly status", "NOTES.md"], "expected": "1"}]}'
```

```text
{
  "appended": true,
  "blocking": [],
  ...
  "owed": [],
  "record": "myrepo-2i3r",
  "remedy": "",
  "schema": "basicly.tracker.review.v1"
}
```

Check the gate agrees:

```sh
basicly policy dor myrepo-2i3r
```

```text
DoR: READY (myrepo-2i3r)
```

If it says `NOT READY` it names what is missing.
Add a missing section with `basicly tracker write -- update myrepo-2i3r -d '...'`, and a
missing field with `--acceptance '...'` or `--requirements '...'`. A change to either one
makes the review stale, so record the review again after it.

Now the install output has an id to reference. The `tracker-claim` hook refuses a
commit that changes files outside the ledger unless you hold the record it names, so
claim the record, then commit:

```sh
basicly tracker write -- update myrepo-2i3r --status in_progress
git add -A
git commit -m "chore: install basicly (myrepo-2i3r)"
```

Lefthook should report each managed dispatcher as successful.

## Step 4 — ask the loop where the bead stands

```sh
basicly loop status myrepo-2i3r
```

```text
issue:       myrepo-2i3r (task, in_progress)
phase:       intake
worktree:    (none)
gates:       advance BLOCKED
  missing:   verify
checkpoints: (none)
rework:      verify=0
ready set:   myrepo-2i3r
blocked:     (none)
```

Nothing recorded this phase anywhere — it was *derived* from the tracker just
now. That is why this command is the right first move in any session,
including one that starts after a crash or on a different machine.

## Step 5 — intake and classify

One command drives a whole phase *boundary*, resolving what it may on the way:

```sh
basicly loop run myrepo-2i3r --work-type task --runner manual
```

`--work-type task` is your call, and it decides the shape of everything after:
a `task` (or `bug`/`chore`) builds directly, a `feature` stops and demands a
child plan. Classify by shape, not ambition — one coherent change is a `task`.

`--runner manual` means **you** write the code in the worktree. Leave it off and
the harness dispatches a headless coding agent instead. The task request authorizes
normal phases without another grant or mandatory token budget; see
[run several lanes in parallel](../how-to/run-parallel-lanes.md).

The command classifies the task and provisions its worktree without asking for another approval:

```text
override: runner.default=manual
Created worktree 'myrepo-2i3r'
  path:   /path/to/myrepo.worktrees/myrepo-2i3r
  branch: harness/myrepo-2i3r  (base main @ 35284db)
  .basicly/ledger/redirect: tracker shared with the base checkout
  node_modules: copied from myrepo (identical package-lock.json)
  hooks: post-merge, pre-commit, commit-msg, pre-push — repository-local Lefthook hooks installed
  ...
[classified] intake -> classify: recorded work type 'task'
[blocked] classify -> classify: worktree 'myrepo-2i3r' provisioned; awaiting the agent's work
```

The task request already authorizes work within its scope. The same command works
without a terminal, grant, mandatory token budget or phase confirmation code.
Required readiness and verification gates still apply.

The exit code here is `1` and nothing failed: `loop run` exits non-zero at *every* phase
boundary it stops on. Read the last line, not `$?`.

Three things just happened that are worth understanding:

- The work is isolated in a **sibling** worktree — `../myrepo.worktrees/…`,
  never inside the repo, so whole-tree gates and file watchers do not see it.
- `.basicly/ledger/redirect` points that worktree's tracker at the base checkout's.
  There is one work graph; a read or a write from either side reaches it.
- The engine committed the claim to git *before* creating the worktree, so the
  claim is in history from the moment work starts.

## Step 6 — do the work, and commit it on the branch

```sh
cd ../myrepo.worktrees/myrepo-2i3r
printf '# Notes\n\nRun `basicly status` to see what the harness installed.\n' > NOTES.md
git add NOTES.md
git commit -m "docs: add a getting started note (myrepo-2i3r)"
grep -c "basicly status" NOTES.md
```

The last command is the check the review planned, and it prints `1`.

**The loop never commits your work for you.** Landing rebases the branch, so an
uncommitted change (or a branch with no commit) makes the next step stop with
*commit the work on `<branch>` before landing*.

## Step 7 — land, verify, ship

Go back to the base checkout — landing mutates it, and running from inside the
worktree strands the merge:

```sh
cd ../../myrepo
python3 .basicly/core/kit/tracker/cli.py confirm .basicly/ledger myrepo-2i3r --evidence '{"checks": [{"criterion": "Given a reader who opens the repo when they read NOTES.md then a one line getting started note is present", "command": ["grep", "-c", "basicly status", "NOTES.md"], "result": "1", "exit_code": 0}]}'
basicly loop run myrepo-2i3r --runner manual
```

`confirm` records the result of each planned check; the loop requires actual confirmation
before merge and will not close the bead without it. Record it from the base checkout, because the tracker that `cli.py` writes in
the worktree is not the shared one.

```text
override: runner.default=manual
  ...
Cleaned up worktree 'myrepo-2i3r' (worktree + branch + metadata).
[merged] build -> verify: merged harness/myrepo-2i3r @ b8d7b80790cd (1 commit(s)) into main @ 8b65704; landing 1.0s (tracker-commit 0.7s, merge 0.3s)
[shipped] verify -> ship: ship checkpoint satisfied
[tore-down] ship -> done: worktree torn down and issue closed; cost rollup recorded; the curator bound no claims; tracker state committed
```

One command rebased the branch, ran the verify gate, recorded the result on the bead,
merged, completed the authorized ship step, tore the worktree down and closed the
bead. This one exits `0`, because it reached `done` rather than stopping.

Do not read the transcript top to bottom as the order things happened in: the `[...]` and
phase lines print together at the end, after the work they describe, which is why
the cleanup line sits above the `[merged]` line that preceded it.

The `[merged]` line proves that the branch landed before cleanup and closure.
The task request covers those normal transitions. A failed required gate still blocks them.
The remaining `checkpoint` names in status output are compatibility records, not another consent request.

Your verify gate passed vacuously here: a fresh `basicly.toml` declares no
checks, so there was nothing to run. Before you use this on a real repo, wire
your tests in — [wire up the verify gate](../how-to/wire-up-the-verify-gate.md).

## What you just built

```sh
git log --oneline -6
```

```text
58ba56c chore(tracker): close the shipped track (myrepo-2i3r)
8b65704 chore(worktree): merge a harness worktree back to its base
b8d7b80 docs: add a getting started note (myrepo-2i3r)
5d37092 chore(tracker): sync tracker state for the harness loop (myrepo-2i3r)
35284db chore(tracker): record the claim before provisioning (myrepo-2i3r)
e1e08ac chore: install basicly (myrepo-2i3r)
```

You wrote two of those six — the install commit and the change itself. The
engine wrote the merge and the three `chore(tracker)` commits, at the claim, the
landing, and the close, which is why you never `git add .basicly/ledger` yourself on
loop-tracked work.

```sh
basicly loop status myrepo-2i3r
```

The fields that moved since Step 4:

```text
issue:       myrepo-2i3r (task, closed)
phase:       done
worktree:    myrepo-2i3r on harness/myrepo-2i3r
gates:       advance ALLOWED
  passed:    verify
checkpoints: classify, ship
rework:      verify=0
```

The user task request authorized the normal phases. The verify gate and merge still ran.
No L-level grant, mandatory budget or repeated phase approval was needed. A larger feature
uses the same workflow with a decomposition step in the middle.

## Where to go next

- [Customize the guidance](../how-to/customize-the-catalog.md) — your rules, in
  the overlay an upgrade never touches.
- [Wire up the verify gate](../how-to/wire-up-the-verify-gate.md) — before the
  gate is load-bearing, make it non-vacuous.
- [Unblock a commit a hook refused](../how-to/unblock-a-commit.md) — the errors
  you will actually meet.
- [Upgrade, check drift, uninstall](../how-to/upgrade-and-check-drift.md).
- [Run several lanes in parallel](../how-to/run-parallel-lanes.md) — one epic,
  many worktrees, a serial merge queue.
- [Resume or hand over a track](../how-to/resume-a-track.md) — after a crash, or
  onto a different agent.
