"""Tests for sibling git-worktree isolation (create, provision, cleanup)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import cli, worktree

# Bound at import to the real function, so it stays reachable past the autouse
# stub below (which rebinds ``worktree.provision_deps`` for the other tests).
real_provision_deps = worktree.provision_deps


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


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_cleanup_removes_worktree_branch_and_metadata(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup removes the dir, deletes the harness branch, and drops the record."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("gone")
    assert session.path.is_dir()

    worktree.cleanup("gone")

    assert not session.path.exists()
    assert "harness/gone" not in _branches(git_repo)
    assert "main" in _branches(git_repo)  # base untouched
    assert worktree.load_session("gone", git_repo) is None


def test_cleanup_reclaims_stale_session(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A record whose worktree dir vanished out-of-band is still reclaimable."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("stale")
    # Remove the worktree behind git's back, leaving a dangling session record.
    shutil.rmtree(session.path)

    assert [s.name for s in worktree.stale_sessions(git_repo)] == ["stale"]
    worktree.cleanup("stale")
    assert worktree.load_session("stale", git_repo) is None
    assert "harness/stale" not in _branches(git_repo)


def test_cleanup_keeps_unmerged_branch_without_force(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unmerged branch survives a plain cleanup and is removed with force."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("wip")
    (session.path / "extra.txt").write_text("work\n", encoding="utf-8")
    for args in (["add", "extra.txt"], ["commit", "-m", "feat: wip (basicly-x)"]):
        subprocess.run(["git", *args], cwd=session.path, capture_output=True, text=True, check=True)

    worktree.cleanup("wip")  # unmerged: dir gone, branch kept
    assert not session.path.exists()
    assert "harness/wip" in _branches(git_repo)

    worktree.cleanup("wip", force=True)  # reclaim: branch gone
    assert "harness/wip" not in _branches(git_repo)


def test_provision_deps_selects_commands_by_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """provision_deps runs uv sync / npm install only for manifests present."""
    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run", lambda args, **_kw: calls.append(args))

    # Both ecosystems present -> both commands, in order.
    both = tmp_path / "both"
    both.mkdir()
    (both / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (both / "package.json").write_text("{}\n", encoding="utf-8")
    notes = real_provision_deps(both)
    assert calls == [["uv", "sync"], ["npm", "install"]]
    assert notes == [".venv: uv sync", "node_modules: npm install"]

    # Neither manifest -> no commands, no notes.
    calls.clear()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert real_provision_deps(empty) == []
    assert calls == []

    # uv.lock alone still triggers uv sync (no pyproject needed).
    calls.clear()
    lock_only = tmp_path / "lock"
    lock_only.mkdir()
    (lock_only / "uv.lock").write_text("", encoding="utf-8")
    assert real_provision_deps(lock_only) == [".venv: uv sync"]
    assert calls == [["uv", "sync"]]


def test_create_and_cleanup_leave_base_head_untouched(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full create->cleanup cycle never moves the base branch's HEAD."""
    monkeypatch.chdir(git_repo)
    before = subprocess.run(
        ["git", "rev-parse", "main"], cwd=git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    worktree.create("cycle")
    worktree.cleanup("cycle")

    after = subprocess.run(
        ["git", "rev-parse", "main"], cwd=git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert after == before


def test_cleanup_rejects_unknown_name(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup of a name with no record and no worktree is rejected."""
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="no worktree named"):
        worktree.cleanup("nope")


def test_cli_worktree_create_list_cleanup(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The worktree subcommands create, list, and clean up a session."""
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "cli-a"]) == 0
    assert (git_repo.parent / "repo.worktrees" / "cli-a").is_dir()
    assert cli.main(["worktree", "list"]) == 0

    assert cli.main(["worktree", "cleanup", "cli-a"]) == 0
    assert worktree.load_session("cli-a", git_repo) is None


def test_cli_worktree_enforces_concurrency_cap(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating past [worktree].concurrency is refused with a non-zero exit."""
    (git_repo / "basicly.toml").write_text("[worktree]\nconcurrency = 1\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "first"]) == 0
    assert cli.main(["worktree", "create", "second"]) == 1
    assert worktree.load_session("second", git_repo) is None


def test_cli_worktree_uses_configured_base_branch(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A create with no --base forks from [worktree].base_branch."""
    subprocess.run(
        ["git", "branch", "develop"], cwd=git_repo, capture_output=True, text=True, check=True
    )
    (git_repo / "basicly.toml").write_text(
        '[worktree]\nbase_branch = "develop"\n', encoding="utf-8"
    )
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "on-develop"]) == 0
    session = worktree.load_session("on-develop", git_repo)
    assert session is not None
    assert session.base == "develop"
