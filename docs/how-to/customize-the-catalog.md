# How to customize the guidance your agents read

`.claude/CLAUDE.md`, `AGENTS.md` and `.github/copilot-instructions.md` are
**outputs**. Editing them is undone by the next `basicly build` and refused by
the drift hook. Everything you want to change lives in one of two places:

| Where | What it is | What `basicly install` does to it |
| --- | --- | --- |
| `.basicly-local/fragments/` | **your** overlay — hand-authored fragments | Nothing; it is never touched |
| `.basicly/core/` | the managed catalog basicly ships | Re-syncs it. A shipped file you hand-edited is kept unless you pass `--force`, which overwrites it; a file of your own is kept but warned about on every run |

So: author in the overlay, always.

## Fill in the two stubs install left you

Install writes two overlay fragments as drafts, because only you can write them:

```text
.basicly-local/fragments/user/project/project-overview.fragment.yaml
.basicly-local/fragments/user/commands/commands.fragment.yaml
```

They are inert until you flip the status — each carries the note
`# Draft until you fill it in: set 'status: active' and run 'basicly build'`.
Replace the `TODO` lines, set `status: active`, and rebuild:

```sh
basicly build
```

```text
Wrote .claude/CLAUDE.md
Wrote AGENTS.md
Wrote .github/copilot-instructions.md
Updated .basicly/generated-manifest.json
```

One edit, three agent families updated consistently. That is the whole point of
the catalog: you never maintain the same rule in three dialects.

## Add a fragment of your own

`basicly catalog new fragment <name>` scaffolds one — but note where it lands:

```sh
basicly catalog new fragment team-conventions
```

```text
Wrote .basicly/core/fragments/project/team-conventions.fragment.yaml
```

That is the **managed** tree. The scaffold command is aimed at catalog authors,
so move the file into your overlay before you fill it in, or every install warns
about it:

```text
Warning: files of unknown origin in the managed core were kept
(move yours to the overlay; core is managed by basicly install):
  fragments/project/team-conventions.fragment.yaml
```

```sh
mkdir -p .basicly-local/fragments/user/project
mv .basicly/core/fragments/project/team-conventions.fragment.yaml \
   .basicly-local/fragments/user/project/
```

A minimal fragment — `schema_version`, `id`, `description`, `category`,
`applies_to` and `body` are required; `title` supplies the rendered heading:

```yaml
schema_version: 1
id: team-conventions
description: Conventions this team follows.
category: project
priority: medium
applies_to: [all]
tags: [conventions]
title: Team Conventions
status: active
body: |
  - Branch names are `feat/<short-slug>`.
```

`basicly build`, and the section appears in all three files:

```text
## Team Conventions

- Branch names are `feat/<short-slug>`.
```

Three knobs decide where a fragment shows up:

- `applies_to` — `[all]`, or a subset of `claude` / `codex` / `copilot`.
- `priority` — `critical` / `high` / `medium` / `low`, which orders the sections.
- `paths` — a glob. A fragment with `paths: tests/**` is projected as a
  *path-scoped rule* for the agents that support one, so it loads when the agent
  touches a matching file instead of costing context on every turn.

## See what is in the catalog now

```sh
basicly catalog list
```

The table gives each entry's category, priority, `applies_to`, path scope and
status — the fastest way to check whether the rule you are about to write
already exists.

## Ship less of it

Technology-tagged sources (the `tool-*` skills, shell and platform fragments)
are skipped at projection time when they fall outside your stack:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.11.0 basicly install --technologies python,zsh
```

`uvx` is one of three ways to reach the same verb, not the command itself:
`uv run basicly install` works inside a checkout of basicly itself, and a bare
`basicly install` works once the executable is on `PATH` (put it there with
`uv tool install`). Install syncs the catalog into your repo; it never puts
`basicly` on `PATH`.

The selection is recorded in `basicly.toml` under `[catalog] technologies`, so
later installs keep it. Untagged sources are universal and always ship.

## Never edit these

Every file below is projected output. Change the source and rebuild; the paired
check command exits non-zero when the file on disk no longer matches.

| Output | Rebuild | Check |
| --- | --- | --- |
| `.claude/CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`, `.claude/rules/*.md` | `basicly build` | `basicly check` |
| `.claude/skills/**`, `.agents/skills/**` | `basicly skills-build` | `basicly skills-check` |
| `.claude/agents/**`, `.github/agents/**` | `basicly agents-build` | `basicly agents-check` |
| `.pre-commit-config.yaml`, agent hook config | `basicly hooks-build` | `basicly hooks-check` |
| the managed deny-list in `.claude/settings.json` | `basicly permissions-build` | `basicly permissions-check` |

Only the first row is covered by `.basicly/generated-manifest.json`, which is
why `basicly check` alone does not notice a hand-edited skill —
`basicly install` re-runs all five.
