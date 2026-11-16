from __future__ import annotations

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
