from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_claim import _ask, vault_claim
from tests.test_kit_vault_share import _machine, bare, vault_share

__all__ = ["bare"]


@pytest.mark.parametrize("condition", ["retracted", "unrepaired", "ended"])
def test_a_waiter_preserves_the_publishers_terminal_refusal(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, condition: str
) -> None:
    clone, state = _machine(tmp_path, bare, "terminal-refresh")
    vault_claim.vault_clone.fetch(clone, clock=time.time)
    (clone.folder / vault_claim.vault_clone.FETCHED_NAME).write_text("0", encoding="utf-8")
    refreshes = 0

    def busy(*_args: Any, **_options: Any) -> dict:
        raise vault_claim.events.LockUnavailableError("the publisher is busy")

    def fetch(*_args: Any, **_options: Any) -> dict:
        nonlocal refreshes
        refreshes += 1
        return {"fetched": True, "retracted": condition == "retracted"}

    def unrepaired(*_args: Any, **_options: Any) -> dict:
        return {"retracted": "retained"} if condition == "unrepaired" else {}

    monkeypatch.setattr(vault_claim, "_check", lambda *_: None)
    monkeypatch.setattr(vault_share, "share", busy)
    monkeypatch.setattr(vault_claim.vault_clone, "fetch_with", fetch)
    monkeypatch.setattr(vault_claim.vault_clone, "unrepaired", unrepaired)
    monkeypatch.setattr(
        vault_claim.vault_clone,
        "ended",
        lambda *_: {"payload": {"reason": "finished"}} if condition == "ended" else None,
    )
    shown = vault_claim.claim(_ask(clone, state, "waiter"), deadline=2)

    assert shown["exit"] == (vault_claim.UNSETTLED if condition == "ended" else vault_claim.FIX), (
        shown
    )
    assert refreshes == 1
    assert len(vault_claim.vault_queue.pending(clone.folder / "queue.jsonl")) == 1


def test_a_claim_keeps_publishing_while_another_reader_holds_its_progress_hint(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "held-progress-reader")
    vault_claim.vault_clone.fetch(clone, clock=time.time)
    path = clone.folder / vault_share.PROGRESS_NAME
    path.write_text("a" * 32, encoding="ascii")
    replace = Path.replace

    def sharing_denied(self: Path, target: Path | str) -> Path:
        if Path(target) == path:
            raise PermissionError("the open reader denies Windows file replacement")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", sharing_denied)
    with path.open(encoding="ascii"):
        shown = vault_claim.claim(_ask(clone, state, "publisher"))

    assert shown["exit"] == vault_claim.LANDED, shown
    assert len(path.read_text(encoding="ascii")) == 32
    assert path.read_text(encoding="ascii") != "a" * 32
    assert not vault_claim.vault_queue.pending(clone.folder / "queue.jsonl")
