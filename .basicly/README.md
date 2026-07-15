# basicly

A source-of-truth projector that generates AI agent configuration files from small, tool-agnostic Markdown fragments.

## Why

Keep agent instructions (Claude Code, GitHub Copilot, Codex, etc.) in one place. Author a rule once as a fragment, then project it to each target's native format and activation rules.

## Layout

This directory looks the same as it would in any consumer repo after running
`basicly install` — it contains only catalog data, never engine code. The
engine lives at [`src/basicly/`](../src/basicly/) in this repo's own root, the same
place it would live in `basicly`'s own source distribution, not something `basicly`
ever writes into a consumer repo.

```text
.basicly/
  core/
    fragments/    # managed core fragments shipped by basicly (guidance, non-deterministic)
    skills/       # managed skill catalog shipped by basicly
    hooks/        # managed git hook scripts (gating, deterministic) - see hooks/README.md
    targets/      # per-target registry files (YAML)
    templates/    # Jinja2 templates for each target
  generated-manifest.json  # deterministic projection record

.basicly-local/
  fragments/      # user-owned overlay fragments
```

Fragments and skills are the **suggestive** half of the harness (Markdown guidance a
model reads); hooks under `core/hooks/` are the **gating** half (scripts that
mechanically block a bad commit/push). Both are first-class, catalog-distributed
artifact types — see [`docs/architecture.md`](../docs/architecture.md) §3, §4.

## Fragments

Each fragment is a Markdown file with YAML front matter:

```markdown
---
id: python-style
description: Python style conventions for this repo.
category: code-style
priority: medium
applies_to: [all]
scope:
  paths: ["**/*.py"]
---

- Use type hints for public functions.
- Prefer `pathlib` over `os.path`.
- Format with `ruff`.
```

Fields:

- `id` — stable, unique identifier.
- `description` — one-line summary.
- `category` — controlled vocabulary (e.g. `project`, `code-style`, `security`).
- `applies_to` — list of target names, or `[all]` for cross-tool baseline.
- `priority` — `critical` | `high` | `medium` | `low`.
- `scope.paths` — glob list; non-default scopes produce path-scoped outputs.
- `status` — `active` | `draft` | `deprecated`.
- `title` — optional display heading.
- `source` — `"core"` or `"user"` (reserved for phase 2; defaults to `"core"`).
- `override` — boolean, allows a user fragment to replace core fragments (reserved).
- `replaces` — list of fragment ids to remove when this fragment is active (reserved).
- `extends` — list of fragment ids this fragment augments (reserved).

## Targets

Targets are defined in `.basicly/core/targets/<name>.yaml`. Each target declares its outputs, templates, and fragment selection rules.

## CLI

Run from the repository root:

```bash
# List active fragments
PYTHONPATH=src uv run python -m basicly.cli list

# Refresh managed core layout only
PYTHONPATH=src uv run python -m basicly.cli update

# Build all enabled targets
PYTHONPATH=src uv run python -m basicly.cli build

# Build only one target
PYTHONPATH=src uv run python -m basicly.cli build --target claude

# Check generated files are up to date (CI gate)
PYTHONPATH=src uv run python -m basicly.cli check

# List source skill collection entries
PYTHONPATH=src uv run python -m basicly.cli skills-list

# Project skills into .claude/skills (default)
PYTHONPATH=src uv run python -m basicly.cli skills-build

# Project skills into all default roots
PYTHONPATH=src uv run python -m basicly.cli skills-build --all-default-roots

# Check projected skills are synchronized
PYTHONPATH=src uv run python -m basicly.cli skills-check
```

## CI

The `.github/workflows/basicly.yml` workflow runs `check` on every push and pull request to `main`.

## Adding a fragment

1. Create core fragments under `.basicly/core/fragments/<category>/`.
2. Create user override fragments under `.basicly-local/fragments/user/<category>/`.
3. Set `applies_to` to `[all]` for cross-tool rules, or to specific target names.
4. Run `build` and commit the updated generated files and manifest.

## Path configuration

Paths are configured in `basicly.toml`:

1. `paths.core_fragments`
2. `paths.overlay_fragments`
3. `paths.targets`
4. `paths.templates`
5. `paths.manifest`

This allows users to choose a custom overlay folder name instead of `.basicly-local`.

## Adding a target

1. Add a renderer module at `src/basicly/renderers/<name>.py`.
2. Add templates under `.basicly/core/templates/<name>/`.
3. Add a registry file at `.basicly/core/targets/<name>.yaml`.
4. Run `build` and commit.

## Skill collection

`basicly` also supports a repository-controlled skill catalog:

- Source of truth: `.basicly/core/skills/<skill-name>/SKILL.md`
- Projection roots (optional): `.claude/skills`, `.github/skills`, `.agents/skills`
- Default behavior: `skills-build` syncs source skills into `.claude/skills`

This keeps skills shippable when extracting the `basicly` engine into a standalone repository while still allowing downstream repos to consume projected skill files.

**Known gap**: the skill source format is still Markdown+YAML-frontmatter (`SKILL.md`)
at the catalog level. Per [`docs/architecture.md`](../docs/architecture.md) §4/§11,
this should migrate to a non-`SKILL.md`-named, Python-authored source so a broad
filesystem scan for `SKILL.md` by a coding agent can't discover the catalog copy in
addition to the projected one — not yet executed.

## Git hooks

`basicly` also ships git hook scripts as a first-class catalog artifact under
[`core/hooks/`](core/hooks/README.md) — the deterministic, gating counterpart to
fragments/skills. This repo dogfoods them directly via
[`.pre-commit-config.yaml`](../.pre-commit-config.yaml); there is no `hooks-build`
projection command yet for installing them into a fresh consumer repo.

## User customizations (phase 2 preview)

The `.basicly-local/fragments/user/` directory is reserved for user-added fragments that
survive updates to the core fragments shipped with basicly. The schema already accepts
`source`, `override`, `replaces`, and `extends` fields with safe defaults. The full
verification and override workflow is described in
[`docs/architecture.md`](../docs/architecture.md) §6.

## Extracting basicly

The engine in `src/basicly/` and templates in `.basicly/core/templates/` have no terminal-specific content. To reuse basicly in another repo:

1. Copy `src/basicly/` and `.basicly/core/templates/`.
2. Replace `.basicly/core/fragments/` and `.basicly/core/targets/` with the new repo's content.
3. Keep the CLI interface and manifest format unchanged.
