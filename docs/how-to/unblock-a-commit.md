# How to unblock a commit a hook refused

A basicly repo enforces at **commit time**: eleven git hooks across pre-commit,
commit-msg and pre-push, and a refusal at either of the first two leaves the tree
exactly as it was. Find your message below, apply the fix, re-run the same
`git commit`.

Never reach for `--no-verify`. The hook is the floor the whole harness rests on,
and skipping it moves the failure to CI or to a reviewer.

## `Commit message does not reference a beads issue id`

```text
ERROR: Commit message does not reference a beads issue id.
```

Every commit names the tracked issue it belongs to, as a parenthetical after the
description. File one if it does not exist yet, then re-commit:

```sh
br create "Title" --type task --priority 2
git commit -m "fix(cli): stop the parser eating a trailing flag (myrepo-4f2)"
```

The id must exist in `.beads/issues.jsonl` — the hook reads the tracker, so a
plausible-looking id is not enough. This is also the first thing a fresh install
hits, because the install output itself is a commit.

## `no rank-1 floor declared`

```text
catalog lint: routing: rank-1 rate 80/87 = 92.0% (no floor declared)
catalog lint: FAILED
  no rank-1 floor declared — set `[catalog] rank1_floor` in basicly.toml below
  the measured baseline (currently 92.0%)
```

The hook measured how often the correct skill ranks first for its own eval
queries and refuses to pick your threshold for you. Set it just below the
measured rate in `basicly.toml`:

```toml
[catalog]
rank1_floor = 0.90
```

From then on it is a ratchet: a new skill whose description collides with an
existing one drops the rate and fails the commit until you make the two
descriptions distinguishable.

## `files were modified by this hook`

```text
pre-commit-script........................................................Failed
- hook id: pre-commit-script
- files were modified by this hook
```

Something in the staged set is a **generated or cached** artifact that a hook
rewrites as it runs. The usual cause on a fresh install is `__pycache__`: the
hook scripts are Python, and the `.gitignore` basicly writes covers only
`basicly.local.toml`, so a `git add -A` sweeps the `.pyc` files in and every
subsequent commit fails.

```sh
printf '__pycache__/\n' >> .gitignore
git rm -r --cached .basicly/core/hooks/__pycache__
```

If it is not `__pycache__`, run `git diff` right after the failure — the hook
already applied its repair, so staging that repair and re-committing is
usually the whole fix.

## `Stale generated files detected`

```text
Stale generated files detected. Run `basicly build` to fix.
  AGENTS.md: expected sha256:c2f9975…, found sha256:66fc965…
```

You (or an agent) hand-edited a **projected** file. `CLAUDE.md`, `AGENTS.md`,
`.github/copilot-instructions.md`, everything under `.claude/skills/`, and
`.pre-commit-config.yaml` are outputs, not sources — the fix belongs in the
fragment, and the file is then regenerated:

```sh
basicly build
```

To keep the edit, move it into the overlay first:
[customize the catalog](customize-the-catalog.md). `basicly check` is the same
comparison as a standalone command; it exits 1 on drift and 0 when clean, which
is what the scaffolded CI workflow runs.

## A verify check failed

```text
  notes-present: PASS
  always-fails: FAIL
[verify] FAIL: always-fails
```

That is your own check, wired in `basicly.toml` — fix the code it is complaining
about. Re-run just the gate without committing:

```sh
basicly verify --mode fast
```

Add `--fix` to apply each check's declared `fix_command` (formatters and other
lossless mechanical repairs) before the checks run. See
[wire up the verify gate](wire-up-the-verify-gate.md).

## The commit is fine but the landing refuses

```text
base:      DIRTY - 7 path(s); a landing will refuse until committed
VERDICT:   not ready - base checkout is dirty
```

This is not a hook. Landing a worktree mutates the base checkout, so it counts
**untracked** files there too. Check it:

```sh
git -C /path/to/base status --short
```

If the dirt is yours, commit it. If it belongs to another session sharing that
checkout, **hold the branch until that session commits** — never `git clean`,
`git checkout --` or stash someone else's files to unblock a merge.
