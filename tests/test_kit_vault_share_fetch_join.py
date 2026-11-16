from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_share import _ids, _machine, _share, _write, bare, git_runner, vault_share

if TYPE_CHECKING:
    import pytest

__all__ = ["bare"]


def test_a_publisher_reuses_a_refresh_completed_while_it_waits(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "overlapping-publisher")
    written = _write(clone, state, 1, "queued")
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    base_lock = vault_share.events.LedgerLock
    fetches: list[str] = []

    @contextmanager
    def observed_lock(directory: Path | str, **options: Any) -> Iterator[Any]:
        if Path(directory) == clone.folder / "fetch" and entered.is_set():
            waiting.set()
        with base_lock(directory, **options) as held:
            yield held

    def fetching(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            fetches.append(args[0])
            entered.set()
            assert release.wait(5)
        return git_runner.run(args, cwd, **options)

    monkeypatch.setattr(vault_share.events, "LedgerLock", observed_lock)
    with ThreadPoolExecutor(max_workers=2) as workers:
        refresh = workers.submit(
            vault_share.vault_clone.fetch, clone, clock=time.time, runner=fetching
        )
        try:
            assert entered.wait(5)
            publish = workers.submit(
                _share,
                clone,
                state,
                runner=fetching,
                plan=vault_share.Plan(time.time, urgent=True, deadline_s=5),
            )
            assert waiting.wait(5)
        finally:
            release.set()
        assert refresh.result(timeout=10)["fetched"] is True
        shown = publish.result(timeout=10)

    assert shown["pending"] == 0 and shown["shared"] == 1
    assert set(_ids(bare)) == set(written)
    assert fetches == ["fetch"]


def test_a_rejected_publisher_reuses_a_refresh_completed_between_retry_checks(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "retrying-publisher")
    other, other_state = _machine(tmp_path, bare, "peer-publisher")
    written = _write(clone, state, 1, "own")
    peer = _write(other, other_state, 1, "peer")
    original = vault_share.vault_clone._advanced_head
    fetches: list[str] = []
    completed: list[str] = []
    pushed = False

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal pushed
        if args[0] == "fetch":
            fetches.append(args[0])
        if "push" in args and not pushed:
            pushed = True
            assert _share(other, other_state)["pending"] == 0
        return git_runner.run(args, cwd, **options)

    def completed_refresh(machine: Any, previous: str, run: Any, timeout: float) -> str:
        observed = original(machine, previous, run, timeout)
        if machine == clone and not completed:
            assert observed == ""
            refreshed = vault_share.vault_clone.fetch(machine, clock=time.time, runner=runner)
            assert refreshed["seen"] != previous
            completed.append(str(refreshed["seen"]))
        return observed

    monkeypatch.setattr(vault_share.vault_clone, "_advanced_head", completed_refresh)
    shown = _share(
        clone,
        state,
        runner=runner,
        plan=vault_share.Plan(time.time, urgent=True, deadline_s=10),
    )

    assert shown["pending"] == 0 and shown["shared"] == 1
    assert set(_ids(bare)) == set(written + peer)
    assert len(completed) == 1
    assert fetches == ["fetch", "fetch"]


def test_a_joined_fetch_reports_the_head_it_actually_validated(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clones = vault_share.vault_clone
    clone, _state = _machine(tmp_path, bare, "joined-witness")
    producer, producer_state = _machine(tmp_path, bare, "later-publisher")
    clones.fetch(clone, clock=time.time)
    clones.fetch(producer, clock=time.time)
    original_head = git_runner.run(["rev-parse", clones.HEAD_REF], clone.bare).strip()
    written = _write(producer, producer_state, 1, "later canonical event")
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    base_lock = clones.events.LedgerLock
    facts: dict[str, Any] = {}

    @contextmanager
    def observed_lock(directory: Path | str, **options: Any) -> Iterator[Any]:
        if Path(directory) == clone.folder / "fetch" and entered.is_set():
            waiting.set()
        with base_lock(directory, **options) as held:
            yield held

    def helper(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            entered.set()
            assert release.wait(10)
        return git_runner.run(args, cwd, **options)

    def reader(args: list[str], cwd: Path, **options: Any) -> str:
        result = git_runner.run(args, cwd, **options)
        if args[:4] == ["rev-parse", "--verify", "-q", clones.SEEN_REF] and not facts:
            assert result.strip() == original_head
            published = _share(producer, producer_state)
            assert published["pending"] == 0
            git_runner.run(
                ["fetch", "--quiet", "--no-tags", "origin", clones.FETCH_REFSPEC],
                clone.bare,
                allow_file=clone.allow_file,
            )
            facts["later_canonical_head"] = published["head"]
            assert facts["later_canonical_head"] != original_head
            assert clones._read(clone.folder / clones.COVERS_NAME).strip() == original_head
            assert not set(written) & clones.valid_ids(clone)
        return result

    monkeypatch.setattr(clones.events, "LedgerLock", observed_lock)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(clones.fetch, clone, clock=time.time, runner=helper)
        try:
            assert entered.wait(10)
            second = workers.submit(
                clones.fetch_with,
                clone,
                clones.Fetch(time.time, runner=reader, coalesce=True),
            )
            assert waiting.wait(10)
        finally:
            release.set()
        assert first.result(timeout=15)["fetched"] is True
        shown = second.result(timeout=15)

    assert facts["later_canonical_head"]
    assert shown["coalesced"] is True
    assert shown["head"] == shown["seen"] == original_head, {**facts, "report": shown}
