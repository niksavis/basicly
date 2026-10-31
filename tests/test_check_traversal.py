from __future__ import annotations

import importlib.util
import os
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize("kind", ["comments", "wiring"])
def test_excluded_directories_are_never_entered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root / ".basicly/core/kit/comments/cli.py"
        if kind == "comments"
        else root / ".scripts/wired_or_deleted.py"
    )
    spec = importlib.util.spec_from_file_location(f"traversal_{kind}", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    visible = tmp_path / "src" / "app.py"
    visible.parent.mkdir()
    visible.write_text("VALUE = 1\n", encoding="utf-8")
    hidden = tmp_path / "node_modules" / "pkg"
    hidden.mkdir(parents=True)
    (hidden / "hidden.py").write_text("VALUE = 2\n", encoding="utf-8")
    scan = os.scandir

    def admit(path: Any) -> Any:
        assert "node_modules" not in Path(path).parts
        return scan(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", admit)
        found = (
            module.covered_files([tmp_path], module.SKIPPED_DIRECTORIES)
            if kind == "comments"
            else list(module._iter_files(tmp_path, "**/*.py"))
        )
    assert found == [visible]


def test_comments_scanning_works_without_path_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).resolve().parents[1] / ".basicly/core/kit/comments/cli.py"
    cli = runpy.run_path(str(source))
    visible = tmp_path / "app.py"
    visible.write_text("VALUE = 1\n", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.delattr(Path, "walk")
        found = cli["covered_files"]([tmp_path], cli["SKIPPED_DIRECTORIES"])
    assert found == [visible]
