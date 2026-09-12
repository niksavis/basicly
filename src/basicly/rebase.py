from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import load_worktree_config
from .worktree import git, run

MERGE_COMMIT_ON_BRANCH = "rebase-merge-commit"

REPLAY_DROPPED_PATHS = "rebase-dropped-paths"

MAX_REGENERATED_REBASE_STEPS = 100

_CONFLICT_MARKER = re.compile(rb"^(?:<{7}|>{7})", re.MULTILINE)


@dataclass(frozen=True)
class ReplayOutcome:
    status: str = ""
    detail: str = ""
    conflicts: tuple[str, ...] = ()
    regenerated: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.status


def unmerged_paths(cwd: Path) -> tuple[str, ...]:
    proc = git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def merge_commits(cwd: Path, base: str, branch: str) -> tuple[str, ...]:

    proc = git(["rev-list", "--merges", f"{base}..{branch}"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def _tree_paths(cwd: Path, ref: str) -> frozenset[str] | None:
    proc = git(["ls-tree", "-r", "--name-only", ref], cwd=cwd, check=False)
    if proc.returncode != 0:
        return None
    return frozenset(line.strip() for line in proc.stdout.splitlines() if line.strip())


def dropped_paths(cwd: Path, before: str, base: str) -> tuple[str, ...]:

    before_paths = _tree_paths(cwd, before)
    after_paths = _tree_paths(cwd, "HEAD")
    if before_paths is None or after_paths is None:
        return ()
    lost = before_paths - after_paths
    if not lost:
        return ()
    fork = git(["merge-base", base, before], cwd=cwd, check=False)
    if fork.returncode != 0:
        return tuple(sorted(lost))
    forked_paths = _tree_paths(cwd, fork.stdout.strip())
    base_paths = _tree_paths(cwd, base)
    if forked_paths is None or base_paths is None:
        return tuple(sorted(lost))
    return tuple(sorted(lost - (forked_paths - base_paths)))


def unresolved(path: Path) -> bool:

    try:
        blob = path.read_bytes()
    except OSError:
        return False
    return _CONFLICT_MARKER.search(blob) is not None


def _rebuilt_clean(
    worktree_path: Path, conflicts: tuple[str, ...], commands: Mapping[str, tuple[str, ...]]
) -> bool:
    for path in conflicts:
        if run(list(commands[path]), cwd=worktree_path, check=False).returncode != 0:
            return False
    return not any(unresolved(worktree_path / path) for path in conflicts)


def refresh_generated(repo_root: Path, worktree_path: Path, bead: str) -> tuple[str, ...]:

    rebuilt = []
    for path, command in sorted(load_worktree_config(repo_root).regenerate_commands.items()):
        if run(list(command), cwd=worktree_path, check=False).returncode != 0:
            continue
        if git(["diff", "--quiet", "--", path], cwd=worktree_path, check=False).returncode != 0:
            rebuilt.append(path)
    if not rebuilt or git(["add", "--", *rebuilt], cwd=worktree_path, check=False).returncode != 0:
        return ()
    message = f"chore(regen): rebuild the artifacts the rebase left stale ({bead})"
    if git(["commit", "-m", message], cwd=worktree_path, check=False).returncode != 0:
        return ()
    return tuple(rebuilt)


def rebuild_generated_conflicts(repo_root: Path, worktree_path: Path) -> tuple[str, ...] | None:

    commands = load_worktree_config(repo_root).regenerate_commands
    if not commands:
        return None

    rebuilt: set[str] = set()
    for _ in range(MAX_REGENERATED_REBASE_STEPS):
        conflicts = unmerged_paths(worktree_path)
        if not conflicts or not commands.keys() >= set(conflicts):
            return None
        if not _rebuilt_clean(worktree_path, conflicts, commands):
            return None
        if git(["add", "--", *conflicts], cwd=worktree_path, check=False).returncode != 0:
            return None
        rebuilt.update(conflicts)
        proceed = git(
            ["-c", "core.editor=true", "rebase", "--continue"], cwd=worktree_path, check=False
        )
        if proceed.returncode == 0:
            return tuple(sorted(rebuilt))
    return None


def replay(repo_root: Path, worktree_path: Path, base: str, branch: str) -> ReplayOutcome:

    carried = merge_commits(worktree_path, base, branch)
    if carried:
        return ReplayOutcome(
            MERGE_COMMIT_ON_BRANCH,
            f"{branch} carries {len(carried)} merge commit(s) `git rebase` would skip, "
            f"discarding any resolution held only there: {', '.join(carried)}. "
            "Linearize the branch (rebase it onto the merged base in the lane) and re-land.",
        )

    before = git(["rev-parse", branch], cwd=worktree_path, check=False)
    tip = before.stdout.strip() if before.returncode == 0 else ""

    regenerated: tuple[str, ...] = ()
    rebase = git(["rebase", base, branch], cwd=worktree_path, check=False)
    if rebase.returncode != 0:
        rebuilt = rebuild_generated_conflicts(repo_root, worktree_path)
        if rebuilt is None:
            conflicts = unmerged_paths(worktree_path)
            git(["rebase", "--abort"], cwd=worktree_path, check=False)
            where = f" in: {', '.join(conflicts)}" if conflicts else ""
            return ReplayOutcome(
                "rebase-conflicts",
                f"rebase of {branch} onto {base} hit conflicts{where}",
                conflicts=conflicts,
            )
        regenerated = rebuilt

    if tip:
        lost = dropped_paths(worktree_path, tip, base)
        if lost:
            git(["reset", "--hard", tip], cwd=worktree_path, check=False)
            return ReplayOutcome(
                REPLAY_DROPPED_PATHS,
                f"rebase of {branch} onto {base} reported success but lost "
                f"{len(lost)} tracked path(s): {', '.join(lost)}. "
                f"{branch} has been restored to {tip[:12]}; nothing was landed.",
            )

    return ReplayOutcome(regenerated=regenerated)
