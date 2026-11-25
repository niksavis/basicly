from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from basicly import worktree, worktree_preservation
from tests import test_worktree_safety

safety_repo = test_worktree_safety.safety_repo


def _fixture_usage(root: Path) -> Path:
    directory = root / ".basicly/usage"
    directory.mkdir(parents=True)
    (directory / ".gitignore").write_bytes(b"*\n")
    (directory / "verify-cache.json").write_text('{"checks": []}', encoding="utf-8")
    (directory / "lane-notes.json").write_text("{}", encoding="utf-8")
    return directory


@pytest.mark.parametrize("change", ["new", "changed"])
def test_cleanup_refuses_generated_files_changed_after_preservation(
    safety_repo: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    session = worktree.create("late-cache", repo_root=safety_repo)
    cache = _fixture_usage(session.path)
    preserve = worktree_preservation.preserve

    def preserve_then_change(main: Path, root: Path, paths: tuple[Path, ...]) -> Path | None:
        manifest = preserve(main, root, paths)
        name = "late.json" if change == "new" else "verify-cache.json"
        (cache / name).write_text('{"late": true}', encoding="utf-8")
        return manifest

    monkeypatch.setattr(worktree_preservation, "preserve", preserve_then_change)
    with pytest.raises(SystemExit, match="changed during"):
        worktree.cleanup(session.name, repo_root=safety_repo)
    assert session.path.exists()
    assert (cache / "verify-cache.json").exists()
    monkeypatch.setattr(worktree_preservation, "preserve", preserve)
    worktree.cleanup(session.name, repo_root=safety_repo)
    assert not session.path.exists()


def test_partial_preservation_failure_leaves_sources_intact_and_cleanup_retryable(
    safety_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = worktree.create("partial-copy", repo_root=safety_repo)
    cache = _fixture_usage(session.path)
    before = {p.relative_to(cache): p.read_bytes() for p in cache.rglob("*") if p.is_file()}
    copy = shutil.copyfile
    calls = 0

    def interrupted_copy(source: Path, target: Path) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fixture interruption")
        return str(copy(source, target))

    monkeypatch.setattr(shutil, "copyfile", interrupted_copy)
    with pytest.raises(OSError, match="fixture interruption"):
        worktree.cleanup(session.name, repo_root=safety_repo)
    assert {p.relative_to(cache): p.read_bytes() for p in cache.rglob("*") if p.is_file()} == before
    monkeypatch.setattr(shutil, "copyfile", copy)
    worktree.cleanup(session.name, repo_root=safety_repo)
    assert not session.path.exists()


def test_cleanup_refuses_to_archive_past_the_growth_limit(
    safety_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = worktree.create("first-archive", repo_root=safety_repo)
    _fixture_usage(first.path)
    worktree.cleanup(first.name, repo_root=safety_repo)
    second = worktree.create("over-limit", repo_root=safety_repo)
    usage = _fixture_usage(second.path)
    monkeypatch.setattr(worktree_preservation, "ARCHIVE_LIMIT_BYTES", 0)
    with pytest.raises(SystemExit, match="over the 0 MB limit"):
        worktree.cleanup(second.name, repo_root=safety_repo)
    assert (usage / "lane-notes.json").exists()
