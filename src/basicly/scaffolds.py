"""File templates `basicly install` scaffolds into a consumer repo.

Three files, one contract: each is written **once** when absent, is the user's
to edit afterwards (install never overwrites it), and is removed by
``uninstall --purge`` only while still byte-identical to the template here.

They live apart from :mod:`basicly.config` because they are not configuration:
nothing in this module is read back, parsed, or merged — they are literal file
bodies the installer copies out, and the only thing that ever compares against
them is the purge check. ``DEFAULT_CONFIG_TOML`` deliberately stays in
:mod:`basicly.config`: that one *is* the config schema's own default, and a
reader who wants to know what a key defaults to looks there.
"""

from __future__ import annotations

# Scaffolded into .vscode/tasks.json by `basicly install` when absent — one
# single-command task per harness operation (no shell && chaining, so the
# commands work in PowerShell 5, cmd, and POSIX shells alike). The file is the
# user's after scaffolding: install never overwrites it, and uninstall --purge
# deletes it only when still byte-identical to this scaffold.
VSCODE_TASKS_JSON = """\
{
  // Scaffolded by `basicly install`; yours to edit — install never overwrites it.
  "version": "2.0.0",
  "tasks": [
    {
      "label": "basicly: build",
      "detail": "Regenerate agent instruction files after editing overlay fragments",
      "type": "shell",
      "command": "@UVX@ build",
      "problemMatcher": []
    },
    {
      "label": "basicly: skills-build",
      "detail": "Re-project skills into every agent root",
      "type": "shell",
      "command": "@UVX@ skills-build",
      "problemMatcher": []
    },
    {
      "label": "basicly: hooks-build",
      "detail": "Re-project and activate the git hooks",
      "type": "shell",
      "command": "@UVX@ hooks-build",
      "problemMatcher": []
    },
    {
      "label": "basicly: update",
      "detail": "Install or upgrade: converge core, projections, skills, and hooks",
      "type": "shell",
      "command": "@UVX@ install",
      "problemMatcher": []
    },
    {
      "label": "basicly: uninstall",
      "detail": "Remove everything basicly manages (overlay and config survive)",
      "type": "shell",
      "command": "@UVX@ uninstall",
      "problemMatcher": []
    }
  ]
}
""".replace("@UVX@", "uvx --from git+https://github.com/niksavis/basicly@main basicly")

# Scaffolded into .github/workflows/basicly-gates.yml by `basicly install` when
# absent — the consumer CI floor mirroring the local git-hook gates. Assumes no
# consumer stack beyond git + uv on the runner: the commit-message hooks are
# stdlib-only (plain python3), drift/verify run through the uvx git+ channel,
# and `basicly verify` executes only the checks the consumer configured (an
# empty config passes). Same contract as the other scaffolds: written once,
# then the user's; uninstall --purge removes it only while byte-identical.
CONSUMER_CI_WORKFLOW = """\
# Scaffolded by `basicly install`; yours to edit — install never overwrites it.
name: basicly-gates

# Tracker-only pushes skip CI: the harness loop necessarily commits tracker state
# separately from the work, and the local commit-msg hooks are the deterministic
# floor for those commits. Both stores are named while both exist.
"on":
  push:
    branches: [main]
    paths-ignore:
      - ".basicly/ledger/**"
  pull_request:
    branches: [main]
    paths-ignore:
      - ".basicly/ledger/**"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  commit-messages:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Validate commit messages
        shell: bash
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            base_sha="${{ github.event.pull_request.base.sha }}"
            head_sha="${{ github.event.pull_request.head.sha }}"
            range="${base_sha}..${head_sha}"
          else
            before_sha="${{ github.event.before }}"
            zeros="0000000000000000000000000000000000000000"
            if [ -z "${before_sha}" ] || [ "${before_sha}" = "${zeros}" ]; then
              range="${{ github.sha }}"
            else
              range="${before_sha}..${{ github.sha }}"
            fi
          fi
          echo "Checking commit messages in range: ${range}"
          failed=0
          while IFS= read -r sha; do
            [ -z "${sha}" ] && continue
            msg_file="$(mktemp)"
            git log -1 --format='%B' "${sha}" > "${msg_file}"
            python3 .basicly/core/hooks/commit-msg.py "${msg_file}" || failed=1
            python3 .basicly/core/hooks/tracker-commit-msg.py "${msg_file}" || failed=1
            rm -f "${msg_file}"
          done < <(git log --format='%H' "${range}")
          exit "${failed}"

  gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Catalog lint
        run: @UVX@ catalog lint
      - name: Projection drift check
        run: @UVX@ check
      - name: Skill projection drift check
        run: @UVX@ skills-check
      - name: Hook wiring drift check
        run: @UVX@ hooks-check
      - name: Configured verify checks
        run: @UVX@ verify --mode full
""".replace("@UVX@", "uvx --from git+https://github.com/niksavis/basicly@main basicly")

# Scaffolded into the user overlay by `basicly install` when absent — the two
# highest-signal descriptive blocks an agent instruction file needs (project
# overview and verbatim-runnable commands). Their content is per-repo, so each
# ships as a draft the consumer fills in and activates: draft fragments load
# and lint but never project (the planner keeps only active ones), so the
# placeholders cannot leak into generated files. Same contract as the other
# scaffolds: written once, then the file is the user's. Keyed by path relative
# to the overlay `user/` root.
OVERLAY_FRAGMENT_STUBS: dict[str, str] = {
    "project/project-overview.fragment.yaml": """\
schema_version: 1
id: project-overview
description: What this project is - purpose, stack, entry points.
category: project
priority: critical
applies_to: [all]
tags: [overview, priming]
# Draft until you fill it in: set `status: active` and run `basicly build`.
status: draft
title: Project Overview
body: |
  - Purpose: TODO - what this project does and who uses it, in 1-2 lines.
  - Stack: TODO - the languages, frameworks, and versions that matter (e.g. Python 3.14 + uv).
  - Entry points: TODO - the main binary/module/service and where it lives.
  - Architecture docs: TODO - pointer to the authoritative doc; do not embed a directory map here.
""",
    "commands/commands.fragment.yaml": """\
schema_version: 1
id: commands
description: Verbatim-runnable commands for everyday development.
category: commands
priority: high
applies_to: [all]
tags: [commands, build, test]
# Draft until you fill it in: set `status: active` and run `basicly build`.
status: draft
title: Commands
body: |
  Commands in code fences are exact - run them verbatim instead of improvising variants.

  Setup:

  ```sh
  # TODO: dependency install (e.g. uv sync --group dev)
  ```

  Test:

  ```sh
  # TODO: full test suite (e.g. uv run pytest -q)
  ```

  Single test:

  ```sh
  # TODO: one test file or case (e.g. uv run pytest tests/test_x.py -q)
  ```

  Lint / format:

  ```sh
  # TODO: linter and formatter (e.g. uv run ruff check)
  ```
""",
}
