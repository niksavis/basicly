from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess  # nosec B404
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


def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(  # nosec B603 B607
        ["git", *arguments], cwd=root, env=env, check=True, capture_output=True, text=True
    ).stdout


def _staged_repository(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "b").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "hook@example.invalid")
    _git(root, "config", "user.name", "hook")
    (root / "b" / "keep.md").write_text("k\n", encoding="utf-8")
    (root / "b" / "gone.md").write_text("g\n", encoding="utf-8")
    (root / "pin").write_text("1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    (root / "pin").write_text("2\n", encoding="utf-8")
    _git(root, "add", "pin")
    return root


def _snapshot(tmp_path: Path):
    module = _load_staged_snapshot()
    root = _staged_repository(tmp_path)
    directory = tmp_path / "hook"
    directory.mkdir()
    state = module.prepare(root, directory)
    return module, root, state, {**os.environ, **module.job_env(state, "pre-commit")}


def test_a_deletion_a_job_stages_reaches_the_index_and_the_checkout(tmp_path: Path) -> None:
    module, root, state, env = _snapshot(tmp_path)
    (state.tree / "b" / "gone.md").unlink()
    (state.tree / "b" / "keep.md").write_text("k2\n", encoding="utf-8")
    _git(state.tree, "add", "-A", "--", "b", env=env)

    module.publish(state)

    staged = _git(root, "diff", "--cached", "--name-status").split("\n")
    assert {"D\tb/gone.md", "M\tb/keep.md", "M\tpin"} <= set(staged)
    assert not (root / "b" / "gone.md").exists()
    assert (root / "b" / "keep.md").read_text(encoding="utf-8") == "k2\n"


def test_a_rename_a_job_stages_leaves_no_old_copy_in_the_checkout(tmp_path: Path) -> None:
    module, root, state, env = _snapshot(tmp_path)
    (state.tree / "b" / "moved").mkdir()
    _git(state.tree, "mv", "b/gone.md", "b/moved/gone.md", env=env)

    module.publish(state)

    assert not (root / "b" / "gone.md").exists()
    assert (root / "b" / "moved" / "gone.md").read_text(encoding="utf-8") == "g\n"
    assert "D\tb/gone.md" in _git(root, "diff", "--cached", "--name-status", "--no-renames")


def test_a_staged_path_missing_from_the_snapshot_is_refused_by_name(tmp_path: Path) -> None:
    module, root, state, env = _snapshot(tmp_path)
    index = module.index_path(root)
    before = index.read_bytes()
    (state.tree / "b" / "keep.md").write_text("k2\n", encoding="utf-8")
    _git(state.tree, "add", "b/keep.md", env=env)
    (state.tree / "b" / "keep.md").unlink()

    with pytest.raises(ValueError, match=r"b/keep\.md"):
        module.publish(state)

    assert index.read_bytes() == before
    assert (root / "b" / "keep.md").read_text(encoding="utf-8") == "k\n"
    assert not index.with_name(index.name + ".lock").exists()
