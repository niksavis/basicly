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
