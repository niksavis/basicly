"""Lane rows for work no supervisor is holding, from the worktrees git already tracks.

`board_facts` derives lanes from `supervise.derive_session`, which needs a live lock, so a lane
started with `basicly loop run` produced no row and the board read `no pass is running` with
four worktrees on disk (basicly-kqh9dj8). Not a second liveness model: `basicly-ncday7` already
shipped `board_sections.LANE_STATES`, and one branch set `lanes` to `()` before any of them
could be assigned. This supplies rows in the same words, so both boards read alike.

`landing` is never claimed. A landing holds no lock and writes no marker, so nothing on disk
tells it from waiting; both read `waits-to-land` and `LANDING_UNOBSERVABLE` says why.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import board_sections, checkout, supervise

# What `checkout.git` raises when git refuses or is absent: a RuntimeError carrying the failed
# command, or an OSError from the spawn. Named rather than caught broadly, so a bug in this
# module surfaces instead of being read as "git would not answer".
GIT_SILENT = (RuntimeError, OSError)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from collections.abc import Mapping, Sequence
    from datetime import datetime

# Carried on the row: a board that silently merges two states is the defect this ends.
LANDING_UNOBSERVABLE = "a landing holds no lock, so this row cannot tell landing from waiting"

# The branch a harness worktree is measured ahead of.
BASE_BRANCH = "main"

# The record statuses that mean nobody is coming back to this worktree.
PARKED_STATUSES = frozenset({"deferred"})

# With no supervisor registering a stream, recency is the only evidence anybody is inside, and
# `basicly-ze0po3` bars drawing an empty worktree as a running pass. The `basicly-fiow1sr`
# worktree that set the number sat a day untouched holding nine staged files.
FRESH_AFTER_S = 900.0


def _ahead(path: Path, base: str) -> int | None:
    """Commits on *path*'s HEAD that *base* does not hold, or None where git will not answer.

    A merged branch answers 0, as does one that did nothing; neither is work in flight.
    """
    try:
        out = checkout.git(["rev-list", "--count", f"{base}..HEAD"], cwd=path).stdout
    except GIT_SILENT:
        return None
    return int(out.strip()) if out.strip().isdigit() else None


def _changed(path: Path) -> tuple[str, ...] | None:
    """The paths *path* has uncommitted, or None where git will not answer.

    Untracked files count: an unstaged new module is work, not idleness.
    """
    try:
        out = checkout.git(["status", "--porcelain"], cwd=path).stdout
    except GIT_SILENT:
        return None
    return tuple(line[3:].strip() for line in out.splitlines() if line[3:].strip())


def touched_within(path: Path, changed: Sequence[str], now: float, window: float) -> bool:
    """Whether any of *changed* was written under *window* seconds before *now*.

    The closest thing to "an agent is in here" a lockless checkout can observe. A path git
    names and the filesystem lacks is skipped: that change already happened.
    """
    for name in changed:
        try:
            if now - (path / name).stat().st_mtime < window:
                return True
        except OSError:
            continue
    return False


def _tracked(repo_root: Path) -> dict[str, Path]:
    """Every worktree git tracks for *repo_root*, by directory name, or empty where it will not.

    Guarded because `board_facts.document` is built over non-repository temporary directories
    in nineteen tests, which an unguarded `git worktree list` failed.
    """
    try:
        return {path.name: path for path in checkout.registered_worktrees(repo_root)}
    except GIT_SILENT:
        return {}


def state_for(
    changed: Sequence[str] | None, fresh: bool, ahead: int | None, status: str
) -> tuple[str, str]:
    """The lane state for one worktree, and the detail that explains it.

    A parked record outranks its tree. Recent changes mean an agent is inside; the same
    changes left cold mean a worktree standing open, which `basicly-ze0po3` bars from reading
    as a running pass. Commits on a clean tree wait on the merge queue.
    """
    if status in PARKED_STATUSES:
        return supervise.LANE_PARKED, "the record is deferred"
    if changed and fresh:
        return supervise.LANE_RUNNING, f"{len(changed)} file(s) changed in the last few minutes"
    if changed:
        return supervise.LANE_PARKED, (
            f"{len(changed)} uncommitted file(s), none written recently - "
            "a worktree standing open with no agent observed in it"
        )
    if ahead:
        return supervise.LANE_WAITS_TO_LAND, LANDING_UNOBSERVABLE
    return supervise.LANE_QUEUED, "a worktree with no commits and no changes"


def lanes(
    repo_root: Path,
    details: Sequence[board_sections.DetailFacts],
    phase_map: Mapping[str, str],
    statuses: Mapping[str, str],
    moment: datetime,
) -> tuple[board_sections.LaneFacts, ...]:
    """A row per record whose worktree git still tracks, ordered by record id.

    *details* is `board_facts.details`' own output, so bindings are read once. A binding whose
    worktree git no longer tracks is dropped; the ref outlives the directory.

    *moment* is injected, not read: an mtime another process wrote is subtracted from the same
    clock the snapshot is dated with, the rule `board_sections`' stamp comparison follows.

    `live` stays unset. A live agent registers a stream with a supervisor, and there is none.
    """
    now = moment.timestamp()
    tracked = _tracked(repo_root)
    rows = []
    for detail in sorted(details, key=lambda row: row.id):
        path = tracked.get(detail.worktree)
        if not detail.worktree or path is None:
            continue
        changed, ahead = _changed(path), _ahead(path, BASE_BRANCH)
        fresh = touched_within(path, changed or (), now, FRESH_AFTER_S)
        state, why = state_for(changed, fresh, ahead, statuses.get(detail.id, ""))
        rows.append(
            board_sections.LaneFacts(
                id=detail.id,
                phase=phase_map.get(detail.id, ""),
                status=statuses.get(detail.id, ""),
                state=state,
                state_detail=why,
                provisioned=True,
                branch=detail.branch,
            )
        )
    return tuple(rows)
