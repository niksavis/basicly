from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_clone import _batch, _clone, _fetch, origin, vault_clone

if TYPE_CHECKING:
    import pytest

    from tests.test_kit_vault_shapes import Vault

__all__ = ["origin"]


def test_an_unchanged_validated_head_preserves_the_cached_files(
    tmp_path: Path, origin: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, origin)
    _batch(origin)
    _fetch(clone)
    writes: list[str] = []
    original = vault_clone.durable

    def durable(path: Path, text: str, **options: Any) -> None:
        writes.append(path.name)
        original(path, text, **options)

    monkeypatch.setattr(vault_clone, "durable", durable)
    shown = _fetch(clone)

    assert shown["fetched"] is True
    assert shown["moved"] is False
    assert len(vault_clone.valid_events(clone)) == 1
    assert writes == [vault_clone.FETCH_GENERATION_NAME]


def test_an_unchanged_head_with_a_missing_cover_rebuilds_the_cached_events(
    tmp_path: Path, origin: Vault
) -> None:
    clone = _clone(tmp_path, origin)
    _batch(origin)
    _fetch(clone)
    (clone.folder / vault_clone.COVERS_NAME).unlink()
    (clone.folder / vault_clone.EVENTS_NAME).write_text("", encoding="utf-8")

    shown = _fetch(clone)

    assert shown["fetched"] is True
    assert len(vault_clone.valid_events(clone)) == 1
    assert (clone.folder / vault_clone.COVERS_NAME).read_text().strip() == shown["head"]
