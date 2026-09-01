# Contributing to basicly

Thanks for your interest in basicly. This page explains how the repo is
developed and what a contribution needs to pass before it can land.

## Contribution policy

basicly is maintained by a single maintainer and evolves through a gated,
issue-tracked pipeline. Contributions are welcome with expectations set
accordingly:

- **Bug reports and ideas**: open a GitHub issue — this is the most useful
  contribution and needs no setup.
- **Pull requests**: possible, but every commit must pass the repo's mechanical
  gates (see below), including a reference to a record the tracker holds. For
  anything larger than a typo fix, open an issue
  first so the work can be triaged into the tracker before you invest time.
- There is no guaranteed review SLA; small, focused changes have the best odds.

## Development setup

All commands run through [uv](https://docs.astral.sh/uv/) in a checkout; the
`basicly` entry point resolves from the workspace, so no `PYTHONPATH` prefix
is needed:

```sh
uv sync --group dev        # one-time: create the dev environment
uv run basicly hooks-build # activate the git gates for all three stages
```

Use `basicly hooks-build`, not `pre-commit install`. A bare `pre-commit install`
rewrites the pre-push hook without the ledger guard `hooks-build` writes, and
`basicly hooks-check` then still reports pre-push as not installed. Run
`basicly hooks-check` after setup: a clone starts with no active git hook at
all, and that command names any stage still missing.

Note: the markdownlint hook runs on Node.js; have a Linux/macOS-native `node`
on `PATH` (on WSL, a Windows Node install will not work for hooks).

Committer requirements: the projected git hooks run `uv run python ...`, so
every committer needs uv on `PATH` and Python 3.14+ — `basicly hooks-check`
warns when uv is missing. Driving the harness loop needs nothing further: the work
tracker is an append-only event ledger this repository owns and commits.

## Everyday contributor commands

Core projector commands (fragments → agent instruction files):

```sh
uv run basicly catalog list    # table of active fragments: id, category, priority, scope
uv run basicly build   # render generated files; --target <name> builds one target, --verify runs the catalog gate first and writes nothing on failure
uv run basicly check   # fail when generated files or the manifest drifted (what CI runs)
```

Skill projection commands (`skill.yaml` sources → `SKILL.md` at target roots):

```sh
uv run basicly catalog list skill    # table of skills in the catalog
uv run basicly skills-build   # project skills; --all-default-roots covers .claude/skills and .agents/skills, --root <dir> adds a custom root (repeatable)
uv run basicly skills-check   # fail when a projected SKILL.md is missing or stale
```

## Quality gates

Run these locally before pushing — CI runs the same set:

```sh
uv run pytest -q                # test suite
uv run ruff check               # lint
uv run ruff format --check      # formatting
uv run basicly check            # generated agent files in sync
uv run basicly skills-check --all-default-roots   # projected skills in sync
uv run basicly agents-check     # projected agent definitions in sync
uv run basicly hooks-check      # hook wiring in sync
uv run basicly permissions-check   # projected agent deny-list in sync
```

Never bypass a failing gate (`--no-verify` is off the table); fix the reported
cause instead.

## Commit conventions

Two `commit-msg` hooks gate every commit:

1. **Conventional Commits**: `type(scope): description` — description all
   lowercase, letters/digits/spaces/hyphens only, no ending punctuation.
2. **Tracker reference**: the message must reference a record id the tracker
   holds, as a parenthetical after the description, for example:

   ```text
   feat(projection): add fragment loader (basicly-idr)
   ```

Create the record first and use the id it prints — ids cannot be invented:

```sh
uv run basicly tracker write -- create "Title" -t task --parent <parent-id> --json
```

`uv run basicly session start` is where a session begins: it prints the newest
`[session handover <date>]` note (where the last session stopped and what comes
next), the ranked ready set, what is blocked and by what, the live grants, and the
architecture decisions the tree does not hold yet. `basicly tracker ready` lists
what is open and unblocked, if you are looking for a parent or for something to
pick up. End a session by writing the next handover as a note on the root record
you worked (`basicly tracker write -- comments add <root-id> "[session handover <date>] ..."`).

## Catalog authoring

Guidance content (skills, fragments, hooks) is authored as YAML sources under
`.basicly/core/`, never as hand-written projected markdown:

- Scaffold with `uv run basicly catalog new skill <name>` or
  `uv run basicly catalog new fragment <name>`.
- `basicly catalog lint` (a pre-commit gate) enforces the source format.
- Projected files (`CLAUDE.md`, `AGENTS.md`, `SKILL.md`, and friends) are
  generated — edit the source and rebuild; direct edits are rejected.

## Architecture

Read [`docs/architecture/architecture.md`](docs/architecture/architecture.md) before non-trivial
changes — it is the authoritative reference for the directory contract, the
catalog model, and the verification pipeline.

## Portability rules

- Never commit machine- or user-specific absolute paths, usernames, or
  hostnames; defaults must work on Windows, Linux, and macOS.
- Never commit secrets; use environment variables.

## License

Released under the [MIT License](LICENSE). By contributing you agree that your
contributions are released under the same license.
