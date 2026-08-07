# Tutorial — from `basicly install` to your first shipped bead

This is a walkthrough, not a reference. Follow it top to bottom on a **scratch
git repo** and you will end with one unit of work filed, built in its own
worktree, merged, and closed by the harness — one sitting, no agent spend.
Every command and every quoted output below was executed against a fresh repo on
basicly 0.8.0, with that repo's generated bead-id prefix swapped for `myrepo`.

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
| [`br`](https://github.com/Dicklesworthstone/beads_rust) 0.2.16+ | The work graph the loop derives every phase from | `br --version` |

Do this on a throwaway repo the first time. The loop rewrites hooks, adds
generated files, and makes commits; you want to see all of that somewhere you
do not mind.

Every command below is written as bare `basicly`. Run it as the pinned form so
you always get the version you chose:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.8.0 basicly <args>
```

## Step 1 — install the harness

From the repo root:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.8.0 basicly install
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
engine: basicly 0.8.0
repo: consumer
catalog: installed by basicly 0.8.0 at ... (matches engine)
drift: generated files up to date
Hooks
┏━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ manager ┃ specs ┃ projection ┃ activation ┃
┡━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ git     │ 11    │ in sync    │ installed  │
│ claude  │ 2     │ in sync    │ -          │
│ copilot │ 1     │ in sync    │ -          │
└─────────┴───────┴────────────┴────────────┘
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
catalog lint: routing: rank-1 rate 80/87 = 92.0% (no floor declared)
catalog lint: FAILED
  no rank-1 floor declared — set `[catalog] rank1_floor` in basicly.toml below the measured baseline (currently 92.0%)
```

Take the number it just measured for you and set the floor a little under it, in
`basicly.toml`:

```toml
[catalog]
rank1_floor = 0.90
```

That floor is a ratchet: adding a skill whose description collides with an
existing one drops the rate and fails the commit.

## Step 3 — file the bead you are about to work on

Nothing commits in this repo without a bead id — the `beads-commit-msg` hook
refuses it, install output included:

```text
ERROR: Commit message does not reference a beads issue id.
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
br create "Add a getting started note" --type task --priority 2 -d '## Acceptance Criteria

- Given a reader who opens the repo when they read NOTES.md then a one line getting started note is present

## Scope

- `NOTES.md`
'
```

```text
✓ Created myrepo-etk: Add a getting started note
```

Check the gate agrees:

```sh
basicly policy dor myrepo-etk
```

```text
DoR: READY (myrepo-etk)
```

If it says `NOT READY` it names the missing heading and repeats the scaffold
command; fill that section in with `br update myrepo-etk -d '...'`.

Now the install output has an id to reference, so commit it:

```sh
git add -A
git commit -m "chore: install basicly (myrepo-etk)"
```

Every hook should report `Passed`.

## Step 4 — ask the loop where the bead stands

```sh
basicly loop status myrepo-etk
```

```text
issue:       myrepo-etk (task, open)
phase:       intake
worktree:    (none)
gates:       advance BLOCKED
  missing:   verify
checkpoints: (none)
rework:      verify=0
ready set:   myrepo-etk
blocked:     (none)
```

Nothing recorded this phase anywhere — it was *derived* from the tracker just
now. That is why this command is the right first move in any session,
including one that starts after a crash or on a different machine.

## Step 5 — intake and classify

One command drives a whole phase *boundary*, resolving what it may on the way:

```sh
basicly loop run myrepo-etk --work-type task --runner manual
```

`--work-type task` is your call, and it decides the shape of everything after:
a `task` (or `bug`/`chore`) builds directly, a `feature` stops and demands a
child plan. Classify by shape, not ambition — one coherent change is a `task`.

`--runner manual` means **you** write the code in the worktree. Leave it off and
the harness dispatches a headless coding agent instead, which meters spend and
needs an autonomy grant; see
[run several lanes in parallel](../how-to/run-parallel-lanes.md).

The command stops at a human checkpoint:

```text
[blocked] intake -> intake: recorded work type 'task'; classify checkpoint awaiting approval
checkpoint classify: CONFIRMATION REQUIRED (myrepo-etk)
  Approving records the work type and provisions a worktree. No code changes yet.
  ...
  basicly loop run myrepo-etk --work-type task --runner manual --confirm 348a269b
```

The confirm code is one-time and expires in 15 minutes. Re-run the **whole
reprinted command** — the code belongs on that command, not on a bare
`policy checkpoint --approve`, which approves the checkpoint and leaves the loop
parked:

```sh
basicly loop run myrepo-etk --work-type task --runner manual --confirm 348a269b
```

```text
Created worktree 'myrepo-etk'
  path:   /path/to/myrepo.worktrees/myrepo-etk
  branch: harness/myrepo-etk  (base main @ 5d349d1)
  .beads/redirect: tracker shared with the base checkout
  hooks: pre-commit, commit-msg, pre-push
[blocked] classify -> classify: worktree 'myrepo-etk' provisioned; awaiting the agent's work
```

Three things just happened that are worth understanding:

- The work is isolated in a **sibling** worktree — `../myrepo.worktrees/…`,
  never inside the repo, so whole-tree gates and file watchers do not see it.
- `.beads/redirect` points that worktree's tracker at the base checkout's. There
  is one work graph; a `br` command from either side hits it.
- The engine committed the claim to git *before* creating the worktree, so the
  claim is in history from the moment work starts.

## Step 6 — do the work, and commit it on the branch

```sh
cd ../myrepo.worktrees/myrepo-etk
printf '# Notes\n\nRun `basicly status` to see what the harness installed.\n' > NOTES.md
git add NOTES.md
git commit -m "docs: add a getting started note (myrepo-etk)"
```

**The loop never commits your work for you.** Landing rebases the branch, so an
uncommitted change (or a branch with no commit) makes the next step stop with
*commit the work on `<branch>` before landing*.

## Step 7 — land, verify, ship

Go back to the base checkout — landing mutates it, and running from inside the
worktree strands the merge:

```sh
cd ../../myrepo
basicly loop run myrepo-etk --runner manual
```

```text
[merged] build -> verify: merged harness/myrepo-etk into main @ d605fb4
[blocked] verify -> verify: ship checkpoint awaiting human approval
checkpoint ship: CONFIRMATION REQUIRED (myrepo-etk)
  The merge to the base branch has ALREADY happened, at the build->verify landing.
  Approving tears down the worktree and closes the bead. It publishes nothing,
  pushes nothing, and creates no tag or release.
  ...
  basicly loop run myrepo-etk --runner manual --confirm 477a4f2f
```

That single advance rebased the branch, ran the verify gate, recorded the
result on the bead, and merged. Read the `[merged]` line, not the exit code —
`loop run` exits non-zero at *every* checkpoint, which is not a failure.

Your verify gate passed vacuously here: a fresh `basicly.toml` declares no
checks, so there was nothing to run. Before you use this on a real repo, wire
your tests in — [wire up the verify gate](../how-to/wire-up-the-verify-gate.md).

Approve the ship:

```sh
basicly loop run myrepo-etk --runner manual --confirm 477a4f2f
```

```text
Cleaned up worktree 'myrepo-etk' (worktree + branch + metadata).
checkpoint ship: APPROVED (myrepo-etk)
[tore-down] ship -> done: worktree torn down and issue closed; cost rollup recorded; tracker state committed
```

## What you just built

```sh
git log --oneline -6
```

```text
4df2f2b chore(beads): close the shipped track (myrepo-etk)
d605fb4 chore(worktree): merge a harness worktree back to its base
1fccce5 docs: add a getting started note (myrepo-etk)
c0a8fc1 chore(beads): sync tracker state for the harness loop (myrepo-etk)
5d349d1 chore(beads): record the claim before provisioning (myrepo-etk)
6ee6f7f chore: install basicly (myrepo-etk)
```

You wrote two of those six — the install commit and the change itself. The
engine wrote the merge and the three `chore(beads)` commits, at the claim, the
landing, and the close, which is why you never `git add .beads` yourself on
loop-tracked work.

```sh
basicly loop status myrepo-etk
```

The fields that moved since Step 4:

```text
issue:       myrepo-etk (task, closed)
phase:       done
worktree:    myrepo-etk on harness/myrepo-etk
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
