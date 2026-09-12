from __future__ import annotations

from pathlib import Path

import pytest

from basicly import tracker_paths, tracker_usage


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / tracker_paths.LEDGER_DIR_NAME).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def worktree_of(repo: Path, tmp_path: Path) -> Path:

    worktree = tmp_path / "wt"
    ledger = worktree / tracker_paths.LEDGER_DIR_NAME
    ledger.mkdir(parents=True)
    (ledger / tracker_paths.REDIRECT_NAME).write_text(f"{repo}\n", encoding="utf-8")
    return worktree


def test_the_tracker_root_follows_the_redirect_to_the_base_checkout(
    repo: Path, worktree_of: Path
) -> None:

    assert tracker_paths.tracker_root(worktree_of) == repo
    assert tracker_paths.tracker_root(repo) == repo


def test_the_committed_ledger_directory_is_the_switch(repo: Path, tmp_path: Path) -> None:

    assert tracker_paths.ledger_dir(repo).is_dir()
    assert not tracker_paths.ledger_dir(tmp_path / "elsewhere").is_dir()


def test_classify_access_reports_unknown_as_unclassified() -> None:

    assert tracker_usage.classify_access("list") == "read"
    assert tracker_usage.classify_access("create") == "write"
    assert tracker_usage.classify_access("teleport") == "unclassified"


def test_classify_access_covers_two_word_subcommands() -> None:

    assert tracker_usage.classify_access("comments list") == "read"
    assert tracker_usage.classify_access("comments add") == "write"
    assert tracker_usage.classify_access("gate list") == "read"
    assert tracker_usage.classify_access("gate report") == "write"
    assert tracker_usage.classify_access("dep cycles") == "read"
    assert tracker_usage.classify_access("dep add") == "write"
    assert tracker_usage.classify_access("dep list") == "read"


@pytest.mark.parametrize(
    ("args", "surface", "remainder"),
    [
        (["show", "b-1", "--json"], "show", ["b-1", "--json"]),
        (["comments", "add", "b-1", "text"], "comments add", ["b-1", "text"]),
        (["gate", "list", "b-1"], "gate list", ["b-1"]),
        (["dep", "--help"], "dep", ["--help"]),
        ([], "", []),
        (["--version"], "", ["--version"]),
    ],
)
def test_split_invocation_names_the_surface_and_what_followed_it(
    args: list[str], surface: str, remainder: list[str]
) -> None:
    assert tracker_usage.split_invocation(args) == (surface, remainder)


def test_every_group_subcommand_has_at_least_one_classified_surface() -> None:

    classified = tracker_usage.READ_SUBCOMMANDS | tracker_usage.WRITE_SUBCOMMANDS
    for group in tracker_usage.GROUP_SUBCOMMANDS:
        assert any(surface.startswith(f"{group} ") for surface in classified), group


def test_the_two_classes_are_disjoint() -> None:
    assert not (tracker_usage.READ_SUBCOMMANDS & tracker_usage.WRITE_SUBCOMMANDS)
