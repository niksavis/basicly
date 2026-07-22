"""Project path configuration for basicly."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from . import permissions
from .runner import AUTO, BUILTIN_RUNNERS, HEADLESS, PROMPT_VIA, USAGE_FORMATS, RunnerSpec
from .schema import TECHNOLOGIES

COPILOT_RUNNER = "copilot"

CONFIG_FILE = "basicly.toml"

# Gitignored per-machine overlay: keys here override CONFIG_FILE for the
# harness sections only ([worktree], [verify], [policy], [runner]). Projection
# config ([paths], [catalog]) shapes repo-committed outputs, so it stays
# repo-level and never reads the overlay.
LOCAL_CONFIG_FILE = "basicly.local.toml"

# Scaffolded into a consumer repo by `basicly install`. Kept next to the
# defaults below; test_config asserts parsing this yields exactly the built-in
# defaults, so the two can never drift apart.
DEFAULT_CONFIG_TOML = """\
# basicly path wiring. Managed core catalog is materialized and upgraded by
# `basicly install`; the overlay is always yours to edit.
#
# Per-machine harness settings ([worktree], [verify], [policy], [runner]) can
# be overridden in a gitignored basicly.local.toml next to this file — keys
# there win over this file, so machine-specific choices (e.g. runner default)
# stay out of the shared config. [paths] and [catalog] are repo-level only.
[paths]
core_fragments = ".basicly/core/fragments"
overlay_fragments = [".basicly-local/fragments"]
targets = ".basicly/core/targets"
templates = ".basicly/core/templates"
manifest = ".basicly/generated-manifest.json"

# Catalog technology selection. Absent = the full catalog ships. List the
# stack/environment tags this repo wants and technology-tagged sources outside
# it are skipped at projection time (untagged sources are universal and always
# ship). Recorded by `basicly install --technologies ...`.
#
# [catalog]
# technologies = ["python", "zsh"]

# Sibling git-worktree isolation for harness tracks.
[worktree]
# Branch new harness/<name> worktrees fork from. Empty = the current branch.
base_branch = ""
# Cap on how many worktrees may exist at once.
concurrency = 4

# Deterministic verify gate. Each check runs in the listed modes; a "staged"
# check with staged_suffix runs only against staged files of that suffix.
# No checks are enabled by default — declare the ones your stack actually has
# (an empty config passes vacuously; a configured command missing from PATH
# fails the run with a one-line message). Python examples:
#
# [[verify.checks]]
# name = "ruff"
# command = ["ruff", "check"]
# modes = ["fast", "full", "staged"]
# staged_suffix = ".py"
#
# [[verify.checks]]
# name = "pytest"
# command = ["pytest", "-q"]
# modes = ["full"]

# Loop gate/checkpoint policy: which gates block advancement and the rework cap.
[policy]
# Gate names (from [verify] / br gate report) that MUST pass to advance. Any
# recorded gate not listed here is advisory (never blocks).
required_gates = ["verify"]
# Rework retries allowed before a node escalates to a human.
max_rework = 2

# Agent-agnostic runner: how the harness invokes a coding agent headless to do a
# node's work in its worktree. "auto" detects claude -> codex -> copilot on PATH,
# else falls back to the "manual" handoff (no command is guessed for an unknown
# agent). Add or override an agent with [[runner.agents]]; verify any command
# with `basicly runner dry-run` before a live run.
[runner]
default = "auto"
# [[runner.agents]]
# name = "opencode"
# command = ["opencode", "run", "{prompt}"]
# prompt_via = "arg"   # or "stdin"
# model = "opus"       # optional: injects `--model opus` after the binary,
#                      # or substitutes a `{model}` placeholder if the command has one
# sandbox = "workspace-write"   # optional: injects `--sandbox workspace-write` (codex
#                               # defaults this); network is disabled by default in it
# approval = "on-failure"       # optional: injects `-a on-failure` (codex defaults this)
# git_name = "opencode-bot"        # optional bot git identity: dispatched commits
# git_email = "bot@example.com"    # use it (both keys or neither). Must satisfy
#                                  # basicly.identityAllowEmail when strict mode is on.
"""

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
      "command": "@UVX@ skills-build --all-default-roots",
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

# Tracker-only pushes (.beads/**) skip CI: the harness loop necessarily commits
# beads state separately from the work, and the local commit-msg hooks are the
# deterministic floor for those commits.
"on":
  push:
    branches: [main]
    paths-ignore:
      - ".beads/**"
  pull_request:
    branches: [main]
    paths-ignore:
      - ".beads/**"
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
            python3 .basicly/core/hooks/beads-commit-msg.py "${msg_file}" || failed=1
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
        run: @UVX@ skills-check --all-default-roots
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

# Default concurrency cap when no basicly.toml (or no [worktree]) is present.
DEFAULT_WORKTREE_CONCURRENCY = 4

# Modes the verify runner understands.
VERIFY_MODES = ("fast", "full", "staged")

# Policy defaults when no basicly.toml (or no [policy]) is present.
DEFAULT_REQUIRED_GATES = ("verify",)
DEFAULT_MAX_REWORK = 2

# The three human checkpoints the loop enforces (architecture §12.2).
CHECKPOINTS = ("classify", "decompose", "ship")

# The fixed br work classes the classifier may assign (architecture §12.1).
# bug/chore are leaf tracks; task/feature/epic nest fractally.
WORK_TYPES = ("bug", "chore", "task", "feature", "epic")


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved paths used by the projector CLI."""

    core_fragments_dir: Path
    overlay_fragments_dirs: tuple[Path, ...]
    targets_dir: Path
    templates_dir: Path
    manifest_path: Path
    legacy_fragments_dir: Path

    @property
    def core_root(self) -> Path:
        """Root of the managed core catalog, derived from the fragments dir.

        Every command that touches the core tree (init materialization, hooks
        projection) must use this single notion so a custom `core_fragments`
        in basicly.toml relocates the whole catalog consistently.
        """
        return self.core_fragments_dir.parent

    @property
    def state_path(self) -> Path:
        """Install provenance file, sibling of the core root (§9).

        `.basicly/core` -> `.basicly/state/install.json`; follows a custom
        core location the same way the core root itself does.
        """
        return self.core_root.parent / "state" / "install.json"


def _harness_section(repo_root: Path, name: str) -> dict:
    """The named harness section with basicly.local.toml keys overriding basicly.toml.

    Key-level shallow merge: a key set in the gitignored local overlay replaces
    the same key from the shared file wholesale (so a local ``checks`` or
    ``agents`` list is taken as-is, not concatenated). A missing file or a
    non-table section contributes nothing. Only harness sections go through
    this merge — projection config ([paths], [catalog]) reads basicly.toml
    alone.
    """
    merged: dict = {}
    for filename in (CONFIG_FILE, LOCAL_CONFIG_FILE):
        config_path = repo_root / filename
        if not config_path.exists():
            continue
        section = tomllib.loads(config_path.read_text(encoding="utf-8")).get(name, {})
        if isinstance(section, dict):
            merged.update(section)
    return merged


@dataclass(frozen=True)
class WorktreeConfig:
    """Settings for sibling git-worktree isolation."""

    # None means "fork from the branch currently checked out".
    base_branch: str | None
    concurrency: int


def load_worktree_config(repo_root: Path) -> WorktreeConfig:
    """Load ``[worktree]`` settings (basicly.toml + local overlay), with defaults."""
    defaults = WorktreeConfig(base_branch=None, concurrency=DEFAULT_WORKTREE_CONCURRENCY)

    section = _harness_section(repo_root, "worktree")

    base = section.get("base_branch")
    base_branch = base.strip() if isinstance(base, str) and base.strip() else None

    concurrency = section.get("concurrency")
    if not (isinstance(concurrency, int) and not isinstance(concurrency, bool) and concurrency > 0):
        concurrency = defaults.concurrency

    return WorktreeConfig(base_branch=base_branch, concurrency=concurrency)


@dataclass(frozen=True)
class VerifyCheck:
    """A single configured verify check."""

    name: str
    command: tuple[str, ...]
    modes: frozenset[str]
    # When set and running in "staged" mode, run only against staged files with
    # this suffix (and skip when none are staged).
    staged_suffix: str | None = None


@dataclass(frozen=True)
class VerifyConfig:
    """The consumer's configured verify checks."""

    checks: tuple[VerifyCheck, ...]

    def for_mode(self, mode: str) -> tuple[VerifyCheck, ...]:
        """Return the checks that participate in *mode*, in configured order."""
        return tuple(check for check in self.checks if mode in check.modes)


def load_verify_config(repo_root: Path) -> VerifyConfig:
    """Load ``[verify].checks`` (basicly.toml + local overlay).

    Returns an empty config when the files or section are absent. Raises
    ``ValueError`` on a malformed check entry rather than silently dropping it —
    a lost gate must never pass unnoticed.
    """
    section = _harness_section(repo_root, "verify")
    raw_checks = section.get("checks")
    if not isinstance(raw_checks, list):
        return VerifyConfig(())

    checks: list[VerifyCheck] = []
    for entry in raw_checks:
        checks.append(_parse_verify_check(entry))
    return VerifyConfig(tuple(checks))


def _parse_verify_check(entry: object) -> VerifyCheck:
    if not isinstance(entry, dict):
        raise ValueError(f"[verify.checks] entry must be a table, got {type(entry).__name__}")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("[verify.checks] entry is missing a non-empty 'name'")

    command = entry.get("command")
    if not (isinstance(command, list) and command and all(isinstance(a, str) for a in command)):
        raise ValueError(f"verify check {name!r} needs a non-empty 'command' list of strings")

    modes = entry.get("modes")
    if not (isinstance(modes, list) and modes and all(isinstance(m, str) for m in modes)):
        raise ValueError(f"verify check {name!r} needs a non-empty 'modes' list of strings")
    unknown = [m for m in modes if m not in VERIFY_MODES]
    if unknown:
        raise ValueError(
            f"verify check {name!r} has unknown mode(s) {unknown}; allowed: {list(VERIFY_MODES)}"
        )

    staged_suffix = entry.get("staged_suffix")
    if staged_suffix is not None and not isinstance(staged_suffix, str):
        raise ValueError(f"verify check {name!r} 'staged_suffix' must be a string")

    return VerifyCheck(
        name=name.strip(),
        command=tuple(command),
        modes=frozenset(modes),
        staged_suffix=staged_suffix or None,
    )


@dataclass(frozen=True)
class PolicyConfig:
    """Loop gate/checkpoint policy settings."""

    required_gates: tuple[str, ...]
    max_rework: int


def load_policy_config(repo_root: Path) -> PolicyConfig:
    """Load ``[policy]`` settings (basicly.toml + local overlay), with defaults."""
    defaults = PolicyConfig(required_gates=DEFAULT_REQUIRED_GATES, max_rework=DEFAULT_MAX_REWORK)

    section = _harness_section(repo_root, "policy")

    raw_gates = section.get("required_gates")
    if isinstance(raw_gates, list) and all(isinstance(g, str) for g in raw_gates):
        required_gates = tuple(g.strip() for g in raw_gates if g.strip())
    else:
        required_gates = defaults.required_gates

    max_rework = section.get("max_rework")
    if not (isinstance(max_rework, int) and not isinstance(max_rework, bool) and max_rework >= 0):
        max_rework = defaults.max_rework

    return PolicyConfig(required_gates=required_gates, max_rework=max_rework)


def load_technology_selection(repo_root: Path) -> frozenset[str] | None:
    """Load the ``[catalog] technologies`` selection from basicly.toml.

    Returns ``None`` when no selection is recorded (everything ships). Raises
    ``ValueError`` on a malformed or out-of-vocabulary selection — a typo that
    silently dropped catalog content must never pass unnoticed.
    """
    config_path = repo_root / CONFIG_FILE
    if not config_path.exists():
        return None

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    section = data.get("catalog", {})
    if not isinstance(section, dict) or "technologies" not in section:
        return None

    raw = section["technologies"]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("[catalog] technologies must be a list of strings")
    selection = frozenset(item.strip() for item in raw if item.strip())
    unknown = sorted(selection - TECHNOLOGIES)
    if unknown:
        raise ValueError(
            f"[catalog] technologies contains unknown value(s): {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(TECHNOLOGIES))})"
        )
    return selection


def record_technology_selection(repo_root: Path, technologies: list[str]) -> None:
    """Record the technology selection as ``[catalog] technologies`` in basicly.toml.

    Rewrites the existing ``technologies`` line in place when a ``[catalog]``
    section already carries one; otherwise appends a fresh section. The rest of
    the (user-owned) file is left untouched — the result is parsed back before
    writing, and on an unsupported layout the file is left as-is and a
    ``ValueError`` names the manual edit to make instead.
    """
    config_path = repo_root / CONFIG_FILE
    wanted = sorted(set(technologies))
    rendered = "[" + ", ".join(f'"{tech}"' for tech in wanted) + "]"
    line = f"technologies = {rendered}\n"
    section = f"\n# Catalog technology selection (see docs: technology scoping).\n[catalog]\n{line}"

    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG_TOML + section, encoding="utf-8")
        return

    original = config_path.read_text(encoding="utf-8")
    text = _splice_technologies(original, line, section)
    try:
        recorded = tomllib.loads(text).get("catalog", {}).get("technologies")
    except tomllib.TOMLDecodeError as exc:
        recorded = exc
    if not isinstance(recorded, list) or sorted(recorded) != wanted:
        raise ValueError(
            f"cannot record the technology selection in {CONFIG_FILE} (unsupported "
            f"[catalog] layout); set 'technologies = {rendered}' under [catalog] by hand"
        )
    config_path.write_text(text, encoding="utf-8")


def _splice_technologies(text: str, line: str, section: str) -> str:
    """Return *text* with the ``[catalog] technologies`` line replaced or added."""
    lines = text.splitlines(keepends=True)
    in_catalog = False
    header_index: int | None = None
    for index, current in enumerate(lines):
        stripped = current.strip()
        if stripped.startswith("["):
            in_catalog = stripped == "[catalog]"
            if in_catalog and header_index is None:
                header_index = index
        elif in_catalog and stripped.startswith("technologies"):
            lines[index] = line
            return "".join(lines)
    if header_index is not None:
        lines.insert(header_index + 1, line)
        return "".join(lines)
    return text.rstrip("\n") + "\n" + section


@dataclass(frozen=True)
class RunnerConfig:
    """Agent runner settings: the available adapters and the default selection."""

    specs: tuple[RunnerSpec, ...]
    default: str


def load_runner_config(repo_root: Path) -> RunnerConfig:
    """Load ``[runner]`` settings, merging config overrides onto the built-in adapters.

    Reads basicly.toml plus the local overlay. Returns the built-in adapters
    with ``default = "auto"`` when the files or section are absent. Each
    ``[[runner.agents]]`` entry overrides a built-in by name or adds a new
    agent. Raises ``ValueError`` on a malformed entry rather than silently
    dropping it — a lost adapter must never pass unnoticed.
    """
    section = _harness_section(repo_root, "runner")

    specs = {spec.name: spec for spec in BUILTIN_RUNNERS}
    raw_agents = section.get("agents")
    if isinstance(raw_agents, list):
        for entry in raw_agents:
            spec = _parse_runner_agent(entry)
            specs[spec.name] = spec

    _inject_copilot_deny_tools(specs)

    default = section.get("default")
    default = default.strip() if isinstance(default, str) and default.strip() else AUTO

    return RunnerConfig(specs=tuple(specs.values()), default=default)


def _inject_copilot_deny_tools(specs: dict[str, RunnerSpec]) -> None:
    """Fold the baseline deny-list into the copilot runner as ``--deny-tool`` specs.

    Invocation-time enforcement of the permissions.yaml deny-list for Copilot,
    which has no config-file deny (basicly-lqz5). Sourced from the same catalog
    manifest as the projected Claude deny (:mod:`basicly.permissions`), so the
    guardrail has one authoring home. A non-headless override under the name is
    left untouched — a handoff has no argv to carry flags.
    """
    spec = specs.get(COPILOT_RUNNER)
    if spec is None or spec.kind != HEADLESS:
        return
    deny = permissions.copilot_deny_specs(permissions.load_deny_rules())
    if deny:
        specs[COPILOT_RUNNER] = replace(spec, deny_tools=tuple(deny))


def _parse_runner_agent(entry: object) -> RunnerSpec:
    if not isinstance(entry, dict):
        raise ValueError(f"[[runner.agents]] entry must be a table, got {type(entry).__name__}")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("[[runner.agents]] entry is missing a non-empty 'name'")

    command = entry.get("command")
    if not (isinstance(command, list) and command and all(isinstance(a, str) for a in command)):
        raise ValueError(f"runner agent {name!r} needs a non-empty 'command' list of strings")

    prompt_via = entry.get("prompt_via", "arg")
    if prompt_via not in PROMPT_VIA:
        raise ValueError(
            f"runner agent {name!r} has unknown prompt_via {prompt_via!r}; "
            f"allowed: {list(PROMPT_VIA)}"
        )

    model = entry.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError(f"runner agent {name!r} has a 'model' that must be a non-empty string")
    model = model.strip() if isinstance(model, str) else None

    # Optional sandbox/approval guardrail overrides (basicly-t0kt), injected as
    # `--sandbox <mode>` / `-a <policy>` by format_command. An explicit override
    # replaces the builtin default (e.g. codex's), so a null is not re-defaulted.
    sandbox = entry.get("sandbox")
    approval = entry.get("approval")
    for key, value in (("sandbox", sandbox), ("approval", approval)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"runner agent {name!r} has a {key!r} that must be a non-empty string")
    sandbox = sandbox.strip() if isinstance(sandbox, str) else None
    approval = approval.strip() if isinstance(approval, str) else None

    # Optional opt-in bot git identity (basicly-smzg): both keys or neither.
    git_name = entry.get("git_name")
    git_email = entry.get("git_email")
    for key, value in (("git_name", git_name), ("git_email", git_email)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"runner agent {name!r} has a {key!r} that must be a non-empty string")
    git_name = git_name.strip() if isinstance(git_name, str) else None
    git_email = git_email.strip() if isinstance(git_email, str) else None
    if (git_name is None) != (git_email is None):
        raise ValueError(
            f"runner agent {name!r} must set both 'git_name' and 'git_email' or neither "
            "(a bot git identity needs a name and an email)"
        )

    # Optional usage-report format for token telemetry (basicly-kjc5.1). An
    # entry replaces a builtin wholesale (no re-defaulting, same stance as
    # sandbox/approval), so an override of claude/codex must restate the format
    # to keep exact usage extraction; absent falls back to the chars/4 estimate.
    usage_format = entry.get("usage_format")
    if usage_format is not None and usage_format not in USAGE_FORMATS:
        raise ValueError(
            f"runner agent {name!r} has unknown usage_format {usage_format!r}; "
            f"allowed: {list(USAGE_FORMATS)}"
        )

    return RunnerSpec(
        name=name.strip(),
        kind=HEADLESS,
        command=tuple(command),
        prompt_via=prompt_via,
        model=model,
        sandbox=sandbox,
        approval=approval,
        git_name=git_name,
        git_email=git_email,
        usage_format=usage_format,
    )


def load_project_paths(repo_root: Path) -> ProjectPaths:
    """Load path settings from basicly.toml, falling back to defaults."""
    defaults = ProjectPaths(
        core_fragments_dir=Path(".basicly/core/fragments"),
        overlay_fragments_dirs=(Path(".basicly-local/fragments"),),
        targets_dir=Path(".basicly/core/targets"),
        templates_dir=Path(".basicly/core/templates"),
        manifest_path=Path(".basicly/generated-manifest.json"),
        legacy_fragments_dir=Path(".basicly/fragments"),
    )

    config_path = repo_root / CONFIG_FILE
    if not config_path.exists():
        return defaults

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        return defaults

    core_fragments_dir = _parse_path_value(paths, "core_fragments", defaults.core_fragments_dir)
    targets_dir = _parse_path_value(paths, "targets", defaults.targets_dir)
    templates_dir = _parse_path_value(paths, "templates", defaults.templates_dir)
    manifest_path = _parse_path_value(paths, "manifest", defaults.manifest_path)

    overlay_fragments = _parse_overlay_paths(paths)
    if overlay_fragments is None:
        overlay_fragments_dirs = defaults.overlay_fragments_dirs
    else:
        overlay_fragments_dirs = tuple(overlay_fragments)

    return ProjectPaths(
        core_fragments_dir=core_fragments_dir,
        overlay_fragments_dirs=overlay_fragments_dirs,
        targets_dir=targets_dir,
        templates_dir=templates_dir,
        manifest_path=manifest_path,
        legacy_fragments_dir=defaults.legacy_fragments_dir,
    )


def _parse_path_value(paths: dict, key: str, default: Path) -> Path:
    value = paths.get(key)
    if isinstance(value, str) and value.strip():
        return Path(value)
    return default


def _parse_overlay_paths(paths: dict) -> list[Path] | None:
    value = paths.get("overlay_fragments")
    if value is None:
        return None

    if isinstance(value, str) and value.strip():
        return [Path(value)]

    if isinstance(value, list):
        parsed = [Path(item) for item in value if isinstance(item, str) and item.strip()]
        return parsed if parsed else None

    return None
