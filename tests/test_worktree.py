"""Tests for sibling git-worktree isolation (create, provision, cleanup)."""

from __future__ import annotations

import json
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


def test_create_never_rewrites_the_checked_out_tracker(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uncommitted base tracker state must NOT be copied over the worktree's file.

    Fresh ids reach the worktree through the ``redirect`` (br and the beads
    hook both follow it); overwriting the checked-out ``issues.jsonl`` with
    the base working-tree version leaves the worktree permanently dirty and
    blocks the landing rebase (basicly-h61t rework 1).
    """
    beads = git_repo / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"x-1"}\n', encoding="utf-8")
    _git(git_repo, "add", ".beads/issues.jsonl")
    _git(git_repo, "commit", "-m", "track beads")
    (beads / "issues.jsonl").write_text('{"id":"x-1"}\n{"id":"x-2"}\n', encoding="utf-8")

    monkeypatch.chdir(git_repo)
    session = worktree.create("fresh-issue")

    checked_out = session.path / ".beads" / "issues.jsonl"
    assert checked_out.read_text(encoding="utf-8") == '{"id":"x-1"}\n'
    assert (session.path / ".beads" / "redirect").read_text(encoding="utf-8").strip() == str(beads)


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_create_rejects_a_br_that_ignores_the_redirect(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A br that resolves the worktree's own .beads fails provisioning fast.

    Such a br silently runs a divergent tracker (lost gates and claims); the
    probe turns that into an explicit upgrade instruction (basicly-o0ph).
    """
    (git_repo / ".beads").mkdir()
    (git_repo / ".beads" / "issues.jsonl").write_text('{"id":"x-1"}\n', encoding="utf-8")

    def _wrong_dir(worktree_path, _args):
        return _Proc(0, json.dumps({"path": str(Path(worktree_path) / ".beads")}))

    monkeypatch.setattr(worktree, "try_run_br", _wrong_dir)
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="redirect-capable br"):
        worktree.create("old-br")


def test_create_probe_skips_absent_or_failing_br(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No br on PATH, or a br error (non-workspace base), skips the probe."""
    (git_repo / ".beads").mkdir()
    (git_repo / ".beads" / "issues.jsonl").write_text('{"id":"x-1"}\n', encoding="utf-8")
    monkeypatch.chdir(git_repo)

    monkeypatch.setattr(worktree, "try_run_br", lambda *_a: None)
    worktree.create("no-br")

    monkeypatch.setattr(worktree, "try_run_br", lambda *_a: _Proc(1, "not a workspace"))
    worktree.create("br-errors")


def test_create_redirects_beads_to_base(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The worktree's .beads redirects at the base checkout's, sharing one tracker.

    br follows the git-ignored ``redirect`` file, so every tracker read/write
    from the worktree hits the base DB/JSONL — no divergent copy (basicly-c9e5).
    """
    beads = git_repo / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"x-1"}\n', encoding="utf-8")
    _git(git_repo, "add", ".beads/issues.jsonl")
    _git(git_repo, "commit", "-m", "track beads")

    monkeypatch.chdir(git_repo)
    session = worktree.create("shared-tracker")

    redirect = session.path / ".beads" / "redirect"
    assert redirect.read_text(encoding="utf-8").strip() == str(beads)


def test_create_leaves_matching_tracker_untouched(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed, unchanged tracker file is not rewritten (worktree stays clean).

    The fixture tracks ``.beads/.gitignore`` the way ``br init`` writes it, so
    the provisioning-written ``redirect`` file stays invisible to git.
    """
    beads = git_repo / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"x-1"}\n', encoding="utf-8")
    (beads / ".gitignore").write_text("redirect\n", encoding="utf-8")
    _git(git_repo, "add", ".beads/issues.jsonl", ".beads/.gitignore")
    _git(git_repo, "commit", "-m", "track beads")

    monkeypatch.chdir(git_repo)
    session = worktree.create("clean-tracker")

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=session.path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert status == ""


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


def test_is_linked_checkout_distinguishes_worktree_from_base(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linked worktree reports True; the primary checkout and a non-repo False."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("linked")

    assert worktree.is_linked_checkout(session.path) is True
    assert worktree.is_linked_checkout(git_repo) is False
    assert worktree.is_linked_checkout(git_repo.parent) is False  # not a repo


def test_cleanup_drops_record_when_branch_already_gone(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-recovered worktree (dir + branch already removed) leaves no orphan record.

    Regression (basicly-9niw): cleanup kept the session ``.json`` whenever the
    branch delete failed — including when the branch was already gone — and the
    stale record kept counting toward the concurrency cap.
    """
    monkeypatch.chdir(git_repo)
    session = worktree.create("recovered")
    # Simulate the manual recovery: remove the worktree and branch out-of-band.
    shutil.rmtree(session.path)
    _git(git_repo, "worktree", "prune")
    _git(git_repo, "branch", "-D", "harness/recovered")
    assert worktree.load_session("recovered", git_repo) is not None  # orphan present

    worktree.cleanup("recovered", force=True)

    assert worktree.load_session("recovered", git_repo) is None


def test_cleanup_reinstalls_base_checkout_hooks(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create -> teardown reinstalls hooks against the base checkout.

    Worktrees share the common ``.git/hooks`` dir, so provisioning can leave
    shims embedding the worktree venv's pre-commit path; cleanup must re-run
    the hook install from the base checkout so a commit there succeeds
    immediately after teardown (regression: basicly-zrj.13.3).
    """
    reinstalls: list[Path] = []
    monkeypatch.setattr(
        worktree,
        "install_worktree_hooks",
        lambda target: (reinstalls.append(Path(target)), "hooks: recorded")[1],
    )
    (git_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)
    worktree.create("hooked")
    reinstalls.clear()  # drop the provisioning-time install; assert on teardown only

    worktree.cleanup("hooked")

    assert reinstalls == [git_repo]


def test_cleanup_skips_hook_reinstall_without_precommit_config(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo with no pre-commit wiring never gains hooks from a teardown."""
    reinstalls: list[Path] = []
    monkeypatch.setattr(
        worktree,
        "install_worktree_hooks",
        lambda target: (reinstalls.append(Path(target)), "hooks: recorded")[1],
    )
    monkeypatch.chdir(git_repo)
    worktree.create("plain")
    reinstalls.clear()

    worktree.cleanup("plain")

    assert reinstalls == []


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


def test_cleanup_refuses_a_worktree_with_uncommitted_work(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uncommitted changes block cleanup unless forced; force discards them."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("dirty")
    (session.path / "wip.txt").write_text("not committed", encoding="utf-8")

    with pytest.raises(SystemExit, match="uncommitted changes"):
        worktree.cleanup("dirty")
    assert session.path.exists()

    worktree.cleanup("dirty", force=True)
    assert not session.path.exists()


def test_cleanup_ignores_dep_dirs_and_tracker_export(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provisioned dep dirs and the tracker export never count as dirt."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("depsonly")
    (session.path / ".venv").mkdir(exist_ok=True)
    (session.path / ".venv" / "marker.txt").write_text("x", encoding="utf-8")
    beads = session.path / ".beads"
    beads.mkdir(exist_ok=True)
    (beads / "issues.jsonl").write_text("{}\n", encoding="utf-8")

    worktree.cleanup("depsonly")
    assert not session.path.exists()
