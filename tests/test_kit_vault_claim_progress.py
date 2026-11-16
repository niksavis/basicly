from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_claim import _ask, vault_claim
from tests.test_kit_vault_share import _machine, _write, bare, vault_share

if TYPE_CHECKING:
    import pytest

__all__ = ["bare"]


def test_a_joiner_reserves_takeover_for_a_publisher_that_stops_progressing(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "progress")
    elapsed = [0.0]
    calls = 0
    shared = vault_share.share

    def busy(*args: Any, **options: Any) -> dict:
        nonlocal calls
        calls += 1
        if calls <= 10:
            assert not options["plan"].unlocked, "the active publisher was duplicated"
            (clone.folder / "share.progress").write_text(str(calls), encoding="utf-8")
            elapsed[0] += 1
            raise vault_claim.events.LockUnavailableError("the publisher is active")
        if not options["plan"].unlocked:
            elapsed[0] += 1
            raise vault_claim.events.LockUnavailableError("the publisher has stalled")
        return shared(*args, **options)

    monkeypatch.setattr(vault_share, "share", busy)
    shown = vault_claim.claim(_ask(clone, state, "joiner"), deadline=30, ticks=lambda: elapsed[0])

    assert shown["exit"] == vault_claim.LANDED, shown
    assert shown["joined"] is True
    assert calls > 10
    assert elapsed[0] < 30


def test_rejected_publications_expose_new_progress_without_changing_the_queue(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "publisher-progress")
    written = _write(clone, state, 1, "progress")
    observed: list[str] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            path = clone.folder / "share.progress"
            observed.append(path.read_text() if path.exists() else "")
            if len(observed) == 1:
                raise vault_share.git_runner.GitCommandError("push", 1, "retry")
        return vault_share.git_runner.run(args, cwd, **options)

    shown = vault_share.share(
        clone, state, plan=vault_share.Plan(lambda: 1000.0, urgent=True), runner=runner
    )

    assert len(observed) == 2
    assert all(observed) and observed[0] != observed[1]
    assert shown["pending"] == 0
    assert vault_share.vault_clone.valid_ids(clone) == set(written)


def test_a_waiting_claim_observes_a_winner_before_its_publisher_returns(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "refresh-progress")
    ready, publish, published, finish = (threading.Event() for _ in range(4))

    def leading(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            ready.set()
            assert publish.wait(5)
            result = vault_claim.git_runner.run(args, cwd, **options)
            published.set()
            assert finish.wait(5)
            return result
        return vault_claim.git_runner.run(args, cwd, **options)

    with ThreadPoolExecutor(max_workers=2) as workers:
        leader = workers.submit(
            vault_claim.claim, _ask(clone, state, "leader", runner=leading), deadline=8
        )
        try:
            assert ready.wait(5)
            follower = workers.submit(vault_claim.claim, _ask(clone, state, "follower"), deadline=8)
            until = time.monotonic() + 5
            while not any(
                row.get("session") == "follower"
                for row in vault_claim.vault_queue.pending(clone.folder / "queue.jsonl")
            ):
                assert time.monotonic() < until
                time.sleep(0.01)
            publish.set()
            assert published.wait(5)
            shown = follower.result(timeout=3)
            assert shown["exit"] == vault_claim.LOST, shown
            assert shown["holder"]["session"] == "leader"
            assert not leader.done()
        finally:
            publish.set()
            finish.set()
        assert leader.result(timeout=10)["exit"] == vault_claim.LANDED


def test_an_exhausted_helper_keeps_time_for_the_claim_response(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "helper-deadline")
    vault_claim.vault_clone.fetch(clone, clock=time.time)
    (clone.folder / vault_claim.vault_clone.FETCHED_NAME).write_text("0", encoding="utf-8")
    elapsed = [0.0]
    fetches: list[float] = []

    def busy(*_args: Any, **_options: Any) -> dict:
        raise vault_claim.events.LockUnavailableError("the publisher is busy")

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            fetches.append(float(options["timeout"]))
            elapsed[0] += float(options["timeout"])
            raise vault_claim.git_runner.GitTimeoutError("the helper exhausted its allowance")
        return vault_claim.git_runner.run(args, cwd, **options)

    def sleep(seconds: float) -> None:
        elapsed[0] += seconds

    monkeypatch.setattr(vault_claim, "_check", lambda *_: None)
    monkeypatch.setattr(vault_share, "share", busy)
    monkeypatch.setattr(vault_claim.time, "sleep", sleep)
    shown = vault_claim.claim(
        _ask(clone, state, "helper", runner=runner), deadline=2, ticks=lambda: elapsed[0]
    )

    assert len(fetches) == 2
    assert shown["exit"] == vault_claim.UNSETTLED, shown
    assert elapsed[0] < 2
    assert len(vault_claim.vault_queue.pending(clone.folder / "queue.jsonl")) == 1
