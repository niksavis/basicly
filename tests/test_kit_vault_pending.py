from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_view import WANT, _carry, _load, _machine, _push, _record, bare

vault_pending = _load("basicly_tracker_kit_vault_pending", "vault_pending.py")
__all__ = ["bare"]

if TYPE_CHECKING:
    import pytest


def _counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = vault_pending.projection

    def counting(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(vault_pending, "projection", counting)
    return calls


def _key(folder: Path) -> str:
    return (folder / vault_pending.KEY_NAME).read_text(encoding="utf-8")


def test_a_warm_pending_view_skips_the_projection_and_any_file_change_rebuilds_it(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _push(tmp_path, bare, "m1", _carry(_record("acme-a1")))
    reader, _state = _machine(tmp_path, bare, "reader")
    folder = vault_pending.present(reader, WANT, clock=time.time)
    first = _key(folder)
    calls = _counted(monkeypatch)

    vault_pending.present(reader, WANT, clock=time.time)
    assert calls == []

    valid = reader.folder / vault_pending.vault_view.vault_clone.EVENTS_NAME
    covers = (reader.folder / vault_pending.vault_view.vault_clone.COVERS_NAME).read_bytes()
    valid.write_text(valid.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    vault_pending.present(reader, WANT, clock=time.time)
    assert (reader.folder / vault_pending.vault_view.vault_clone.COVERS_NAME).read_bytes() == covers
    assert _key(folder) != first and calls == [1]

    queue = reader.folder / vault_pending.vault_queue.QUEUE_NAME
    second = _key(folder)
    with queue.open("a", encoding="utf-8") as stream:
        stream.write("{}\n")
    vault_pending.present(reader, WANT, clock=time.time)
    assert _key(folder) != second
