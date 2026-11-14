from __future__ import annotations

import hashlib
import importlib.util
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
SOURCE_FILES = ("vault_push_guard.py", "vault_shapes.py", "git_runner.py")
HOOK_PREFIX = "#!/bin/sh\nBASICLY_VAULT_PUSH_GUARD=1\n"


def _load(name: str) -> Any:
    key = "basicly_tracker_kit_" + name
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, _HERE / (name + ".py"))
    if spec is None or spec.loader is None:
        raise ImportError("The vault hook installer is incomplete. Reinstall the tracker.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


git_runner = _load("git_runner")
events = _load("events")
guard = _load("vault_push_guard")


class HookInstallError(ValueError):
    pass


def _git(root: Path, *args: str) -> str:
    return git_runner.run(list(args), root).strip()


def _configured(root: Path, key: str) -> str:
    try:
        return _git(root, "config", "--get", key)
    except git_runner.GitCommandError as exc:
        if exc.returncode != 1:
            raise
        return ""


def _snapshot(gitdir: Path) -> Path:
    contents = {name: (_HERE / name).read_bytes() for name in SOURCE_FILES}
    fingerprint = hashlib.sha256()
    for name, content in contents.items():
        fingerprint.update(name.encode("utf-8") + b"\0" + content)
    destination = gitdir / "basicly-vault-guard" / fingerprint.hexdigest()
    if destination.is_symlink():
        raise HookInstallError(
            "The guard snapshot is a symlink. Preserve it and inspect the clone."
        )
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, content in contents.items():
        path = destination / name
        if path.is_symlink() or (path.exists() and path.read_bytes() != content):
            raise HookInstallError(
                "The installed vault guard was changed. Preserve it and reinstall protection."
            )
        if not path.exists():
            _replace(path, content)
    return destination


def _replace(path: Path, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix="vault-pre-push-", dir=path.parent)
    draft = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        draft.chmod(0o700)
        draft.replace(path)
    finally:
        if draft.exists():
            draft.unlink()


@dataclass(frozen=True)
class Target:
    root: Path
    gitdir: Path
    hooks: Path
    expected: str
    override: bool


def _target(repository: Path, owned: bool) -> Target:
    root = repository.resolve(strict=True)
    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir")).resolve()
    common = Path(_git(root, "rev-parse", "--git-common-dir"))
    common = common.resolve() if common.is_absolute() else (root / common).resolve()
    if common != gitdir:
        raise HookInstallError("Protect the vault's main clone before creating worktrees.")
    expected = _configured(root, "remote.origin.url")
    if not expected:
        raise HookInstallError(
            "The clone has no origin remote. Configure its vault remote before protection."
        )
    expected = guard.remote_location(expected, root)
    if not owned:
        try:
            marker = _git(root, "show", "HEAD:vault.toml")
        except git_runner.GitRunnerError as exc:
            raise HookInstallError(
                "This is not a vault clone. Protect a clone that contains vault.toml."
            ) from exc
        if marker != 'format = 1\nkind = "vault"':
            raise HookInstallError(
                "The clone has no supported vault marker. Check its repository before protection."
            )
    hooks = gitdir / "hooks"
    current = _configured(root, "core.hooksPath")
    current_path = Path(current) if current else hooks
    if not current_path.is_absolute():
        current_path = root / current_path
    if not owned and current_path.resolve() != hooks:
        raise HookInstallError(
            "The clone uses another hooks directory. "
            "Preserve it and integrate the vault guard explicitly."
        )
    if hooks.is_symlink() or (gitdir / "basicly-vault-guard").is_symlink():
        raise HookInstallError(
            "Vault protection paths are symlinks. Preserve them and inspect the clone."
        )
    hook = hooks / "pre-push"
    if hook.is_symlink() or (hook.exists() and not hook.read_text("utf-8").startswith(HOOK_PREFIX)):
        raise HookInstallError(
            "An unrelated pre-push hook already exists. "
            "Preserve it before installing vault protection."
        )
    return Target(root, gitdir, hooks, expected, bool(current and current_path.resolve() != hooks))


def _install(repository: Path, owned: bool) -> dict[str, object]:
    target = _target(repository, owned)
    root, gitdir, hooks, expected = target.root, target.gitdir, target.hooks, target.expected
    hook = hooks / "pre-push"
    hooks.mkdir(exist_ok=True)
    with events.LedgerLock(gitdir / "vault-push-install"):
        snapshot = _snapshot(gitdir)
        argv = [
            Path(sys.executable).as_posix(),
            "-I",
            "-S",
            (snapshot / "vault_push_guard.py").as_posix(),
            "--repository",
            gitdir.as_posix(),
            "--origin-root",
            root.as_posix(),
            "--expected-remote",
            expected,
        ]
        text = HOOK_PREFIX + "exec " + shlex.join(argv) + ' "$@"\n'
        before = hook.read_bytes() if hook.exists() else None
        changed = before != text.encode("utf-8")
        if changed:
            _replace(hook, text.encode("utf-8"))
        try:
            if target.override:
                _git(root, "config", "--local", "core.hooksPath", str(hooks))
        except git_runner.GitRunnerError:
            if before is None:
                hook.unlink()
            else:
                _replace(hook, before)
            raise
    return {"protected": True, "changed": changed, "repository": str(root)}


def install(repository: Path, *, owned: bool = False) -> dict[str, object]:
    try:
        return _install(repository, owned)
    except (OSError, git_runner.GitRunnerError) as exc:
        raise HookInstallError(
            "Vault protection could not be installed. "
            "Check --repository and preserve its existing hooks before retrying."
        ) from exc
