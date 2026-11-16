from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_clone import _batch, _clone, _fetch, origin, vault_clone
from tests.test_kit_vault_shapes import WRITER_B

if TYPE_CHECKING:
    import pytest

    from tests.test_kit_vault_shapes import Vault

__all__ = ["origin"]


def test_fetch_reads_commit_ancestry_once_for_the_validated_chain(
    tmp_path: Path, origin: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    _batch(origin)
    clone = _clone(tmp_path, origin)
    runner = vault_clone.git_runner.run
    ancestry: list[list[str]] = []

    def observed(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "rev-list":
            ancestry.append(args)
        return runner(args, cwd, **options)

    monkeypatch.setattr(vault_clone.git_runner, "run", observed)
    shown = _fetch(clone, runner=observed)

    assert shown["moved"] is True
    assert len(vault_clone.valid_events(clone)) == 1
    assert len(ancestry) == 1


def test_the_combined_ancestry_read_still_quarantines_a_merge(
    tmp_path: Path, origin: Vault
) -> None:
    origin.git("checkout", "-q", "-b", "peer")
    _batch(origin, WRITER_B, "peer")
    origin.git("checkout", "-q", "main")
    _batch(origin, record="main")
    origin.git("merge", "-q", "--no-ff", "peer", "-m", "merge")
    merged = origin.git("rev-parse", "HEAD")
    clone = _clone(tmp_path, origin)

    shown = _fetch(clone)

    assert shown["seen"] == merged
    assert [row["commit"] for row in shown["quarantined"]] == [merged]
    assert {row["record"] for row in vault_clone.valid_events(clone)} == {"main"}
