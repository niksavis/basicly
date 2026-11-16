from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_claim import _ask, vault_claim
from tests.test_kit_vault_share import _machine, bare

if TYPE_CHECKING:
    import pytest

__all__ = ["bare"]


def test_a_failed_helper_leaves_time_for_the_publisher_to_finish(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "helper-retry-budget")
    vault_claim.vault_clone.fetch(clone, clock=time.time)
    (clone.folder / vault_claim.vault_clone.FETCHED_NAME).write_text("0", encoding="utf-8")
    elapsed = [0.0]
    attempts: list[float] = []
    calls = 0
    share = vault_claim.vault_share.share

    def publisher(*args: Any, **options: Any) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise vault_claim.events.LockUnavailableError("the publisher is finishing")
        return share(*args, **options)

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch" and calls == 1:
            allowance = float(options["timeout"])
            attempts.append(allowance)
            elapsed[0] += allowance
            raise vault_claim.git_runner.GitTimeoutError("the helper used its allowance")
        return vault_claim.git_runner.run(args, cwd, **options)

    def sleep(seconds: float) -> None:
        elapsed[0] += seconds

    monkeypatch.setattr(vault_claim, "_check", lambda *_: None)
    monkeypatch.setattr(vault_claim.vault_share, "share", publisher)
    monkeypatch.setattr(vault_claim, "time", SimpleNamespace(sleep=sleep))
    shown = vault_claim.claim(
        _ask(clone, state, "publisher", runner=runner), deadline=4, ticks=lambda: elapsed[0]
    )

    assert shown["exit"] == vault_claim.LANDED, shown
    assert calls == 2
    assert len(attempts) == 2
    assert sum(attempts) <= 2
    assert elapsed[0] < 4
    assert shown["claim"] in vault_claim.vault_clone.valid_ids(clone)
    assert not vault_claim.vault_queue.pending(clone.folder / "queue.jsonl")
