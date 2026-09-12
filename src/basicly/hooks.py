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
    HOOK_PYTHON,
    excluded_hooks_present,
    managed_hook_mismatches,
    parse_config,
    render_precommit_config,
    retired_hooks_present,
)
from .projection import SyncResult, atomic_write_text, sync_file
from .schema import ValidationError, technology_selected

HOOKS_MANIFEST = "hooks.yaml"
PRECOMMIT_CONFIG = ".pre-commit-config.yaml"

GIT_MANAGER = "git"
CLAUDE_MANAGER = "claude"
COPILOT_MANAGER = "copilot"
HOOK_MANAGERS = (GIT_MANAGER, CLAUDE_MANAGER, COPILOT_MANAGER)

AGENT_HOOK_HOSTS = {CLAUDE_MANAGER: "claude", COPILOT_MANAGER: "copilot"}

COPILOT_HOOKS_DIR = Path(".github/hooks")
COPILOT_MANAGED_PREFIX = "basicly-"

COPILOT_EVENTS = {
    "posttooluse": "postToolUse",
    "pretooluse": "preToolUse",
    "sessionstart": "sessionStart",
}


@dataclass(frozen=True)
class HookSpec:
    id: str
    script: str
    stage: str
    pass_filenames: bool = False
    always_run: bool = False
    files: str = ""
    manager: str = GIT_MANAGER
    technologies: tuple[str, ...] = ()
    matcher: str = ""


def _catalog_hooks_dir() -> Path:
    return bundled_catalog_root() / "hooks"


def load_hook_specs(hooks_dir: Path | None = None) -> list[HookSpec]:
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
                files=str(entry.get("files", "")),
                manager=manager,
                technologies=tuple(technologies),
                matcher=str(entry.get("matcher", "")),
            )
        )
    return specs


def selected_hook_specs(specs: list[HookSpec], selection: frozenset[str] | None) -> list[HookSpec]:
    return [spec for spec in specs if technology_selected(spec.technologies, selection)]


def git_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    return [spec for spec in specs if spec.manager == GIT_MANAGER]


def claude_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    return [spec for spec in specs if spec.manager == CLAUDE_MANAGER]


def copilot_hook_specs(specs: list[HookSpec]) -> list[HookSpec]:
    return [spec for spec in specs if spec.manager == COPILOT_MANAGER]


def agent_hook_surface_present(
    manager: str, *, which: Callable[[str], str | None] | None = None
) -> bool:

    which = which or shutil.which
    host = AGENT_HOOK_HOSTS.get(manager)
    return host is not None and which(host) is not None


def _copilot_hook_path(repo_root: Path, spec: HookSpec) -> Path:
    return repo_root / COPILOT_HOOKS_DIR / f"{COPILOT_MANAGED_PREFIX}{spec.id}.json"


def render_copilot_hook(spec: HookSpec, hooks_relpath: str) -> str:
    event = COPILOT_EVENTS.get(spec.stage)
    if event is None:
        raise ValueError(
            f"copilot hook '{spec.id}' has stage {spec.stage!r}; allowed: {sorted(COPILOT_EVENTS)}"
        )
    script = f"{hooks_relpath}/{spec.script}"
    entry: dict = {
        "type": "command",
        "bash": f"{HOOK_PYTHON} {shlex.quote(script)}",
        "powershell": f"{HOOK_PYTHON} '{script}'",
    }
    if spec.matcher:
        entry["matcher"] = spec.matcher
    config = {"version": 1, "hooks": {event: [entry]}}
    return json.dumps(config, indent=2) + "\n"


def sync_copilot_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> SyncResult:
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
                result.written.append(path)
    return result


def check_copilot_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> list[tuple[Path, str]]:
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

    existing_text = config_path.read_text(encoding="utf-8")
    parsed = parse_config(config_path, existing_text)
    if (
        managed_hook_mismatches(parsed, specs, hooks_relpath)
        or excluded_hooks_present(parsed, excluded_ids)
        or retired_hooks_present(parsed, all_ids, hooks_relpath)
    ):
        rendered = render_precommit_config(existing_text, specs, hooks_relpath, all_ids)
        sync_file(config_path, rendered.encode("utf-8"), result)
    else:
        result.unchanged.append(config_path)

    return result


def hook_stages(specs: list[HookSpec]) -> list[str]:

    stages: list[str] = []
    for spec in git_hook_specs(specs):
        if spec.stage not in stages:
            stages.append(spec.stage)
    return stages


def _git_hooks_dir(repo_root: Path) -> Path:

    try:
        result = subprocess.run(
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


PRE_PUSH_STAGE = "pre-push"
PRE_PUSH_GUARD_MARKER = "basicly: ledger-safe pre-push dispatch"
_PRE_PUSH_ANCHOR = 'ARGS+=(--hook-dir "$HERE" -- "$@")\n'
_PRE_PUSH_GUARD = f"""
# {PRE_PUSH_GUARD_MARKER} (basicly-6ajmrc). `hook-impl` stashes the unstaged tree before
# any hook runs and restores it after; the ledger is append-only shared state a live
# engine process writes *during* the push, so a kill inside that window drops every event
# appended since the stash. `run --all-files` is the same stage without a stash, and every
# pre-push hook here is `always_run` with `pass_filenames: false`, so the two do identical
# work. Anything unstaged outside the ledger takes the unchanged path, stash included.
if [ -n "$(git diff --name-only --ignore-submodules -- .basicly/ledger/)" ] &&
   [ -z "$(git diff --name-only --ignore-submodules -- . ':(exclude).basicly/ledger/')" ]; then
    ARGS=(run --hook-stage pre-push --all-files)
fi
"""


def apply_pre_push_guard(repo_root: Path) -> bool:

    path = _git_hooks_dir(repo_root) / PRE_PUSH_STAGE
    try:
        text = path.read_text(encoding="utf-8")
        mode = path.stat().st_mode
    except OSError:
        return False
    if PRE_PUSH_GUARD_MARKER in text:
        return True
    if _PRE_PUSH_ANCHOR not in text:
        return False
    atomic_write_text(path, text.replace(_PRE_PUSH_ANCHOR, _PRE_PUSH_ANCHOR + _PRE_PUSH_GUARD, 1))
    path.chmod(mode)
    return True


def missing_hook_installations(repo_root: Path, stages: list[str]) -> list[str]:

    hooks_dir = _git_hooks_dir(repo_root)
    missing: list[str] = []
    for stage in stages:
        hook_file = hooks_dir / stage
        installed = False
        if hook_file.exists():
            text = hook_file.read_text(encoding="utf-8", errors="ignore")
            installed = "pre-commit" in text
            if stage == PRE_PUSH_STAGE:
                installed = installed and PRE_PUSH_GUARD_MARKER in text
        if not installed:
            missing.append(stage)
    return missing


def _pre_commit_command(args: list[str]) -> list[str] | None:

    pre_commit = shutil.which("pre-commit")
    if pre_commit:
        return [pre_commit, *args]
    if shutil.which("uv"):
        return ["uv", "tool", "run", "pre-commit", *args]
    return None


def install_hooks(repo_root: Path, stages: list[str]) -> tuple[bool, str]:

    stage_args: list[str] = []
    for stage in stages:
        stage_args += ["-t", stage]
    manual = "uvx pre-commit install --install-hooks " + " ".join(stage_args)

    if not (repo_root / ".git").exists():
        return False, (
            "not a git repository (no .git); run `git init`, then `basicly hooks-build` "
            "to activate the gates"
        )

    cmd = _pre_commit_command(["install", "--install-hooks", *stage_args])
    if cmd is None:
        return False, f"neither pre-commit nor uv is on PATH; install uv, then run: {manual}"

    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=False)  # noqa: S603 — argv built here, no shell
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return False, f"pre-commit install failed ({detail}); run manually: {manual}"
    message = result.stdout.strip() or "hooks installed"
    if PRE_PUSH_STAGE in stages and not apply_pre_push_guard(repo_root):
        message += (
            "\nwarning: the pre-push hook was not recognised, so the ledger guard "
            "was not applied; a push will stash unstaged ledger writes"
        )
    return True, message


def uninstall_hooks(repo_root: Path, stages: list[str]) -> tuple[bool, str]:

    stage_args: list[str] = []
    for stage in stages:
        stage_args += ["-t", stage]
    manual = "uvx pre-commit uninstall " + " ".join(stage_args)

    cmd = _pre_commit_command(["uninstall", *stage_args])
    if cmd is None:
        return False, f"neither pre-commit nor uv is on PATH; run manually: {manual}"

    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=False)  # noqa: S603 — argv built here, no shell
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return False, f"pre-commit uninstall failed ({detail}); run manually: {manual}"
    return True, result.stdout.strip() or "hooks uninstalled"


def remove_managed_hooks(repo_root: Path) -> str | None:

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

    try:
        proc = subprocess.run(
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

    if src.resolve() == dst.resolve():
        return True
    catalog = _tracked_identity(src, src if src.is_dir() else src.parent)
    return catalog is not None and catalog == _tracked_identity(dst, repo_root)


def check_hooks(
    repo_root: Path, core_hooks_dir: Path, selection: frozenset[str] | None = None
) -> list[tuple[Path, str]]:
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
