from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

LOCK_FILE = Path(".basicly/usage/landing.lock")
LIMIT_S = 3600.0
POLL_S = 5.0
UNLOCK = "basicly worktree unlock --lane {lane}"


class LandingLockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Holder:
    lane: str
    record: str
    pid: int | None
    age_s: float


def _payload(lane: str, record: str) -> str:
    return json.dumps(
        {"lane": lane, "record": record, "pid": os.getpid(), "limit_s": LIMIT_S}, sort_keys=True
    )


def _take(path: Path, lane: str, record: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(_payload(lane, record))
    return True


def holder(repo_root: Path, now: Callable[[], float] = time.time) -> Holder | None:
    path = repo_root / LOCK_FILE
    try:
        age = now() - path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError, ValueError:
        data = {}
        age = 0.0
    if not isinstance(data, dict):
        data = {}
    pid = data.get("pid")
    return Holder(
        lane=str(data.get("lane") or "an unnamed lane"),
        record=str(data.get("record") or "no record"),
        pid=pid if isinstance(pid, int) else None,
        age_s=age,
    )


def refusal(found: Holder) -> str:
    minutes = found.age_s / 60
    if found.age_s > LIMIT_S:
        return (
            f"the landing lock held by {found.lane} ({found.record}, pid {found.pid}) is "
            f"{minutes:.0f} min old, over its {LIMIT_S / 60:.0f} min limit; nothing was "
            f"taken; if no landing runs, remove it with {UNLOCK.format(lane=found.lane)}"
        )
    return (
        f"another landing holds the lock: {found.lane} ({found.record}, pid {found.pid}), "
        f"{minutes:.0f} min old; wait until it ends and run the merge again, or pass --wait"
    )


@contextlib.contextmanager
def hold(
    repo_root: Path,
    *,
    lane: str,
    record: str,
    wait_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    path = repo_root / LOCK_FILE
    started = time.monotonic()
    while not _take(path, lane, record):
        found = holder(repo_root)
        if found is None:
            continue
        if found.age_s > LIMIT_S or time.monotonic() - started >= wait_s:
            raise LandingLockedError(refusal(found))
        sleep(POLL_S)
    try:
        yield
    finally:
        _release(path)


def _release(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return
    if isinstance(data, dict) and data.get("pid") == os.getpid():
        path.unlink(missing_ok=True)


def unlock(repo_root: Path, lane: str) -> str:
    found = holder(repo_root)
    if found is None:
        return "no landing lock is held"
    if found.lane != lane:
        raise LandingLockedError(
            f"the landing lock is held by {found.lane}, not {lane}; "
            f"run {UNLOCK.format(lane=found.lane)} only when no landing of it runs"
        )
    (repo_root / LOCK_FILE).unlink(missing_ok=True)
    return (
        f"removed the landing lock of {found.lane} ({found.record}, {found.age_s / 60:.0f} min old)"
    )
