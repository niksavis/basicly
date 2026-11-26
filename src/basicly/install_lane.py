from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from . import scaffolds
from .checkout import git, worktrees_root

if TYPE_CHECKING:
    from pathlib import Path


def lane_path(repo_root: Path, lane: str) -> Path:
    return worktrees_root(repo_root) / lane


def refusal(repo_root: Path, lane: str) -> str | None:
    path = lane_path(repo_root, lane)
    if not path.exists():
        return None
    if not git(["status", "--porcelain"], cwd=path, check=False).stdout.strip():
        return None
    return (
        f"basicly install --lane {lane}: the lane {path} has uncommitted changes; "
        f"inspect them with git -C {path} status, then commit them "
        f"or remove the lane with basicly worktree cleanup {lane}"
    )


def plan_lines(repo_root: Path, lane: str) -> list[str]:
    path = lane_path(repo_root, lane)
    state = "exists and is clean" if path.exists() else "would be created"
    return [f"lane: {lane} at {path} ({state})", f"release wheel: {scaffolds.DIST_SOURCE}"]


def check(repo_root: Path, lane: str, *, dry_run: bool) -> int | None:
    if refused := refusal(repo_root, lane):
        print(refused, file=sys.stderr)
        return 1
    if not dry_run:
        print(
            f"basicly install --lane {lane} writes nothing without --dry-run; "
            "to install a release in a lane and commit it, run from the workspace: "
            "orch install --release X.Y.Z --repo NAME=RECORD",
            file=sys.stderr,
        )
        return 2
    for line in plan_lines(repo_root, lane):
        print(line)
    return None
