from __future__ import annotations

import errno
import importlib.util
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = Path(__file__).parent.parent / ".basicly/core/kit/tracker/events.py"
_SPEC = importlib.util.spec_from_file_location("tracker_locking_regressions", SOURCE)
assert _SPEC and _SPEC.loader
events = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = events
_SPEC.loader.exec_module(events)


def test_an_empty_owner_file_does_not_authorize_another_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events.json, "dump", lambda *_args, **_kwargs: None)
    with events.LedgerLock(tmp_path) as owner:
        assert owner.path.stat().st_size == 0
        with pytest.raises(events.LockUnavailableError):
            events.LedgerLock(tmp_path, timeout_s=0).acquire()
        assert owner.held


def test_a_live_owner_is_not_stolen_after_the_old_age_limit(tmp_path: Path) -> None:
    now = [1000.0]
    with events.LedgerLock(tmp_path, monotonic=lambda: now[0]) as owner:
        now[0] += 60
        with pytest.raises(events.LockUnavailableError):
            events.LedgerLock(tmp_path, timeout_s=0, monotonic=lambda: now[0]).acquire()
        assert owner.held


def test_release_by_a_non_owner_preserves_the_owner(tmp_path: Path) -> None:
    with events.LedgerLock(tmp_path) as owner:
        events.LedgerLock(tmp_path).release()
        with pytest.raises(events.LockUnavailableError):
            events.LedgerLock(tmp_path, timeout_s=0).acquire()
        assert owner.held


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("error_number", [None, errno.EAGAIN, errno.EIO])
def test_portable_locking_distinguishes_contention_from_a_broken_backend(
    monkeypatch: pytest.MonkeyPatch, platform: str, error_number: int | None
) -> None:
    backend = ModuleType("msvcrt" if platform == "nt" else "fcntl")
    calls: list[tuple[int, ...]] = []

    def lock(*args: int) -> None:
        calls.append(args)
        if error_number is not None:
            raise OSError(error_number, "backend failed")

    if platform == "nt":
        backend.__dict__.update(LK_NBLCK=2, locking=lock)
        monkeypatch.setattr(events.locking.os, "lseek", lambda *_args: 0)
    else:
        backend.__dict__.update(LOCK_EX=2, LOCK_NB=4, flock=lock)
    monkeypatch.setitem(sys.modules, backend.__name__, backend)
    monkeypatch.setattr(events.locking.os, "name", platform)
    if error_number == errno.EIO:
        with pytest.raises(OSError) as caught:
            events.locking.try_exclusive(23)
        assert caught.value.errno == errno.EIO
    else:
        assert events.locking.try_exclusive(23) is (error_number is None)
    assert calls == [(23, 2, 1)] if platform == "nt" else calls == [(23, 6)]


_CHILD = """
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("isolated_events", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ledger = Path(sys.argv[2])
if sys.argv[3] == "hold":
    owner = module.LedgerLock(ledger).acquire()
    print("owned", flush=True)
    sys.stdin.readline()
else:
    for index in range(30):
        text = sys.argv[3] + ":" + str(index)
        module.append(ledger, [module.Draft("demo-aa11", "note", {"text": text})])
"""


def test_a_process_exit_releases_ownership_without_deleting_the_lock(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", _CHILD, str(SOURCE), str(tmp_path), "hold"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    ready = threading.Event()
    output: list[str] = []

    def read_ready() -> None:
        assert child.stdout is not None
        output.append(child.stdout.readline().strip())
        ready.set()

    reader = threading.Thread(target=read_ready, daemon=True)
    reader.start()
    try:
        assert ready.wait(10), "the child never acquired its lock"
        assert output == ["owned"]
        with pytest.raises(events.LockUnavailableError):
            events.LedgerLock(tmp_path, timeout_s=0).acquire()
    finally:
        child.terminate()
        child.communicate(timeout=10)
        reader.join(timeout=10)
    with events.LedgerLock(tmp_path, timeout_s=0) as successor:
        assert successor.held


def test_parallel_consumer_processes_append_one_contiguous_record_sequence(tmp_path: Path) -> None:
    def run(writer: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-S", "-c", _CHILD, str(SOURCE), str(tmp_path), str(writer)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    stored, bad = events.read_events(tmp_path)
    assert bad == []
    assert len(stored) == 120
    assert len({event.id for event in stored}) == 120
    assert sorted(event.seq for event in stored) == list(range(1, 121))
    folded = events.fold(stored)
    assert folded.forked == []
    assert folded.mismatched_totals == []
