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
  gates (see below), including a reference to a tracker issue that exists in
  `.beads/issues.jsonl`. For anything larger than a typo fix, open an issue
  first so the work can be triaged into the tracker before you invest time.
- There is no guaranteed review SLA; small, focused changes have the best odds.

## Development setup

All commands run through [uv](https://docs.astral.sh/uv/) in a checkout; the
`basicly` entry point resolves from the workspace, so no `PYTHONPATH` prefix
is needed:

```sh
uv sync --group dev   # one-time: create the dev environment
uv run pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push   # activate the git gates for all three stages
```

Note: the markdownlint hook runs on Node.js; have a Linux/macOS-native `node`
on `PATH` (on WSL, a Windows Node install will not work for hooks).

## Everyday contributor commands

Core projector commands (fragments → agent instruction files):

```sh
uv run basicly list    # table of active fragments: id, category, priority, scope
uv run basicly build   # render generated files; --target <name> builds one target, --verify runs the catalog gate first and writes nothing on failure
uv run basicly check   # fail when generated files or the manifest drifted (what CI runs)
```

Skill projection commands (`skill.yaml` sources → `SKILL.md` at target roots):

```sh
uv run basicly skills-list    # table of skills in the catalog
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
```

Never bypass a failing gate (`--no-verify` is off the table); fix the reported
cause instead.

## Commit conventions

Two `commit-msg` hooks gate every commit:

1. **Conventional Commits**: `type(scope): description` — description all
   lowercase, letters/digits/spaces/hyphens only, no ending punctuation.
2. **Tracker reference**: the message must reference a beads issue id that
   exists in `.beads/issues.jsonl`, as a parenthetical after the description,
   for example:

   ```text
   feat(projection): add fragment loader (basicly-idr)
   ```

Create the issue first with `br create "Title" --type task` (the
[beads](https://github.com/steveyegge/beads) CLI, installed with the dev
environment) and use the id it prints — ids cannot be invented.

## Catalog authoring

Guidance content (skills, fragments, hooks) is authored as YAML sources under
`.basicly/core/`, never as hand-written projected markdown:

- Scaffold with `uv run basicly skills-new <name>` or
  `uv run basicly fragment-new <name>`.
- `catalog-lint` (a pre-commit gate) enforces the source format.
- Projected files (`CLAUDE.md`, `AGENTS.md`, `SKILL.md`, and friends) are
  generated — edit the source and rebuild; direct edits are rejected.

## Architecture

Read [`docs/architecture.md`](docs/architecture.md) before non-trivial
changes — it is the authoritative reference for the directory contract, the
catalog model, and the verification pipeline.

## Portability rules

- Never commit machine- or user-specific absolute paths, usernames, or
  hostnames; defaults must work on Windows, Linux, and macOS.
- Never commit secrets; use environment variables.

## License

Released under the [MIT License](LICENSE). By contributing you agree that your
contributions are released under the same license.
