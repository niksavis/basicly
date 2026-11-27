from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .checkout import git

REMOTE = "origin"
PR_PREFIX = "pr"

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


def _by_ancestry(
    main: Path, head: str, landed: str, base: str | None, started: str | None
) -> str | None:
    trunk = git(["rev-list", "--first-parent", landed], cwd=main, check=False).stdout.split()
    fresh = not started or head.startswith(started) or started.startswith(head)
    if head in trunk and fresh:
        return f"it has no commit of its own beyond {REMOTE}/{base}"
    if git(["merge-base", "--is-ancestor", head, landed], cwd=main, check=False).returncode == 0:
        return ""
    cherry = git(["cherry", landed, head], cwd=main, check=False)
    rows = [row for row in cherry.stdout.splitlines() if row.strip()]
    if cherry.returncode == 0 and rows and all(row.startswith("- ") for row in rows):
        return ""
    return None


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
    if (decided := _by_ancestry(main, head, landed, base, started)) is not None:
        return decided
    missing = unlanded(main, landed, head)
    if missing is None:
        return f"it could not be compared with {REMOTE}/{base}"
    if missing:
        return (
            f"{REMOTE}/{base} does not hold {len(missing)} path(s) it changed, so it has not "
            f"landed: {', '.join(missing)}"
        )
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


def _default_base(main: Path) -> str | None:
    listed = git(["ls-remote", "--symref", REMOTE, "HEAD"], cwd=main, check=False)
    for line in listed.stdout.splitlines():
        if line.startswith("ref: refs/heads/"):
            return line.removeprefix("ref: refs/heads/").split("\t")[0]
    return None


def lane_extras(main: Path, lane: str) -> list[tuple[str, str]] | None:
    patterns = [f"refs/heads/harness/{lane}-*", f"refs/heads/{PR_PREFIX}/{lane}/*"]
    listed = git(["ls-remote", "--heads", REMOTE, *patterns], cwd=main, check=False)
    if listed.returncode != 0:
        return None
    name = re.escape(lane)
    wanted = re.compile(rf"refs/heads/(harness/{name}-[0-9]+|{PR_PREFIX}/{name}/[0-9a-f]{{12}})")
    found = []
    for line in listed.stdout.splitlines():
        head, _, ref = line.partition("\t")
        if match := wanted.fullmatch(ref.strip()):
            found.append((match.group(1), head))
    return found


def _proof(main: Path, head: str, landed: str) -> str:
    if git(["merge-base", "--is-ancestor", head, landed], cwd=main, check=False).returncode == 0:
        return "ancestor"
    cherry = git(["cherry", landed, head], cwd=main, check=False)
    rows = [row for row in cherry.stdout.splitlines() if row.strip()]
    if cherry.returncode == 0 and rows and all(row.startswith("- ") for row in rows):
        return "patch-equivalent"
    return ""


def _by_hand(branch: str, head: str) -> str:
    ref = f"refs/heads/{branch}"
    return f"git push --force-with-lease={ref}:{head} {REMOTE} :{ref}"


def _extra_note(main: Path, branch: str, head: str, base: str, landed: str) -> str:
    if not _local(main, head):
        return (
            f"kept {REMOTE}/{branch}: head {head[:12]} is not fetched here; "
            f"run git fetch {REMOTE} and clean up again"
        )
    proof = _proof(main, head, landed)
    if not proof:
        return (
            f"kept {REMOTE}/{branch}: head {head[:12]} is not on {REMOTE}/{base}; land it, "
            f"or delete it by hand with: {_by_hand(branch, head)}"
        )
    ref = f"refs/heads/{branch}"
    gone = git(
        ["push", f"--force-with-lease={ref}:{head}", REMOTE, f":{ref}"], cwd=main, check=False
    )
    if gone.returncode != 0:
        return (
            f"kept {REMOTE}/{branch}: git push refused the delete: {(gone.stderr or '').strip()}; "
            f"check git ls-remote {REMOTE} {ref}, "
            f"then delete it by hand with: {_by_hand(branch, head)}"
        )
    return f"deleted {REMOTE}/{branch}: head {head[:12]} is on {REMOTE}/{base} ({proof})"


def delete_lane_extras(
    main: Path, lane: str, base: str | None, owners: Mapping[str, str]
) -> list[str]:
    if git(["remote", "get-url", REMOTE], cwd=main, check=False).returncode != 0:
        return []
    found = lane_extras(main, lane)
    retry = f"run git fetch {REMOTE}, then basicly worktree cleanup {lane} again"
    kept = f"kept the retry and pr branches of {lane}"
    if found is None:
        return [f"{kept}: git ls-remote {REMOTE} failed; {retry}"]
    if not found:
        return []
    base = base or _default_base(main)
    landed, absent = _base_head(main, base)
    if absent or base is None:
        return [f"{kept}: {absent or 'origin has no default branch'}; {retry}"]
    if not _local(main, landed):
        return [f"{kept}: {REMOTE}/{base} is not fetched here; {retry}"]
    notes = []
    for branch, head in found:
        if branch in owners:
            notes.append(
                f"kept {REMOTE}/{branch}: it is the branch of lane {owners[branch]}; "
                f"clean up that lane with basicly worktree cleanup {owners[branch]}"
            )
            continue
        notes.append(_extra_note(main, branch, head, base, landed))
    return notes


def report_extras(main: Path, lane: str, record: Any, sessions: list[Any]) -> None:
    owners = {other.branch: other.name for other in sessions if other.name != lane}
    for note in delete_lane_extras(main, lane, record.base if record else None, owners):
        print(f"  {note}")
