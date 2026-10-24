# How to run several lanes in parallel

One `feature` or `epic`, many children, one worktree per child, a **serial merge
queue** at the end. This is the path `basicly loop run` does not cover — it
drives a single bead.

Prerequisite: you have shipped a single track first
([tutorial](../tutorial/first-loop.md)). Everything below assumes you know what
a phase transition and a landing look like.

## 1. Write the child plan

Children come from a plan file, not from the model's memory. The **plan gate**
requires a **list** of acceptance criteria, a scope, a declared dependency list and an end-to-end demonstration.
The engine derives required verification from scope. A token limit is optional and comes only from an explicit user request.

```toml
[[children]]
title = "Add an install note"
acceptance = ["Given a reader when they open docs/install.md then the install command is shown"]
scope = ["docs/install.md"]
depends_on = []
demonstration = "open `docs/install.md` and run the command it shows"

[[children]]
title = "Add a loop note"
acceptance = ["Given a reader when they open docs/loop.md then the loop commands are shown"]
scope = ["docs/loop.md"]
depends_on = []
demonstration = "run `basicly loop status <issue>` as `docs/loop.md` documents it"
```

- `depends_on` names **sibling titles**, not issue ids — the plan is written
  before anything is created. Write `depends_on = ["Add an install note"]` and
  that edge is recorded as a `blocks` dependency on the tracker, so `basicly tracker blocked`
  carries the ordering even between children whose scopes never overlap and
  nothing would otherwise serialize. `[]` is a declaration ("nothing blocks
  this"); omitting the key is not, and is refused.
- `budget_tokens` optionally carries an explicit user limit. Omit it when no limit was requested.
- Historical `integrity` metadata is optional. The engine selects required checks from the actual scope.
- `demonstration` is how the child is exercised end to end — a command to run, a
  request to make, or a test that goes through the consumer surface — with the
  runnable part in backticks. A child that cannot name one has no
  consumer-visible behaviour yet, which means it was sliced horizontally ("add
  the model", "add the service", "add the CLI") and nothing at verify can derive
  a check for it. Splitting it the other way is cheap here and expensive later,
  which is why the refusal is at plan time.

`acceptance` is a list even with one entry; a bare string is refused:

```text
Error: children[0] 'acceptance' must be a non-empty list of non-empty strings
```

A missing required field is refused before the engine creates children. Optional spending and historical classification fields do not require user input.

A demonstration is refused twice over — once for being absent, and once for
naming nothing anybody could run:

```text
Error: children[0] ('Add a loop note') declares no demonstration; a child that cannot name how it is exercised end to end has no consumer-visible behaviour for a check to be derived from, so split the plan differently rather than describing this one
Error: children[0] ('Add a loop note') declares a demonstration naming nothing runnable ('the docs will describe the loop'); name the command to run, the request to make or the test that exercises it through the consumer surface, backticked, as a scope glob is
```

So is a cycle in the declared graph, naming its members:

```text
Error: the declared dependency graph has a cycle through a -> b -> a
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
  The grouping reads **scopes only**, so it is an upper bound: a `depends_on`
  you declared also orders two lanes, and the group count does not show it —
  read `basicly tracker blocked` after creating them for the ordering you will get.
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

Read the final `VERDICT` and each named blocker. Preflight writes nothing.
Run it from the base checkout so it can read recorded usage and forecast evidence.

`lanes: 0 dispatchable now` can mean that worktrees are not provisioned yet.
The supervisor provisions from the ranked open children when the pass starts.

## 4. Clear what the verdict named

Commit unrelated base changes before landing. The engine owns the tracker commits for loop work.
Resolve readiness, dependency, scope and work-in-progress refusals using the details in the verdict.
The task request already authorizes normal classification, decomposition and completion within that scope.
Do not request an L-level grant or mandatory budget to clear those transitions.

If the user explicitly requests a token ceiling, record it:

```sh
basicly policy budget myrepo-9vf --tokens 30000000
```

The number is an example, not a default budget. Without an explicit limit, omit that command.
Observed usage and forecasts remain available without turning either into consent questions.
An explicit exhausted limit remains a blocker until the user changes or removes it.

## 5. Run the pass

```sh
basicly loop supervise myrepo-9vf
```

It provisions worktrees, dispatches the unblocked lanes up to `[worktree]
concurrency`, routes each outcome, and lands green work through a **serial**
merge queue. Re-run preflight and re-run supervise until it reports done.

Confirm a landing from the `[merged]` line, never from the exit code —
`supervise` can stop at a named missing fact or failed gate. Read the
run's own summary block; `grep`-ing the output for `[merged]` hides a failure
whose message you did not predict.

## 6. Declare the files every lane touches

Two config declarations prevent the collision class that kills unattended runs:

```toml
[worktree]
append_only_paths = ["CHANGELOG.md"]

[worktree.regenerate_commands]
".basicly/generated-manifest.json" = ["basicly", "build"]
"docs/plan.md" = ["python", "gen_plan.py", "--fix", "--block", "counts"]
```

- **`append_only_paths`** — a file your convention has every lane append its own
  entry to, which therefore appears in no child's `## Scope`. Declaring it
  *serializes* the lanes that would collide there and makes preflight warn
  (`contend:`). Without it the collision is invisible until the merge queue
  bounces the later lanes — one rework attempt each.
- **`[worktree.regenerate_commands]`** — artifacts that are a function of the
  tree (a manifest, a lockfile, a generated block). A landing rebase whose
  conflicts are *all* keyed here is resolved by re-running **each path's own**
  command in the lane's worktree; a conflict touching anything else still
  bounces to the lane. A path only partly generated is safe to declare: if the
  rebuild leaves a conflict marker behind — the conflict was in the
  hand-authored half — the landing bounces instead of staging it.

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
