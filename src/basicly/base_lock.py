"""The base checkout is a single-writer resource; this is the queue for it.

Every lane gets its own checkout, but the commit that publishes its claim runs in
the **base** one; unguarded, concurrent dispatches lose to each other's git
(basicly-kjc5.63). :func:`hold` queues them: a loser waits, and one that waits past
:data:`WAIT_S` is told it lost to *contention*, not to a rejected commit. Unlike
the supervisor's lock, which refuses a contender, here everyone must get in.

Liveness is the lock's mtime and nothing refreshes it, so a holder older than
:data:`HOLD_BUDGET_S` is declared crashed and its lock stolen; the theft costs one
lane a duplicate-publish refusal, not correctness.

:func:`hold_file` is that queue over any lock path, budgets and wording parameterised:
the confirm-code store needed the same serialisation (basicly-kas8q7) and a second
hand-rolled `O_EXCL` loop would have re-opened the windows this one closed.
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


class LockBusyError(RuntimeError):
    """A live holder kept a lock file past a waiter's whole budget."""


class BaseCheckoutBusyError(LockBusyError):
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
    """Create the lock atomically; False when another dispatch already holds it.

    PermissionError is busy, not broken: windows surfaces a peer's in-flight
    unlink as a delete-pending window that refuses the create (basicly-s2obqz).
    """
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError, PermissionError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(_payload())
    return True


def _unlink(path: Path) -> None:
    """One removal attempt; the seam :func:`_release` retries through."""
    path.unlink(missing_ok=True)


# Bounded and short: the colliding reader holds the file for one `read_text`.
RELEASE_ATTEMPTS = 8


def _release(path: Path, sleep: Callable[[float], None]) -> None:
    """Remove the lock, retrying the sharing violation a waiter's read causes.

    Windows refuses to delete a file a `holder()` poll holds open; unretried, that
    stranded the lock and wedged every waiter (basicly-s2obqz). The last attempt
    propagates: a lock undeletable past the retries must be loud.
    """
    for _ in range(RELEASE_ATTEMPTS - 1):
        try:
            _unlink(path)
        except PermissionError:
            sleep(POLL_S)
        else:
            return
    _unlink(path)


def holder(repo_root: Path) -> tuple[int | None, float] | None:
    """The base checkout lock's age and holding pid; None when nobody holds it."""
    return holder_of(repo_root / LOCK_FILE)


def holder_of(path: Path) -> tuple[int | None, float] | None:
    """*path*'s age, and the holding pid when readable; None when nobody holds it."""
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


def _busy(path: Path, pid: int | None, age: float, waited: float) -> BaseCheckoutBusyError:
    """The refusal, worded so nobody reads contention as a rejected commit."""
    who = f"pid {pid}" if pid is not None else "an unidentified process"
    return BaseCheckoutBusyError(
        f"another dispatch holds the base checkout ({who}, {age:.0f}s into its "
        f"tracker-state commit) and did not release it in {waited:.0f}s; this is "
        "contention between concurrent dispatches, not a rejected commit — let the "
        f"running dispatch finish and re-run, or delete {path} if none is running"
    )


def _generic_busy(path: Path, pid: int | None, age: float, waited: float) -> LockBusyError:
    """The refusal for a lock whose caller did not word one of its own."""
    who = f"pid {pid}" if pid is not None else "an unidentified process"
    return LockBusyError(
        f"{who} has held {path} for {age:.0f}s and did not release it in {waited:.0f}s; "
        "let the holder finish and re-run, or delete the lock if none is running"
    )


@contextlib.contextmanager
def hold_file(  # noqa: PLR0913 - reason in basicly.d/basicly-kas8q7.toml
    path: Path,
    *,
    hold_budget_s: float,
    wait_s: float,
    poll_s: float = POLL_S,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    busy: Callable[[Path, int | None, float, float], Exception] = _generic_busy,
) -> Iterator[None]:
    """Hold *path* as a cross-process mutex for the block; queue for it if busy.

    What a second resource reuses here is not the loop, which is short, but what the
    loop learned: a create refused by a delete-pending window is busy rather than
    broken, a stale holder is stolen from by rename-aside so exactly one waiter wins,
    and a release retries the sharing violation a reader causes — three windows already
    paid for once each on windows (basicly-s2obqz).

    A holder older than *hold_budget_s* is declared crashed; *busy* words the refusal a
    live holder outlasting *wait_s* earns, given the path, the pid, that holder's age
    and the wait.

    Yields:
        Nothing; the block runs with *path* held.

    Raises:
        Exception: whatever *busy* returns, :class:`LockBusyError` by default.
    """
    _ignored_dir(path.parent)
    started = monotonic()
    while not _take(path):
        pid, age = holder_of(path) or (None, 0.0)
        if age > hold_budget_s and _steal(path):
            break
        waited = monotonic() - started
        if waited >= wait_s:
            raise busy(path, pid, age, waited)
        sleep(poll_s)
    try:
        yield
    finally:
        _release(path, sleep)


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
    with hold_file(
        repo_root / LOCK_FILE,
        hold_budget_s=HOLD_BUDGET_S,
        wait_s=wait_s,
        poll_s=poll_s,
        monotonic=monotonic,
        sleep=sleep,
        busy=_busy,
    ):
        yield
