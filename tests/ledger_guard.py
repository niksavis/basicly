"""Attribute a write to a watched store to the test that issued it.

Whether a change to a directory this suite must not write to came from **this process**,
and from which test, or from somebody else. The boundary is attribution against
:mod:`tests.conftest`, which owns only which store is live and when a watch opens.

**Nothing here reverts anything (basicly-vkh0.51).** The guard this replaces wrote the old
bytes back before failing the test in flight: a hand edit to an append-only log, racing a
writer holding the ledger lock this process never takes. Measured 2026-08-19 in the base
checkout, twenty tracker writes issued while the suite ran produced zero events; the same
twenty in a quiet tree landed twenty.

Attribution is :pep:`578`'s ``open`` event because no monkeypatch covers every route — the
engine reaches its store through several seams and a kit loaded by path bypasses all of
them, while ``io.open`` and ``os.open`` raise the event wherever they are called from,
carrying the path as the writer spelled it: the incident's was relative, against a cwd
left at the real repository (basicly-e2mz.43). The byte comparison stays beside it as the
only thing that sees a writer this process is in no position to attribute.
"""

from __future__ import annotations

import os
import sys
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

# The append-only artifact the byte comparison judges. The hook watches the whole
# directory, so a write to the lock file or the snapshot is attributed too.
LOG_GLOB = "events-*.jsonl"

# `open` mode characters that can modify the file; `r+` truncates nothing and still writes.
_WRITE_MODES = frozenset("wax+")

# The `os.open` flags that mean the same, for the call that passes no mode.
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

# Open watches, read by the hook on every write-mode `open`, and the paths already
# reported as changed by somebody we cannot name. Under one lock because a list append
# beside another thread's read is two operations, not one.
_LOCK = threading.Lock()
_WATCHES: list[Watch] = []
_ANNOUNCED: set[str] = set()


class LedgerChangedWarning(UserWarning):
    """A watched store changed and no write from this process accounts for it."""


def _key(path: str | os.PathLike[str]) -> str:
    """*path* as the absolute, case-folded string the hook compares against a root.

    ``absolute`` rather than ``resolve``: this runs inside every write-mode ``open`` in the
    process, so it stays off the filesystem. A watch registers its root both as given and
    resolved, which is what keeps a symlinked temp dir matching.
    """
    return os.path.normcase(os.path.normpath(Path(path).absolute()))


def _writes(mode: object, flags: object) -> bool:
    """Whether an ``open`` audit event with *mode* and *flags* can modify the file."""
    if isinstance(mode, str):
        return bool(_WRITE_MODES & set(mode))
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _audit(event: str, args: tuple[object, ...]) -> None:
    """Record a write-mode ``open`` under any open watch's root.

    Runs inside ``open``, so it must neither raise — the exception would surface at an
    unrelated caller's ``open`` — nor open anything, which would recurse. String work and a
    set insert only, after a mode check that rejects almost every event.
    """
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
    """One store watched across one test: what changed, and whether the test changed it.

    :attr:`written` and :attr:`unexplained` are empty until :func:`watching` closes the
    watch; read them after the block, not inside it.
    """

    def __init__(self, root: Path, nodeid: str) -> None:
        """Snapshot *root*'s event logs; *nodeid* is the test the writes are charged to."""
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
        """Note that this process opened *opened* for writing, if it is under the root."""
        if any(opened == root or opened.startswith(root + os.sep) for root in self._prefixes):
            self._opened.add(opened)

    def close(self) -> None:
        """Read the store back and split what changed into attributed and not.

        A path this process opened for writing is reported whether or not the bytes moved:
        the writable handle on the live store is the defect, and an append that happened to
        write nothing is one line away from the incident.
        """
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
    """Watch *root* for the duration of the block, attributing writes to *nodeid*."""
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
    """Fail the test that wrote to *watch*'s store; warn about a change it did not make.

    Writes nothing, ever: a test's own write stays for whoever repairs the log, and another
    process's write is not rolled back under a lock this process does not hold.

    Raises:
        Failed: the test opened a path under the watched root for writing.
    """
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
