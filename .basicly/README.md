# basicly

The catalog: one set of YAML sources projected into the configuration files each
coding agent natively reads.

## Why

Keep agent instructions — Claude Code, GitHub Copilot, Codex — in one place. Author a
rule once as a fragment, then project it to every target's own format and activation
rules, so the three never drift apart by hand.

## Layout

This directory looks the same as it would in any consumer repo after running
`basicly install` — it contains only catalog data, never engine code. The
engine lives at [`src/basicly/`](../src/basicly/) in this repo's own root, the same
place it would live in `basicly`'s own source distribution, not something `basicly`
ever writes into a consumer repo.

```text
.basicly/
  core/
    fragments/      # always-on and path-scoped guidance
    skills/         # on-demand runbooks, loaded when their description matches
    output-styles/  # the host's answer format, replaced for every session
    agents/         # subagent definitions
    hooks/          # git hook scripts - see hooks/README.md
    permissions/    # the managed deny-list
    kit/            # the three standalone kits, also published as wheels
    models/         # the model-tier map - see models/README.md
    rubrics/        # scoring rubrics the loop reads
    targets/        # per-target registry files
    templates/      # Jinja2 templates for each target
    schemas/        # JSON Schema for every source type
  generated-manifest.json  # deterministic projection record

.basicly-local/
  fragments/        # user-owned overlay fragments, never touched by an upgrade
```

Fragments, skills and output styles are the **suggestive** half of the harness — text a
model reads. Hooks under `core/hooks/` are the **gating** half: scripts that mechanically
block a bad commit or push. Both are first-class, catalog-distributed artifact types — see
[`docs/architecture/architecture.md`](../docs/architecture/architecture.md) §10, §13.

## Sources are YAML, never a discoverable filename

A coding agent auto-discovers context by filename: `SKILL.md`, `AGENTS.md`, `CLAUDE.md`,
`*.instructions.md`. So a **source** here is YAML, and the discoverable markdown is emitted
only at the target roots by the projector. `basicly catalog lint` refuses a `SKILL.md` or a
`*.fragment.md` under this directory, because a source with a discoverable name is loaded
twice: once as itself and once as the projection.

| Type | Source | Projects to |
| --- | --- | --- |
| fragment | `core/fragments/<category>/<id>.fragment.yaml` | the always-on files, or `.claude/rules/<id>.md` when scoped |
| skill | `core/skills/<slug>/skill.yaml` | `SKILL.md` under `.claude/skills/` and `.agents/skills/` |
| output style | `core/output-styles/<slug>/style.yaml` | `.claude/output-styles/<slug>.md` |
| agent | `core/agents/<slug>/agent.yaml` | `.claude/agents/` and `.github/agents/` |

Every source carries a `# yaml-language-server: $schema=` header pointing at
`core/schemas/`, so an editor and an agent both validate it as they write.

## Fragments

A fragment is a YAML file whose prose lives in a `body:` block scalar:

```yaml
# yaml-language-server: $schema=../../schemas/fragment.schema.json
schema_version: 1
id: use
description: How to use the AGENTS.md baseline file.
category: project
applies_to: [all]
body: |
  - User instructions in the current task override this file.
```

`applies_to` selects targets — `[all]`, or a target name. A `scope.paths` key makes the
fragment path-scoped, so it loads only when the agent touches a matching file. Follow the
`catalog-authoring` skill; `basicly catalog new fragment` scaffolds one.

## Targets

`core/targets/<name>.yaml` registers a target: its outputs, their templates, the filter
that selects fragments for each, and the size caps. See
[`docs/architecture/architecture.md`](../docs/architecture/architecture.md) §12.

## CLI

Run from the repository root:

```sh
# Build and check the always-on files and path-scoped rules
uv run basicly build
uv run basicly check

# One target only
uv run basicly build --target claude

# The other projected types, each with its own build and check
uv run basicly skills-build
uv run basicly skills-check
uv run basicly styles-build
uv run basicly styles-check
uv run basicly agents-build
uv run basicly agents-check
uv run basicly hooks-build
uv run basicly hooks-check
uv run basicly permissions-build
uv run basicly permissions-check

# Validate every source against its schema
uv run basicly catalog lint

# Print what the sources compose to, and which file each item came from
uv run basicly catalog dump
```

`basicly check` covers the always-on files and the scoped rules only. Each other type has
its own check, and all of them run in `basicly verify`.

## CI

The `.github/workflows/basicly.yml` workflow runs the projection checks on every push and
pull request to `main`. `basicly verify --mode full` runs them together with the ratchets,
the type checks and the test suite.

## Adding a fragment

1. `uv run basicly catalog new fragment <id> --category <category>`.
2. Fill in `description`, `applies_to` and the `body:` block scalar.
3. Run `uv run basicly build` and commit the updated generated files and manifest.

A user override goes under `.basicly-local/fragments/user/<category>/` instead, where an
upgrade never touches it.

## Adding a target

1. Add a renderer module at `src/basicly/renderers/<name>.py`.
2. Add templates under `.basicly/core/templates/<name>/`.
3. Add a registry file at `.basicly/core/targets/<name>.yaml`.
4. Run `uv run basicly build` and commit.

## Path configuration

`basicly.toml` sets `paths.core_fragments`, `paths.overlay_fragments`, `paths.targets`,
`paths.templates` and `paths.manifest`, so a consumer can choose a different overlay
directory name instead of `.basicly-local`.

## Extracting basicly

The engine in `src/basicly/` and the templates in `.basicly/core/templates/` carry no
repo-specific content. A consumer does not copy them: `basicly install` materializes the
catalog and `uvx --from git+https://github.com/niksavis/basicly basicly install` needs
nothing on `PATH` first. Copying is the fallback for an air-gapped tree, not the route.
