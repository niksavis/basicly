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

from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from pathlib import Path

# Pinned to the version doing the scaffolding, never a branch: a consumer whose catalog
# is vendored at one version must be linted by that version's engine, or whatever `main`
# holds that morning decides whether their commits pass.
DIST_SOURCE = f"git+https://github.com/niksavis/basicly@v{__version__}"
UVX_COMMAND = f"uvx --from {DIST_SOURCE} basicly"

# What basicly generates into a consumer tree and must never reach a commit, with the
# reason for each. `install` appends whichever the consumer's .gitignore is missing.
# The ledger's two folds are rebuilt from the committed log; a committed fold recreates
# the dual-store conflict the log exists to escape. A `.basicly-bak` is the copy an
# overwrite keeps, and nothing ignored it, so an upgrade left untracked files behind.
GENERATED_IGNORES: tuple[tuple[str, str], ...] = (
    (
        "basicly.local.toml",
        "Per-machine basicly overrides; harness keys win over basicly.toml.",
    ),
    (
        ".basicly/ledger/snapshot.jsonl",
        "Derived folds of the committed event log. The log is the truth; never commit a fold.",
    ),
    (".basicly/ledger/checkpoint-*.jsonl", ""),
    (
        "*.basicly-bak",
        "Your copy of a file basicly replaced; delete it once you have merged what you want.",
    ),
)

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
""".replace("@UVX@", UVX_COMMAND)

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
""".replace("@UVX@", UVX_COMMAND)

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


# A consumer's own linter and formatter are driven by one of these, and the managed core
# is an ordinary tracked directory — so tooling scoped to the repo root reaches into it.
# One consumer's `ruff-check` reported 1192 E501 inside `.basicly/core/**` at their
# 88-character limit, blocking every commit, and their `prettier` and `ruff-format`
# rewrote 75 of our files on the way (basicly-8cd7wo5). Install cannot edit these — a
# consumer's config is theirs — so it names the exclusion and leaves the edit to them.
FOREIGN_TOOLING: tuple[tuple[str, str], ...] = (
    (
        ".pre-commit-config.yaml",
        "add `exclude: ^\\.basicly/core/` to each hook that scans the repo",
    ),
    ("ruff.toml", 'add `extend-exclude = [".basicly/core"]`'),
    (".ruff.toml", 'add `extend-exclude = [".basicly/core"]`'),
    (".prettierignore", "add a `.basicly/core/` line"),
    (".eslintignore", "add a `.basicly/core/` line"),
)

CORE_EXCLUDE_HEADING = (
    "Your repo drives its own linters or formatters. The managed core is tracked, so "
    "tooling scoped to the repo root will lint and rewrite it. Exclude `.basicly/core/`:"
)

# Claude Code loads `./CLAUDE.md` and `./.claude/CLAUDE.md` both, and only the second is
# a projection target — so install neither overwrites nor mentions a root one, and the
# consumer ends with two always-on instruction files and no notice (basicly-8cd7wo5
# sibling). Naming it is the whole fix; merging them is the consumer's call.
CLAUDE_SHADOW_NOTE = (
    "You have a root CLAUDE.md and install just wrote .claude/CLAUDE.md. Claude Code "
    "loads both, so they are now two always-on instruction files. Nothing overwrote "
    "yours — decide what belongs in each, or move yours into the overlay."
)


# A secret scanner flags a 64-character hex string on entropy alone, and every `created`
# event an import writes carries one: `payload.import_digest`, the sha256 of the export
# it came from. A consumer's 702-record import gave them 702 flagged lines and a blocked
# commit (basicly-nu3z2md sibling). A baseline is the wrong instrument — the digests are
# regenerated on every import — so the answer is a path exclusion.
SECRET_SCANNERS: tuple[tuple[str, str], ...] = (
    (".secrets.baseline", "detect-secrets"),
    (".gitleaks.toml", "gitleaks"),
    ("gitleaks.toml", "gitleaks"),
    (".trufflehogignore", "trufflehog"),
)

LEDGER_EXCLUDE_HEADING = (
    "Your repo runs a secret scanner. The tracker ledger carries a sha256 provenance "
    "digest on every imported record, which any scanner flags as high-entropy hex — one "
    "hit per record. Exclude `.basicly/ledger/` rather than baselining it; the digests "
    "are rewritten on every import. Detected:"
)


def _secret_scanners(repo_root: Path) -> list[str]:
    """The secret scanners this repo is configured for, by name and by the file naming it."""
    found = {name: path for path, name in SECRET_SCANNERS if (repo_root / path).is_file()}
    precommit = repo_root / ".pre-commit-config.yaml"
    if precommit.is_file():
        text = precommit.read_text(encoding="utf-8")
        for name in ("detect-secrets", "gitleaks", "trufflehog"):
            if name in text:
                found.setdefault(name, ".pre-commit-config.yaml")
    return [f"  {path}: {name}" for name, path in sorted(found.items())]


def install_notes(repo_root: Path) -> list[str]:
    """What install noticed about this repo that it will not change on the repo's behalf."""
    notes: list[str] = []
    found = [(name, advice) for name, advice in FOREIGN_TOOLING if (repo_root / name).is_file()]
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file() and "[tool.ruff]" in pyproject.read_text(encoding="utf-8"):
        found.append((
            "pyproject.toml",
            'add `extend-exclude = [".basicly/core"]` under [tool.ruff]',
        ))
    if found:
        notes.append(CORE_EXCLUDE_HEADING)
        notes.extend(f"  {name}: {advice}" for name, advice in found)
    if scanners := _secret_scanners(repo_root):
        notes.append(LEDGER_EXCLUDE_HEADING)
        notes.extend(scanners)
    if (repo_root / "CLAUDE.md").is_file():
        notes.append(CLAUDE_SHADOW_NOTE)
    return notes
