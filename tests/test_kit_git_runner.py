from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "git_runner.py"


def _load() -> ModuleType:
    name = "basicly_tracker_kit_git_runner_under_test"
    spec = importlib.util.spec_from_file_location(name, KIT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.mark.parametrize("value", ["a" * 40, "0123456789abcdef" * 4])
def test_a_full_commit_id_passes(value: str) -> None:
    assert runner.commit_id(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "HEAD", "a" * 39, "A" * 40, "a" * 41, "a" * 40 + "\n", "-" + "a" * 39, "main", 7],
)
def test_a_bad_commit_id_is_refused(value: object) -> None:
    with pytest.raises(runner.GitValueError, match="commit id"):
        runner.commit_id(value)


def test_a_new_writer_id_passes_its_own_check() -> None:
    made = runner.new_writer_id()

    assert runner.writer_id(made) == made
    assert made != runner.new_writer_id()


@pytest.mark.parametrize(
    "value", ["", "a" * 31, "a" * 33, "G" * 32, "../" + "a" * 29, "a" * 31 + "/", "a" * 32 + " "]
)
def test_a_bad_writer_id_is_refused(value: str) -> None:
    with pytest.raises(runner.GitValueError, match="writer id"):
        runner.writer_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/owner/repo-backlog.git",
        "https://bitbucket.org/owner/repo-backlog",
        "ssh://git@github.com/owner/repo-backlog.git",
        "ssh://git@example.com:2222/team/repo-backlog.git",
        "git@github.com:owner/repo-backlog.git",
    ],
)
def test_a_forge_url_passes(value: str) -> None:
    assert runner.remote_url(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://github.com/owner/repo.git",
        "ext::sh -c touch% /tmp/pwned",
        "fd::17",
        "-uevil",
        "--upload-pack=touch /tmp/pwned",
        "git@-oProxyCommand=evil:repo",
        "-oProxyCommand=evil@host:repo",
        "git@github.com:-repo",
        "https://github.com/owner/repo git",
        "https://github.com/owner/repo\n",
        "ssh://-oProxyCommand=evil/repo",
        "/srv/git/repo.git",
        "file:///srv/git/repo.git",
    ],
)
def test_a_bad_url_is_refused(value: str) -> None:
    with pytest.raises(runner.GitValueError, match="backlog URL"):
        runner.remote_url(value)


def test_a_file_url_passes_only_when_allowed() -> None:
    value = "file:///srv/git/repo.git"

    assert runner.remote_url(value, allow_file=True) == value


def test_git_output_is_returned(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")

    assert runner.run(["rev-parse", "--is-inside-work-tree"], repo).strip() == "true"


def test_a_failed_git_command_raises_with_its_exit_code(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")

    with pytest.raises(runner.GitCommandError) as caught:
        runner.run(["rev-parse", "--verify", "--quiet", "no-such-ref"], repo)

    assert caught.value.returncode == 1
    assert str(caught.value) == "git rev-parse failed with exit code 1"


def test_the_ext_transport_is_refused_by_git(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    marker = tmp_path / "pwned"

    with pytest.raises(runner.GitCommandError):
        runner.run(
            ["ls-remote", runner.END_OF_OPTIONS, f"ext::sh -c touch% {marker.as_posix()}"],
            repo,
        )

    assert not marker.exists()


def test_a_local_remote_is_refused_unless_file_is_allowed(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    subprocess.run(
        ["git", "-C", str(source), "commit", "-q", "--allow-empty", "-m", "seed"],
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    url = source.resolve().as_uri()

    with pytest.raises(runner.GitCommandError):
        runner.run(["ls-remote", runner.END_OF_OPTIONS, url], tmp_path)

    assert "HEAD" in runner.run(
        ["ls-remote", runner.END_OF_OPTIONS, url], tmp_path, allow_file=True
    )


def test_the_callers_repository_variables_do_not_reach_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    other = _init_repo(tmp_path / "other")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))

    found = runner.run(["rev-parse", "--absolute-git-dir"], repo).strip()

    assert Path(found).resolve() == (repo / ".git").resolve()


def test_git_missing_from_path_raises_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(runner.GitMissingError, match="install git"):
        runner.run(["--version"], tmp_path)


@pytest.mark.parametrize("args", [[], ["status", 3]])
def test_arguments_that_are_not_strings_are_refused(tmp_path: Path, args: list[object]) -> None:
    with pytest.raises(runner.GitValueError):
        runner.run(args, tmp_path)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.is_file() and stat.read_text().split(") ", 1)[1].startswith("Z"))


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX; Windows kills git")
def test_a_timeout_stops_git_and_its_children_and_raises_by_name(tmp_path: Path) -> None:
    pid_file = tmp_path / "ssh.pid"
    hang = f"echo $$ > '{pid_file.as_posix()}'; exec sleep 30 #"

    started = time.monotonic()
    with pytest.raises(runner.GitTimeoutError, match="git ls-remote did not finish within 1 s"):
        runner.run(
            ["ls-remote", runner.END_OF_OPTIONS, "ssh://git@example.invalid/owner/repo.git"],
            tmp_path,
            timeout=1.0,
            env={"GIT_SSH_COMMAND": hang},
        )
    elapsed = time.monotonic() - started

    assert elapsed < 10
    deadline = time.monotonic() + 5
    pid = int(pid_file.read_text().strip())
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)
