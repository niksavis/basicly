from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

from . import __version__, dropin, owned_store, permissions, session, tracker, tree_schema, ui
from .context_window import (
    AGENT_WINDOW,
    DECLARED_WINDOW,
    DEFAULT_CONTEXT_WINDOW,
    FALLBACK_WINDOW,
)
from .lane_log import DEFAULT_RETAINED_SESSIONS
from .models import ModelResolutionError
from .runner import (
    AGENT_TIER,
    AUTO,
    BUILTIN_RUNNERS,
    DEFAULT_MAX_AGENT_PROCESSES,
    DEFAULT_QUIET_AFTER,
    DEFAULT_STALL_AFTER,
    DENY_STYLES,
    FAMILY_DEFAULT_TIER,
    HEADLESS,
    PROMPT_VIA,
    USAGE_FORMATS,
    RunnerSpec,
    resolve_model,
)
from .schema import MODEL_TIERS, TECHNOLOGIES
from .tree_schema import Table

COPILOT_RUNNER = "copilot"

CONFIG_FILE = "basicly.toml"

LOCAL_CONFIG_FILE = "basicly.local.toml"

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
concurrency = 5
# Files this repo's convention has EVERY lane append its own entry to, which no
# bead's "## Scope" therefore names — a changelog, a release-notes file. Two
# lanes appending at the same anchor conflict when the later one rebases, so a
# declared path serializes the plan's children instead of letting the merge
# queue discover it. A child that really does not collide (it owns the entry, or
# writes a distinct section) declares the path in its own scope AND under
# "shared" to stay parallel. Absent = nothing is treated as append-only.
#
# append_only_paths = ["CHANGELOG.md"]
#
# Artifacts every lane's edit regenerates and no bead declares — a projection
# manifest, a lockfile. They collide the same way, but serializing the lanes buys
# nothing: the file is a function of the tree, so it simply needs rebuilding once.
# A landing rebase whose conflicts are ALL declared here is resolved by running each
# one's own command in the lane's worktree and continuing; a conflict touching any
# other path, or one the rebuild leaves a conflict marker in, still bounces to the
# lane, untouched. Keyed by path, so a path declared without a rebuild cannot exist.
#
# [worktree.regenerate_commands]
# ".basicly/generated-manifest.json" = ["basicly", "build"]

# Deterministic verify gate. Each check runs in the listed modes; a "staged"
# check with staged_suffix runs only against staged files of that suffix.
# No checks are enabled by default — declare the ones your stack actually has
# (an empty config passes vacuously; a configured command missing from PATH
# fails the run with a one-line message).
#
# A check may also declare fix_command — a deterministic, lossless repair the
# pre-commit hook applies to the staged files before gating (and that
# `basicly verify --fix` applies before the checks), so a mechanical repair like
# reformatting never costs a review cycle. The check still runs either way.
#
# Python examples:
#
# [[verify.checks]]
# name = "ruff"
# command = ["ruff", "check"]
# modes = ["fast", "full", "staged"]
# staged_suffix = ".py"
#
# [[verify.checks]]
# name = "ruff-format"
# command = ["ruff", "format", "--check"]
# fix_command = ["ruff", "format"]
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
# Units that may stand downstream of build - merged and parked in verify, or
# waiting on a ship checkpoint - before a further dispatch is refused. Bounds
# unlanded work, which [worktree] concurrency does not: that bounds how many
# lanes run at once, this bounds how much finished-but-unreviewed output piles
# up. Lower it when review, not machine capacity, is what runs out.
max_downstream_wip = 5

# The body sections each work type owes beyond the acceptance criteria every bead
# owes whatever its type. This is the Definition-of-Ready rule itself, so changing
# what a bug must carry is an edit here rather than a basicly release. A type left
# out of the table owes nothing extra; delete the table and the built-in set below
# applies, with a warning saying so.
[policy.type_sections]
bug = ["## Steps to Reproduce"]
epic = ["## Success Criteria"]

# Working-set sizing governor for decompose (factory D8). A child's context
# cost is estimated deterministically (instruction overhead + scope read-cost
# x per-class build factor, chars/4 tokens) and a plan outside the band is
# refused: above the max split into more packages; below the min (when the
# scope matches existing files) merge with a sibling in its scope group.
# [policy.sizing]
# working_set_min = 4600   # recalibrated 2026-09-12 when the comment ban removed
#                          # 41.75% of the tree's tokens (basicly-phglc2x): the min is
#                          # a proxy for "enough work to justify a lane" and its unit
#                          # moved, so it was scaled by the measured ratio. The max was
#                          # not: it is a hard context-capacity bound in real tokens.
# working_set_max = 64000
# calibration_min_samples = 10   # measured spend ratios replace the declared prior
#                                # only past this many paired records per class
# calibration_window = 50        # rolling run-record window per task class
# unsized_lane_quantile = 0.9    # bound for a lane with no readable scope: the
#                                # quantile of recent lane actuals, so at most one
#                                # lane in ten is expected to exceed it
# [policy.sizing.build_factor]
# task = 3.0
# bug = 2.0
# chore = 1.5

# Agent-agnostic runner: how the harness invokes a coding agent headless to do a
# node's work in its worktree. "auto" detects claude -> codex -> copilot on PATH,
# else falls back to the "manual" handoff (no command is guessed for an unknown
# agent). Add or override an agent with [[runner.agents]]; verify any command
# with `basicly runner dry-run` before a live run.
[runner]
default = "auto"
# Ceiling on concurrently live agent processes across every class the engine
# spawns: lane runners draw on [worktree] concurrency reserved slots, the decider
# on one reserved slot, and read-only helpers queue on the remainder.
# max_agent_processes = 8
# [[runner.agents]]
# name = "opencode"
# command = ["opencode", "run", "{prompt}"]
# prompt_via = "arg"   # or "stdin"
# model = "opus"       # optional: injects `--model opus` after the binary,
#                      # or substitutes a `{model}` placeholder if the command has one
# tier = "high"        # optional, and preferred over `model`: a portable model tier
#                      # (low | medium | high | maximum) resolved to the concrete id
#                      # this family's surface accepts, via .basicly/core/models.
#                      # A tier that resolves to nothing refuses the dispatch rather
#                      # than falling back to another tier's model. `model` wins if both.
# vendor = "openai"    # optional: whose model a tier resolves to. Only meaningful on a
#                      # multi-vendor surface (copilot serves four); defaults per family.
# sandbox = "workspace-write"   # optional: injects `--sandbox workspace-write` (codex
#                               # defaults this); network is disabled by default in it
# approval = "never"            # optional: injects `-a never` (codex defaults this).
#                               # Validated against the CLI's own enum by
#                               # `basicly runner dry-run`: a value it rejects
#                               # fails every dispatch at argument parsing.
# git_name = "opencode-bot"        # optional bot git identity: dispatched commits
# git_email = "bot@example.com"    # use it (both keys or neither). Must satisfy
#                                  # basicly.identityAllowEmail when strict mode is on.
"""


DEFAULT_WORKTREE_CONCURRENCY = 5

VERIFY_MODES = ("fast", "full", "staged")

DEFAULT_REQUIRED_GATES = ("verify",)
DEFAULT_MAX_REWORK = 2

DEFAULT_MAX_DOWNSTREAM_WIP = 5

VERIFY_GATE_PROVIDER = "basicly-verify"
RUBRIC_GATE_PROVIDER = "basicly-rubric"
ENGINE_GATE_PROVIDERS = frozenset({VERIFY_GATE_PROVIDER, RUBRIC_GATE_PROVIDER})

DEFAULT_WORKING_SET_MIN = 4_600
DEFAULT_WORKING_SET_MAX = 264_000
DEFAULT_BUILD_FACTOR_SEEDS = {"task": 3.0, "bug": 2.0, "chore": 1.5}
DEFAULT_BUILD_FACTOR = 3.0
DEFAULT_CALIBRATION_MIN_SAMPLES = 10
DEFAULT_CALIBRATION_WINDOW = 50
DEFAULT_CONTEXT_CEILING = 0.6
DEFAULT_UNSIZED_LANE_QUANTILE = 0.9

CHECKPOINTS = ("classify", "decompose", "ship")

LOOP_PHASES = ("intake", "classify", "decompose", "build", "verify", "validate", "ship")

AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3")
DEFAULT_AUTONOMY = "L0"

DEFAULT_DECIDER_MAX_DECISIONS = 50

DEFAULT_MAX_SUBTASKS_PER_LANE = 10

SCOPE_COLLISION_POLICIES = ("block", "warn")
DEFAULT_SCOPE_COLLISION = "block"

WORK_TYPES = ("bug", "chore", "task", "feature", "epic")

DEFAULT_TYPE_SECTIONS: dict[str, tuple[str, ...]] = {
    "bug": ("## Steps to Reproduce",),
    "epic": ("## Success Criteria",),
}


@dataclass(frozen=True)
class ProjectPaths:
    core_fragments_dir: Path
    overlay_fragments_dirs: tuple[Path, ...]
    targets_dir: Path
    templates_dir: Path
    manifest_path: Path
    legacy_fragments_dir: Path

    @property
    def core_root(self) -> Path:

        return self.core_fragments_dir.parent

    @property
    def state_path(self) -> Path:

        return self.core_root.parent / "state" / "install.json"


_OPEN_TABLE = Table(open_keys=True)

_VERIFY_CHECK_TABLE = Table(
    keys=frozenset({"name", "command", "modes", "staged_suffix", "fix_command", "inputs"})
)

_RATCHET_TABLE = Table(
    keys=frozenset({"count_delta", "rebaseline_reason"}),
    tables={"frozen": _OPEN_TABLE, "rebaselined": _OPEN_TABLE},
)

_RUNNER_AGENT_TABLE = Table(
    keys=frozenset({
        "name",
        "command",
        "prompt_via",
        "model",
        "tier",
        "vendor",
        "sandbox",
        "approval",
        "deny_style",
        "git_name",
        "git_email",
        "usage_format",
        "context_window",
    })
)

_SIZING_TABLE = Table(
    keys=frozenset({
        "working_set_min",
        "working_set_max",
        "calibration_min_samples",
        "calibration_window",
        "context_ceiling",
        "unsized_lane_quantile",
    }),
    tables={"build_factor": _OPEN_TABLE},
)

CONFIG_SCHEMA: dict[str, Table] = {
    "paths": Table(
        keys=frozenset({"core_fragments", "overlay_fragments", "targets", "templates", "manifest"})
    ),
    "catalog": Table(keys=frozenset({"technologies", "rank1_floor", "rank1_floor_high_water"})),
    "worktree": Table(
        keys=frozenset({"base_branch", "concurrency", "append_only_paths"}),
        tables={"regenerate_commands": _OPEN_TABLE},
    ),
    "verify": Table(arrays={"checks": _VERIFY_CHECK_TABLE}),
    "ratchet": Table(
        keys=frozenset({"base_commit"}),
        tables={
            "code_citations": _RATCHET_TABLE,
            "module_size": _RATCHET_TABLE,
            "noqa_debt": _RATCHET_TABLE,
            "release_notes": _RATCHET_TABLE,
            "spend_accuracy": _RATCHET_TABLE,
        },
    ),
    "policy": Table(
        keys=frozenset({
            "required_gates",
            "max_rework",
            "autonomy",
            "notify_command",
            "decider_max_decisions",
            "max_subtasks_per_lane",
            "max_downstream_wip",
            "scope_collision",
        }),
        tables={
            "evidence": _OPEN_TABLE,
            "sizing": _SIZING_TABLE,
            "type_sections": _OPEN_TABLE,
        },
    ),
    "runner": Table(
        keys=frozenset({
            "default",
            "decider",
            "runner_timeout",
            "max_agent_processes",
            "stall_after",
            "quiet_after",
            "lane_token_ceiling",
            "lane_log_sessions",
            "default_tier",
            "copilot_session_store",
        }),
        tables={"context_windows": _OPEN_TABLE},
        arrays={"agents": _RUNNER_AGENT_TABLE},
    ),
    "tracker": Table(keys=frozenset({"mode", "prefix"})),
    "privacy": Table(arrays={"denied": Table(keys=frozenset({"name", "token"}))}),
}

_ROOT_TABLE = Table(tables=CONFIG_SCHEMA)


def _validation_schema(repo_root: Path) -> Table:

    schema = tree_schema.read(repo_root)
    return _ROOT_TABLE if schema is None else Table(tables=schema)


def _config_documents(repo_root: Path) -> dict[str, dict]:

    documents: dict[str, dict] = {}
    base = repo_root / CONFIG_FILE
    if base.exists():
        documents[CONFIG_FILE] = tomllib.loads(base.read_text(encoding="utf-8"))
    documents.update(dropin.documents(repo_root))
    local = repo_root / LOCAL_CONFIG_FILE
    if local.exists():
        documents[LOCAL_CONFIG_FILE] = tomllib.loads(local.read_text(encoding="utf-8"))
    return documents


def unknown_config_keys(repo_root: Path) -> list[str]:

    return _problems(_config_documents(repo_root), _validation_schema(repo_root))


@dataclass(frozen=True)
class _Pass:
    filename: str
    root: Table


def _problems(documents: dict[str, dict], root: Table) -> list[str]:
    return [
        problem
        for filename, data in documents.items()
        for problem in _unknown_in_table(_Pass(filename, root), data, root, "")
    ]


def _unknown_in_table(walk: _Pass, table: dict, schema: Table, path: str) -> list[str]:
    problems: list[str] = []
    for name, value in table.items():
        child = f"{path}.{name}" if path else str(name)
        if name in schema.tables and isinstance(value, dict):
            problems += _unknown_in_table(walk, value, schema.tables[name], child)
        elif name in schema.arrays and isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    problems += _unknown_in_table(walk, entry, schema.arrays[name], child)
        elif name in schema.keys or name in schema.tables or name in schema.arrays:
            continue
        elif not schema.open_keys:
            problems.append(_unknown_message(walk, name, value, schema, path))
    return problems


def _unknown_message(walk: _Pass, name: object, value: object, schema: Table, path: str) -> str:
    kind = "section" if isinstance(value, dict) else "key"
    if path:
        accepted = ", ".join(sorted(schema.keys | set(schema.tables) | set(schema.arrays)))
        message = (
            f"{walk.filename}: unknown {kind} {name!r} in [{path}]; [{path}] accepts {accepted}"
        )
    else:
        sections = ", ".join(f"[{section}]" for section in sorted(walk.root.tables))
        message = f"{walk.filename}: unknown {kind} {name!r}; this file's sections are {sections}"

    hints = _accepting_clause(name, "", walk.root)
    if isinstance(value, dict):
        hints += [clause for key in value for clause in _accepting_clause(key, "its ", walk.root)]
    if hints:
        message += " - " + "; ".join(hints)
    return message


def _accepting_clause(name: object, prefix: str, root: Table) -> list[str]:
    where = _accepting(name, root)
    return [f"{prefix}{name!r} is accepted in {', '.join(where)}"] if where else []


def _accepting(name: object, root: Table) -> list[str]:
    found: list[str] = []

    def walk(schema: Table, path: str, array: bool) -> None:
        if name in schema.keys:
            found.append(f"[[{path}]]" if array else f"[{path}]")
        for child, table in schema.tables.items():
            walk(table, f"{path}.{child}" if path else child, False)
        for child, table in schema.arrays.items():
            walk(table, f"{path}.{child}" if path else child, True)

    walk(root, "", False)
    return sorted(found)


def _validated_documents(repo_root: Path) -> dict[str, dict]:

    documents = _config_documents(repo_root)
    if problems := _problems(documents, _validation_schema(repo_root)):
        unreadable = (
            tree_schema.ships_engine_source(repo_root) and tree_schema.read(repo_root) is None
        )
        raise ValueError(
            "\n".join(problems)
            + f"\nbasicly {__version__} refuses a config name it cannot honour rather than "
            "ignoring it, because an ignored key leaves the file stating one behaviour and "
            "the engine performing another. Remove or correct the name, or upgrade basicly "
            "if it comes from a newer version."
            + (f"\n{tree_schema.ORDERING_RULE}" if unreadable else "")
        )
    return documents


def _harness_section(repo_root: Path, name: str) -> dict:

    merged: dict = {}
    for data in _validated_documents(repo_root).values():
        section = data.get(name, {})
        if isinstance(section, dict):
            merged.update(section)
    merged.update(session.overrides_for(name))
    return merged


def load_tracker_prefix(repo_root: Path) -> str | None:

    prefix = _harness_section(repo_root, "tracker").get("prefix")
    return str(prefix) if prefix else None


def load_tracker_mode(repo_root: Path) -> str:

    mode = _harness_section(repo_root, "tracker").get("mode")
    if mode is None:
        return tracker.DEFAULT_TRACKER_MODE
    if mode not in tracker.TRACKER_MODES:
        raise ValueError(
            f"[tracker] mode = {mode!r} is not one of {', '.join(tracker.TRACKER_MODES)}; "
            f"the owned ledger is the only store this engine has"
        )
    return mode


tracker.set_mode_reader(load_tracker_mode)
owned_store.set_prefix_reader(load_tracker_prefix)


@dataclass(frozen=True)
class WorktreeConfig:
    base_branch: str | None
    concurrency: int
    append_only_paths: tuple[str, ...] = ()
    regenerate_commands: dict[str, tuple[str, ...]] = field(default_factory=dict)


def load_worktree_config(repo_root: Path) -> WorktreeConfig:
    defaults = WorktreeConfig(base_branch=None, concurrency=DEFAULT_WORKTREE_CONCURRENCY)

    section = _harness_section(repo_root, "worktree")

    base = section.get("base_branch")
    base_branch = base.strip() if isinstance(base, str) and base.strip() else None

    concurrency = section.get("concurrency")
    if not (isinstance(concurrency, int) and not isinstance(concurrency, bool) and concurrency > 0):
        concurrency = defaults.concurrency

    raw_paths = section.get("append_only_paths")
    append_only = _append_only_paths(raw_paths) if isinstance(raw_paths, list) else ()

    raw_generated = section.get("regenerate_commands")
    regenerate = _regenerate_commands(raw_generated) if isinstance(raw_generated, dict) else {}

    return WorktreeConfig(
        base_branch=base_branch,
        concurrency=concurrency,
        append_only_paths=append_only,
        regenerate_commands=regenerate,
    )


CHANGELOG_FRAGMENT_DIR = "changelog.d"


def lane_scope(record: str) -> tuple[str, ...]:

    return (
        f"{dropin.FRAGMENT_DIR}/{record}.toml",
        f"{CHANGELOG_FRAGMENT_DIR}/{record}.*.md",
    )


_PATH_LIST_WILDCARDS = "*?["

_GLOB_REFUSALS = {
    "append_only_paths": (
        "an append-only path must be one literal path, because this list adds "
        "serialization edges and a wildcard would serialize every lane over a subtree "
        "nobody can name"
    ),
    "regenerate_commands": (
        "a generated path must be one literal path, because this table authorises the "
        "engine to overwrite both sides of a conflict on it, and a wildcard would extend "
        "that authority over source files nobody listed"
    ),
}


def _append_only_paths(entries: list) -> tuple[str, ...]:

    return _literal_paths(entries, "append_only_paths")


def _literal_paths(entries: list, key: str) -> tuple[str, ...]:

    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(
                f"[worktree] {key} entries must be paths, got {type(entry).__name__}: {entry!r}"
            )
        path = entry.strip()
        if not path:
            continue
        if any(char in path for char in _PATH_LIST_WILDCARDS):
            raise ValueError(f"[worktree] {key} entry {path!r} is a glob; {_GLOB_REFUSALS[key]}")
        paths.append(path)
    return tuple(paths)


def _regenerate_commands(section: dict) -> dict[str, tuple[str, ...]]:

    table = {path.strip(): argv for path, argv in section.items()}
    commands: dict[str, tuple[str, ...]] = {}
    for path in _literal_paths(list(table), "regenerate_commands"):
        label = f"[worktree.regenerate_commands] {path!r}"
        entries = table[path]
        if not isinstance(entries, list) or not all(isinstance(word, str) for word in entries):
            raise ValueError(f"{label} must be an argv list of strings, got {entries!r}")
        argv = tuple(word.strip() for word in entries if word.strip())
        if not argv:
            raise ValueError(
                f"{label} declares no command; the engine cannot rebuild an artifact it has "
                "no command for, so the declaration would silently do nothing and the "
                "conflict would still bounce the lane"
            )
        commands[path] = argv
    return commands


@dataclass(frozen=True)
class VerifyCheck:
    name: str
    command: tuple[str, ...]
    modes: frozenset[str]
    staged_suffix: str | None = None
    fix_command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class VerifyConfig:
    checks: tuple[VerifyCheck, ...]

    def for_mode(self, mode: str) -> tuple[VerifyCheck, ...]:
        return tuple(check for check in self.checks if mode in check.modes)


def load_verify_config(repo_root: Path) -> VerifyConfig:

    declared: list[object] = []
    for filename, data in _validated_documents(repo_root).items():
        section = data.get("verify")
        checks = section.get("checks") if isinstance(section, dict) else None
        if isinstance(checks, list):
            declared = list(checks) if filename == LOCAL_CONFIG_FILE else [*declared, *checks]
    override = session.overrides_for("verify").get("checks")
    if isinstance(override, list):
        declared = list(override)
    return VerifyConfig(tuple(_parse_verify_check(entry) for entry in declared))


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

    fix_command = entry.get("fix_command")
    if fix_command is not None and not (
        isinstance(fix_command, list)
        and fix_command
        and all(isinstance(a, str) for a in fix_command)
    ):
        raise ValueError(f"verify check {name!r} 'fix_command' must be a non-empty list of strings")

    return VerifyCheck(
        name=name.strip(),
        command=tuple(command),
        modes=frozenset(modes),
        staged_suffix=staged_suffix or None,
        fix_command=tuple(fix_command) if fix_command else None,
    )


@dataclass(frozen=True)
class PolicyConfig:
    required_gates: tuple[str, ...]
    max_rework: int
    autonomy: str = DEFAULT_AUTONOMY
    notify_command: tuple[str, ...] = ()
    decider_max_decisions: int = DEFAULT_DECIDER_MAX_DECISIONS
    max_subtasks_per_lane: int = DEFAULT_MAX_SUBTASKS_PER_LANE
    max_downstream_wip: int = DEFAULT_MAX_DOWNSTREAM_WIP
    evidence: dict[str, str] = field(default_factory=dict)
    scope_collision: str = DEFAULT_SCOPE_COLLISION


def load_policy_config(repo_root: Path) -> PolicyConfig:
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

    autonomy = section.get("autonomy")
    if not (isinstance(autonomy, str) and autonomy.strip() in AUTONOMY_LEVELS):
        autonomy = DEFAULT_AUTONOMY
    else:
        autonomy = autonomy.strip()

    raw_notify = section.get("notify_command")
    notify_command: tuple[str, ...] = ()
    if (
        isinstance(raw_notify, list)
        and raw_notify
        and all(isinstance(a, str) and a.strip() for a in raw_notify)
    ):
        notify_command = tuple(raw_notify)

    raw_evidence = section.get("evidence")
    evidence = (
        {str(phase): str(path).strip() for phase, path in raw_evidence.items()}
        if isinstance(raw_evidence, dict)
        else {}
    )

    raw_collision = section.get("scope_collision")
    scope_collision = (
        raw_collision.strip()
        if isinstance(raw_collision, str) and raw_collision.strip() in SCOPE_COLLISION_POLICIES
        else DEFAULT_SCOPE_COLLISION
    )

    return PolicyConfig(
        required_gates=required_gates,
        max_rework=max_rework,
        autonomy=autonomy,
        notify_command=notify_command,
        evidence=evidence,
        scope_collision=scope_collision,
        decider_max_decisions=_positive_int(
            section.get("decider_max_decisions"), DEFAULT_DECIDER_MAX_DECISIONS
        ),
        max_subtasks_per_lane=_positive_int(
            section.get("max_subtasks_per_lane"), DEFAULT_MAX_SUBTASKS_PER_LANE
        ),
        max_downstream_wip=_positive_int(
            section.get("max_downstream_wip"), DEFAULT_MAX_DOWNSTREAM_WIP
        ),
    )


def load_type_sections(repo_root: Path) -> dict[str, tuple[str, ...]]:

    declared = _harness_section(repo_root, "policy").get("type_sections")
    if not isinstance(declared, dict):
        _say_type_sections_fallback()
        return dict(DEFAULT_TYPE_SECTIONS)
    if unknown := sorted(name for name in declared if name not in WORK_TYPES):
        raise ValueError(
            f"[policy.type_sections] names unknown work type(s) {', '.join(unknown)}; "
            f"allowed: {list(WORK_TYPES)}"
        )
    return {name: _type_section_headings(name, value) for name, value in declared.items()}


def _type_section_headings(work_type: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(heading, str) and heading.strip() for heading in value
    ):
        raise ValueError(
            f"[policy.type_sections] {work_type!r} must be a list of section headings, "
            f"not {value!r}"
        )
    return tuple(heading.strip() for heading in value)


@cache
def _say_type_sections_fallback() -> None:

    ui.warn(
        f"[policy.type_sections] is undeclared, so basicly {__version__}'s built-in "
        "per-work-type sections apply: "
        + "; ".join(f"{name} {list(sections)}" for name, sections in DEFAULT_TYPE_SECTIONS.items())
        + ". Declare the table in basicly.toml to own the rule."
    )


@dataclass(frozen=True)
class SizingConfig:
    working_set_min: int
    working_set_max: int
    build_factors: dict[str, float]
    calibration_min_samples: int
    calibration_window: int
    configured_build_factors: frozenset[str] = frozenset()
    context_ceiling: float = DEFAULT_CONTEXT_CEILING
    unsized_lane_quantile: float = DEFAULT_UNSIZED_LANE_QUANTILE


def load_sizing_config(repo_root: Path) -> SizingConfig:

    section = _harness_section(repo_root, "policy").get("sizing")
    if not isinstance(section, dict):
        section = {}

    working_set_min = _positive_int(section.get("working_set_min"), DEFAULT_WORKING_SET_MIN)
    working_set_max = _positive_int(section.get("working_set_max"), DEFAULT_WORKING_SET_MAX)
    if working_set_min >= working_set_max:
        working_set_min = DEFAULT_WORKING_SET_MIN
        working_set_max = DEFAULT_WORKING_SET_MAX

    factors = dict(DEFAULT_BUILD_FACTOR_SEEDS)
    configured: set[str] = set()
    raw_factors = section.get("build_factor")
    if isinstance(raw_factors, dict):
        for task_class, value in raw_factors.items():
            number = isinstance(value, int | float) and not isinstance(value, bool)
            if isinstance(task_class, str) and task_class.strip() and number and value > 0:
                factors[task_class.strip()] = float(value)
                configured.add(task_class.strip())

    return SizingConfig(
        working_set_min=working_set_min,
        working_set_max=working_set_max,
        build_factors=factors,
        configured_build_factors=frozenset(configured),
        calibration_min_samples=_positive_int(
            section.get("calibration_min_samples"), DEFAULT_CALIBRATION_MIN_SAMPLES
        ),
        calibration_window=_positive_int(
            section.get("calibration_window"), DEFAULT_CALIBRATION_WINDOW
        ),
        context_ceiling=_window_fraction(section.get("context_ceiling")),
        unsized_lane_quantile=_quantile_fraction(section.get("unsized_lane_quantile")),
    )


def _quantile_fraction(value: object) -> float:

    if isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= 1:
        return float(value)
    return DEFAULT_UNSIZED_LANE_QUANTILE


def _window_fraction(value: object) -> float:

    if isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= 1:
        return float(value)
    return DEFAULT_CONTEXT_CEILING


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _positive_float(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default


def load_technology_selection(repo_root: Path) -> frozenset[str] | None:

    data = _validated_documents(repo_root).get(CONFIG_FILE)
    if data is None:
        return None

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


def _rate(section: Mapping[str, object], key: str) -> float | None:

    if key not in section:
        return None
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"[catalog] {key} must be a number between 0 and 1")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"[catalog] {key} must be between 0 and 1 (got {value})")
    return float(value)


def load_routing_floor(repo_root: Path) -> tuple[float | None, float | None]:

    data = _validated_documents(repo_root).get(CONFIG_FILE)
    if data is None:
        return None, None
    section = data.get("catalog", {})
    if not isinstance(section, dict):
        return None, None
    return _rate(section, "rank1_floor"), _rate(section, "rank1_floor_high_water")


def record_technology_selection(repo_root: Path, technologies: list[str]) -> None:

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
    specs: tuple[RunnerSpec, ...]
    default: str
    decider: str | None = None
    runner_timeout: float = 3600.0
    max_agent_processes: int = DEFAULT_MAX_AGENT_PROCESSES
    stall_after: float = DEFAULT_STALL_AFTER
    quiet_after: float = DEFAULT_QUIET_AFTER
    lane_token_ceiling: int = 0
    lane_log_sessions: int = DEFAULT_RETAINED_SESSIONS
    default_tier: str | None = None


def load_runner_config(repo_root: Path) -> RunnerConfig:

    section = _harness_section(repo_root, "runner")

    default_tier = section.get("default_tier")
    if default_tier is not None and default_tier not in MODEL_TIERS:
        raise ValueError(
            f"[runner] default_tier {default_tier!r} is not a known model tier; "
            f"allowed: {list(MODEL_TIERS)}"
        )

    specs = {spec.name: spec for spec in BUILTIN_RUNNERS}
    raw_agents = section.get("agents")
    if isinstance(raw_agents, list):
        for entry in raw_agents:
            spec = _parse_runner_agent(entry)
            specs[spec.name] = spec

    _inject_copilot_deny_tools(specs)
    _inject_copilot_session_store(specs, section)
    _apply_context_windows(specs, section)
    _apply_default_tier(specs, default_tier)

    default = section.get("default")
    default = default.strip() if isinstance(default, str) and default.strip() else AUTO

    decider = section.get("decider")
    decider = decider.strip() if isinstance(decider, str) and decider.strip() else None

    raw_timeout = section.get("runner_timeout")
    if (
        not isinstance(raw_timeout, int | float)
        or isinstance(raw_timeout, bool)
        or raw_timeout <= 0
    ):
        raw_timeout = 3600.0
    timeout = float(raw_timeout)

    return RunnerConfig(
        specs=tuple(specs.values()),
        default=default,
        decider=decider,
        runner_timeout=timeout,
        max_agent_processes=_positive_int(
            section.get("max_agent_processes"), DEFAULT_MAX_AGENT_PROCESSES
        ),
        stall_after=_positive_float(section.get("stall_after"), DEFAULT_STALL_AFTER),
        quiet_after=_positive_float(section.get("quiet_after"), DEFAULT_QUIET_AFTER),
        lane_token_ceiling=max(0, _positive_int(section.get("lane_token_ceiling"), 0)),
        lane_log_sessions=_positive_int(
            section.get("lane_log_sessions"), DEFAULT_RETAINED_SESSIONS
        ),
        default_tier=default_tier,
    )


def _inject_copilot_deny_tools(specs: dict[str, RunnerSpec]) -> None:

    spec = specs.get(COPILOT_RUNNER)
    if spec is None or spec.kind != HEADLESS:
        return
    deny = permissions.copilot_deny_specs(permissions.load_deny_rules())
    if deny:
        specs[COPILOT_RUNNER] = replace(spec, deny_tools=tuple(deny))


def _inject_copilot_session_store(specs: dict[str, RunnerSpec], section: dict) -> None:

    value = section.get("copilot_session_store")
    spec = specs.get(COPILOT_RUNNER)
    if spec is None or not isinstance(value, str) or not value.strip():
        return
    specs[COPILOT_RUNNER] = replace(spec, session_store=Path(value.strip()))


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

    tier, vendor = _parse_model_tier(entry, name)

    sandbox = entry.get("sandbox")
    approval = entry.get("approval")
    for key, value in (("sandbox", sandbox), ("approval", approval)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"runner agent {name!r} has a {key!r} that must be a non-empty string")
    sandbox = sandbox.strip() if isinstance(sandbox, str) else None
    approval = approval.strip() if isinstance(approval, str) else None

    deny_style = entry.get("deny_style")
    if deny_style is not None and deny_style not in DENY_STYLES:
        raise ValueError(
            f"runner agent {name!r} has unknown deny_style {deny_style!r}; "
            f"allowed: {list(DENY_STYLES)}"
        )

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

    usage_format = entry.get("usage_format")
    if usage_format is not None and usage_format not in USAGE_FORMATS:
        raise ValueError(
            f"runner agent {name!r} has unknown usage_format {usage_format!r}; "
            f"allowed: {list(USAGE_FORMATS)}"
        )

    context_window, context_window_source = _context_window(entry, name)

    return RunnerSpec(
        name=name.strip(),
        kind=HEADLESS,
        command=tuple(command),
        prompt_via=prompt_via,
        model=model,
        tier=tier,
        vendor=vendor,
        tier_source=AGENT_TIER if tier is not None else None,
        deny_style=deny_style,
        sandbox=sandbox,
        approval=approval,
        git_name=git_name,
        git_email=git_email,
        usage_format=usage_format,
        context_window=context_window,
        context_window_source=context_window_source,
    )


def _apply_default_tier(specs: dict[str, RunnerSpec], default_tier: str | None) -> None:

    if default_tier is None:
        return
    for name, spec in specs.items():
        if spec.tier is None and spec.model is None:
            specs[name] = replace(spec, tier=default_tier, tier_source=FAMILY_DEFAULT_TIER)


def _parse_model_tier(entry: dict, name: str) -> tuple[str | None, str | None]:

    tier = entry.get("tier")
    if tier is not None and tier not in MODEL_TIERS:
        raise ValueError(
            f"runner agent {name!r} has unknown model tier {tier!r}; allowed: {list(MODEL_TIERS)}"
        )
    vendor = entry.get("vendor")
    if vendor is not None and (not isinstance(vendor, str) or not vendor.strip()):
        raise ValueError(f"runner agent {name!r} has a 'vendor' that must be a non-empty string")
    return tier, vendor.strip() if isinstance(vendor, str) else None


def untiered_metered_runners(config: RunnerConfig, *, repo_root: Path | None = None) -> list[str]:

    problems: list[str] = []
    for spec in config.specs:
        if spec.kind != HEADLESS:
            continue
        if spec.tier is None and spec.model is None:
            problems.append(
                f"runner {spec.name!r} meters what it spends but declares no model tier and no "
                f"model, so its cost lands on a model nobody named and the tier refusal cannot "
                f"fire; declare [runner] default_tier, or a 'tier' on [[runner.agents]] for "
                f"{spec.name!r}"
            )
            continue
        try:
            resolution = resolve_model(spec, repo_root=repo_root)
        except ModelResolutionError as exc:
            problems.append(f"runner {spec.name!r} declares a tier that cannot be pinned: {exc}")
            continue
        if resolution.model is None:
            problems.append(
                f"runner {spec.name!r} declares tier {resolution.tier!r} ({resolution.source}) but "
                f"no model was pinned, so the dispatch is metered against the session's own "
                f"model: {resolution.note or 'the family cannot express a model'}"
            )
    return problems


def _context_window(entry: dict, name: str) -> tuple[int, str]:

    value = entry.get("context_window", DEFAULT_CONTEXT_WINDOW)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"runner agent {name!r} has a 'context_window' that must be an integer")
    if value <= 0:
        raise ValueError(f"runner agent {name!r} has a 'context_window' that must be positive")
    source = AGENT_WINDOW if "context_window" in entry else FALLBACK_WINDOW
    return value, source


def _apply_context_windows(specs: dict[str, RunnerSpec], section: dict) -> None:

    windows = section.get("context_windows")
    if not isinstance(windows, dict):
        return
    for name, value in windows.items():
        spec = specs.get(name)
        if spec is None:
            raise ValueError(
                f"[runner] context_windows declares a window for unknown agent {name!r}; "
                f"known agents: {sorted(specs)}"
            )
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(
                f"[runner] context_windows {name!r} must be a positive integer of tokens"
            )
        specs[name] = replace(spec, context_window=value, context_window_source=DECLARED_WINDOW)


def load_project_paths(repo_root: Path) -> ProjectPaths:

    defaults = ProjectPaths(
        core_fragments_dir=Path(".basicly/core/fragments"),
        overlay_fragments_dirs=(Path(".basicly-local/fragments"),),
        targets_dir=Path(".basicly/core/targets"),
        templates_dir=Path(".basicly/core/templates"),
        manifest_path=Path(".basicly/generated-manifest.json"),
        legacy_fragments_dir=Path(".basicly/fragments"),
    )

    data = _validated_documents(repo_root).get(CONFIG_FILE)
    if data is None:
        return defaults

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
        return parsed or None

    return None
