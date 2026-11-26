from __future__ import annotations

from pathlib import Path

import pytest

from basicly import remote_branches, worktree
from tests.remote_branches_fixture import _git, _lane, _on_origin, seeded_main


@pytest.fixture
def main(tmp_path: Path) -> Path:
    return seeded_main(tmp_path)


def test_a_replaced_lane_whose_work_landed_by_copy_has_its_remote_branch_deleted(
    main: Path,
) -> None:
    old = _lane(main, "harness/old", "d.txt")
    (main / "later.txt").write_text("later\n", encoding="utf-8")
    _git(main, "add", "later.txt")
    _git(main, "commit", "-q", "-m", "later work on main")
    _git(main, "cherry-pick", old)
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "harness/old", "main", worktree.unlanded_paths)

    assert note.startswith("deleted origin/harness/old")
    assert not _on_origin(main, "harness/old")


def test_a_branch_whose_head_is_on_the_base_is_deleted_after_the_base_changed_its_path(
    main: Path,
) -> None:
    _lane(main, "pr/lane/old", "e.txt")
    _git(main, "merge", "-q", "--no-ff", "-m", "land", "pr/lane/old")
    (main / "e.txt").write_text("changed on main\n", encoding="utf-8")
    _git(main, "commit", "-q", "-am", "main changes the path again")
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "pr/lane/old", "main", worktree.unlanded_paths)

    assert note.startswith("deleted origin/pr/lane/old")
    assert not _on_origin(main, "pr/lane/old")


def test_a_rebased_branch_whose_patch_is_on_the_base_is_deleted_after_the_base_changed_its_path(
    main: Path,
) -> None:
    old = _lane(main, "pr/lane/rebased", "f.txt")
    (main / "other.txt").write_text("other work on main\n", encoding="utf-8")
    _git(main, "add", "other.txt")
    _git(main, "commit", "-q", "-m", "other work on main")
    _git(main, "cherry-pick", old)
    (main / "f.txt").write_text("changed again on main\n", encoding="utf-8")
    _git(main, "commit", "-q", "-am", "main changes the path again")
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "pr/lane/rebased", "main", worktree.unlanded_paths)

    assert note.startswith("deleted origin/pr/lane/rebased")
    assert not _on_origin(main, "pr/lane/rebased")


def test_a_branch_with_one_patch_not_on_the_base_is_kept(main: Path) -> None:
    first = _lane(main, "pr/lane/partial", "g.txt")
    _git(main, "switch", "-q", "pr/lane/partial")
    (main / "h.txt").write_text("not landed\n", encoding="utf-8")
    _git(main, "add", "h.txt")
    _git(main, "commit", "-q", "-m", "work that never landed")
    _git(main, "push", "-q", "origin", "pr/lane/partial")
    _git(main, "switch", "-q", "main")
    (main / "other.txt").write_text("other work on main\n", encoding="utf-8")
    _git(main, "add", "other.txt")
    _git(main, "commit", "-q", "-m", "other work on main")
    _git(main, "cherry-pick", first)
    _git(main, "push", "-q", "origin", "main")

    note = remote_branches.delete_landed(main, "pr/lane/partial", "main", worktree.unlanded_paths)

    assert note.startswith("kept origin/pr/lane/partial")
    assert "h.txt" in note
