# basicly

`basicly` is a harness distribution repository for coding agents.

It packages reusable, versioned building blocks that help agents work reliably across repositories:

- **Suggestive guidance** (non-deterministic): skills, instructions, prompts, and policy fragments a model reads.
- **Deterministic gates** (hard gates): git hook scripts that mechanically block a bad commit/push regardless of whether the guidance was followed.
- **Projection tooling**: generate target-native agent config files from one source of truth.

Both halves are first-class, catalog-distributed artifacts — see [`docs/architecture.md`](docs/architecture.md) for the full design.

## What this repo provides

- A source-of-truth projector under `.basicly/`.
- Fragment-driven generation for agent ecosystems (for example Claude and Copilot).
- Skill projection workflows so shared skills can be synced into target roots.
- Git hook scripts under `.basicly/core/hooks/`, wired via `.pre-commit-config.yaml`.
- Release and quality workflows in `.github/workflows/`.

## Project intent

This repository is designed to be consumed by other repositories.

Think of it as Lego bricks for agent enablement:

1. Choose which blocks to install (skills, hooks, policies, workflows).
2. Configure the selected blocks for your target repo.
3. Run checks/build to keep generated and projected files in sync.

## Install

One command installs the harness into a consumer repo — and the same command performs every upgrade:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.1.3 basicly install
```

No `uv`/Python on the machine? The bootstrap shim installs `uv` first, then runs
the same command (append `-s -- --ref v0.1.3` to pin, plus any `basicly install`
arguments):

```sh
curl -fsSL https://raw.githubusercontent.com/niksavis/basicly/main/.scripts/bootstrap.sh | sh
```

Windows: `.scripts/bootstrap.ps1` is the PowerShell twin.

Pin `@v0.1.3` for reproducible installs, or track `@main` for the latest. Install converges everything: managed core catalog, generated agent instruction files, projected skills, activated git hooks, a beads tracker workspace, VS Code tasks, and a CI gates workflow. Customize via YAML fragments in `.basicly-local/fragments/user/` — install never touches them.

## Uninstall

One command removes everything basicly manages (core catalog, generated files, projected skills and agents, the managed hook block); your overlay and `basicly.toml` survive:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.1.3 basicly uninstall
```

Add `--purge` to also remove the user overlay, `basicly.toml`, and the scaffolded VS Code tasks/CI workflow (only when still unedited).

## Quick start

### In a consumer repo (end users)

Day-to-day use needs nothing beyond `install` above — re-running it is also the
upgrade path (there is no separate `update` command). The scaffolded VS Code
tasks wrap the same pinned commands. To inspect or re-sync by hand, run these
from the consumer repo root with the same pin used to install:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.1.3 basicly check   # exit non-zero when generated files drifted
uvx --from git+https://github.com/niksavis/basicly@v0.1.3 basicly build   # regenerate agent instruction files
```

### Contributing to this repo

All commands run through `uv` in a checkout; the `basicly` entry point resolves
from the workspace, so no `PYTHONPATH` prefix is needed:

```sh
uv sync --group dev   # one-time: create the dev environment
uv run pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push   # activate the git gates for all three stages
```

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

## Architecture

See [`docs/architecture.md`](docs/architecture.md) — the single authoritative
architecture reference for this project. It defines the directory contract, the
fragment/skill/hook catalog model, the verification pipeline, and the current
implementation gaps that become beads issues.
