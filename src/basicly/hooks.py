"""Project catalog git-hook scripts and their manager wiring into a consumer repo.

The catalog describes hooks tool-agnostically in ``hooks.yaml``; this module
renders that into a hook manager's native config. Only pre-commit is supported
today (``.pre-commit-config.yaml``), and the managed hooks are confined to a
single ``repo: local`` block so foreign repos/hooks the consumer already has are
never clobbered. See docs/architecture/architecture.md §10.2, §16.

What that config *looks like* is :mod:`basicly.precommit_config`'s answer, not this
module's: the boundary is the installation against the document. Everything here
touches the consumer's tree — loads the manifest, writes files, prunes retired ones,
runs ``pre-commit install`` — and asks that module for the text to write and for a
verdict on the text it read back.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import bundled_catalog_root, iter_catalog_files
from .precommit_config import (
    excluded_hooks_present,
    managed_hook_mismatches,
    parse_config,
    render_precommit_config,
)
from .projection import SyncResult, atomic_write_text, sync_file
from .schema import ValidationError, technology_selected

HOOKS_MANIFEST = "hooks.yaml"
PRECOMMIT_CONFIG = ".pre-commit-config.yaml"

# Hook managers a manifest entry may target: git hooks rendered into the
# pre-commit config, Claude Code agent hooks rendered into the committed
# .claude/settings.json (see claude_settings.sync_agent_hooks), or Copilot
# agent hooks rendered into a managed .github/hooks/basicly-<id>.json.
GIT_MANAGER = "git"
CLAUDE_MANAGER = "claude"
COPILOT_MANAGER = "copilot"
HOOK_MANAGERS = (GIT_MANAGER, CLAUDE_MANAGER, COPILOT_MANAGER)

# The host executable that reads each agent manager's projected hooks. Git has no
# entry: its hooks are run by git itself, which `missing_hook_installations` already
# answers for. Both hosts have a hook surface, re-probed 2026-08-15 against the
# installed binaries — claude 2.1.233 (`--include-hook-events`, `--bare` skips hooks)
# and Copilot CLI 1.0.79 (`copilot help config`: a `hooks` key and `disableAllHooks`).
# The 2026-08-08 "copilot has no hook surface" probe was wrong and is retracted in
# `.basicly/core/kit/tier/README.md`; what remains true there is narrower — no copilot
# hook is known to fire for an *agent spawn*.
AGENT_HOOK_HOSTS = {CLAUDE_MANAGER: "claude", COPILOT_MANAGER: "copilot"}

COPILOT_HOOKS_DIR = Path(".github/hooks")
# Filename prefix marking a Copilot hook file as basicly-managed (JSON carries
# no comment marker; ownership rides the name).
COPILOT_MANAGED_PREFIX = "basicly-"

# Copilot hook event names its .github/hooks schema accepts for `stage`.
COPILOT_EVENTS = {"posttooluse": "postToolUse", "pretooluse": "preToolUse"}


@dataclass(frozen=True)
class HookSpec:
    """A single catalog hook, described independently of any hook manager."""

    id: str
    script: str
    stage: str
    pass_filenames: bool = False
    always_run: bool = False
    manager: str = GIT_MANAGER
    technologies: tuple[str, ...] = ()
    # Agent-hook tool filter (claude manager only): regex the manager applies
    # to the tool name. Empty = the manager's default write-tools matcher.
    matcher: str = ""


def _catalog_hooks_dir() -> Path:
    return bundled_catalog_root() / "hooks"


def load_hook_specs(hooks_dir: Path | None = None) -> list[HookSpec]:
    """Load hook specs from ``hooks.yaml`` in the given (or bundled) hooks dir."""
    hooks_dir = hooks_dir or _catalog_hooks_dir()
    manifest = hooks_dir / HOOKS_MANIFEST
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    entries = data.get("hooks")
    if not isinstance(entries, list):
        raise ValueError(f"{manifest}: 'hooks' must be a list")

    specs: list[HookSpec] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{manifest}: each hook must be a mapping")
        missing = [key for key in ("id", "script", "stage") if key not in entry]
        if missing:
            raise ValueError(f"{manifest}: hook entry is missing {', '.join(missing)}")
        manager = str(entry.get("manager", GIT_MANAGER))
        if manager not in HOOK_MANAGERS:
            raise ValueError(
                f"{manifest}: hook '{entry['id']}' has unknown manager {manager!r}; "
                f"allowed: {list(HOOK_MANAGERS)}"
            )
        technologies = entry.get("technologies") or []
        if not isinstance(technologies, list) or not all(
            isinstance(item, str) for item in technologies
        ):
            raise ValueError(
                f"{manifest}: hook '{entry['id']}' technologies must be a list of strings"
            )
        specs.append(
            HookSpec(
                id=str(entry["id"]),
                script=str(entry["script"]),
                stage=str(entry["stage"]),
                pass_filenames=bool(entry.get("pass_filenames", False)),
                always_run=bool(entry.get("always_run", False)),
                manager=manager,
                technologies=tuple(technologies),
                matcher=str(entry.get("matcher", "")),
            )
        )
    return specs


def selected_hook_specs(specs: list[HookSpec], selection: frozenset[str] | None) -> list[HookSpec]:
    """Return the specs the technology *selection* keeps (untagged = universal)."""
    return [spec for spec in specs if technology_selected(spec.technologies, selection)]


def git_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    """Return the specs rendered into the pre-commit config (``manager: git``)."""
    return [spec for spec in specs if spec.manager == GIT_MANAGER]


def claude_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    """Return the specs rendered into Claude Code agent hooks (``manager: claude``)."""
    return [spec for spec in specs if spec.manager == CLAUDE_MANAGER]


def copilot_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    """Return the specs rendered into Copilot hook files (``manager: copilot``)."""
    return [spec for spec in specs if spec.manager == COPILOT_MANAGER]


def agent_hook_surface_present(
    manager: str, *, which: Callable[[str], str | None] | None = None
) -> bool:
    """Whether the host that would run *manager*'s projected agent hooks exists here.

    PATH presence of :data:`AGENT_HOOK_HOSTS`'s entry, resolved through an injected
    *which* like :func:`basicly.runner.is_available` — the suite hides the ambient agent
    CLIs, so a caller-supplied resolver is the only way to assert either answer. False
    for ``git`` and any other manager with no host binary.

    This is what keeps the delivered tier reported rather than assumed (basicly-0p8n):
    a projected hook whose host never runs here cannot fire, and reading its file's
    existence as enforcement is the built-and-never-connected defect. The git hooks are
    the floor on every host either way, so a False here narrows the claim, not the
    guarantees.
    """
    which = which or shutil.which
    host = AGENT_HOOK_HOSTS.get(manager)
    return host is not None and which(host) is not None


def _copilot_hook_path(repo_root: Path, spec: HookSpec) -> Path:
    return repo_root / COPILOT_HOOKS_DIR / f"{COPILOT_MANAGED_PREFIX}{spec.id}.json"


def render_copilot_hook(spec: HookSpec, hooks_relpath: str) -> str:
    """Render one managed Copilot hook file (.github/hooks schema, version 1)."""
    event = COPILOT_EVENTS.get(spec.stage)
    if event is None:
        raise ValueError(
            f"copilot hook '{spec.id}' has stage {spec.stage!r}; allowed: {sorted(COPILOT_EVENTS)}"
        )
    script = f"{hooks_relpath}/{spec.script}"
    entry: dict = {
        "type": "command",
        # bash covers Linux/macOS (and the cloud agent sandbox); powershell
        # covers Windows. Both run the same interpreter-managed script.
        "bash": f"uv run python {shlex.quote(script)}",
        "powershell": f"uv run python '{script}'",
    }
    if spec.matcher:
        entry["matcher"] = spec.matcher
    config = {"version": 1, "hooks": {event: [entry]}}
    return json.dumps(config, indent=2) + "\n"


def sync_copilot_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> SyncResult:
    """Write one managed hook file per copilot spec; prune excluded/retired ones."""
    all_specs = copilot_hook_specs(load_hook_specs())
    specs = selected_hook_specs(all_specs, selection)
    result = SyncResult()
    hooks_relpath = core_hooks_dir.as_posix()

    wanted = {_copilot_hook_path(repo_root, spec) for spec in specs}
    for spec in specs:
        rendered = render_copilot_hook(spec, hooks_relpath)
        sync_file(_copilot_hook_path(repo_root, spec), rendered.encode("utf-8"), result)

    hooks_dir = repo_root / COPILOT_HOOKS_DIR
    if hooks_dir.is_dir():
        for path in sorted(hooks_dir.glob(f"{COPILOT_MANAGED_PREFIX}*.json")):
            if path not in wanted:
                path.unlink()
                result.written.append(path)  # a removal is a change worth reporting
    return result


def check_copilot_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> list[tuple[Path, str]]:
    """Return (path, reason) for Copilot hook files that are missing or stale."""
    all_specs = copilot_hook_specs(load_hook_specs())
    specs = selected_hook_specs(all_specs, selection)
    hooks_relpath = core_hooks_dir.as_posix()
    mismatches: list[tuple[Path, str]] = []

    wanted = {}
    for spec in specs:
        path = _copilot_hook_path(repo_root, spec)
        wanted[path] = render_copilot_hook(spec, hooks_relpath).encode("utf-8")
        if not path.exists():
            mismatches.append((path, "missing"))
        elif path.read_bytes() != wanted[path]:
            mismatches.append((path, "content mismatch"))

    hooks_dir = repo_root / COPILOT_HOOKS_DIR
    if hooks_dir.is_dir():
        mismatches.extend(
            (path, "not in the catalog (stale managed hook file)")
            for path in sorted(hooks_dir.glob(f"{COPILOT_MANAGED_PREFIX}*.json"))
            if path not in wanted
        )
    return mismatches


def remove_copilot_hooks(repo_root: Path) -> int:
    """Delete every managed Copilot hook file (uninstall path)."""
    hooks_dir = repo_root / COPILOT_HOOKS_DIR
    if not hooks_dir.is_dir():
        return 0
    removed = 0
    for path in sorted(hooks_dir.glob(f"{COPILOT_MANAGED_PREFIX}*.json")):
        path.unlink()
        removed += 1
    if not any(hooks_dir.iterdir()):
        hooks_dir.rmdir()
    return removed


def sync_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> SyncResult:
    """Merge the pre-commit wiring for the materialized hook scripts.

    ``core_hooks_dir`` is the on-disk hooks location (e.g.
    ``.basicly/core/hooks``). The scripts themselves are core content owned by
    ``basicly install`` (provenance-guarded sync, §9) — copying them here too
    would silently clobber a hand-edit install deliberately kept, so this only
    wires the config and requires the core to be materialized first.
    """
    result = SyncResult()
    src = _catalog_hooks_dir()
    dst = repo_root / core_hooks_dir

    if src.resolve() != dst.resolve() and not dst.is_dir():
        raise ValidationError("core hooks are not materialized; run `basicly install` first", dst)

    all_specs = git_hook_specs(load_hook_specs(src))
    specs = selected_hook_specs(all_specs, selection)
    all_ids = {spec.id for spec in all_specs}
    excluded_ids = all_ids - {spec.id for spec in specs}
    hooks_relpath = core_hooks_dir.as_posix()
    config_path = repo_root / PRECOMMIT_CONFIG

    if not config_path.exists():
        rendered = render_precommit_config(None, specs, hooks_relpath)
        sync_file(config_path, rendered.encode("utf-8"), result)
        return result

    # The config is co-owned with the consumer: leave it untouched when the
    # managed hooks are already semantically in sync (preserving their comments
    # and formatting); rewrite only when a managed hook is missing, wrong, or
    # stranded after a technology selection excluded it.
    existing_text = config_path.read_text(encoding="utf-8")
    parsed = parse_config(config_path, existing_text)
    if managed_hook_mismatches(parsed, specs, hooks_relpath) or excluded_hooks_present(
        parsed, excluded_ids
    ):
        rendered = render_precommit_config(existing_text, specs, hooks_relpath, all_ids)
        sync_file(config_path, rendered.encode("utf-8"), result)
    else:
        result.unchanged.append(config_path)

    return result


def hook_stages(specs: list[HookSpec]) -> list[str]:
    """Return the distinct git stages used by the given hook specs, in first-seen order.

    Only ``manager: git`` specs participate — the result feeds
    ``pre-commit install -t <stage>``, which agent-hook stages must never reach.
    """
    stages: list[str] = []
    for spec in git_hook_specs(specs):
        if spec.stage not in stages:
            stages.append(spec.stage)
    return stages


def _git_hooks_dir(repo_root: Path) -> Path:
    """Resolve the active git hooks directory (worktree- and hooksPath-aware).

    Falls back to ``<repo>/.git/hooks`` when git cannot be run (not on PATH) or
    the query yields nothing — callers such as the read-only ``status`` command
    must degrade gracefully rather than crash on a git-less environment.
    """
    try:
        result = subprocess.run(
            # `shutil.which` — the alternative — buys nothing: `except OSError` below is
            # already the git-not-installed branch, and PATH resolution is the behaviour.
            ["git", "rev-parse", "--git-path", "hooks"],  # noqa: S607 — PATH git, see above
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return repo_root / ".git" / "hooks"
    rel = result.stdout.strip()
    if not rel:
        return repo_root / ".git" / "hooks"
    path = Path(rel)
    return path if path.is_absolute() else repo_root / path


def missing_hook_installations(repo_root: Path, stages: list[str]) -> list[str]:
    """Return the stages whose git hook is not installed by pre-commit.

    A stage counts as installed when ``<git-hooks-dir>/<stage>`` exists and is a
    pre-commit-generated dispatcher (contains the ``pre-commit`` marker). This is
    a local-activation check, distinct from the projected-file drift check.
    """
    hooks_dir = _git_hooks_dir(repo_root)
    missing: list[str] = []
    for stage in stages:
        hook_file = hooks_dir / stage
        installed = False
        if hook_file.exists():
            text = hook_file.read_text(encoding="utf-8", errors="ignore")
            installed = "pre-commit" in text
        if not installed:
            missing.append(stage)
    return missing


def _pre_commit_command(args: list[str]) -> list[str] | None:
    """The argv to run pre-commit with *args*, or None when it cannot be run.

    Prefers a ``pre-commit`` already on PATH; otherwise runs it through
    ``uv tool run`` (uvx), which provisions the tool in an ephemeral environment.
    ``uv run`` is deliberately *not* the fallback: it resolves pre-commit only
    when the consumer repo declares it as a project dependency — which a fresh
    consumer never does — so it fails with "program not found" (basicly-x5gh).
    Returns None only when neither pre-commit nor uv is on PATH.
    """
    pre_commit = shutil.which("pre-commit")
    if pre_commit:
        return [pre_commit, *args]
    if shutil.which("uv"):
        return ["uv", "tool", "run", "pre-commit", *args]
    return None


def install_hooks(repo_root: Path, stages: list[str]) -> tuple[bool, str]:
    """Activate the gates by running ``pre-commit install`` for the given stages.

    Returns ``(ok, message)``. Degrades gracefully: runs pre-commit via
    ``uv tool run`` (uvx) when it isn't on PATH, guards a non-git target, and
    otherwise returns actionable guidance instead of raising, so a consumer
    without the prerequisites is told exactly what to do.
    """
    stage_args: list[str] = []
    for stage in stages:
        stage_args += ["-t", stage]
    manual = "uvx pre-commit install --install-hooks " + " ".join(stage_args)

    # pre-commit install needs a git repo; a bare consumer folder has no .git
    # (a worktree has a .git file, so exists() covers both). Skip with clear
    # guidance rather than surfacing an opaque pre-commit error (basicly-x5gh).
    if not (repo_root / ".git").exists():
        return False, (
            "not a git repository (no .git); run `git init`, then `basicly hooks-build` "
            "to activate the gates"
        )

    cmd = _pre_commit_command(["install", "--install-hooks", *stage_args])
    if cmd is None:
        return False, f"neither pre-commit nor uv is on PATH; install uv, then run: {manual}"

    # `cmd` is `_pre_commit_command`'s `shutil.which` result plus this module's literals;
    # no consumer string reaches it and `shell=False` passes the argv straight to execve.
    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=False)  # noqa: S603 — argv built here, no shell
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return False, f"pre-commit install failed ({detail}); run manually: {manual}"
    return True, result.stdout.strip() or "hooks installed"


def uninstall_hooks(repo_root: Path, stages: list[str]) -> tuple[bool, str]:
    """Deactivate the gates via ``pre-commit uninstall`` for the given stages.

    Mirrors :func:`install_hooks`: returns ``(ok, message)`` and runs pre-commit
    via ``uv tool run`` (uvx) when it isn't on PATH.
    """
    stage_args: list[str] = []
    for stage in stages:
        stage_args += ["-t", stage]
    manual = "uvx pre-commit uninstall " + " ".join(stage_args)

    cmd = _pre_commit_command(["uninstall", *stage_args])
    if cmd is None:
        return False, f"neither pre-commit nor uv is on PATH; run manually: {manual}"

    # Same argv provenance as `install_hooks`: `_pre_commit_command` plus literals.
    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=False)  # noqa: S603 — argv built here, no shell
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return False, f"pre-commit uninstall failed ({detail}); run manually: {manual}"
    return True, result.stdout.strip() or "hooks uninstalled"


def remove_managed_hooks(repo_root: Path) -> str | None:
    """Strip basicly's managed hooks from the pre-commit config (uninstall path).

    Removes the managed hook entries from every ``local`` repo; when nothing
    else remains, the file is deleted and the git hooks are uninstalled.
    Returns a human-readable summary, or None when there was nothing to do.
    Uses the bundled manifest for the managed ids, so it works after the core
    tree itself is gone.
    """
    config_path = repo_root / PRECOMMIT_CONFIG
    if not config_path.exists():
        return None

    parsed = parse_config(config_path, config_path.read_text(encoding="utf-8"))
    specs = git_hook_specs(load_hook_specs())
    managed_ids = {spec.id for spec in specs}

    kept: list = []
    changed = False
    for repo in parsed.get("repos") or []:
        if isinstance(repo, dict) and repo.get("repo") == "local":
            hooks = [
                hook
                for hook in (repo.get("hooks") or [])
                if not (isinstance(hook, dict) and hook.get("id") in managed_ids)
            ]
            if len(hooks) != len(repo.get("hooks") or []):
                changed = True
            if hooks:
                kept.append({**repo, "hooks": hooks})
        else:
            kept.append(repo)

    if not changed:
        return None

    if kept:
        parsed["repos"] = kept
        atomic_write_text(
            config_path,
            yaml.safe_dump(parsed, sort_keys=False, default_flow_style=False),
        )
        return f"Removed managed hooks from {PRECOMMIT_CONFIG} (foreign hooks preserved)"

    config_path.unlink()
    ok, message = uninstall_hooks(repo_root, hook_stages(specs))
    note = f"Deleted {PRECOMMIT_CONFIG} (only managed hooks remained)"
    return note if ok else f"{note}; {message}"


def _tracked_identity(path: Path, cwd: Path) -> tuple[Path, str] | None:
    """Identify *path* as a tracked location: (git common dir, working-tree-relative path).

    *cwd* is an existing directory in the same working tree as *path* (*path* itself
    need not exist yet). Returns ``None`` when git cannot answer — not a repository,
    git not on PATH, or *path* outside the working tree — in which case the caller has
    no evidence about the relationship and must compare contents.
    """
    try:
        proc = subprocess.run(
            # Partial path for the same reason as `_git_hooks_dir`: PATH resolution is the
            # behaviour, and `except OSError` below is the git-not-installed branch.
            ["git", "rev-parse", "--git-common-dir", "--show-toplevel"],  # noqa: S607 — as above
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode != 0 or len(lines) != 2:
        return None
    common = Path(lines[0])
    if not common.is_absolute():
        common = cwd / common
    try:
        relative = path.resolve().relative_to(Path(lines[1]).resolve())
    except ValueError:
        return None
    return common.resolve(), relative.as_posix()


def _is_same_tracked_path(src: Path, dst: Path, repo_root: Path) -> bool:
    """True when the catalog dir and its target are one tracked path in two working trees.

    Comparing contents only means something when *dst* is a *projection* of *src* —
    a consumer's materialized copy, which can drift from the installed catalog. It is
    not a projection when both are the same working-tree-relative path in the same git
    repository: then they are the same tracked file seen through two checkouts, and any
    difference is uncommitted or branch-local work, not drift. basicly installed
    editable from its own checkout hits this on every harness worktree that edits a
    hook script (basicly-9o6s); ``src is dst`` is the degenerate case of it.

    Path equality is required as well as repository identity: a consumer's in-repo
    ``.venv`` puts the *packaged* catalog inside the consumer's own repository, and
    keying on repository identity alone would disable this gate for them.
    """
    if src.resolve() == dst.resolve():
        return True
    catalog = _tracked_identity(src, src if src.is_dir() else src.parent)
    return catalog is not None and catalog == _tracked_identity(dst, repo_root)


def check_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> list[tuple[Path, str]]:
    """Return (path, reason) for any hook script or wiring that is out of sync."""
    mismatches: list[tuple[Path, str]] = []
    src = _catalog_hooks_dir()
    dst = repo_root / core_hooks_dir

    if not _is_same_tracked_path(src, dst, repo_root):
        for path in iter_catalog_files(src):
            target = dst / path.relative_to(src)
            if not target.exists():
                mismatches.append((target, "missing"))
            elif target.read_bytes() != path.read_bytes():
                mismatches.append((target, "differs from catalog"))

    all_specs = git_hook_specs(load_hook_specs(src))
    specs = selected_hook_specs(all_specs, selection)
    excluded_ids = {spec.id for spec in all_specs} - {spec.id for spec in specs}
    config_path = repo_root / PRECOMMIT_CONFIG
    if not config_path.exists():
        mismatches.append((config_path, "missing"))
        return mismatches

    existing_text = config_path.read_text(encoding="utf-8")
    parsed = parse_config(config_path, existing_text)
    mismatches.extend(
        (config_path, reason)
        for reason in managed_hook_mismatches(parsed, specs, core_hooks_dir.as_posix())
    )
    mismatches.extend(
        (config_path, reason) for reason in excluded_hooks_present(parsed, excluded_ids)
    )

    return mismatches
