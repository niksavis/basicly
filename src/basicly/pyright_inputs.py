from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath

from .runner import sanitised_project_env
from .verify_receipt import InputFingerprint, implementation_digest

_IGNORED = frozenset({".git", ".venv", "node_modules", "__pycache__"})
_CONFIG_KEYS = frozenset({
    "venvPath",
    "venv",
    "extraPaths",
    "include",
    "exclude",
    "typeCheckingMode",
    "pythonVersion",
    "pythonPlatform",
})


class UnsupportedInputsError(ValueError):
    pass


def _secret(name: str) -> bool:
    return name.startswith(".env")


def _files(root: Path, *, sources: bool) -> list[Path]:
    result: list[Path] = []
    for directory, dirs, files in root.walk(on_error=_unreadable):
        if sources and any(_secret(name) for name in dirs):
            raise UnsupportedInputsError("protected input directory")
        ignored = frozenset({".git", ".venv"}) if sources else frozenset({".git"})
        dirs[:] = sorted(name for name in dirs if not _secret(name) and name not in ignored)
        for name in dirs:
            if (directory / name).is_symlink():
                raise UnsupportedInputsError("symlinked input directory")
        for name in sorted(files):
            if _secret(name):
                if sources and name.endswith((".py", ".pyi", ".pth")):
                    raise UnsupportedInputsError("protected source input")
                continue
            selected = (
                not sources
                or name.endswith((".py", ".pyi", ".pth"))
                or name
                in {
                    "py.typed",
                    "pyproject.toml",
                    "pyrightconfig.json",
                }
            )
            if selected:
                path = directory / name
                if path.is_symlink():
                    raise UnsupportedInputsError("symlinked input file")
                result.append(path)
    return result


def _unreadable(error: OSError) -> None:
    raise UnsupportedInputsError("unreadable input inventory") from error


def _digest_files(root: Path, *, sources: bool, workspace: Path | None = None) -> str:
    digest = hashlib.sha256()
    for path in _files(root, sources=sources):
        relative = path.relative_to(root).as_posix()
        if (
            workspace is not None
            and path.name in {"RECORD", "direct_url.json"}
            and any(part.endswith(".dist-info") for part in path.parts)
        ):
            continue
        content = path.read_bytes()
        if workspace is not None and path.suffix == ".pth":
            lines = content.decode("utf-8").splitlines()
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line.startswith(("#", "import ", "import\t")):
                    continue
                target = (path.parent / line).resolve()
                if not target.is_relative_to(workspace):
                    raise UnsupportedInputsError("external analysis search path")
                parts = target.relative_to(workspace).parts
                if any(
                    part in _IGNORED or _secret(part) for part in parts
                ) and not target.is_relative_to(root):
                    raise UnsupportedInputsError("excluded analysis search path")
            content = content.replace(str(workspace).encode(), b"<workspace>")
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8))
        digest.update(encoded)
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _configuration(root: Path) -> None:
    if (root / "pyrightconfig.json").exists():
        raise UnsupportedInputsError("pyrightconfig.json requires a reviewed input recipe")
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    config = data.get("tool", {}).get("pyright", {})
    if not isinstance(config, dict) or any(
        key not in _CONFIG_KEYS and not key.startswith("report") for key in config
    ):
        raise UnsupportedInputsError("unsupported Pyright configuration")
    if config.get("venvPath") != "." or config.get("venv") != ".venv":
        raise UnsupportedInputsError("analysis environment must be .venv in this checkout")
    for key in ("extraPaths", "include", "exclude"):
        paths = config.get(key, [])
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise UnsupportedInputsError(f"unsupported {key}")
        for path in paths:
            parts = path.replace("\\", "/").split("/")
            if (
                PurePosixPath(path).is_absolute()
                or bool(PureWindowsPath(path).root)
                or ":" in path
                or ".." in parts
                or any(_secret(part) for part in parts)
            ):
                raise UnsupportedInputsError(f"external or protected {key}")
            if key in {"extraPaths", "include"} and any(part in _IGNORED for part in parts):
                raise UnsupportedInputsError("excluded analysis search path")


def execution_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    original = sanitised_project_env(environment, root)
    if os.name != "posix":
        return original
    kept = {
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
        "TZ",
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
    }
    env = {
        name: value
        for name, value in original.items()
        if name.upper() in kept or name.startswith(("PYTHON", "PYRIGHT_", "NODE_", "LC_"))
    }
    paths = list(
        dict.fromkeys(
            str((root / entry).absolute())
            for entry in original.get("PATH", os.defpath).split(os.pathsep)
        )
    )
    lookup = os.pathsep.join(paths)
    directories = {
        str(Path(tool).parent)
        for name in ("pyright", "node")
        if (tool := shutil.which(name, path=lookup)) is not None
    }
    env["PATH"] = os.pathsep.join(
        dict.fromkeys(str(Path(path).resolve()) for path in paths if path in directories)
    )
    if "VIRTUAL_ENV" in env:
        try:
            _configuration(root)
        except OSError, ValueError, TypeError, AttributeError:
            return env
        del env["VIRTUAL_ENV"]
    return env


def _runtime(root: Path) -> dict[str, str]:
    env = execution_environment(root, os.environ)
    unsupported = (
        "PYTHONPATH",
        "PYTHONHOME",
        "NODE_PATH",
        "NODE_OPTIONS",
        "PYRIGHT_PYTHON_FORCE_VERSION",
        "PYRIGHT_PYTHON_PYLANCE_VERSION",
        "PYRIGHT_PYTHON_NODE_VERSION",
    )
    if any(env.get(name) for name in unsupported):
        raise UnsupportedInputsError("runtime override requires a reviewed input recipe")
    if env.get("PYRIGHT_PYTHON_USE_BUNDLED_PYRIGHT", "true").lower() not in {"true", "1"}:
        raise UnsupportedInputsError("unbundled Pyright")
    launcher_name = shutil.which("pyright", path=env.get("PATH"))
    node_name = shutil.which("node", path=env.get("PATH"))
    if os.name != "posix" or launcher_name is None or node_name is None:
        raise UnsupportedInputsError("unsupported runtime launcher")
    launcher = Path(launcher_name)
    lines = launcher.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise UnsupportedInputsError("empty Pyright launcher")
    first = lines[0]
    interpreter = Path(first.removeprefix("#!"))
    if not first.startswith("#!/") or not interpreter.is_file():
        raise UnsupportedInputsError("unsupported Pyright interpreter")
    launcher_body = "\n".join(lines[1:]).strip().encode()
    if hashlib.sha256(launcher_body).hexdigest() != (
        "9eba366535d11962cd60ee4af4e473368f6614d49634270693c4c46da99af261"
    ):
        raise UnsupportedInputsError("unreviewed Pyright launcher")
    runtime = _interpreter_paths(interpreter, launcher.parent, env)
    site = Path(runtime["purelib"])
    if runtime["nodejs_wheel"] or env.get("PYRIGHT_PYTHON_GLOBAL_NODE", "true").lower() not in {
        "true",
        "1",
    }:
        raise UnsupportedInputsError("unsupported Node launcher strategy")
    if runtime["pyright"] != str(site / "pyright/__init__.py"):
        raise UnsupportedInputsError("unsupported Pyright import location")
    version = json.loads((site / "pyright/dist/package.json").read_text(encoding="utf-8"))
    if version.get("version") != "1.1.411":
        raise UnsupportedInputsError("unsupported Pyright version")
    return {
        "environment": hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest(),
        "launcher": hashlib.sha256(launcher.read_bytes()).hexdigest(),
        "python": hashlib.sha256(interpreter.read_bytes()).hexdigest(),
        "node": hashlib.sha256(Path(node_name).read_bytes()).hexdigest(),
        "runtime": _digest_files(site, sources=False),
        "stdlib": _digest_files(Path(runtime["stdlib"]), sources=False),
        "platform": platform.platform(),
    }


def _interpreter_paths(interpreter: Path, directory: Path, env: dict[str, str]) -> dict[str, str]:
    program = (
        "import importlib.util,json,sysconfig;"
        "p=importlib.util.find_spec('pyright');"
        "print(json.dumps(dict(purelib=sysconfig.get_path('purelib'),"
        "stdlib=sysconfig.get_path('stdlib'),pyright=p.origin if p else '',"
        "nodejs_wheel='present' if importlib.util.find_spec('nodejs_wheel') else '')))"
    )
    try:
        result = subprocess.run(  # noqa: S603 — exact reviewed launcher's interpreter, no shell
            [str(interpreter), "-c", program],
            cwd=directory,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise UnsupportedInputsError("Pyright interpreter metadata unavailable") from exc
    keys = {"purelib", "stdlib", "pyright", "nodejs_wheel"}
    if (
        not isinstance(data, dict)
        or data.keys() != keys
        or not all(isinstance(value, str) for value in data.values())
    ):
        raise UnsupportedInputsError("invalid Pyright interpreter metadata")
    return data


def fingerprint(repo_root: Path) -> str:
    root = repo_root.resolve()
    try:
        _configuration(root)
        sites = sorted((root / ".venv/lib").glob("python*/site-packages"))
        if len(sites) != 1 or not sites[0].is_dir():
            raise UnsupportedInputsError("unsupported analysis environment layout")
        payload = {
            "recipe": "pyright-1.1.411-v5",
            "implementation": implementation_digest(),
            "source": _digest_files(root, sources=True),
            "analysis": _digest_files(sites[0], sources=True, workspace=root),
            "analysis_layout": sites[0].relative_to(root).as_posix(),
            "analysis_python": hashlib.sha256((root / ".venv/pyvenv.cfg").read_bytes()).hexdigest(),
            "runtime": _runtime(root),
        }
        return InputFingerprint(payload)
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        raise UnsupportedInputsError(
            f"Pyright inputs unavailable: {type(exc).__name__}: {exc}"
        ) from exc
