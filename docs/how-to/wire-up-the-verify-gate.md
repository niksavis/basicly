# How to wire up the verify gate

A fresh `basicly.toml` declares **no** verify checks, and an empty gate passes
vacuously:

```text
No verify checks configured for mode 'fast' in basicly.toml; nothing to gate.
```

The loop still records `verify: pass` and still ships the bead. Until you
declare the checks your stack actually has, the required gate proves nothing.
This is the first configuration a real repo owes.

## Declare the checks

Each check is a `[[verify.checks]]` table in `basicly.toml`: a name, an argv
list (no shell — the command is not string-parsed), and the modes it runs in.

```toml
[[verify.checks]]
name = "ruff"
command = ["uv", "run", "ruff", "check"]
modes = ["fast", "full", "staged"]
staged_suffix = ".py"

[[verify.checks]]
name = "ruff-format"
command = ["uv", "run", "ruff", "format", "--check"]
fix_command = ["uv", "run", "ruff", "format"]
modes = ["fast", "full", "staged"]
staged_suffix = ".py"

[[verify.checks]]
name = "pytest"
command = ["uv", "run", "pytest", "-q"]
modes = ["full"]
```

## The three modes, and which one runs when

| Mode | Runs | Put here |
| --- | --- | --- |
| `staged` | the pre-commit hook, against staged files of `staged_suffix` | lint and format — fast, file-local |
| `fast` | `basicly verify`, and any phase re-running the checks | lint, type check, a quick unit subset |
| `full` | the build→verify landing (`--mode full` is the loop's default) | the whole suite |

A `staged` check with no `staged_suffix` runs against everything staged. A
configured command that is missing from `PATH` fails the run with a one-line
message rather than being skipped — a check you cannot run is not a check that
passed.

## Run it

```sh
basicly verify --mode fast
```

```text
============================================================
  notes-present: PASS
[verify] PASS (mode: fast)
```

On a failure it names the check and exits non-zero:

```text
  notes-present: PASS
  always-fails: FAIL
[verify] FAIL: always-fails
```

`--fix` applies each check's `fix_command` before the checks run. Keep those
strictly mechanical and lossless — a formatter, an import sorter. The check
still runs afterwards either way, so a `fix_command` never turns a red into a
green by itself.

## How the gate reaches a bead

You almost never record it by hand. The build→verify `loop advance` is the only
step that merges a worktree back, and it runs verify against the rebased tree
and reports the gate itself:

```text
[merged] build -> verify: merged harness/myrepo-etk into main @ d605fb4
```

Recording the gate out of band during build (`basicly verify --issue …`) makes
the derived phase jump to verify **with the merge skipped**, and the loop then
ships and closes the bead with the code stranded on the branch. Record it
manually only *after* a landing has merged — re-verifying after rework, from the
base checkout:

```sh
basicly verify --mode full --issue myrepo-etk
```

Inspect what the loop will decide on:

```sh
basicly policy gate myrepo-etk
```

## Required versus advisory

`[policy] required_gates` decides which gate names block an advance. The default
is one:

```toml
[policy]
required_gates = ["verify"]
max_rework = 2
```

Any recorded gate *not* in that list is advisory — it is visible on the bead and
never blocks. That is the line between deterministic checks (tests, lint, types:
required) and judged output from an AI reviewer (advisory, always). A failed
required gate sends the node into the bounded rework loop, `max_rework` attempts
before it escalates to a human:

```sh
basicly policy rework myrepo-etk --gate verify
```
