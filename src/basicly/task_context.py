from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from . import checkout, owned_store, runner

CONTEXTS = Path(".basicly/usage/task-contexts")
_CONFIG_FILES = (
    "basicly.toml",
    "AGENTS.md",
    "CLAUDE.md",
    ".mcp.json",
    ".codex/config.toml",
    ".claude/settings.json",
    ".claude/settings.local.json",
)
_CONTINUATION = frozenset({
    "--resume",
    "--continue",
    "--last",
    "--fork-session",
    "--session-id",
    "resume",
    "fork",
})


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _file_identity(path: Path) -> str:
    if any(part.casefold().startswith(".env") for part in path.parts):
        raise ValueError("protected session configuration input")
    if path.is_symlink():
        raise ValueError("session configuration must not be a symlink")
    if not path.exists():
        return "absent"
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("unsupported session configuration file")
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("session configuration changed while reading")
    return digest


def _claude_mcp(cwd: Path) -> object:
    path = Path.home() / ".claude.json"
    digest = _file_identity(path)
    if digest == "absent":
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or _file_identity(path) != digest:
        raise ValueError("invalid or changing Claude host configuration")
    prefixes = (
        "mcp",
        "enabledmcp",
        "disabledmcp",
        "allowedtools",
        "hastrustdialog",
        "permission",
        "enableallprojectmcp",
    )
    projects = data.get("projects", {})
    project = projects.get(str(cwd), {}) if isinstance(projects, dict) else {}
    if not isinstance(project, dict):
        raise ValueError("invalid Claude project configuration")
    return {
        "global": {
            key: value for key, value in data.items() if key.casefold().startswith(prefixes)
        },
        "project": {
            key: value for key, value in project.items() if key.casefold().startswith(prefixes)
        },
    }


def _configuration(spec: runner.RunnerSpec, cwd: Path, role: str | None) -> str:
    executable = shutil.which(spec.binary) if spec.binary else None
    if executable is None:
        raise ValueError("cannot identify session executable")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    paths = [*(cwd / name for name in _CONFIG_FILES)]
    paths += [
        codex_home / "config.toml",
        codex_home / "AGENTS.md",
        claude_home / "settings.json",
        claude_home / "CLAUDE.md",
    ]
    file_flags = {
        "--settings",
        "--mcp-config",
        "--append-system-prompt-file",
        "--system-prompt-file",
    }
    for position, argument in enumerate(spec.command):
        flag, separator, value = argument.partition("=")
        if flag not in file_flags:
            continue
        if not separator:
            if position + 1 >= len(spec.command):
                raise ValueError("session configuration flag requires a value")
            value = spec.command[position + 1]
        if flag in {"--settings", "--mcp-config"} and value.lstrip().startswith("{"):
            continue
        candidate = Path(value)
        paths.append(candidate if candidate.is_absolute() else cwd / candidate)
    if role is not None:
        paths.append(cwd / ".claude/agents" / f"{role}.md")
    for parent in cwd.parents:
        paths.extend(
            parent / name
            for name in ("AGENTS.md", "CLAUDE.md", ".codex/config.toml", ".claude/settings.json")
        )
    with Path(executable).resolve().open("rb") as handle:
        executable_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"_", "PWD", "OLDPWD", "SHLVL"}
    }
    return _digest({
        "spec": asdict(spec),
        "role": role,
        "executable": executable_hash,
        "configuration": {str(path): _file_identity(path) for path in paths},
        "environment": _digest(environment),
        "claude_mcp": _claude_mcp(cwd) if spec.resume_style == runner.RESUME_FLAGS else None,
        "model": runner.resolve_model(spec, repo_root=cwd, role=role).model,
    })


def _revision(cwd: Path) -> str:
    head = checkout.run(["git", "rev-parse", "HEAD"], cwd=cwd).stdout.strip()
    diff = checkout.run(
        [
            "git",
            "diff",
            "--binary",
            "HEAD",
            "--",
            ".",
            ":(exclude).basicly/usage/**",
            ":(exclude).basicly/ledger/**",
            ":(exclude).env*",
            ":(exclude)**/.env*",
        ],
        cwd=cwd,
    ).stdout
    names = checkout.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=cwd
    ).stdout
    untracked = {}
    for name in names.split("\0"):
        if (
            not name
            or name.startswith((".basicly/usage/", ".basicly/ledger/"))
            or any(part.casefold().startswith(".env") for part in Path(name).parts)
        ):
            continue
        path = cwd / name
        if path.is_symlink():
            untracked[name] = str(path.readlink())
        elif path.is_file():
            untracked[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return _digest({"head": head, "diff": diff, "untracked": untracked})


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _seed(path: Path, identity: dict, revision: str) -> runner.SessionSeed:
    stored = _read(path)
    value = stored.get("session_id")
    if stored.get("identity") == identity and stored.get("revision") == revision:
        try:
            return runner.SessionSeed(str(uuid.UUID(value)), exists=True)
        except ValueError, TypeError, AttributeError:
            pass
    return runner.SessionSeed(str(uuid.uuid4()), exists=False)


def _dispatch(spec: runner.RunnerSpec, prompt: str, cwd: Path, **kwargs) -> runner.RunResult:
    with runner.process_budget().slot(runner.LANE):
        return runner.run(spec, prompt, cwd, **kwargs)


def has_continuation(spec: runner.RunnerSpec) -> bool:
    return bool(_CONTINUATION.intersection(arg.partition("=")[0] for arg in spec.command))


def _fresh_reason(spec: runner.RunnerSpec, cwd: Path, role: str | None) -> str | None:
    if spec.resume_style not in (runner.RESUME_FLAGS, runner.CODEX_RESUME):
        return "unsupported-adapter"
    if has_continuation(spec):
        raise ValueError("task runner command must not contain session continuation")
    if runner.resolve_model(spec, repo_root=cwd, role=role).model is None:
        return "model-unpinned"
    if any(arg.partition("=")[0] in {"--add-dir", "--plugin-dir"} for arg in spec.command):
        return "unsupported-context-input"
    return None


def run_task(
    repo_root: Path,
    issue_id: str,
    spec: runner.RunnerSpec,
    prompt: str,
    cwd: Path,
    **kwargs,
) -> runner.RunResult:
    if "seed" in kwargs:
        raise ValueError("task context selects its own session; do not supply seed")
    cwd = cwd.resolve()
    reason = _fresh_reason(spec, cwd, kwargs.get("role"))
    if reason is not None:
        return replace(_dispatch(spec, prompt, cwd, **kwargs), context_action=reason)
    kwargs["capture_usage"] = True
    identity = {
        "task": issue_id,
        "worktree": str(cwd),
        "family": "codex" if spec.resume_style == runner.CODEX_RESUME else "claude",
        "configuration": _configuration(spec, cwd, kwargs.get("role")),
    }
    directory = repo_root / CONTEXTS / _digest(identity)
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    path = directory / "session.json"
    events = owned_store.packaged_kit("events")
    lock_directory = repo_root / CONTEXTS / ".owners" / _digest({"worktree": str(cwd)})
    lock = events.LedgerLock(lock_directory, timeout_s=0)
    try:
        lock.acquire()
    except events.LockUnavailableError as error:
        raise RuntimeError("this task context is already running; wait for its owner") from error
    try:
        seed = _seed(path, identity, _revision(cwd))
        result = _dispatch(spec, prompt, cwd, seed=seed, **kwargs)
        observed = result.session_id
        if observed is not None:
            observed = str(uuid.UUID(observed))
            expected = result.seed.session_id if result.seed is not None else seed.session_id
            if observed != expected and (seed.exists or spec.resume_style == runner.RESUME_FLAGS):
                if path.exists():
                    path.replace(path.with_suffix(f".rejected-{uuid.uuid4().hex}.json"))
                return replace(
                    result,
                    returncode=1,
                    session_id=None,
                    context_action="session-mismatch",
                    stderr="native session identity did not match the selected task",
                )
            if _configuration(spec, cwd, kwargs.get("role")) == identity["configuration"]:
                payload = {"identity": identity, "revision": _revision(cwd), "session_id": observed}
                temporary = path.with_suffix(f".{os.getpid()}.tmp")
                temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
                temporary.replace(path)
        action = (
            ("resumed" if observed == seed.session_id else "refreshed") if seed.exists else "fresh"
        )
        if observed is None:
            action = "session-unobserved"
        return replace(result, context_action=action)
    finally:
        lock.release()
