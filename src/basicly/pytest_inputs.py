from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePath, PureWindowsPath

from . import checkout, hook_python, pyright_inputs
from .runner import sanitised_project_env
from .verify_receipt import InputFingerprint, implementation_digest

_COMMAND = (
    "pytest",
    "-q",
    "-n",
    "{workers}",
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


def _dependency(root: Path, workspace: Path | None = None, *, engine: Path | None = None) -> str:
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
                        if (
                            target != engine
                            and not target.is_relative_to(root.resolve())
                            and not (
                                workspace is not None and target in {workspace, workspace / "src"}
                            )
                        ):
                            raise pyright_inputs.UnsupportedInputsError(
                                "external pytest import path"
                            )
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _project_launcher(root: PurePath) -> PurePath:
    if isinstance(root, PureWindowsPath):
        return root / ".venv/Scripts/pytest.exe"
    return root / ".venv/bin/pytest"


def execution(
    root: Path, command: Sequence[str], environment: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    env = execution_environment(environment, root)
    if (root / ".venv").is_dir():
        return [str(_project_launcher(root.resolve())), *command[1:]], env
    return list(command), env


def execution_environment(
    environment: dict[str, str], repo_root: Path | None = None
) -> dict[str, str]:
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
    if repo_root is not None and (repo_root / ".venv").is_dir():
        root = repo_root.resolve()
        cleaned["VIRTUAL_ENV"] = str(root / ".venv")
        cleaned["PATH"] = os.pathsep.join(
            dict.fromkeys([
                str(_project_launcher(root).parent),
                *cleaned.get("PATH", os.defpath).split(os.pathsep),
            ])
        )
    return cleaned


def _runtime_paths(interpreter: Path, directory: Path, env: dict[str, str]) -> dict[str, str]:
    program = (
        "import importlib.util,json,sys,sysconfig;"
        "p=importlib.util.find_spec('pytest');"
        "print(json.dumps(dict(purelib=sysconfig.get_path('purelib'),"
        "stdlib=sysconfig.get_path('stdlib'),executable=sys.executable,"
        "pytest=p.origin if p else '')))"
    )
    try:
        result = checkout.run(
            [str(interpreter), "-c", program],
            cwd=directory,
            env=env,
            check=False,
            timeout=10,
        )
        result.check_returncode()
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise pyright_inputs.UnsupportedInputsError(
            "pytest interpreter metadata unavailable"
        ) from exc
    keys = {"purelib", "stdlib", "executable", "pytest"}
    if (
        not isinstance(data, dict)
        or data.keys() != keys
        or not all(isinstance(value, str) and Path(value).is_absolute() for value in data.values())
    ):
        raise pyright_inputs.UnsupportedInputsError("invalid pytest interpreter metadata")
    if Path(data["executable"]).absolute() != interpreter.absolute():
        raise pyright_inputs.UnsupportedInputsError("pytest interpreter identity changed")
    if Path(data["pytest"]) != Path(data["purelib"]) / "pytest/__init__.py":
        raise pyright_inputs.UnsupportedInputsError("unsupported pytest import location")
    return data


def fingerprint(repo_root: Path) -> str:
    root = repo_root.resolve()
    try:
        command, environment = execution(
            root, _COMMAND, sanitised_project_env(dict(os.environ), root)
        )
        if any(environment.get(name) for name in ("PYTHONPATH", "PYTHONHOME")):
            raise pyright_inputs.UnsupportedInputsError(
                "pytest import override requires a reviewed input recipe"
            )
        launcher = shutil.which(command[0], path=environment.get("PATH"))
        if not launcher or os.name != "posix":
            raise pyright_inputs.UnsupportedInputsError("unsupported pytest launcher platform")
        path = Path(launcher)
        lines = path.read_bytes().splitlines()
        if not lines:
            raise pyright_inputs.UnsupportedInputsError("empty pytest launcher")
        first = lines[0].decode()
        interpreter = Path(first.removeprefix("#!"))
        if not first.startswith("#!/") or not interpreter.is_file():
            raise pyright_inputs.UnsupportedInputsError("unsupported pytest interpreter")
        runtime = _runtime_paths(interpreter, path.parent, environment)
        payload = {
            "recipe": "pytest-ledger-independent-v4",
            "implementation": implementation_digest(),
            "workspace": _workspace(root),
            "environment": environment,
            "launcher": hashlib.sha256(path.read_bytes()).hexdigest(),
            "python": hashlib.sha256(interpreter.read_bytes()).hexdigest(),
            "runtime_paths": runtime,
            "runtime": _dependency(Path(runtime["purelib"]), root),
            "stdlib": _dependency(Path(runtime["stdlib"])),
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
        return InputFingerprint(payload)
    except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
        raise pyright_inputs.UnsupportedInputsError(f"pytest inputs unavailable: {exc}") from exc


def _landing_python(interpreter: Path, root: Path, environment: dict[str, str]) -> str:
    program = (
        "import json,sysconfig;"
        "from pathlib import Path;"
        "p=sysconfig.get_paths();"
        "v=[p[k] for k in ('stdlib','platstdlib','purelib','platlib')];"
        "d=sysconfig.get_config_var('LIBDIR');"
        "n=sysconfig.get_config_var('LDLIBRARY');"
        "v.extend([str(Path(d)/n)] if d and n else []);"
        "print(json.dumps(sorted(set(v))))"
    )
    result = checkout.run(
        [str(interpreter), "-I", "-c", program],
        cwd=root,
        env=environment,
        check=False,
        timeout=10,
    )
    if result.returncode:
        raise pyright_inputs.UnsupportedInputsError("landing Python runtime unavailable")
    paths = json.loads(result.stdout)
    if (
        not isinstance(paths, list)
        or not paths
        or any(not isinstance(path, str) or not Path(path).is_absolute() for path in paths)
    ):
        raise pyright_inputs.UnsupportedInputsError("invalid landing Python runtime metadata")
    contents = {str(interpreter.resolve()): hashlib.sha256(interpreter.read_bytes()).hexdigest()}
    for value in paths:
        path = Path(value)
        if path.is_dir():
            contents[value] = _dependency(path, root, engine=Path(__file__).resolve().parent.parent)
        elif path.is_file():
            contents[value] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            raise pyright_inputs.UnsupportedInputsError("missing landing Python runtime input")
    return hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest()


def landing_fingerprint(
    repo_root: Path,
    commands: Sequence[str],
    candidate: str,
    configuration: dict[str, object],
) -> InputFingerprint:
    root = repo_root.resolve()
    environment = execution_environment(sanitised_project_env(dict(os.environ), root), root)
    label = f"GITHEAD_{candidate}"
    if label in environment and environment[label] != candidate:
        raise pyright_inputs.UnsupportedInputsError("conflicting Git merge label")
    environment[label] = candidate
    runtime: dict[str, str] = {}
    for relative in (".venv/pyvenv.cfg", ".venv/bin", ".venv/Scripts"):
        path = root / relative
        if path.is_file():
            runtime[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            for entry in sorted(path.iterdir()):
                if entry.is_file():
                    if entry.name.startswith(".env"):
                        raise pyright_inputs.UnsupportedInputsError("protected landing runtime")
                    runtime[entry.relative_to(root).as_posix()] = hashlib.sha256(
                        str(entry.resolve()).encode() + entry.read_bytes()
                    ).hexdigest()
    for site in [
        *sorted((root / ".venv/lib").glob("python*/site-packages")),
        root / ".venv/Lib/site-packages",
    ]:
        if site.is_dir():
            runtime[site.relative_to(root).as_posix()] = _dependency(site, root)
    interpreters = {str(Path(sys.executable).absolute())}
    for name in ("python", "python3"):
        if found := shutil.which(name, path=environment.get("PATH")):
            interpreters.add(str(Path(found).absolute()))
    return InputFingerprint({
        "recipe": "landing-v2",
        "configuration": configuration,
        "workspace": _workspace(root),
        "runtime": runtime,
        "python_runtime": {
            interpreter: _landing_python(Path(interpreter), root, environment)
            for interpreter in sorted(interpreters)
        },
        "environment": environment,
        "tools": {
            name: {
                "path": str(Path(found).resolve()),
                "content": hashlib.sha256(Path(found).read_bytes()).hexdigest(),
            }
            if (found := shutil.which(name, path=environment.get("PATH")))
            else None
            for name in sorted(set(commands) | {"git", "uv", "node", "python", "python3"})
        },
        "implementation": implementation_digest(),
        "platform": platform.platform(),
        "git_config": checkout.git(["config", "--list", "--show-origin"], cwd=root).stdout,
        "tags": checkout.git(
            ["tag", "--list", "--format=%(refname) %(objectname)"], cwd=root
        ).stdout,
    })
