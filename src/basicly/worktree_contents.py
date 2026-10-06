from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import tracker_paths, usage, verify_artifact

DEP_DIRS = (".venv", "node_modules")


@dataclass(frozen=True)
class RemovalVerdict:
    may_remove: bool
    holds: str
    indeterminate: bool = False


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
        usage.VERIFY_CHECKS_FILE.as_posix(),
        verify_artifact.RUN_ARTIFACT.as_posix(),
        (usage.VERIFY_CHECKS_FILE.parent / ".gitignore").as_posix(),
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

    if not line.startswith("!! .basicly/core/"):
        return False
    candidate = worktree / line[3:].strip().strip('"').rstrip("/")
    if candidate.name != "__pycache__" or not candidate.is_dir():
        return False
    if any(
        path.is_symlink()
        for path in (candidate, *candidate.parents)
        if path.is_relative_to(worktree)
    ):
        return False
    children = tuple(candidate.iterdir())
    return bool(children) and all(
        child.is_file() and not child.is_symlink() and child.suffix == ".pyc" for child in children
    )
