from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_claim import vault_claim
from tests.test_kit_vault_share import _ids, _machine, _write, bare

if TYPE_CHECKING:
    import pytest

vault_share = vault_claim.vault_share
__all__ = ["bare"]


def test_an_urgent_publisher_includes_arrivals_after_its_first_stable_snapshot(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "settle-arrivals")
    initial = _write(clone, state, 1, "initial")
    late: list[str] = []
    built = False
    pushes = 0
    prune = vault_share.prune

    def arriving(held: Any) -> list:
        snapshot = prune(held)
        if built and not late:
            late.extend(_write(clone, state, 1, "late"))
        return snapshot

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal built, pushes
        shown = vault_share.git_runner.run(args, cwd, **options)
        if "commit-tree" in args:
            built = True
        if "push" in args:
            pushes += 1
        return shown

    monkeypatch.setattr(vault_share, "prune", arriving)
    shown = vault_share.share(
        clone,
        state,
        plan=vault_share.Plan(lambda: 1000.0, urgent=True, keep_going=False),
        runner=runner,
    )

    assert late
    assert shown["pending"] == 0
    assert pushes == 1
    assert _ids(bare) == dict.fromkeys([*initial, *late], 1)
