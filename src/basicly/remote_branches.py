from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .checkout import git

REMOTE = "origin"

Unlanded = Callable[[Path, str, str], tuple[str, ...] | None]


def remote_head(main: Path, branch: str) -> str | None:
    listed = git(["ls-remote", "--heads", REMOTE, f"refs/heads/{branch}"], cwd=main, check=False)
    if listed.returncode != 0:
        return None
    return listed.stdout.split()[0] if listed.stdout.strip() else ""


def _local(main: Path, sha: str) -> bool:
    return git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=main, check=False).returncode == 0


def _base_head(main: Path, base: str | None) -> tuple[str, str]:
    landed = remote_head(main, base) if base else ""
    if landed is None:
        return "", f"git ls-remote {REMOTE} failed for the base {base}, so it was not compared"
    if not landed:
        return "", f"origin has no base branch {base!r} to compare it with"
    return landed, ""


def _keep_reason(
    main: Path, head: str, base: str | None, unlanded: Unlanded, started: str | None
) -> str:
    landed, absent = _base_head(main, base)
    if absent:
        return absent
    if not _local(main, head) or not _local(main, landed):
        return (
            f"its head or {REMOTE}/{base} is not fetched here; "
            f"run git fetch {REMOTE} and clean up again"
        )
    missing = unlanded(main, landed, head)
    if missing is None:
        return f"it could not be compared with {REMOTE}/{base}"
    if missing:
        return (
            f"{REMOTE}/{base} does not hold {len(missing)} path(s) it changed, so it has not "
            f"landed: {', '.join(missing)}"
        )
    trunk = git(["rev-list", "--first-parent", landed], cwd=main, check=False).stdout.split()
    fresh = not started or head.startswith(started) or started.startswith(head)
    if head in trunk and fresh:
        return f"it has no commit of its own beyond {REMOTE}/{base}"
    return ""


def delete_landed(
    main: Path, branch: str, base: str | None, unlanded: Unlanded, started: str | None = None
) -> str:
    if git(["remote", "get-url", REMOTE], cwd=main, check=False).returncode != 0:
        return ""
    head = remote_head(main, branch)
    if head is None:
        return f"kept {REMOTE}/{branch}: git ls-remote {REMOTE} failed, so it was not checked"
    if not head:
        return ""
    if reason := _keep_reason(main, head, base, unlanded, started):
        return f"kept {REMOTE}/{branch}: {reason}"
    ref = f"refs/heads/{branch}"
    lease = f"--force-with-lease={ref}:{head}"
    gone = git(["push", lease, REMOTE, f":{ref}"], cwd=main, check=False)
    if gone.returncode != 0:
        return f"kept {REMOTE}/{branch}: git push refused the delete: {(gone.stderr or '').strip()}"
    return f"deleted {REMOTE}/{branch}: {REMOTE}/{base} holds every path it changed"


def reclaim(main: Path, branch: str, record: Any, unlanded: Unlanded) -> str:
    base, started = (record.base, record.base_head) if record else (None, None)
    note = delete_landed(main, branch, base, unlanded, started)
    if note.startswith("deleted"):
        print(f"  {note}")
        return ""
    return note
