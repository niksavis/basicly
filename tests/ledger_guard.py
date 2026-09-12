from __future__ import annotations

import os
import sys
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

LOG_GLOB = "events-*.jsonl"

_WRITE_MODES = frozenset("wax+")

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

_LOCK = threading.Lock()
_WATCHES: list[Watch] = []
_ANNOUNCED: set[str] = set()


class LedgerChangedWarning(UserWarning):
    pass


def _key(path: str | os.PathLike[str]) -> str:

    return os.path.normcase(os.path.normpath(Path(path).absolute()))


def _writes(mode: object, flags: object) -> bool:
    if isinstance(mode, str):
        return bool(_WRITE_MODES & set(mode))
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _audit(event: str, args: tuple[object, ...]) -> None:

    if event != "open" or not _WATCHES:
        return
    path, mode, flags = args
    if not isinstance(path, str | os.PathLike) or not _writes(mode, flags):
        return
    opened = _key(path)
    with _LOCK:
        for watch in _WATCHES:
            watch.record(opened)


class Watch:
    def __init__(self, root: Path, nodeid: str) -> None:
        self.root = root
        self.nodeid = nodeid
        self.written: tuple[str, ...] = ()
        self.unexplained: tuple[str, ...] = ()
        self._prefixes = tuple({_key(root), _key(root.resolve())})
        self._opened: set[str] = set()
        self._before = (
            {path: path.read_bytes() for path in sorted(root.glob(LOG_GLOB))}
            if root.is_dir()
            else {}
        )

    def record(self, opened: str) -> None:
        if any(opened == root or opened.startswith(root + os.sep) for root in self._prefixes):
            self._opened.add(opened)

    def close(self) -> None:

        self.written = tuple(sorted(self._opened))
        changed = [path for path, held in self._before.items() if path.read_bytes() != held]
        if self.root.is_dir():
            changed += [
                path for path in sorted(self.root.glob(LOG_GLOB)) if path not in self._before
            ]
        self.unexplained = tuple(
            sorted(str(path) for path in changed if _key(path) not in self._opened)
        )


@contextmanager
def watching(root: Path, nodeid: str) -> Iterator[Watch]:
    watch = Watch(root, nodeid)
    with _LOCK:
        _WATCHES.append(watch)
    try:
        yield watch
    finally:
        with _LOCK:
            _WATCHES.remove(watch)
        watch.close()


def report(watch: Watch) -> None:

    if watch.written:
        pytest.fail(
            f"{watch.nodeid} opened this checkout's own ledger for writing: "
            f"{', '.join(watch.written)}. Nothing has been undone — the log is append-only,"
            f" so whatever was written is still there and has to be repaired deliberately. Use"
            f" `tests.flipped_tracker.flipped_repo` for a real ledger of its own, and check"
            f" that every fixture it needs actually runs — a fixture named but not requested"
            f" is how this happens."
        )
    for path in watch.unexplained:
        with _LOCK:
            announced = path in _ANNOUNCED
            _ANNOUNCED.add(path)
        if not announced:
            warnings.warn(
                f"{path} changed while {watch.nodeid} ran, and no write from this process"
                f" explains it — most likely another process using this checkout's tracker."
                f" No test is blamed for it and no byte was restored. Reported once per path"
                f" per session.",
                LedgerChangedWarning,
                stacklevel=2,
            )


sys.addaudithook(_audit)
