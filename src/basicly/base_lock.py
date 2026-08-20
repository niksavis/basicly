"""The base checkout is a single-writer resource; this is the queue for it.

Every lane gets its own checkout, but the commit that publishes its claim runs in
the **base** one. Measured 2026-08-19: four same-second `basicly loop run` dispatches
left one commit and three non-zero exits having done nothing — two on ``git
commit``'s exit 1 (a peer had committed the same dirt, so nothing was staged) and
one on exit 128 (a peer held ``.git/index.lock``). Fan-out was bounded by an
unguarded serial step, not by the isolation model (basicly-kjc5.63).

:func:`hold` is the queue they were missing: a loser waits, and one that waits past
:data:`WAIT_S` is told it lost to *contention*. That wording is half the bug —
``command failed (1): git commit`` reads as a rejected commit, so an operator
debugged the hook chain. Not the supervisor's lock, which *refuses* a contender to
keep supervisor-ness a singleton; here everyone must eventually get in.

**Failure mode.** Liveness is the lock's mtime and nothing refreshes it: the section
has no thread to beat from. It runs the whole pre-commit chain, so
:data:`HOLD_BUDGET_S` is minutes — and a slower commit is declared crashed and its
lock stolen. The theft costs one lane the pre-fix behaviour, not correctness.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

LOCK_FILE = Path(".basicly/usage/base-checkout.lock")

# One gated `git commit`, so the hold is minutes. An older lock is a crashed holder.
HOLD_BUDGET_S = 300.0

# The worktree cap's worth of holders, each taking the whole budget above. Past this,
# waiting longer is not the answer: something is wedged and the operator must hear so.
WAIT_S = 4 * HOLD_BUDGET_S

POLL_S = 0.25


def _ignored_dir(directory: Path) -> None:
    """Make *directory* exist and be invisible to git, `.gitignore` itself included.

    The guarded section refuses to commit anything that is not tracker state, so a lock
    file git can see would break the step it protects.
    """
    directory.mkdir(parents=True, exist_ok=True)
    gitignore = directory / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")


class BaseCheckoutBusyError(RuntimeError):
    """A concurrent dispatch held the base checkout for longer than the wait budget."""


# Staleness subtracts a filesystem mtime, which shares no origin with a monotonic
# clock. Same seam and exemption as `supervise._now`; every other duration is monotonic.
def _now() -> float:
    """Wall-clock seconds; indirection so tests can pin the clock."""
    return time.time()


def _payload() -> str:
    """What a waiter's refusal is able to say about this holder."""
    return json.dumps({"pid": os.getpid()}, sort_keys=True)


def _take(path: Path) -> bool:
    """Create the lock atomically; False when another dispatch already holds it."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(_payload())
    return True


def holder(repo_root: Path) -> tuple[int | None, float] | None:
    """The lock's age, and the holding pid when readable; None when nobody holds it."""
    path = repo_root / LOCK_FILE
    try:
        age = _now() - path.stat().st_mtime
    except OSError:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        data = {}
    pid = data.get("pid") if isinstance(data, dict) else None
    return (pid if isinstance(pid, int) else None, age)


def _steal(path: Path) -> bool:
    """Take a crashed holder's lock over; False when another waiter got there first.

    The rename-aside admits exactly one waiter: ``Path.replace`` succeeds for one
    caller and raises for the rest.
    """
    tombstone = path.with_name(f"{path.name}.stale.{os.getpid()}")
    try:
        path.replace(tombstone)
    except OSError:
        return False
    tombstone.unlink(missing_ok=True)
    return _take(path)


def _busy(pid: int | None, age: float, waited: float, path: Path) -> BaseCheckoutBusyError:
    """The refusal, worded so nobody reads contention as a rejected commit."""
    who = f"pid {pid}" if pid is not None else "an unidentified process"
    return BaseCheckoutBusyError(
        f"another dispatch holds the base checkout ({who}, {age:.0f}s into its "
        f"tracker-state commit) and did not release it in {waited:.0f}s; this is "
        "contention between concurrent dispatches, not a rejected commit — let the "
        f"running dispatch finish and re-run, or delete {path} if none is running"
    )


@contextlib.contextmanager
def hold(
    repo_root: Path,
    *,
    wait_s: float = WAIT_S,
    poll_s: float = POLL_S,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    """Hold the base checkout for the duration of the block; queue for it if busy.

    *monotonic* and *sleep* are injected so a test never spends the budget, and the
    clock is monotonic so a wall-time step cannot resize the queue.

    Yields:
        Nothing; the block runs with the base checkout held.

    Raises:
        BaseCheckoutBusyError: the budget elapsed with the lock still held.
    """
    path = repo_root / LOCK_FILE
    _ignored_dir(path.parent)
    started = monotonic()
    while not _take(path):
        pid, age = holder(repo_root) or (None, 0.0)
        if age > HOLD_BUDGET_S and _steal(path):
            break
        waited = monotonic() - started
        if waited >= wait_s:
            raise _busy(pid, age, waited, path)
        sleep(poll_s)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
