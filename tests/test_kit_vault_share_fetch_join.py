from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_share import _ids, _machine, _write, bare, git_runner, vault_share

__all__ = ["bare"]


@pytest.mark.parametrize("urgent", [True, False])
def test_overlapping_urgent_shares_reuse_a_completed_fetch_generation(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, urgent: bool
) -> None:
    clone, state = _machine(tmp_path, bare, "share-fetch-join")
    vault_share.vault_clone.fetch(clone, clock=time.time)
    written = _write(clone, state, 1, "overlap")
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    second_fetched = threading.Event()
    counts: Counter[str] = Counter()
    original_lock = vault_share.events.LedgerLock
    guard = threading.Lock()
    lock_entries = 0

    @contextmanager
    def lock(directory: Path | str, **options: Any) -> Iterator[Any]:
        nonlocal lock_entries
        ordinal = 0
        if Path(directory) == clone.folder / "fetch":
            with guard:
                lock_entries += 1
                ordinal = lock_entries
            if ordinal == 2:
                waiting.set()
        with original_lock(directory, **options) as held:
            yield held
        if ordinal == 2:
            second_fetched.set()

    def runner(name: str):
        def run(args: list[str], cwd: Path, **options: Any) -> str:
            if args[0] == "fetch":
                counts[name] += 1
                if name == "first":
                    entered.set()
                    assert release.wait(timeout=5)
            if name == "first" and args[0] == "ls-tree":
                assert second_fetched.wait(timeout=5)
            assert "push" not in args
            return git_runner.run(args, cwd, **options)

        return run

    monkeypatch.setattr(vault_share.events, "LedgerLock", lock)
    plan = vault_share.Plan(
        time.time, urgent=urgent, unlocked=True, stop_when=second_fetched.is_set
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(vault_share.share, clone, state, plan=plan, runner=runner("first"))
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(
                vault_share.share, clone, state, plan=plan, runner=runner("second")
            )
            assert waiting.wait(timeout=5)
        finally:
            release.set()
        shown = [first.result(timeout=10), second.result(timeout=10)]

    assert counts == (Counter(first=1) if urgent else Counter(first=1, second=1))
    assert all(result["pending"] == 1 for result in shown)
    assert _ids(bare) == {}

    recovered = vault_share.share(clone, state, plan=vault_share.Plan(time.time))

    assert recovered["pending"] == 0
    assert _ids(bare) == {written[0]: 1}
