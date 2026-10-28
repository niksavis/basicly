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
| `staged` | `basicly verify --mode staged`, with suffix-matching staged paths appended | file-local lint and format |
| `fast` | pre-commit, a local pre-push, or `basicly verify --mode fast` | lint, type check, a quick unit subset |
| `full` | ordinary `basicly verify`, pre-push under continuous integration, and loop landings | the whole suite |

A `staged` check without `staged_suffix` receives no filenames and uses its
configured command's scope. Add `--affected` to validate index contents from the
CLI; ordinary verification reads the working tree. A configured command that is
missing from `PATH` fails the run with a one-line
message rather than being skipped — a check you cannot run is not a check that
passed.

## Scope a check to what it reads

A check may declare the files it reads, as repo-relative globs:

```toml
[[verify.checks]]
name = "lint-imports"
command = ["uv", "run", "lint-imports"]
modes = ["fast", "full"]
inputs = ["src/**/*.py", ".importlinter"]
```

The pre-commit hook then skips that check, by name, on a commit whose staged diff
matches none of its globs — a docs-only commit stops paying for the type checker.
Nothing else reads the key: `full`, the mode a landing and a push run, runs every
declared check whatever it says about its inputs, so a skip can never be what a
green landing rested on.

Two rules keep that safe. A check declaring no `inputs` always runs, so the key is
opt-in per check and a repo that adds none keeps today's behaviour. And an
undeterminable diff runs everything: git failing to report the staged paths, or an
empty index, skips nothing.

Derive the list from the tool's own scope rather than guessing it. A list *narrower*
than what the check really reads costs a bounced landing — the gate the commit
skipped fails on the rebased tree instead. A glob that matches nothing is worse,
because it skips the check on every commit there is; this repo pins its own lists
against `git ls-files` in
`tests/test_git_hooks/test_check_runner.py::test_every_declared_input_glob_matches_a_tracked_file`.

## Select checks with a consumer-owned dependency graph

An optional provider can select existing checks from a graph your repository
already owns. basicly does not infer language dependencies from this declaration:

```toml
[verify.affected]
command = ["python3", ".scripts/affected-checks.py"]
checks = ["ruff", "ruff-format"]
timeout_seconds = 30
```

The named checks must already exist. Checks outside this list always run when
the provider is configured. Git pre-commit uses the provider inside its staged
snapshot. To request the same behavior explicitly:

```sh
basicly verify --affected
```

This command reads configuration and executes checks in a temporary copy of the
index. An unstaged repair cannot hide a staged failure. It preserves the original
index and working tree. `--affected` cannot combine with `--fix` or `--issue`;
use ordinary verification to repair files or record a required gate.
Ordinary verification and loop landings retain broad checks. A local pre-push runs the
`fast` checks whose inputs match a file that the pushed commits change; when it cannot
name those files, it runs every `fast` check. With the `CI` environment variable set,
pre-push runs the `full` checks.
Existing installations need no configuration changes when upgrading.

The provider receives one JSON document on stdin:

```json
{
  "schema": "basicly-affected/v1",
  "snapshot_tree": "<Git index tree id>",
  "base_tree": "<HEAD tree id>",
  "changes": [
    {"status": "R100", "old_path": "old.py", "path": "new.py"}
  ],
  "checks": ["ruff", "ruff-format"]
}
```

It returns that request unchanged, with four additional fields:

```json
{
  "complete": true,
  "selected": ["ruff", "ruff-format"],
  "reasons": {"ruff": "changed Python owner", "ruff-format": "changed Python owner"},
  "unverified": []
}
```

The response must echo the complete request, including the change list.
Explain every requested check, including checks left unselected. Additions carry
`path`; deletions carry `old_path`; modifications carry both; renames and copies
retain both names. The provider must account for shared configuration, lockfiles,
dependents and unknown ownership using its actual graph. Return `complete: false`
or nonempty `unverified` when it cannot answer. basicly validates the protocol and
snapshot binding; graph completeness remains a claim of the consumer's provider.

Missing, invalid, stale, failed or timed-out responses run all checks in the
requested mode and report the fallback. Responses over one MiB also fall back.
Provider execution is bounded by the configured timeout, at most 300 seconds.
Other untracked inputs prevent selective execution. The prepared `.venv`,
`node_modules`, `build/lefthook` and `.basicly/usage` links are runtime metadata.
A provider that changes snapshot inputs fails verification before checks run.

Git writes selection and command outcomes to
`.basicly/usage/affected-run.json`. The CLI records selection, fallback reasons,
executed checks and skipped checks in `.basicly/usage/verify-run.json`.
The selection records the provider request and bounded stdout/stderr diagnostic
paths under `.basicly/usage/affected-provider.*`.
Unselected checks did not execute; they are not cached passes. This integration
adds no build-system dependency or persistent result cache.

Maintainers can validate a built wheel outside this checkout:

```sh
BASICLY_BASELINE_WHEEL=/path/to/released.whl BASICLY_VALIDATION_WHEEL=/path/to/candidate.whl uv run python .scripts/validate_affected_consumer.py
```

Provide prepared wheels and offline Python dependencies. The script downloads
nothing. It exercises native commits, partial staging, graph dependents,
unrelated checks, foreign hooks, fresh installation and upgrade. It reports
command counts and three warm hook samples separately from repository setup.
Its small Python fixture does not establish mixed-stack scaling performance.

## Reuse a proven compiler result during landing

Legacy configurations need no changes. Checks run fresh unless they declare a
reviewed recipe:

```toml
[[verify.checks]]
name = "pyright-linux"
command = ["pyright", "--pythonplatform", "Linux"]
modes = ["full"]
reuse = "pyright"
```

The loop carries the actual successful report from its pre-merge verification.
During post-merge verification, it can reuse a matching compiler result.
Ordinary `basicly verify` executes its checks afresh by default.
The `inputs` globs above select staged checks and cannot establish reusable proof.

The initial recipe supports bundled Pyright 1.1.411 through its reviewed POSIX console launcher
and an existing global Node executable. It supports the Linux, Darwin and Windows
analysis targets. Windows hosts and unsupported launcher strategies run fresh.
The project must configure `.venv` under its checkout in `pyproject.toml`.
External search paths, configuration inheritance and other unsupported settings
also run fresh.

The receipt binds the command, mode, source names and contents, configuration,
analysis environment, tool files, interpreter, Node executable and environment.
It inventories Python sources and stubs, including untracked files, to detect new
imports and shadowing. It excludes Git metadata and virtual environments from
source discovery. Python files inside Node dependencies and bytecode directories
remain inputs because explicit imports can reach them.
Analysis search paths into excluded directories refuse reuse.
Protected dotenv files are never read. Protected source inputs refuse reuse.

The engine compares inputs before and after verification. A missing, failed,
unsupported or changed receipt executes the check afresh. If another check
changes inputs during post-merge verification, the borrowed checks execute again.
Without an enabled cache, receipts stay within one process and one landing.
Other checks, including tests, execute afresh.

### Reuse compiler results across commands

Enable the repository-local cache explicitly:

```toml
[verify]
cache = true
```

Checks need a reviewed reuse recipe: `reuse = "pyright"` above, or the
ledger-independent pytest recipe below. The cache is
bounded and stored in ignored `.basicly/usage/verify-cache.json`. The CLI and
loop use the same cache. With caching enabled, pre-push uses the engine available
in the project's environment when its full check commands and fail-fast setting
match the native hook. It revalidates inputs before reusing compiler evidence.
The hook compares recorded compiler time with recorded verification overhead;
without a predicted saving, it keeps fresh execution instead of paying to prove
reuse for cheaper checks. This cost hint never establishes a passing result.
An unavailable or incompatible engine keeps fresh native execution; a failed
engine verification blocks the push without a fallback rerun. Consumer hooks
still work without an installed engine. The persistent cache does not transfer
between checkout paths.

The opted-in Pyright recipe executes with a controlled environment: home,
temporary-directory, locale, Python, Node and Pyright
variables, plus a PATH limited to the resolved compiler and Node directories.
The fingerprint covers that actual environment. Hook metadata and changing host
proxy variables never reach this compiler execution. The ambient `VIRTUAL_ENV`
marker is also removed when the project explicitly selects its reviewed analysis
environment through `.venv`; the reviewed launcher selects the compiler interpreter.
Unsupported configurations keep the marker and run fresh.
This lets one landing transfer receipts between equivalent checkouts using the
same compiler runtime, even when the harness scrubs that marker at the checkout
boundary. Different analysis environments or compiler runtimes still run fresh.
The persistent cache remains local to each checkout. Other checks retain their
inherited environment. Remove `reuse = "pyright"` if a custom compiler setup
requires the full inherited environment.
An engine or repository identity change, corrupt cache, unsupported input or
changed fingerprint runs the compiler freshly. Only stable, successful
verification writes cache entries. Isolated engine installations fingerprint
the actual Pyright interpreter independently from the engine's interpreter.

To execute every check freshly for one command:

```sh
basicly verify --mode full --no-cache
```

This command leaves existing cache evidence intact. Reused outcomes name the
original run and its compiler duration; their current execution duration is zero.
The run's total duration includes fingerprinting and checks that ran freshly.
Compiler reuse does not establish faster tests or constant-time repository scaling.

### Reuse the code test suite across tracker updates

This repository opts its code suite into `reuse = "pytest"` with this exact
reviewed full-mode command:

```toml
[[verify.checks]]
name = "pytest"
command = ["pytest", "-q", "-n", "{workers}", "--durations=25", "-m", "not live_ledger and not fresh_runtime"]
reuse = "pytest"
modes = ["full"]
```

Its repository-copy fixture uses a fixed ledger under `tests/fixtures/work_repo/ledger`.
Tests that read the current ledger carry `live_ledger` and run in the separate,
uncached `pytest-live-ledger` check. Packaging tests carry `fresh_runtime` and
run in that same mandatory fresh group, with inherited proxy settings. The test guard refuses a live-ledger file
open by an unmarked test. The union of both commands retains the entire suite.
Run the ordinary `pytest` command to execute both groups together.

The pytest proof includes tracked and nonignored untracked files outside the
live ledger, including documentation, test fixtures and configuration. It also
includes supplied Lefthook archives, Node dependencies, the pytest launcher,
Python executable, installed Python packages, standard library, environment,
Git configuration, tags and the resolved Git, uv, Node and shell executables. Inputs must stay unchanged until verification ends.
Only a passing complete verification saves evidence. The controlled pytest
environment removes hook-dispatch, per-tool call identifiers, nested-launch
bookkeeping and proxy URL variables (`*_PROXY`, case-insensitive, except
`NO_PROXY`). It deduplicates PATH entries before both hashing and execution.
The cached suite sets `UV_OFFLINE=1` and is for tests that do not need external
network access; local
HTTP tests still run. Proxy values can rotate on every agent tool call, so
removing them from the actual test environment prevents false cache misses.
`NO_PROXY`, certificate settings, Python options and other material environment
inputs remain part of the proof. Remove `reuse = "pytest"` for a suite that
needs inherited proxy settings: ordinary checks keep their inherited environment.
The reviewed suite applies the hook runtime defaults when their variables are absent.
These defaults are `UV_PYTHON=>=3.14`, `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`.
Fingerprinting and execution use the same defaults, so equivalent CLI and hook runs can share evidence.
Explicit values remain unchanged and affect the proof.
Unsupported commands and modes also retain their ordinary environment. The
previous command selecting only `not live_ledger` now runs fresh; update it to
the two-marker expression above after classifying external-runtime tests.

This recipe supports the reviewed POSIX launcher using the current interpreter;
unsupported launchers, protected inputs, missing files and changed inputs execute
fresh. It is opt-in and requires a ledger-independent suite plus fresh ledger
checks. A different pytest command executes fresh. Consumers without an engine
continue through the native fresh-check fallback. `--no-cache` forces execution.
Documentation changes invalidate this proof because tests read documentation.

`.basicly/usage/verify-run.json` records the run identity, duration, disposition,
source run and input fingerprint. `.basicly/usage/landing-timings.json` records
the first landing and the post-merge verification separately. Match their
`verify_run_id` and `source_run_id` fields when comparing elapsed times.

## Run it

Set `fail_fast = true` in the `[verify]` table to stop after the first failed check in the
configured order. Later selected checks are reported as not run, and the
operation still fails. Both the engine and installed Git hooks honor the
setting. It defaults to false; every selected check still runs when earlier
checks pass. Set it to false in `basicly.local.toml` to collect all failures.
Incomplete runs cannot supply successful verification receipts, and retrying
them includes checks that were blocked by the failure.

Fresh checks print `==> <name>` before starting. Managed Git hooks enable
Lefthook's live output, so these markers and child output arrive while the check
runs, including through a pipe. Matching cached compiler results name their
original run instead of printing an execution marker. Streaming preserves check
failures and the existing order of required checks; agent hook protocol output
keeps its separate handling.

Set `jobs = <n>` in the `[verify]` table to let `basicly verify` and the
installed Git hooks run up to `n` selected checks at the same time. It defaults
to 1, which runs them one after another. With more than one job, each check
prints its marker and its captured output in the configured order after it
finishes, so the output does not stream. With `fail_fast = true`, a failure
stops checks that have not started yet, and checks already running finish.
Reused and skipped checks are decided before any check starts. Mark a check
that writes into the working tree with `exclusive = true`: it waits until the
running checks finish, and no other check starts until it is done.

Set `jobs = "auto"` to size it from the machine: one job for every 8 usable
cores, at least 1. A 4-core CI runner then runs checks one after another, and
a 32-core workstation runs 4 at once. Write `{workers}` as a whole argument in
a check command, for example `["pytest", "-n", "{workers}"]`, to receive the
usable cores divided by the job count, at least 1. The configured command keeps
the placeholder, so verification reuse matches on every machine.

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
[merged] build -> verify: merged harness/myrepo-etk @ 3f9c1ab07e42 (2 commit(s)) into main @ d605fb4
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
