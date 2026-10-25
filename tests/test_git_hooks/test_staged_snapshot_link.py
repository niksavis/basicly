from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest


def _load_staged_snapshot():
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "staged-snapshot.py"
    )
    spec = importlib.util.spec_from_file_location("staged_snapshot_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _environment(tmp_path: Path) -> Path:
    source = tmp_path / "repo" / ".venv"
    source.mkdir(parents=True)
    (source / "pyvenv.cfg").write_text("home = here\n", encoding="utf-8")
    return source


def test_link_directory_shares_the_source_instead_of_copying_it(tmp_path: Path) -> None:
    source = _environment(tmp_path)
    destination = tmp_path / "snapshot" / ".venv"
    destination.parent.mkdir()

    _load_staged_snapshot().link_directory(source, destination)
    (source / "written-after-link.txt").write_text("late\n", encoding="utf-8")

    assert (destination / "written-after-link.txt").read_text(encoding="utf-8") == "late\n"


def test_removing_the_snapshot_leaves_the_linked_source_intact(tmp_path: Path) -> None:
    source = _environment(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    _load_staged_snapshot().link_directory(source, snapshot / ".venv")
    shutil.rmtree(snapshot)

    assert (source / "pyvenv.cfg").read_text(encoding="utf-8") == "home = here\n"


def test_link_failure_refuses_copying_and_preserves_the_source(tmp_path: Path, monkeypatch) -> None:
    source = _environment(tmp_path)
    destination = tmp_path / "snapshot" / ".venv"
    destination.parent.mkdir()
    module = _load_staged_snapshot()

    def denied(*_args, **_kwargs):
        raise OSError("symlinks unavailable")

    monkeypatch.setattr(Path, "symlink_to", denied)
    if sys.platform == "win32":
        monkeypatch.setattr(module._winapi, "CreateJunction", denied)
    with pytest.raises(ValueError, match="cannot share a snapshot directory"):
        module.link_directory(source, destination)
    assert not destination.exists()
    assert (source / "pyvenv.cfg").read_text(encoding="utf-8") == "home = here\n"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions require native Windows")
def test_symlink_denied_uses_a_real_junction_and_cleanup_preserves_user_data(
    tmp_path: Path, monkeypatch
) -> None:
    source = _environment(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def denied(*_args, **_kwargs):
        raise OSError("symlinks unavailable")

    monkeypatch.setattr(Path, "symlink_to", denied)
    _load_staged_snapshot().link_directory(source, snapshot / ".venv")
    assert (snapshot / ".venv").is_junction()
    (source / "late.txt").write_text("keep", encoding="utf-8")
    assert (snapshot / ".venv/late.txt").read_text(encoding="utf-8") == "keep"
    shutil.rmtree(snapshot)
    assert (source / "late.txt").read_text(encoding="utf-8") == "keep"
