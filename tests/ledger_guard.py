from __future__ import annotations

import ctypes
import os
import stat
import sys
import threading
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

_NATIVE_PATH = type(Path())

LOG_GLOB = "events-*.jsonl"

_WRITE_MODES = frozenset("wax+")

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

_LOCK = threading.Lock()
_WATCHES: list[Watch] = []
_READ_GUARDS: list[tuple[str, str]] = []
_ANNOUNCED: set[str] = set()
_CACHE: dict[Path, tuple[tuple[int, ...], bytes]] = {}
_CACHE_BYTES = 64 * 1024 * 1024
_CACHE_FILES = 64


def _native_resolver() -> Callable[[str | os.PathLike[str]], str] | None:
    if sys.platform != "linux":
        return None
    try:
        library = ctypes.CDLL(None)
        resolve = library.realpath
        release = library.free
    except AttributeError, OSError:
        return None
    resolve.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    resolve.restype = ctypes.c_void_p
    release.argtypes = [ctypes.c_void_p]
    release.restype = None

    def canonical(path: str | os.PathLike[str]) -> str:
        result = resolve(os.fsencode(path), None)
        if result is None:
            return os.path.realpath(path)
        try:
            return os.fsdecode(ctypes.string_at(result))
        finally:
            release(result)

    return canonical


_RESOLVE = _native_resolver() or os.path.realpath


class LedgerChangedWarning(UserWarning):
    pass


def _cache_identity(state: os.stat_result, platform_name: str) -> tuple[int, ...] | None:
    if platform_name != "posix" or not stat.S_ISREG(state.st_mode) or state.st_ctime_ns % 1000 == 0:
        return None
    return (
        state.st_dev,
        state.st_ino,
        state.st_mode,
        state.st_size,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )


def _read_ledger(path: Path) -> bytes:
    before = _cache_identity(path.stat(), os.name)
    if before is not None:
        with _LOCK:
            cached = _CACHE.get(path)
            if cached is not None and cached[0] == before:
                return cached[1]
    content = path.read_bytes()
    if (
        before is not None
        and before == _cache_identity(path.stat(), os.name)
        and len(content) <= _CACHE_BYTES
    ):
        with _LOCK:
            _CACHE.pop(path, None)
            while (
                len(_CACHE) >= _CACHE_FILES
                or sum(len(held[1]) for held in _CACHE.values()) + len(content) > _CACHE_BYTES
            ):
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[path] = before, content
    return content


def _key(path: str | os.PathLike[str]) -> str:

    return os.path.normcase(_NATIVE_PATH(path).absolute())


def _writes(mode: object, flags: object) -> bool:
    if isinstance(mode, str):
        return bool(_WRITE_MODES & set(mode))
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _audit(event: str, args: tuple[object, ...]) -> None:

    if event != "open" or not (_READ_GUARDS or _WATCHES):
        return
    path, mode, flags = args
    if not isinstance(path, str | os.PathLike):
        return
    opened = _key(path)
    for prefix, node in _READ_GUARDS:
        if any(
            candidate == prefix or candidate.startswith(prefix + os.sep)
            for candidate in (opened, os.path.normcase(_RESOLVE(path)))
        ):
            pytest.fail(f"{node} reads the live ledger; mark it live_ledger so it runs fresh")
    if not _writes(mode, flags):
        return
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
            {path: _read_ledger(path) for path in sorted(root.glob(LOG_GLOB))}
            if root.is_dir()
            else {}
        )

    def record(self, opened: str) -> None:
        if any(opened == root or opened.startswith(root + os.sep) for root in self._prefixes):
            self._opened.add(opened)

    def close(self) -> None:

        self.written = tuple(sorted(self._opened))
        changed = [path for path, held in self._before.items() if _read_ledger(path) != held]
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


@contextmanager
def independent_of(root: Path, nodeid: str) -> Iterator[None]:
    guard = (_key(root.resolve()), nodeid)
    _READ_GUARDS.append(guard)
    try:
        yield
    finally:
        _READ_GUARDS.remove(guard)
