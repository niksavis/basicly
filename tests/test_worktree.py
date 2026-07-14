"""Tests for sibling git-worktree isolation (create + provision)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basicly import worktree


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


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


@pytest.fixture(autouse=True)
def _stub_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the slow, network-bound dep install and hook activation by default.

    ``create``'s git/session/path logic is deterministic and cheap to test; the
    real ``uv sync`` / ``npm install`` / hook install are exercised by dogfood.
    """
    monkeypatch.setattr(worktree, "provision_deps", lambda _wt: ["deps: stubbed"])
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")


def test_main_checkout_and_worktrees_root(git_repo: Path) -> None:
    """The sibling worktrees root is ``<repo>.worktrees`` next to the checkout."""
    assert worktree.main_checkout(git_repo) == git_repo
    assert worktree.worktrees_root(git_repo).name == "repo.worktrees"
    assert worktree.worktrees_root(git_repo).parent == git_repo.parent


def test_create_makes_sibling_worktree_on_harness_branch(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create`` adds a sibling worktree checked out on ``harness/<name>``."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("feature-x")

    expected = git_repo.parent / "repo.worktrees" / "feature-x"
    assert session.path == expected
    assert expected.is_dir()
    assert session.branch == "harness/feature-x"
    assert session.base == "main"

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=expected,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch == "harness/feature-x"


def test_create_persists_loadable_session(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A created worktree writes a session record that round-trips on load."""
    monkeypatch.chdir(git_repo)
    worktree.create("feat")

    loaded = worktree.load_session("feat", git_repo)
    assert loaded is not None
    assert loaded.name == "feat"
    assert loaded.branch == "harness/feat"
    assert [s.name for s in worktree.list_sessions(git_repo)] == ["feat"]


def test_create_copies_env_local_when_present(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present ``.env.local`` is copied into the new worktree."""
    (git_repo / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)
    session = worktree.create("withenv")

    copied = session.path / ".env.local"
    assert copied.read_text(encoding="utf-8") == "SECRET=1\n"


def test_create_rejects_duplicate_name(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Creating a second worktree with a taken name is rejected."""
    monkeypatch.chdir(git_repo)
    worktree.create("dup")
    with pytest.raises(SystemExit, match="already exists"):
        worktree.create("dup")
