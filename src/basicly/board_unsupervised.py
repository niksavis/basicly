from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import board_sections, checkout, loop_state, supervise

GIT_SILENT = (RuntimeError, OSError)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from collections.abc import Mapping, Sequence
    from datetime import datetime

LANDING_UNOBSERVABLE = "a landing holds no lock, so this row cannot tell landing from waiting"

BASE_BRANCH = "main"

MERGED_AWAITING_TEARDOWN = (
    "base holds every commit on this branch - the work merged and the worktree awaits teardown"
)

PAST_BUILD_PHASES = frozenset(loop_state.PHASES[loop_state.PHASES.index("build") + 1 :])

PARKED_STATUSES = frozenset({"deferred"})

FRESH_AFTER_S = 900.0


def _ahead(path: Path, base: str) -> int | None:

    try:
        out = checkout.git(["rev-list", "--count", f"{base}..HEAD"], cwd=path).stdout
    except GIT_SILENT:
        return None
    return int(out.strip()) if out.strip().isdigit() else None


def _changed(path: Path) -> tuple[str, ...] | None:

    try:
        out = checkout.git(["status", "--porcelain"], cwd=path).stdout
    except GIT_SILENT:
        return None
    return tuple(line[3:].strip() for line in out.splitlines() if line[3:].strip())


def touched_within(path: Path, changed: Sequence[str], now: float, window: float) -> bool:

    for name in changed:
        try:
            if now - (path / name).stat().st_mtime < window:
                return True
        except OSError:
            continue
    return False


def _tracked(repo_root: Path) -> dict[str, Path]:

    try:
        return {path.name: path for path in checkout.registered_worktrees(repo_root)}
    except GIT_SILENT:
        return {}


def state_for(
    changed: Sequence[str] | None, fresh: bool, ahead: int | None, status: str, phase: str
) -> tuple[str, str]:

    if status in PARKED_STATUSES:
        return supervise.LANE_PARKED, "the record is deferred"
    if changed and fresh:
        return "", f"{len(changed)} file(s) changed recently; runtime is unobserved"
    if changed:
        return "", (
            f"{len(changed)} uncommitted file(s), none written recently; runtime is unobserved"
        )
    if ahead:
        return "", LANDING_UNOBSERVABLE
    if ahead == 0 and phase in PAST_BUILD_PHASES:
        return supervise.LANE_LANDED, MERGED_AWAITING_TEARDOWN
    reason = (
        "commit comparison unavailable; runtime is unobserved"
        if ahead is None
        else "no commits ahead and no file changes; runtime is unobserved"
    )
    return "", reason


def lanes(
    repo_root: Path,
    details: Sequence[board_sections.DetailFacts],
    phase_map: Mapping[str, str],
    statuses: Mapping[str, str],
    moment: datetime,
) -> tuple[board_sections.LaneFacts, ...]:

    now = moment.timestamp()
    tracked = _tracked(repo_root)
    rows = []
    for detail in sorted(details, key=lambda row: row.id):
        path = tracked.get(detail.worktree)
        if not detail.worktree or path is None:
            continue
        changed, ahead = _changed(path), _ahead(path, BASE_BRANCH)
        fresh = touched_within(path, changed or (), now, FRESH_AFTER_S)
        state, why = state_for(
            changed, fresh, ahead, statuses.get(detail.id, ""), phase_map.get(detail.id, "")
        )
        rows.append(
            board_sections.LaneFacts(
                id=detail.id,
                phase=phase_map.get(detail.id, ""),
                status=statuses.get(detail.id, ""),
                state=state,
                state_detail=why,
                note=why if not state else "",
                provisioned=True,
                branch=detail.branch,
            )
        )
    return tuple(rows)
