"""The tracker write vocabulary, and where the ledger lives (basicly-vkh0.8, .2).

The spool's own tests left with the spool (basicly-vkh0.42.7): recording, promotion, the
surface summary and the read/write ratio all measured a subprocess, and there are no
subprocesses left. What stays is the vocabulary those measurements were built on, which
is now the read-only guard's classifier, and the one rule about where the ledger is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import tracker_paths, tracker_usage


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout that has opted in by committing the ledger directory."""
    (tmp_path / tracker_paths.LEDGER_DIR_NAME).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def worktree_of(repo: Path, tmp_path: Path) -> Path:
    """A loop worktree sharing *repo*'s tracker through the ledger ``redirect``.

    Both halves of the real shape matter: the redirect file naming the base checkout, and
    the worktree's *own* checked-out ledger directory — the latter is what made a reader
    believe the worktree owned a ledger.
    """
    worktree = tmp_path / "wt"
    ledger = worktree / tracker_paths.LEDGER_DIR_NAME
    ledger.mkdir(parents=True)
    (ledger / tracker_paths.REDIRECT_NAME).write_text(f"{repo}\n", encoding="utf-8")
    return worktree


# --- One ledger per repo, never one per worktree (basicly-vkh0.8) --------------


def test_the_tracker_root_follows_the_redirect_to_the_base_checkout(
    repo: Path, worktree_of: Path
) -> None:
    """One authority for the ledger's location, shared with every other caller.

    Teardown deletes the worktree, so a store inside it is a discarded write: every
    engine tracker call made from a lane was lost that way.
    """
    assert tracker_paths.tracker_root(worktree_of) == repo
    assert tracker_paths.tracker_root(repo) == repo


# --- Opt-in --------------------------------------------------------------------


def test_the_committed_ledger_directory_is_the_switch(repo: Path, tmp_path: Path) -> None:
    """No config key: the directory a repo commits is where its tracker is.

    A repo that has not opted in resolves to its own absent directory rather than to
    somebody else's — an unconditional write created ``.basicly/`` in any consumer repo
    as a side effect of a tracker call and left it behind after an uninstall.
    """
    assert tracker_paths.ledger_dir(repo).is_dir()
    assert not tracker_paths.ledger_dir(tmp_path / "elsewhere").is_dir()


# --- Classifying a surface's access --------------------------------------------


def test_classify_access_reports_unknown_as_unclassified() -> None:
    """Fail-closed, which is what the read-only guard rests on.

    "Not known to be a read" is the only safe test a guard against unrecoverable writes
    can make: a refusal is loud and fixed by classifying the surface, while a leaked
    write is silent and, in an append-only log, permanent.
    """
    assert tracker_usage.classify_access("list") == "read"
    assert tracker_usage.classify_access("create") == "write"
    assert tracker_usage.classify_access("teleport") == "unclassified"


def test_classify_access_covers_two_word_subcommands() -> None:
    """``split_invocation`` joins the pair, so a single-word entry can never match it.

    The two halves of one ``dep``/``comments``/``gate`` pair also differ in access class,
    which is the reason the pair is one surface.
    """
    assert tracker_usage.classify_access("comments list") == "read"
    assert tracker_usage.classify_access("comments add") == "write"
    assert tracker_usage.classify_access("gate list") == "read"
    assert tracker_usage.classify_access("gate report") == "write"
    assert tracker_usage.classify_access("dep cycles") == "read"
    assert tracker_usage.classify_access("dep add") == "write"
    # `dep list` was the one `dep` read the set forgot while listing its two siblings,
    # and five measured reads sat in `unclassified` because of it (basicly-vkh0.2).
    assert tracker_usage.classify_access("dep list") == "read"


@pytest.mark.parametrize(
    ("args", "surface", "remainder"),
    [
        (["show", "b-1", "--json"], "show", ["b-1", "--json"]),
        (["comments", "add", "b-1", "text"], "comments add", ["b-1", "text"]),
        (["gate", "list", "b-1"], "gate list", ["b-1"]),
        # A group whose next token is a flag is one word: `dep --help` is not a surface
        # called `dep --help`.
        (["dep", "--help"], "dep", ["--help"]),
        ([], "", []),
        (["--version"], "", ["--version"]),
    ],
)
def test_split_invocation_names_the_surface_and_what_followed_it(
    args: list[str], surface: str, remainder: list[str]
) -> None:
    """Two words when the first is a group, one otherwise, empty when it opens with a flag."""
    assert tracker_usage.split_invocation(args) == (surface, remainder)


def test_every_group_subcommand_has_at_least_one_classified_surface() -> None:
    """A group nothing classifies is a group the read-only guard refuses wholesale.

    Not decoration: the guard treats an unclassified surface as a write, so a group that
    fell out of both sets would silently refuse every read under it.
    """
    classified = tracker_usage.READ_SUBCOMMANDS | tracker_usage.WRITE_SUBCOMMANDS
    for group in tracker_usage.GROUP_SUBCOMMANDS:
        assert any(surface.startswith(f"{group} ") for surface in classified), group


def test_the_two_classes_are_disjoint() -> None:
    """One surface, one access class: a member of both makes the guard's answer arbitrary."""
    assert not (tracker_usage.READ_SUBCOMMANDS & tracker_usage.WRITE_SUBCOMMANDS)
