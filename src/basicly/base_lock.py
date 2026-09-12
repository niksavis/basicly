from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

LOCK_FILE = Path(".basicly/usage/base-checkout.lock")

HOLD_BUDGET_S = 300.0

WAIT_S = 4 * HOLD_BUDGET_S

POLL_S = 0.25


def _ignored_dir(directory: Path) -> None:

    directory.mkdir(parents=True, exist_ok=True)
    gitignore = directory / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")


class LockBusyError(RuntimeError):
    pass


class BaseCheckoutBusyError(LockBusyError):
    pass


def _now() -> float:
    return time.time()


def _payload() -> str:
    return json.dumps({"pid": os.getpid()}, sort_keys=True)


def _take(path: Path) -> bool:

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError, PermissionError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(_payload())
    return True


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


RELEASE_ATTEMPTS = 8


def _release(path: Path, sleep: Callable[[float], None]) -> None:

    for _ in range(RELEASE_ATTEMPTS - 1):
        try:
            _unlink(path)
        except PermissionError:
            sleep(POLL_S)
        else:
            return
    _unlink(path)


def holder(repo_root: Path) -> tuple[int | None, float] | None:
    return holder_of(repo_root / LOCK_FILE)


def holder_of(path: Path) -> tuple[int | None, float] | None:
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

    tombstone = path.with_name(f"{path.name}.stale.{os.getpid()}")
    try:
        path.replace(tombstone)
    except OSError:
        return False
    tombstone.unlink(missing_ok=True)
    return _take(path)


def _busy(path: Path, pid: int | None, age: float, waited: float) -> BaseCheckoutBusyError:
    who = f"pid {pid}" if pid is not None else "an unidentified process"
    return BaseCheckoutBusyError(
        f"another dispatch holds the base checkout ({who}, {age:.0f}s into its "
        f"tracker-state commit) and did not release it in {waited:.0f}s; this is "
        "contention between concurrent dispatches, not a rejected commit — let the "
        f"running dispatch finish and re-run, or delete {path} if none is running"
    )


def _generic_busy(path: Path, pid: int | None, age: float, waited: float) -> LockBusyError:
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
