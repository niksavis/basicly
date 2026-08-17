# How to upgrade, check drift, and uninstall

## Upgrade

There is no `update` command. **Re-running install *is* the upgrade** — it is
idempotent and converges the repo onto whatever version you pin:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.9.0 basicly install
```

The first line tells you what moved in the managed catalog:

```text
Synced core catalog at .basicly/core: 0 new, 0 updated, 0 removed, 147 unchanged
```

Your overlay is not in that count. `.basicly-local/`, `basicly.toml`, and any
scaffolded file you have edited are left alone — install reports
`already exists; left unchanged` for each. A file you added to the *managed*
tree is kept but warned about on every run:

```text
Warning: files of unknown origin in the managed core were kept
(move yours to the overlay; core is managed by basicly install)
```

Confirm the repo actually converged. The version that wrote your generated files
is recorded in `.basicly/state/install.json`, and `basicly status` compares it
against the engine you just ran — `(matches engine)` is the line to look for:

```text
engine: basicly 0.8.0
catalog: installed by basicly 0.8.0 at 2026-08-07T17:15:18Z (matches engine)
drift: generated files up to date
```

## Check drift

`basicly check` re-renders every manifest-tracked output and compares hashes. It
exits **1** when something drifted and **0** when clean:

```text
Stale generated files detected. Run `basicly build` to fix.
  AGENTS.md: expected sha256:c2f9975…, found sha256:66fc965…
```

```text
All generated files and manifest are up to date.
```

`basicly check` covers the instruction files and path-scoped rules only. Skills,
subagents, hooks and the permissions deny-list have their own checks, all of
which exit 0 when in sync:

```sh
basicly check
basicly skills-check
basicly agents-check
basicly hooks-check
basicly permissions-check
```

```text
Projected skills are up to date.
Projected agents are up to date.
Projected hooks are up to date.
Projected permissions deny-list is up to date.
```

Install scaffolds `.github/workflows/basicly-gates.yml` to run this in CI, and
`.vscode/tasks.json` to run it from the editor. Both wrap the same pinned
commands.

## Keep every committer working

The projected git hooks run `uv run python …`, so **every** committer to the repo
needs uv on `PATH` and Python 3.14+ — not just whoever ran install. Diagnose it
before it bites at commit time:

```sh
basicly hooks-check
```

The harness loop needs nothing further. The work tracker is an append-only event
ledger the repository owns and commits, so there is no binary to install and no
version to keep in step across a team.

## Uninstall

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.9.0 basicly uninstall
```

This removes everything basicly manages — the managed core, install state,
generated instruction files, projected skills and subagents, and the managed
hooks. Your overlay, `basicly.toml`, and your own content survive.

Add `--purge` to also remove the overlay, `basicly.toml`, and the scaffolded VS
Code tasks and CI workflow — the last two only while still unedited.

The tracker is not basicly's to delete: `.basicly/ledger/` and your record history stay
either way.
