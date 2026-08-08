"""Tests for where a checkout stands in git's worktree layout.

Driven against a real git repo rather than a stubbed ``git``: every answer here
is a reading of ``rev-parse``/``worktree list`` output, so a fake would be
asserting this module's idea of git rather than git's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basicly import checkout


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real git repo (named ``repo``) with one commit on ``main``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def test_main_checkout_and_worktrees_root(git_repo: Path) -> None:
    """The sibling worktrees root is ``<repo>.worktrees`` next to the checkout."""
    assert checkout.main_checkout(git_repo) == git_repo
    assert checkout.worktrees_root(git_repo).name == "repo.worktrees"
    assert checkout.worktrees_root(git_repo).parent == git_repo.parent


def test_is_linked_checkout_distinguishes_worktree_from_base(git_repo: Path) -> None:
    """A linked worktree reports True; the primary checkout and a non-repo False.

    The linked tree is added with plain ``git worktree add`` rather than through
    ``worktree.create``: the distinction under test is git's own, so nothing the
    harness does to provision a tree should be able to affect the answer.
    """
    linked = git_repo.parent / "linked"
    _git(git_repo, "worktree", "add", str(linked), "-b", "harness/linked")

    assert checkout.is_linked_checkout(linked) is True
    assert checkout.is_linked_checkout(git_repo) is False
    assert checkout.is_linked_checkout(git_repo.parent) is False  # not a repo
