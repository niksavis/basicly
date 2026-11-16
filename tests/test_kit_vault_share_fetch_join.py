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
