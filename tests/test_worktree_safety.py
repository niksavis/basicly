from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from basicly import usage, verify, verify_artifact, worktree
from tests.test_worktree import _git, _init_repo


@pytest.fixture
def safety_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.setattr(worktree, "provision_deps", lambda *_a, **_kw: [])
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    return repo


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../escape",
        "nested/name",
        "nested\\name",
        "C:drive",
        "CON",
        "con.txt",
        "COM1",
        "LPT9",
        "trailing.",
        "two..dots",
        "bad.lock",
        "with space",
        "-option",
        "x" * 251,
    ],
)
@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
def test_invalid_portable_names_refuse_before_any_git_or_filesystem_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    path_type: type[PurePosixPath] | type[PureWindowsPath],
) -> None:
    def unexpected_git(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("invalid name reached git")

    monkeypatch.setattr(worktree, "git", unexpected_git)
    monkeypatch.setattr(worktree.checkout, "git", unexpected_git)
    with pytest.raises(SystemExit, match="invalid worktree name"):
        worktree.create(name, repo_root=str(path_type(tmp_path / "absent")))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stage", ["dependencies", "hooks"])
def test_failed_provisioning_retains_a_session_and_a_safe_cleanup_path(
    safety_repo: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("provisioning refused")

    target = "provision_deps" if stage == "dependencies" else "install_worktree_hooks"
    monkeypatch.setattr(worktree, target, refuse)
    with pytest.raises(RuntimeError, match="cleanup failed-lane"):
        worktree.create("failed-lane", repo_root=safety_repo)
    session = worktree.load_session("failed-lane", safety_repo)
    assert session is not None
    assert session.path.exists()
    assert session.path in worktree.registered_worktrees(safety_repo)
    worktree.cleanup("failed-lane", repo_root=safety_repo)
    assert not session.path.exists()
    assert worktree.load_session("failed-lane", safety_repo) is None


def test_failed_hook_installation_refuses_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(worktree, "load_hook_specs", lambda: [])
    monkeypatch.setattr(worktree, "hook_stages", lambda _specs: ["pre-commit"])
    monkeypatch.setattr(worktree, "install_hooks", lambda *_args: (False, "missing installer"))
    with pytest.raises(RuntimeError, match="missing installer"):
        worktree.install_worktree_hooks(tmp_path)


@pytest.mark.parametrize("force", [False, True])
def test_cleanup_preserves_a_locked_worktree(safety_repo: Path, force: bool) -> None:
    session = worktree.create("active", repo_root=safety_repo)
    _git(safety_repo, "worktree", "lock", "--reason", "active agent", str(session.path))
    with pytest.raises(SystemExit, match="locked"):
        worktree.cleanup("active", force=force, repo_root=safety_repo)
    assert session.path.exists()
    assert worktree.load_session("active", safety_repo) is not None
    _git(safety_repo, "worktree", "unlock", str(session.path))
    worktree.cleanup("active", repo_root=safety_repo)


def test_cleanup_preserves_unknown_ignored_files(safety_repo: Path) -> None:
    (safety_repo / ".gitignore").write_text("private.txt\n.venv/\n", encoding="utf-8")
    _git(safety_repo, "add", ".gitignore")
    _git(safety_repo, "commit", "-m", "chore: ignores")
    session = worktree.create("ignored-work", repo_root=safety_repo)
    artifact = session.path / "private.txt"
    artifact.write_text("retained work\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"private\.txt"):
        worktree.cleanup("ignored-work", repo_root=safety_repo)
    assert artifact.read_text(encoding="utf-8") == "retained work\n"
    worktree.cleanup("ignored-work", force=True, repo_root=safety_repo)


def test_cleanup_preserves_tracked_changes_under_dependency_paths(safety_repo: Path) -> None:
    (safety_repo / "node_modules").mkdir()
    (safety_repo / "node_modules" / "owned.txt").write_text("tracked\n", encoding="utf-8")
    _git(safety_repo, "add", "node_modules")
    _git(safety_repo, "commit", "-m", "chore: tracked dependency fixture")
    session = worktree.create("tracked-dependency", repo_root=safety_repo)
    artifact = session.path / "node_modules" / "owned.txt"
    artifact.write_text("user change\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"owned\.txt"):
        worktree.cleanup("tracked-dependency", repo_root=safety_repo)
    assert artifact.exists()


@pytest.mark.parametrize("force", [False, True])
def test_cleanup_refuses_the_primary_checkout(safety_repo: Path, force: bool) -> None:
    with pytest.raises(SystemExit, match="primary checkout"):
        worktree.cleanup(safety_repo.name, force=force, repo_root=safety_repo)
    assert (safety_repo / "README.md").is_file()


def test_cleanup_refuses_a_session_redirected_to_an_unregistered_directory(
    safety_repo: Path,
) -> None:
    session = worktree.create("tampered", repo_root=safety_repo)
    innocent = safety_repo.parent / "innocent"
    innocent.mkdir()
    session.worktree_path = str(innocent)
    worktree.save_session(session, safety_repo)
    with pytest.raises(SystemExit, match="registered"):
        worktree.cleanup("tampered", force=True, repo_root=safety_repo)
    assert innocent.is_dir()


def test_cleanup_preserves_a_tracked_rename_into_a_dependency_path(safety_repo: Path) -> None:
    session = worktree.create("rename", repo_root=safety_repo)
    (session.path / "node_modules").mkdir()
    _git(session.path, "mv", "README.md", "node_modules/README.md")
    with pytest.raises(SystemExit, match=r"README\.md"):
        worktree.cleanup("rename", repo_root=safety_repo)
    assert session.path.exists()


@pytest.mark.parametrize("name", ["a", "lane-1", "lane.part_2", "x" * 250])
def test_portable_name_controls_are_accepted(name: str) -> None:
    worktree.validate_worktree_name(name)


def test_creation_never_reads_the_dotenv_fixture(
    safety_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = safety_repo / ".env.local"
    dotenv.write_text("SYNTHETIC_TEST_VALUE=1\n", encoding="utf-8")
    read_text = Path.read_text

    def guarded_read(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if path == dotenv:
            pytest.fail("creation tried to read a dotenv file")
        return read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    session = worktree.create("no-secret", repo_root=safety_repo)
    assert dotenv.is_file()
    assert not (session.path / ".env.local").exists()


@pytest.mark.parametrize("user_file", [False, True])
def test_cleanup_distinguishes_verification_output_from_adjacent_user_data(
    safety_repo: Path, user_file: bool
) -> None:
    session = worktree.create("verified", repo_root=safety_repo)
    usage.record_verify_check(session.path, "consumer-check")
    artifact = verify_artifact.write_run_artifact(session.path, verify.VerifyReport("fast", ()))
    assert artifact is not None
    if user_file:
        private = artifact.parent / "user-notes.txt"
        private.write_text("user work\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="user-notes"):
            worktree.cleanup("verified", repo_root=safety_repo)
        assert private.exists()
    else:
        worktree.cleanup("verified", repo_root=safety_repo)
        assert not session.path.exists()
