# How to resume or hand over a track

The loop stores **no side-state**. Every phase is derived from the work graph at
the moment you ask, so resuming after a crash, a context compaction, a machine
change, or a switch from one coding agent to another is a *read* — never a
replay. There is no "resume" command because there is nothing to restart.

## Start every session with these three reads

```sh
basicly tracker ready
```

```text
Ready (4, priority ASC, dependents DESC, id ASC)
┏━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ rank ┃ score ┃ record     ┃ title                       ┃
┡━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1    │ 3002  │ myrepo-oxg │ Adopt basicly in this repo  │
│ 2    │ 3001  │ myrepo-9vf │ Document the probe repo     │
└──────┴───────┴────────────┴─────────────────────────────┘
```

The rank is priority first, then how many records depend on it. `basicly tracker blocked`
answers the other half — what is *not* ready, and which open record holds each one.

```sh
basicly loop status myrepo-9vf.1
```

```text
issue:       myrepo-9vf.1 (task, open)
phase:       intake
worktree:    (none)
gates:       advance BLOCKED
  missing:   verify
checkpoints: (none)
rework:      verify=0
ready set:   myrepo-oxg, myrepo-9vf
blocked:     (none)
```

```sh
basicly worktree list
```

```text
No worktree sessions.
```

Those three answer: what is actionable, what phase this one is parked in and
what it waits on, and what is still provisioned. Reconcile them and continue —
that is the whole procedure.

## Reading the phase you land in

| `phase:` | What it is waiting for | Next command |
| --- | --- | --- |
| `intake` | your work-type call | `basicly loop run <id> --work-type task` |
| `classify` with `worktree: (none)` | the classify checkpoint | re-run the reprinted command with `--confirm <code>` |
| `classify` with a worktree bound | the coding — the branch has no commit yet | do the work and `git commit` it on the branch |
| `build` | the landing | `basicly loop run <id>` from the base checkout |
| `verify` | the ship checkpoint | check `[merged]` happened, then relay the ship code |
| `done` | nothing; it is closed | — |

A track parked at `classify` with a live worktree is the common resume case:
the previous session provisioned it and stopped. Check whether the work is
already there before redoing it:

```sh
git -C ../myrepo.worktrees/myrepo-9vf-1 status --short
git -C ../myrepo.worktrees/myrepo-9vf-1 log --oneline main..HEAD
```

A worktree name replaces dots with hyphens: `myrepo-9vf.1` provisions
`myrepo-9vf-1`.

## Handing over to a different agent

Because state lives in the tracker rather than in a session, a track can start
under Claude and finish under Codex or Copilot. The new agent reads the same
projected instructions and the same work graph. Nothing else transfers — and
nothing else needs to.

Change the runner for a single invocation without editing committed config:

```sh
basicly loop run myrepo-9vf.1 --runner manual
```

`manual` restores the human handoff: the loop provisions and lands, you write
the code.

## Before any tracker write on a checkout you did not just leave

```sh
uv run python .basicly/core/kit/tracker/fsck.py .basicly/ledger
```

The ledger is one append-only log, committed and carried by git, so a checkout is
either up to date with its branch or it is not — there is no second copy to reconcile
and no import to run first. `fsck` answers whether the log on disk is internally
consistent; `git status` answers whether it is the branch's.

That is the whole of it, and it replaced a real hazard: the store this superseded kept a
database beside its export and auto-flushed the *older database over the newer file* on
any mutating command — measured once at 187 records deleted, 47 of them open, with the
create reporting success and no gate firing.

## Do not commit tracker state yourself

On loop-tracked work the engine makes the tracker commits, at three points: the
claim before provisioning, the accumulated ledger dirt rolled up at
landing, and the closing state at ship. So never `git add .basicly/ledger` on a harness
branch. One worktree shares the base checkout's tracker through a git-ignored
the ledger `redirect`, so there is no worktree copy to diverge and nothing to
reconcile.

## A worktree that outlived its track

```sh
basicly worktree list
```

Stale sessions are marked. Remove one, with its merged branch:

```sh
basicly worktree cleanup <name>
```

Only do that once `basicly loop status` shows the bead closed, or you have
confirmed the branch landed — the worktree is where the work is.
