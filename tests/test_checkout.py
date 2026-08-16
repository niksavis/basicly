"""Tests for where a checkout stands in git's worktree layout.

Driven against a real git repo rather than a stubbed ``git``: every answer here
is a reading of ``rev-parse``/``worktree list`` output, so a fake would be
asserting this module's idea of git rather than git's.
"""

from __future__ import annotations

import os
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


def _identity(repo: Path) -> str:
    return (repo / ".git" / "config").read_text(encoding="utf-8")


def test_a_poisoned_git_dir_really_does_outrank_cwd(git_repo: Path, tmp_path: Path) -> None:
    """Positive control for the two below: without it they cannot tell a fix from a no-op.

    A raw ``subprocess`` git carrying ``GIT_DIR`` writes into the repository the variable
    names and ignores the ``cwd`` it was handed, which is the whole incident in one call.
    """
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    before = _identity(git_repo)

    subprocess.run(
        ["git", "config", "user.name", "leaked"],
        cwd=other,
        env={**os.environ, "GIT_DIR": str(git_repo / ".git")},
        check=True,
        capture_output=True,
    )

    assert "leaked" in _identity(git_repo)
    assert _identity(git_repo) != before


def test_git_writes_to_cwd_when_the_inherited_environment_names_another_repo(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every engine git call goes through :func:`checkout.run`, so the scrub goes there.

    The harness runs from git hooks, and a hook in a linked worktree is handed a
    ``GIT_DIR`` (basicly-e2mz.16) — under which ``cwd=`` decides nothing at all.
    """
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    before = _identity(git_repo)
    monkeypatch.setenv("GIT_DIR", str(git_repo / ".git"))

    checkout.git(["config", "user.name", "from-cwd"], cwd=other)

    assert "from-cwd" in _identity(other)
    assert _identity(git_repo) == before


def test_an_explicit_env_is_scrubbed_too(git_repo: Path, tmp_path: Path) -> None:
    """The *env=* branch is scrubbed as well, and it is not a hypothetical one.

    ``release`` builds its env as ``dict(os.environ)`` plus a PYTHONPATH, so an ambient
    ``GIT_DIR`` reaches the child by that path even with the inherited branch covered.
    """
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    before = _identity(git_repo)

    checkout.run(
        ["git", "config", "user.name", "from-cwd"],
        cwd=other,
        env={**os.environ, "GIT_DIR": str(git_repo / ".git")},
    )

    assert "from-cwd" in _identity(other)
    assert _identity(git_repo) == before


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
