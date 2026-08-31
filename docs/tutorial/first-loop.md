# Tutorial — from `basicly install` to your first shipped bead

This is a walkthrough, not a reference. Follow it top to bottom on a **scratch
git repo** and you will end with one unit of work filed, built in its own
worktree, merged, and closed by the harness — one sitting, no agent spend.
Every command and every quoted output below was executed against a fresh repo on
basicly 0.11.0 in a real terminal, with that repo's generated bead-id prefix swapped for
`myrepo`, absolute paths written as `/path/to/...`, and `...` marking an elided line.

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

Do this on a throwaway repo the first time. The loop rewrites hooks, adds
generated files, and makes commits; you want to see all of that somewhere you
do not mind.

Every command below is written as bare `basicly`. Run it as the pinned form so
you always get the version you chose:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.11.0 basicly <args>
```

## Step 1 — install the harness

From the repo root:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.11.0 basicly install
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
engine: basicly 0.11.0
repo: consumer
catalog: installed by basicly 0.11.0 at ... (matches engine)
drift: generated files up to date
Hooks
┏━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ manager ┃ specs ┃ projection ┃ activation ┃
┡━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ git     │ 11    │ in sync    │ installed  │
│ claude  │ 4     │ in sync    │ active     │
│ copilot │ 1     │ in sync    │ active     │
└─────────┴───────┴────────────┴────────────┘
agent hooks: active on claude, copilot; the git hooks stay the commit-time floor
technologies: all (no selection recorded)
overlays: 2 fragment(s), 0 agent(s)
```

`repo: consumer` means this repo *uses* the catalog rather than authoring it.
`git 11 … installed` means eleven git hooks are now live — which is what the
next step is about.

## Step 2 — make the first commit possible

The gates are active from now on, including on the install output itself. Two
of them refuse a fresh repo, and both are one line to fix. Do it now rather
than discovering it mid-commit.

**The hook scripts leave `__pycache__` behind.** They are Python, they run from
`.basicly/core/hooks/`, and the `.gitignore` basicly writes covers only
`basicly.local.toml`. Commit the `.pyc` files by accident and every later commit
fails with `pre-commit-script … files were modified by this hook`. Add the line
yourself:

```sh
printf '__pycache__/\n' >> .gitignore
```

**`catalog-lint` wants a routing floor.** The catalog now sitting in your repo
is measured for how often the right skill ranks first, and the hook refuses to
guess what number you consider acceptable:

```text
catalog lint: routing: rank-1 rate 41/46 = 89.1% (no floor declared)
catalog lint: FAILED
  no rank-1 floor declared — set `[catalog] rank1_floor` in basicly.toml below the measured baseline (currently 89.1%)
```

Take the number it just measured **for you** and set the floor a little under it, in
`basicly.toml` — the rate depends on the catalog your version shipped, so a floor copied
from this page instead of from your own output can land above it and fail every commit:

```toml
[catalog]
rank1_floor = 0.85
```

That floor is a ratchet: adding a skill whose description collides with an
existing one drops the rate and fails the commit.

## Step 3 — file the bead you are about to work on

Nothing commits in this repo without a bead id — the `tracker-commit-msg` hook
refuses it, install output included:

```text
ERROR: Commit message does not reference a tracked issue id.
```

So file one before you commit anything. A bead has to pass the
**Definition of Ready** gate before the loop will build it, and you do not have
to guess what that means — ask for the shape:

```sh
basicly policy scaffold --type task
```

```text
## Acceptance Criteria

- TODO: Given <starting state> when <action> then <observable result>

## Scope

- TODO: one entry per line in exactly this form: - `src/basicly/cli.py` — an entry that is not a backticked glob parses to nothing.
```

Fill both sections in and file it. A `## Scope` entry must be a **backticked
glob alone on its line** — a bare path parses to nothing, and everything
downstream (sizing, parallel grouping) reads it:

```sh
basicly tracker write -- create "Add a getting started note" -t task -p 2 -d '## Acceptance Criteria

- Given a reader who opens the repo when they read NOTES.md then a one line getting started note is present

## Scope

- `NOTES.md`
'
```

```text
created: myrepo-hv33
```

A record with no `--parent` is a *root*, and a root id needs a namespace: set
`[tracker] prefix = "myrepo"` in `basicly.toml` first, or pass `--parent <id>` to hang it
off one you already have. The command refuses rather than guessing, because a guessed
prefix mints an id no later read finds again.

Check the gate agrees:

```sh
basicly policy dor myrepo-hv33
```

```text
DoR: READY (myrepo-hv33)
```

If it says `NOT READY` it names the missing heading and repeats the scaffold
command; fill that section in with
`basicly tracker write -- update myrepo-hv33 -d '...'`.

Now the install output has an id to reference, so commit it:

```sh
git add -A
git commit -m "chore: install basicly (myrepo-hv33)"
```

Every hook should report `Passed`.

## Step 4 — ask the loop where the bead stands

```sh
basicly loop status myrepo-hv33
```

```text
issue:       myrepo-hv33 (task, open)
phase:       intake
worktree:    (none)
gates:       advance BLOCKED
  missing:   verify
checkpoints: (none)
rework:      verify=0
ready set:   myrepo-hv33
blocked:     (none)
```

Nothing recorded this phase anywhere — it was *derived* from the tracker just
now. That is why this command is the right first move in any session,
including one that starts after a crash or on a different machine.

## Step 5 — intake and classify

One command drives a whole phase *boundary*, resolving what it may on the way:

```sh
basicly loop run myrepo-hv33 --work-type task --runner manual
```

`--work-type task` is your call, and it decides the shape of everything after:
a `task` (or `bug`/`chore`) builds directly, a `feature` stops and demands a
child plan. Classify by shape, not ambition — one coherent change is a `task`.

`--runner manual` means **you** write the code in the worktree. Leave it off and
the harness dispatches a headless coding agent instead, which meters spend and
needs an autonomy grant; see
[run several lanes in parallel](../how-to/run-parallel-lanes.md).

The command reaches a human checkpoint — **classify** — and your terminal is what
answers it:

```text
override: runner.default=manual
Created worktree 'myrepo-hv33'
  path:   /path/to/myrepo.worktrees/myrepo-hv33
  branch: harness/myrepo-hv33  (base main @ 252ea7a)
  .basicly/ledger/redirect: tracker shared with the base checkout
  hooks: pre-commit, commit-msg, pre-push — pre-commit installed at /path/to/myrepo/.git/hooks/pre-commit
  ...
[blocked] intake -> intake: recorded work type 'task'; classify checkpoint awaiting approval
checkpoint classify: APPROVED (myrepo-hv33)
[blocked] classify -> classify: worktree 'myrepo-hv33' provisioned; awaiting the agent's work
```

A checkpoint is a *human* decision, and running the command yourself at a terminal **is**
the human. Drive the loop from something with no terminal — a script, a CI job, a coding
agent's tool call — and this command stops instead, printing a one-time code to echo back
on the whole reprinted command; that relay is
[resume or hand over a track](../how-to/resume-a-track.md)'s table, and on this page it
never appears.

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
cd ../myrepo.worktrees/myrepo-hv33
printf '# Notes\n\nRun `basicly status` to see what the harness installed.\n' > NOTES.md
git add NOTES.md
git commit -m "docs: add a getting started note (myrepo-hv33)"
```

**The loop never commits your work for you.** Landing rebases the branch, so an
uncommitted change (or a branch with no commit) makes the next step stop with
*commit the work on `<branch>` before landing*.

## Step 7 — land, verify, ship

Go back to the base checkout — landing mutates it, and running from inside the
worktree strands the merge:

```sh
cd ../../myrepo
basicly loop run myrepo-hv33 --runner manual
```

```text
override: runner.default=manual
  ...
Cleaned up worktree 'myrepo-hv33' (worktree + branch + metadata).
[merged] build -> verify: merged harness/myrepo-hv33 @ 2a1782e156d6 (1 commit(s)) into main @ 8c9f695
[blocked] verify -> verify: ship checkpoint awaiting human approval
checkpoint ship: APPROVED (myrepo-hv33)
[tore-down] ship -> done: worktree torn down and issue closed; cost rollup recorded; the curator bound no claims; tracker state committed
```

One command rebased the branch, ran the verify gate, recorded the result on the bead,
merged, took your terminal as the **ship** approval, tore the worktree down and closed the
bead. This one exits `0`, because it reached `done` rather than stopping.

Do not read the transcript top to bottom as the order things happened in: the `[...]` and
`checkpoint` lines print together at the end, after the work they describe, which is why
the cleanup line sits above the `[merged]` line that preceded it.

Those two decisions arrived together because one command made both. There is no
un-approve, and a ship approved with the work *unmerged* wedges the bead with its commits
stranded — so the `[merged]` line above the approval is the thing to check, and off a
terminal the one-time code is what forces you to check it before relaying.

Your verify gate passed vacuously here: a fresh `basicly.toml` declares no
checks, so there was nothing to run. Before you use this on a real repo, wire
your tests in — [wire up the verify gate](../how-to/wire-up-the-verify-gate.md).

## What you just built

```sh
git log --oneline -6
```

```text
c68ba2c chore(beads): close the shipped track (myrepo-hv33)
8c9f695 chore(worktree): merge a harness worktree back to its base
2a1782e docs: add a getting started note (myrepo-hv33)
c8ac268 chore(beads): sync tracker state for the harness loop (myrepo-hv33)
252ea7a chore(beads): record the claim before provisioning (myrepo-hv33)
14f8fbb chore: install basicly (myrepo-hv33)
```

You wrote two of those six — the install commit and the change itself. The
engine wrote the merge and the three `chore(beads)` commits, at the claim, the
landing, and the close, which is why you never `git add .basicly/ledger` yourself on
loop-tracked work.

```sh
basicly loop status myrepo-hv33
```

The fields that moved since Step 4:

```text
issue:       myrepo-hv33 (task, closed)
phase:       done
worktree:    myrepo-hv33 on harness/myrepo-hv33
gates:       advance ALLOWED
  passed:    verify
checkpoints: classify, ship
rework:      verify=0
```

Two human decisions (classify, ship), one automated gate (verify), one merge.
That is the whole loop; a hundred-file feature runs the same shape with a
decompose step in the middle.

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
