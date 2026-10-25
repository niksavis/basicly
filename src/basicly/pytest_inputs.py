from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import sysconfig
from pathlib import Path

from . import checkout, hook_python, pyright_inputs
from .runner import sanitised_project_env

_COMMAND = (
    "pytest",
    "-q",
    "-n",
    "4",
    "--durations=25",
    "-m",
    "not live_ledger and not fresh_runtime",
)


def supported_command(command: tuple[str, ...], mode: str) -> bool:
    return mode == "full" and command == _COMMAND


def _content(path: Path, root: Path) -> str:
    if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise pyright_inputs.UnsupportedInputsError("symlinked or external pytest input")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace(root: Path) -> dict[str, str]:
    listing = checkout.git(
        ["ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    )
    result: dict[str, str] = {}
    for name in sorted(set(listing.stdout.split("\0")) - {""}):
        path = Path(name)
        if any(part.startswith(".env") for part in path.parts):
            raise pyright_inputs.UnsupportedInputsError("protected pytest input")
        if path.parts[:2] == (".basicly", "ledger"):
            continue
        result[path.as_posix()] = _content(root / path, root)
    for directory in (root / "build/lefthook", root / "node_modules"):
        if directory.exists():
            result[directory.relative_to(root).as_posix()] = _dependency(directory)
    return result


def _dependency(root: Path, workspace: Path | None = None) -> str:
    digest = hashlib.sha256()
    for directory, dirs, files in root.walk(on_error=pyright_inputs._unreadable):
        dirs[:] = sorted(name for name in dirs if name not in {"__pycache__", ".cache"})
        if any((directory / name).is_symlink() for name in dirs):
            raise pyright_inputs.UnsupportedInputsError("symlinked pytest dependency directory")
        for name in sorted(files):
            path = directory / name
            if name.startswith(".env"):
                raise pyright_inputs.UnsupportedInputsError("protected pytest dependency")
            if not path.resolve().is_relative_to(root.resolve()):
                raise pyright_inputs.UnsupportedInputsError("external pytest dependency")
            if path.suffix == ".pth":
                for line in path.read_text().splitlines():
                    if line.strip() and not line.startswith(("#", "import ", "import\t")):
                        target = (path.parent / line.strip()).resolve()
                        if not target.is_relative_to(root.resolve()) and not (
                            workspace is not None and target in {workspace, workspace / "src"}
                        ):
                            raise pyright_inputs.UnsupportedInputsError(
                                "external pytest import path"
                            )
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def execution_environment(environment: dict[str, str]) -> dict[str, str]:
    excluded = {
        "CODEX_TOOL_CALL_ID",
        "SHLVL",
        "_",
        "PWD",
        "OLDPWD",
        "BR_AGENT_NAME",
        "AI_AGENT",
        "CLAUDECODE",
        "PYTEST_CURRENT_TEST",
        "UV_RUN_RECURSION_DEPTH",
        "LEFTHOOK",
        "BASICLY_FOREIGN_HOOK",
        "BASICLY_SECRET_EXCLUDE",
        "BASICLY_FOLD_COMMAND",
        "GIT_EXEC_PATH",
    }
    cleaned = {
        name: value
        for name, value in checkout.sanitised_colour_env(
            checkout.sanitised_git_env(environment)
        ).items()
        if name not in excluded
        and not name.startswith(("BASICLY_HOOK_", "LEFTHOOK_"))
        and (not name.upper().endswith("_PROXY") or name.upper() == "NO_PROXY")
    }
    if "PATH" in cleaned:
        cleaned["PATH"] = os.pathsep.join(
            entry
            for entry in dict.fromkeys(cleaned["PATH"].split(os.pathsep))
            if entry != environment.get("GIT_EXEC_PATH")
        )
    cleaned["UV_OFFLINE"] = "1"
    cleaned["NO_COLOR"] = "1"
    for name, value in hook_python.ENVIRONMENT.items():
        cleaned.setdefault(name, value)
    return cleaned


def fingerprint(repo_root: Path) -> str:
    root = repo_root.resolve()
    try:
        environment = execution_environment(sanitised_project_env(dict(os.environ), root))
        launcher = shutil.which("pytest", path=environment.get("PATH"))
        if not launcher or os.name != "posix":
            raise pyright_inputs.UnsupportedInputsError("unsupported pytest launcher platform")
        path = Path(launcher)
        lines = path.read_bytes().splitlines()
        if not lines:
            raise pyright_inputs.UnsupportedInputsError("empty pytest launcher")
        first = lines[0].decode()
        if (
            not first.startswith("#!/")
            or Path(first[2:]).resolve() != Path(sys.executable).resolve()
        ):
            raise pyright_inputs.UnsupportedInputsError("pytest launcher uses another interpreter")
        payload = {
            "recipe": "pytest-ledger-independent-v2",
            "workspace": _workspace(root),
            "environment": environment,
            "launcher": hashlib.sha256(path.read_bytes()).hexdigest(),
            "python": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "runtime": _dependency(Path(sysconfig.get_path("purelib")), root),
            "stdlib": _dependency(Path(sysconfig.get_path("stdlib"))),
            "tools": {
                tool: hashlib.sha256(Path(found).read_bytes()).hexdigest()
                if (found := shutil.which(tool, path=environment.get("PATH")))
                else None
                for tool in ("git", "uv", "node", "npm", "sh", "bash", "zsh")
            },
            "platform": platform.platform(),
            "git_config": checkout.git(["config", "--list", "--show-origin"], cwd=root).stdout,
            "tags": checkout.git(
                ["tag", "--list", "--format=%(refname) %(objectname)"], cwd=root
            ).stdout,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
        raise pyright_inputs.UnsupportedInputsError(f"pytest inputs unavailable: {exc}") from exc
