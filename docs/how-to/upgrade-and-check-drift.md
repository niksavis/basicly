# How to upgrade, check drift, and uninstall

The basicly release pins Lefthook in its artifact manifest. Re-running install
replaces an older `.basicly/bin/lefthook` (or `lefthook.exe`) with that release's
verified bundled executable and license. This upgrade needs no global Lefthook
installation or separate binary download, and preserves consumer-owned hook
configuration. Lefthook follows the basicly release you selected; it does not
silently switch to an unreviewed upstream version.

## Upgrade

### Markdown gate prerequisites

The markdown gate is enabled by default. The hook refuses a missing `markdownlint-cli2`
entry point or an unusable Node runtime. Install runs the same prerequisite check before
writing files, including with `--dry-run`.

If your repository already declares and locks `markdownlint-cli2`, restore its packages:

```sh
npm ci
```

Otherwise, get the repository owner's approval before adding the dependency:

```sh
npm install --save-dev markdownlint-cli2
```

Commit the package files. For a missing Node runtime, install Node, for example with
`nvm install --lts`. On WSL, the hook refuses a Windows Node reached through `/mnt`.
Install never adds dependencies or opts out automatically.

The enabled hook runs without positional file arguments, so the repository must select
inputs in `.markdownlint-cli2.jsonc`, `.markdownlint-cli2.yaml`, `.markdownlint-cli2.cjs`
or `.markdownlint-cli2.mjs`. Keep an existing configuration and its input and rule choices.
Style-only `.markdownlint.json` files configure rules, not CLI2 inputs. Installation creates
no Markdown input or style policy.

For a throwaway tutorial repository with a clean `README.md`, an explicit owner choice is:

```json
{"globs": ["README.md", "NOTES.md"]}
```

Put this in `.markdownlint-cli2.jsonc` only when no CLI2 configuration already exists.
It keeps every default rule enabled and selects only these consumer files. Choose the
inputs for your real repository deliberately; this example does not claim coverage of
all Markdown or generated files. The first commit lints the real README; the later
`NOTES.md` change is also checked. Commit the configuration with your package files.

A repository owner can explicitly opt out in configuration:

```toml
[hooks.markdownlint]
enabled = false
```

The hook and install then print `markdownlint: opted out by hooks.markdownlint.enabled=false`.
They read `basicly.toml`, sorted `basicly.d/*.toml`, then `basicly.local.toml`. Later settings
win. A malformed setting refuses. A technology selection excluding Node omits this gate
and needs no markdown runtime. `hooks-build --runtime-only` also remains available without Node.

### Optional compiler verification cache

Existing consumers continue to execute verification freshly. To enable local
reuse of proven Pyright results after upgrading, set `cache = true` under
`[verify]` and declare `reuse = "pyright"` on supported checks. Unsupported
commands and tests remain fresh. Use `basicly verify --no-cache` for an explicit
fresh run. See [the verification cache](wire-up-the-verify-gate.md#reuse-compiler-results-across-commands)
for input guards, evidence and runtime limits.

### Protected agent configuration

An agent sandbox can protect `.codex`, `.agents` and the Git hook directory even
when ordinary source edits are allowed. Install checks planned catalog,
instruction, skill, Codex configuration and Git hook changes before updating the
repository. It skips unchanged protected outputs. Unchanged Git launchers and
foreign hook backups are left in place.

If preflight refuses access, it names the destination and the normal command to
retry. For an upgrade you already authorized, the agent requests task-scoped
execution review for that command in the same checkout. The standalone CLI
cannot grant itself access; you can also run the command from your own terminal.
This retry keeps hooks, signing, loop checkpoints and dotenv protections active.
It does not require changing a machine-wide permissions profile.

### From v0.26.12: tracker compaction

Post-merge tracker hooks now report pending shards without folding, staging or creating
commits. Re-running install replaces the managed hook behavior while preserving foreign
hooks and consumer settings. The existing `fold_on_merge = true` setting enables the
read-only reminder. The standalone tracker's `update --fold-on-merge` refreshes its marked
hook block in the same way.

Run `basicly tracker fold` explicitly outside Git hooks when the reminder names pending
shards. The harness loop already folds at its explicit tracker commit boundaries.
Standalone consumers run `basicly-tracker compact .basicly/ledger` and commit the result
through their normal Git checks. Pending shards remain readable until compaction.

### Re-run install

There is no `update` command. **Re-running install *is* the upgrade** — it is
idempotent and converges the repo onto whatever version you pin:

```sh
uvx --from https://github.com/niksavis/basicly/releases/download/v0.26.12/basicly-0.26.12-py3-none-any.whl basicly install
```

`uvx` is one of three ways to reach the same verb, not the command itself:
`uv run basicly install` works inside a checkout of basicly itself, and a bare
`basicly install` works once the executable is on `PATH` (put it there with
`uv tool install`). Install syncs the catalog into your repo; it never puts
`basicly` on `PATH`.

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

A repository that an old release installed can have no `.basicly/state/install.json`.
Install then compares each managed file with every version that an earlier release
shipped. It updates a file that matches one, and it removes the file when this release
dropped it. A file that matches no shipped version is a hand edit, and it is kept
unless you pass `--force`. When a kept file would mix kit modules that cannot load
together, install refuses before it writes anything and names the kept files.

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
output styles, subagents, hooks and the permissions allow and deny lists have their own
checks, all of which exit 0 when in sync:

```sh
basicly check
basicly skills-check
basicly styles-check
basicly agents-check
basicly hooks-check
basicly permissions-check
```

```text
Projected skills are up to date in .claude/skills, .agents/skills.
Projected output styles are up to date in .claude/output-styles.
Projected agents are up to date.
Projected hooks are up to date.
Projected permissions allow and deny lists are up to date.
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
uvx --from https://github.com/niksavis/basicly/releases/download/v0.26.12/basicly-0.26.12-py3-none-any.whl basicly uninstall
```

This removes everything basicly manages — the managed core, install state,
generated instruction files, projected skills and subagents, and the managed
hooks. Your overlay, `basicly.toml`, and your own content survive.

Add `--purge` to also remove the overlay, `basicly.toml`, and the scaffolded VS
Code tasks and CI workflow — the last two only while still unedited.

The tracker is not basicly's to delete: `.basicly/ledger/` and your record history stay
either way.

## Lefthook after a clone

Lefthook is installed at `.basicly/bin/lefthook` (`lefthook.exe` on Windows).
The directory is ignored, so a fresh checkout restores the packaged runtime
before checking hook configuration:

```sh
basicly hooks-build --runtime-only
basicly hooks-check
```

Use the repository's pinned basicly invocation if `basicly` is not on `PATH`:

```sh
uvx --from https://github.com/niksavis/basicly/releases/download/v0.26.12/basicly-0.26.12-py3-none-any.whl basicly hooks-build --runtime-only
uvx --from https://github.com/niksavis/basicly/releases/download/v0.26.12/basicly-0.26.12-py3-none-any.whl basicly hooks-check
```

In a basicly source checkout, use `uv run basicly` instead. Prepare the Lefthook
archives as described in [Contributing](../../CONTRIBUTING.md#development-setup)
first if the runtime command reports missing build artifacts.

Codex's generated-file hook matches `apply_patch`, leaving shell tools available
to run these repair commands. A missing runtime prints the repair steps, and the
installed agent instructions tell the agent to run them and retry. Keep the hooks
enabled: after setup, generated-file edits remain refused.

The first command installs only the pinned executable and MIT license from the
basicly package. It preserves hook configuration and does not activate Git hooks.
The second command checks configuration without rewriting it. Install adds this
setup to the standard scaffolded hook-check command during an upgrade, retaining
consumer steps and backing up the previous workflow. Custom CI commands should
add the same runtime setup before `hooks-check`.
