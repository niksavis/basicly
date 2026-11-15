from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import ledger_guard


@pytest.mark.parametrize("python_resolver", [False, True])
@pytest.mark.parametrize("access", ["direct", "relative", "alias", "renamed", "missing"])
def test_guard_refuses_live_reads_through_current_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    python_resolver: bool,
    access: str,
) -> None:
    if python_resolver:
        monkeypatch.setattr(ledger_guard, "_RESOLVE", os.path.realpath)
    root = tmp_path / "ledger"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = tmp_path / "allowed"
    allowed.write_text("allowed")
    (root / "events-0001.jsonl").write_text("protected")
    target = root / "events-0001.jsonl"
    if access == "relative":
        monkeypatch.chdir(tmp_path)
        target = Path("ledger") / target.name
    elif access in {"alias", "missing"}:
        alias = tmp_path / "alias"
        directory_link(alias, root)
        target = alias / ("missing.jsonl" if access == "missing" else target.name)
    elif access == "renamed":
        target = outside / "events-0001.jsonl"
        target.write_text("protected")
        assert target.read_text() == "protected"
    with ledger_guard.independent_of(root, "path-control"):
        if access == "renamed":
            outside.rename(root / "moved")
            directory_link(outside, root / "moved")
        assert allowed.read_text() == "allowed"
        with pytest.raises(pytest.fail.Exception, match="path-control reads the live ledger"):
            target.read_text()


def directory_link(alias: Path, target: Path) -> None:
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
