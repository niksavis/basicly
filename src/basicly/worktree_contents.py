from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import (
    lefthook_artifacts,
    lefthook_binary,
    tracker_paths,
    usage,
    verify_artifact,
    verify_cache,
)

DEP_DIRS = (".venv", "node_modules")
USAGE_DIR = verify_artifact.RUN_ARTIFACT.parent
RETIRED_COUNTER = "verify-checks.json"


@dataclass(frozen=True)
class RemovalVerdict:
    may_remove: bool
    holds: str
    indeterminate: bool = False
    preserve: tuple[Path, ...] = ()


def classify_worktree_tree(returncode: int, stdout: str) -> RemovalVerdict:

    if returncode != 0:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                f"git status could not be read in the worktree (exit {returncode}); "
                "refusing to remove a tree whose contents are unknown — a lock held by "
                "a concurrent lane is the usual cause, so retry, or pass force to "
                "discard the tree regardless"
            ),
            indeterminate=True,
        )
    expected_noise = (
        *DEP_DIRS,
        (tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).as_posix(),
    )
    known_artifacts = {
        (USAGE_DIR / RETIRED_COUNTER).as_posix(),
        verify_artifact.RUN_ARTIFACT.as_posix(),
        verify_cache.CACHE_PATH.as_posix(),
        (USAGE_DIR / ".gitignore").as_posix(),
    }
    noise_prefixes = tuple(f"{d}/" for d in DEP_DIRS)
    pending: list[str] = []
    unparsable: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if len(line) < 4:
            unparsable.append(line)
            continue
        path = line[3:].strip().strip('"')
        if line[:2] in ("??", "!!") and (
            path in expected_noise or path in known_artifacts or path.startswith(noise_prefixes)
        ):
            continue
        pending.append(line)
    if unparsable:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                "git status returned a line this cannot parse, so the worktree's state "
                "is unknown; refusing to remove it:\n" + "\n".join(unparsable)
            ),
            indeterminate=True,
        )
    if pending:
        return RemovalVerdict(may_remove=False, holds="\n".join(pending))
    return RemovalVerdict(may_remove=True, holds="")


def owned_bytecode_cache(worktree: Path, line: str) -> bool:

    if line[:2] not in ("!!", "??"):
        return False
    try:
        candidate = worktree / _relative(line)
    except ValueError:
        return False
    if candidate.name != "__pycache__" or not candidate.is_dir():
        return False
    if any(
        path.is_symlink()
        for path in (candidate, *candidate.parents)
        if path.is_relative_to(worktree)
    ):
        return False
    children = tuple(candidate.iterdir())
    return bool(children) and all(_source_bytecode(child, worktree) for child in children)


def _bytecode_source(cache: Path, worktree: Path) -> Path | None:
    if cache.is_symlink() or not cache.is_file() or cache.suffix != ".pyc":
        return None
    pattern = (
        rf"(.+)\.{re.escape(str(sys.implementation.cache_tag))}"
        r"(?:\.opt-[12]|-pytest-[0-9A-Za-z.+-]+)?\.pyc"
    )
    match = re.fullmatch(pattern, cache.name)
    if match is None:
        return None
    source = cache.parent.parent / (match.group(1) + ".py")
    if any(part.startswith(".env") for part in source.relative_to(worktree).parts):
        return None
    if not source.is_file() or any(
        path.is_symlink() for path in (source, *source.parents) if path.is_relative_to(worktree)
    ):
        return None
    return source


def _source_bytecode(cache: Path, worktree: Path) -> bool:
    source = _bytecode_source(cache, worktree)
    if source is None:
        return False
    with cache.open("rb") as artifact:
        header = artifact.read(17)
    return (
        len(header) == 17
        and header[:4] == importlib.util.MAGIC_NUMBER
        and int.from_bytes(header[4:8], "little") in (0, 1, 3)
        and header[16] & 0x7F == ord("c")
    )


_CACHE_TAG = b"Signature: 8a477f597d28d172789f06886806bc55"
_TOOL_IGNORES = {
    ".pytest_cache": b"# Created by pytest automatically.\n*\n",
    ".ruff_cache": b"# Automatically created by ruff.\n*\n",
    ".import_linter_cache": b"# Automatically created by Grimp.\n*",
}


def _relative(line: str) -> Path:
    raw = line[3:].strip().strip('"').rstrip("/")
    parsed = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if parsed.is_absolute() or ".." in parsed.parts or windows.anchor or ".." in windows.parts:
        raise ValueError(f"artifact path is not relative to the worktree: {raw}")
    return Path(*parsed.parts)


def _cache_member(root: str, path: Path) -> bool:
    name = path.as_posix()
    if name in {".gitignore", "CACHEDIR.TAG"}:
        return True
    if root == ".pytest_cache":
        return name in {
            "README.md",
            "v",
            "v/cache",
            "v/cache/nodeids",
            "v/cache/lastfailed",
            "v/cache/stepwise",
        }
    if root == ".ruff_cache":
        return re.fullmatch(r"\d+\.\d+\.\d+(?:/\d+)?", name) is not None
    return re.fullmatch(r"(?:[a-f0-9]{40}\.data|[A-Za-z0-9_.-]+\.meta)\.json", name) is not None


def _generated_tree(
    worktree: Path, relative: Path, failure_logs: tuple[str, ...]
) -> tuple[Path, str, set[str]] | None:
    if relative == USAGE_DIR or relative.is_relative_to(USAGE_DIR):
        directory = worktree / USAGE_DIR
        names = {
            RETIRED_COUNTER,
            verify_artifact.RUN_ARTIFACT.name,
            verify_cache.CACHE_PATH.name,
            usage.USAGE_FILE.name,
            ".gitignore",
        }
        names.update(failure_logs)
        return directory, "usage", names
    if relative.parts and relative.parts[0] in _TOOL_IGNORES:
        directory = worktree / relative.parts[0]
        return directory, relative.parts[0], set()
    if relative.parts and relative.parts[0] == "build":
        directory = worktree / "build"
        names = {entry["name"] for entry in lefthook_artifacts.MANIFEST["assets"]}
        return directory, "build", names
    return None


def _allowed_member(kind: str, member: Path, names: set[str]) -> bool:
    if kind == "usage":
        return len(member.parts) == 1 and member.name in names
    if kind == "build":
        return member.as_posix() in {"lefthook", f"lefthook/{lefthook_artifacts.VERSION}"} or (
            member.parts[:2] == ("lefthook", lefthook_artifacts.VERSION)
            and len(member.parts) == 3
            and member.name in names
        )
    return _cache_member(kind, member)


def _regular_members(
    worktree: Path, directory: Path, kind: str, names: set[str]
) -> tuple[Path, ...]:
    if any(
        path.is_symlink()
        for path in (directory, *directory.parents)
        if path.is_relative_to(worktree)
    ):
        raise ValueError(
            f"{directory.relative_to(worktree)}: generated artifact directory is a symlink"
        )
    files = []
    for path in directory.rglob("*"):
        member = path.relative_to(directory)
        if path.is_symlink():
            raise ValueError(f"{path.relative_to(worktree)}: symlink is not disposable")
        if not _allowed_member(kind, member, names):
            raise ValueError(f"{path.relative_to(worktree)}: unknown generated-artifact content")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError(f"{path.relative_to(worktree)}: not a regular artifact")
    return tuple(files)


def _validate_generated(directory: Path, kind: str, files: tuple[Path, ...]) -> None:
    if kind == "build":
        lefthook_artifacts.validate_artifacts(directory / "lefthook" / lefthook_artifacts.VERSION)
    elif kind == "usage":
        for path in files:
            if path.name == ".gitignore":
                if path.read_bytes() != b"*\n":
                    raise ValueError("unknown usage ignore marker")
            elif path.suffix != ".log" and not isinstance(json.loads(path.read_bytes()), dict):
                raise ValueError("unrecognized usage artifact shape")
    else:
        with (directory / "CACHEDIR.TAG").open("rb") as marker:
            if marker.readline(100).rstrip(b"\r\n") != _CACHE_TAG:
                raise ValueError(f"{directory.name}: invalid cache directory tag")
        if (directory / ".gitignore").read_bytes() != _TOOL_IGNORES[kind]:
            raise ValueError(f"{directory.name}: unknown tool cache ignore marker")
        for path in files:
            if path.suffix == ".json" or path.parent.name == "cache":
                json.loads(path.read_bytes())


def generated_files(
    worktree: Path, line: str, *, failure_logs: tuple[str, ...] = ()
) -> tuple[Path, ...] | None:
    if line[:2] not in ("!!", "??"):
        return None
    relative = _relative(line)
    candidate = worktree / relative
    if candidate.name == "__pycache__":
        if not owned_bytecode_cache(worktree, line):
            raise ValueError(f"{relative}: cache header or source association is not recognized")
        return tuple(candidate.iterdir())
    tree = _generated_tree(worktree, relative, failure_logs)
    if tree is None:
        return None
    directory, kind, names = tree
    files = _regular_members(worktree, directory, kind, names)
    _validate_generated(directory, kind, files)
    return files


def owned_lefthook_assets(worktree: Path, line: str) -> bool:
    directory = worktree / ".basicly/bin"
    binary = lefthook_binary.binary_path(worktree).name
    names = {binary, "LICENSE.lefthook", ".gitignore"}
    path = line[3:].strip().strip('"').rstrip("/")
    if not line.startswith("!! ") or path not in {
        ".basicly/bin",
        *(f".basicly/bin/{name}" for name in names),
    }:
        return False
    if any(candidate.is_symlink() for candidate in (directory, directory.parent)):
        return False
    children = tuple(directory.iterdir())
    if any(
        child.name not in names or child.is_symlink() or not child.is_file() for child in children
    ):
        return False
    if (directory / ".gitignore").read_bytes() != b"*\n":
        return False
    return not lefthook_binary.binary_mismatches(worktree)
