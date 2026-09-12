from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from basicly import base_lock, checkout, merge
from tests import flipped_tracker

if TYPE_CHECKING:
    from collections.abc import Iterator

LEDGER_DIRT = " M .basicly/ledger/events-0001.jsonl\n"

SAFETY_S = 30.0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _SharedTree:
    class _Proc:
        def __init__(self, stdout: str = "") -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def __init__(self) -> None:
        self.dirty = True
        self.commits: list[str] = []
        self.log: list[tuple[int, str]] = []
        self.arrived = threading.Semaphore(0)
        self.everybody_here = threading.Event()
        self._guard = threading.Lock()

    def __call__(self, args: list[str], **_kwargs: object) -> _SharedTree._Proc:
        self._note(args[0])
        if args[0] == "status":
            return self._status()
        if args[0] == "add":
            return self._Proc()
        if args[0] == "commit":
            return self._commit(args[-1])
        raise AssertionError(f"unstubbed git subcommand {args[0]!r}")

    def _note(self, subcommand: str) -> None:
        with self._guard:
            self.log.append((threading.get_ident(), subcommand))

    def _status(self) -> _SharedTree._Proc:
        self.everybody_here.wait(SAFETY_S)
        with self._guard:
            return self._Proc(LEDGER_DIRT if self.dirty else "")

    def _commit(self, message: str) -> _SharedTree._Proc:
        with self._guard:
            if not self.dirty:
                raise RuntimeError("command failed (1): git commit\nnothing to commit")
            self.dirty = False
            self.commits.append(message)
        return self._Proc()

    def visits(self) -> list[int]:

        ordered: list[int] = []
        for thread_id, _ in self.log:
            if not ordered or ordered[-1] != thread_id:
                ordered.append(thread_id)
        return ordered


class _CannedGit:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_kwargs: object) -> _SharedTree._Proc:
        self.calls.append(list(args))
        return _SharedTree._Proc(LEDGER_DIRT if args[0] == "status" else "")

    def ran(self, subcommand: str) -> bool:
        return any(call[0] == subcommand for call in self.calls)


@pytest.fixture
def canned_git(monkeypatch: pytest.MonkeyPatch) -> _CannedGit:
    git = _CannedGit()
    monkeypatch.setattr(merge, "git", git)
    flipped_tracker.refuse_spawn(monkeypatch)
    return git


@pytest.fixture
def held(tmp_path: Path) -> Iterator[None]:
    with base_lock.hold(tmp_path):
        yield


@pytest.mark.usefixtures("held")
def test_a_dispatch_that_loses_the_base_checkout_is_told_it_was_contention(
    tmp_path: Path, canned_git: _CannedGit
) -> None:

    with pytest.raises(base_lock.BaseCheckoutBusyError) as caught:
        merge.commit_tracker_state(tmp_path, "basicly-x", wait_s=0.0)

    message = str(caught.value)
    assert "another dispatch holds the base checkout" in message
    assert "contention between concurrent dispatches, not a rejected commit" in message
    assert f"pid {os.getpid()}" in message
    assert not canned_git.ran("status")
    assert not canned_git.ran("commit")


def test_a_queued_dispatch_gets_in_when_its_peer_releases_the_base_checkout(
    tmp_path: Path,
) -> None:

    clock = _Clock()
    peer = base_lock.hold(tmp_path)
    peer.__enter__()
    polls: list[float] = []

    def sleep(seconds: float) -> None:
        polls.append(seconds)
        clock.advance(seconds)
        if len(polls) == 3:
            peer.__exit__(None, None, None)

    with base_lock.hold(tmp_path, monotonic=lambda: clock.now, sleep=sleep):
        assert len(polls) == 3

    assert not (tmp_path / base_lock.LOCK_FILE).exists()


@pytest.mark.usefixtures("held")
def test_a_dispatch_queued_past_its_budget_names_the_holder_it_waited_for(
    tmp_path: Path,
) -> None:

    clock = _Clock()

    def sleep(seconds: float) -> None:
        clock.advance(seconds)

    queued = base_lock.hold(tmp_path, wait_s=60.0, monotonic=lambda: clock.now, sleep=sleep)
    with pytest.raises(base_lock.BaseCheckoutBusyError, match=r"did not release it in 60s"):
        queued.__enter__()


def test_a_crashed_holders_lock_is_taken_over_rather_than_waited_out(tmp_path: Path) -> None:

    path = tmp_path / base_lock.LOCK_FILE
    with base_lock.hold(tmp_path):
        os.utime(path, (0.0, 0.0))
        with base_lock.hold(tmp_path, wait_s=0.0):
            assert path.exists()


def test_a_second_resource_gets_the_queue_with_its_own_budget(tmp_path: Path) -> None:

    path = tmp_path / "usage" / "confirms.lock"
    with base_lock.hold_file(path, hold_budget_s=5.0, wait_s=0.0):
        os.utime(path, (0.0, time.time() - 10.0))
        with base_lock.hold_file(path, hold_budget_s=5.0, wait_s=0.0):
            assert path.exists()
    assert not path.exists()


def test_a_caller_with_no_wording_of_its_own_gets_a_refusal_naming_the_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "usage" / "confirms.lock"
    with (
        base_lock.hold_file(path, hold_budget_s=300.0, wait_s=0.0),
        pytest.raises(base_lock.LockBusyError, match=rf"pid {os.getpid()} has held .*confirms"),
    ):
        base_lock.hold_file(path, hold_budget_s=300.0, wait_s=0.0).__enter__()


def test_a_holder_releases_the_base_checkout_even_when_the_commit_raises(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="the commit blew up"), base_lock.hold(tmp_path):
        raise RuntimeError("the commit blew up")

    assert not (tmp_path / base_lock.LOCK_FILE).exists()
    with base_lock.hold(tmp_path, wait_s=0.0):
        pass


def test_the_release_retries_the_sharing_violation_a_waiter_read_causes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    real = base_lock._unlink
    refusals = [PermissionError("in use"), PermissionError("in use")]
    naps: list[float] = []

    def read_locked(path: Path) -> None:
        if refusals:
            raise refusals.pop()
        real(path)

    monkeypatch.setattr(base_lock, "_unlink", read_locked)
    with base_lock.hold(tmp_path, sleep=naps.append):
        pass

    assert not (tmp_path / base_lock.LOCK_FILE).exists()
    assert naps == [base_lock.POLL_S] * 2


def test_a_release_refused_past_every_retry_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def welded(_path: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("in use")

    monkeypatch.setattr(base_lock, "_unlink", welded)
    with pytest.raises(PermissionError), base_lock.hold(tmp_path, sleep=lambda _s: None):
        pass

    assert attempts == base_lock.RELEASE_ATTEMPTS


def test_a_create_refused_by_a_delete_pending_window_reads_as_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    real_open = os.open
    refusals = [PermissionError("delete pending")]
    naps: list[float] = []

    def delete_pending(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith(base_lock.LOCK_FILE.name) and refusals:
            raise refusals.pop()
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(base_lock.os, "open", delete_pending)
    with base_lock.hold(tmp_path, sleep=naps.append):
        pass

    assert not (tmp_path / base_lock.LOCK_FILE).exists()
    assert naps == [base_lock.POLL_S]


def test_the_lock_stays_invisible_to_the_checkout_it_protects(tmp_path: Path) -> None:

    checkout.git(["init", "--initial-branch=main"], cwd=tmp_path)
    with base_lock.hold(tmp_path):
        assert (tmp_path / base_lock.LOCK_FILE).exists()
        status = checkout.git(["status", "--porcelain"], cwd=tmp_path).stdout
    assert status == ""


def test_four_concurrent_dispatches_all_survive_the_base_checkout_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    tree = _SharedTree()
    monkeypatch.setattr(merge, "git", tree)
    flipped_tracker.refuse_spawn(monkeypatch)
    outcomes: dict[int, object] = {}

    def dispatch(lane: int) -> None:
        tree.arrived.release()
        try:
            outcomes[lane] = merge.commit_tracker_state(tmp_path, f"basicly-x.{lane}")
        except RuntimeError as exc:
            outcomes[lane] = exc

    threads = [threading.Thread(target=dispatch, args=(lane,)) for lane in range(4)]
    for thread in threads:
        thread.start()
    for _ in threads:
        assert tree.arrived.acquire(timeout=SAFETY_S), "a dispatch never reached the commit"
    tree.everybody_here.set()
    for thread in threads:
        thread.join(timeout=SAFETY_S)
        assert not thread.is_alive(), "a dispatch never returned"

    assert [outcome for outcome in outcomes.values() if isinstance(outcome, Exception)] == []
    assert sorted(outcomes) == [0, 1, 2, 3]
    assert sum(1 for outcome in outcomes.values() if outcome is True) == 1
    assert len(tree.commits) == 1
    visits = tree.visits()
    assert len(visits) == len(set(visits)) == 4
