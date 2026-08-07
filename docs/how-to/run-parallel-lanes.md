# How to run several lanes in parallel

One `feature` or `epic`, many children, one worktree per child, a **serial merge
queue** at the end. This is the path `basicly loop run` does not cover — it
drives a single bead.

Prerequisite: you have shipped a single track first
([tutorial](../tutorial/first-loop.md)). Everything below assumes you know what
a checkpoint and a landing look like.

## 1. Write the child plan

Children come from a plan file, not from the model's memory. Each entry is a
title, a **list** of acceptance criteria, and a scope:

```toml
[[children]]
title = "Add an install note"
acceptance = ["Given a reader when they open docs/install.md then the install command is shown"]
scope = ["docs/install.md"]

[[children]]
title = "Add a loop note"
acceptance = ["Given a reader when they open docs/loop.md then the loop commands are shown"]
scope = ["docs/loop.md"]
```

`acceptance` is a list even with one entry; a bare string is refused:

```text
Error: children[0] 'acceptance' must be a non-empty list of non-empty strings
```

## 2. Preview the grouping before you create anything

```sh
basicly decompose myrepo-9vf --plan plan.toml --dry-run
```

```text
decompose (dry-run): 2 children in 2 parallel group(s)
  [group 0] #0 Add an install note
      scope: docs/install.md
  [group 1] #1 Add a loop note
      scope: docs/loop.md
sizing (D8 working-set band):
  Add an install note: 2742 tokens (scope 0 x build factor + overhead)
forecast spend (model unresolved):
  Add an install note: 916925 tokens, $0.67, 1 min — prior (0 paired record(s) for task)
verdict: within band
```

Read three things off it:

- **Parallel groups.** Children whose scopes overlap land in one group and
  run serially. Two groups of one is maximum parallelism; one group of six
  means the scopes collide and you are paying for isolation you do not get.
- **Sizing.** Each child is estimated from what its scope globs actually match.
  `scope 0` means the globs matched no file yet — greenfield, or a broken path.
- **The band verdict.** Only `REFUSED - too large, split it` blocks a dispatch.
  A refusal is a property of the *files* the scope names, not of the size of
  your change: a one-line fix in a huge module estimates the same as a rewrite.

Then create them:

```sh
basicly decompose myrepo-9vf --plan plan.toml
```

```text
decompose: created 2 children under myrepo-9vf in 2 parallel group(s)
  group 0: myrepo-9vf.1
  group 1: myrepo-9vf.2
```

## 3. Preflight — always, it writes nothing

```sh
basicly loop preflight myrepo-9vf
```

```text
config:    recognised
base:      DIRTY - 7 path(s); a landing will refuse until committed
worktrees: 0 live
runner:    claude (headless), timeout 3600s
grant:     NONE - every checkpoint is human
budget:    MISSING - the 'claude' runner meters spend and no budget covers it
checkpts:  decompose UNAPPROVED - blocks provisioning: the root's own advance provisions the lanes and resolves no checkpoint itself
           approve: basicly policy checkpoint myrepo-9vf decompose --approve
lanes:     0 dispatchable now, 2 open child(ren)
per-lane:  4000000 tokens assumed for an unsizeable lane (seed)
forecast:  ~8000000 tokens if all 2 lanes start (per-lane x min(cap 5, 2 open))
band:      8000..132000 working-set tokens
  myrepo-9vf.2      unsized      no scope the estimator can read
contend:   no append-only path declared ([worktree] append_only_paths)
regen:     no generated path declared ([worktree] generated_paths)
VERDICT:   not ready - base checkout is dirty; a metered runner needs a grant with a token budget; the root's decompose checkpoint blocks provisioning
```

That one screen answers every question a failed pass would have charged you for.
Run it **from the base checkout** — `.basicly/usage/` is git-ignored, so a
worktree sees no run records and the forecast loses its measured prior.

`lanes: 0 dispatchable now` before any worktree exists is normal: it counts
*adopted* lanes, and the supervisor provisions from the ranked open children at
pass start.

## 4. Clear what the verdict named

**Dirty base**: commit it. Every grant, checkpoint and answer writes a tracker
marker that dirties `.beads/issues.jsonl`, so expect to commit between steps.

**The root's decompose checkpoint**: a covering grant does not serve this one.
Approve it once, with the command preflight printed:

```sh
basicly policy checkpoint myrepo-9vf decompose --approve
```

**Budget**: a metered runner needs a grant carrying a token ceiling.

```sh
basicly policy grant myrepo-9vf --level L3 --token-budget 30000000
```

Size that number off recorded dispatches, never off how big the work looks —
`mean tokens per lane × lanes`, plus headroom for **one** lane that dies at its
runner timeout, because a killed lane still spends everything it burned before
the kill:

```sh
uv run python -c "
import json, statistics
d = json.load(open('.basicly/usage/run-records.json'))
ok = [r.get('tokens') or 0 for rs in d.values() for r in rs
      if r.get('agent') != 'manual' and r.get('outcome') == 'executed']
print('lanes', len(ok), 'mean', f'{statistics.mean(ok):,.0f}', 'max', f'{max(ok):,}')"
```

Two failure modes are recorded, both from real runs. *"I will do the coding
myself" is not a budget* — `supervise` dispatches a metered runner regardless;
the grant level covers the **checkpoints**, it does not decide who writes the
code. And the ceiling **cannot stop a dispatch it has already started**: spend
is consulted before a pass and recorded after it, so budget for the overshoot
instead of expecting the ceiling to catch it.

## 5. Run the pass

```sh
basicly loop supervise myrepo-9vf
```

It provisions worktrees, dispatches the unblocked lanes up to `[worktree]
concurrency`, routes each outcome, and lands green work through a **serial**
merge queue. Re-run preflight and re-run supervise until it reports done.

Confirm a landing from the `[merged]` line, never from the exit code —
`supervise` exits non-zero at any checkpoint, which is not a failure. Read the
run's own summary block; `grep`-ing the output for `[merged]` hides a failure
whose message you did not predict.

## 6. Declare the files every lane touches

Two config keys prevent the collision class that kills unattended runs:

```toml
[worktree]
append_only_paths = ["CHANGELOG.md"]
generated_paths = [".basicly/generated-manifest.json"]
regenerate_command = ["basicly", "build"]
```

- **`append_only_paths`** — a file your convention has every lane append its own
  entry to, which therefore appears in no child's `## Scope`. Declaring it
  *serializes* the lanes that would collide there and makes preflight warn
  (`contend:`). Without it the collision is invisible until the merge queue
  bounces the later lanes — one rework attempt each.
- **`generated_paths` + `regenerate_command`** — artifacts that are a function
  of the tree (a manifest, a lockfile). A landing rebase whose conflicts are
  *all* in this list is resolved by re-running the command in the lane's
  worktree; a conflict touching anything else still bounces to the lane. Both
  keys or neither.

Serializing is the second-best answer. Where the shared file can be split —
one changelog fragment per bead instead of one `CHANGELOG.md` — split it, and
the collision becomes impossible rather than merely detected.

## Watching a lane

A dispatched lane is a subprocess of the engine, not a subagent of your session.
Four reads answer everything:

```sh
basicly loop status <id>
basicly worktree list
git -C <repo>.worktrees/<name> status --short
git -C <repo>.worktrees/<name> log --oneline main..HEAD
```

A worktree name replaces dots with hyphens: `myrepo-9vf.1` provisions
`myrepo-9vf-1`. Watching the dotted path reports "no worktree" forever.

Do **not** poll for the process. `pgrep -f "loop run <id>"` matches the waiter's
own command line, so a `until ! pgrep …` loop can never exit and reports the job
as still running long after it succeeded — and `pkill -f` on the same pattern
kills the invoking shell instead of the target. The loop keeps no side-state, so
`loop status` plus the worktree's git state is the authoritative read.
