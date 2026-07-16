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

Pin `@v0.1.3` for reproducible installs, or track `@main` for the latest. The repo is public; no authentication is needed. Install converges everything: managed core catalog, generated agent instruction files, projected skills, activated git hooks, a beads tracker workspace, VS Code tasks, and a CI gates workflow. Customize via YAML fragments in `.basicly-local/fragments/user/` — install never touches them. Remove with `basicly uninstall` (add `--purge` to also drop your overlay and config).

## Quick start

### Local development

```bash
uv sync --group dev
uv run pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push
```

### Core projector commands

```bash
PYTHONPATH=src uv run python -m basicly.cli list
PYTHONPATH=src uv run python -m basicly.cli update
PYTHONPATH=src uv run python -m basicly.cli build
PYTHONPATH=src uv run python -m basicly.cli check
```

### Skills projection commands

```bash
PYTHONPATH=src uv run python -m basicly.cli skills-list
PYTHONPATH=src uv run python -m basicly.cli skills-build
PYTHONPATH=src uv run python -m basicly.cli skills-check
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) — the single authoritative
architecture reference for this project. It defines the directory contract, the
fragment/skill/hook catalog model, the verification pipeline, and the current
implementation gaps that become beads issues.
