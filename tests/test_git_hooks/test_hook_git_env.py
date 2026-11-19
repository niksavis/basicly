from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from basicly import hooks, lefthook_config
from tests.native_hooks import repository

INIT_ELSEWHERE = (
    "import subprocess, tempfile\n"
    "folder = tempfile.mkdtemp()\n"
    "subprocess.run(['git', 'init', '-q', folder], check=True)\n"
)
NO_GIT_VARIABLES = (
    "import os\n"
    "names = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')\n"
    "leaked = [key for key in names if key in os.environ]\n"
    "assert not leaked, leaked\n"
)
WRITES_WORKTREE = (
    "import subprocess\n"
    "subprocess.run(['git', 'config', 'core.worktree', '/nowhere/basicly-test'], check=True)\n"
)


@pytest.fixture(autouse=True)
def isolated_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


def _push(repo: Path, job: str) -> subprocess.CompletedProcess[str]:
    (repo / ".basicly/core/hooks/check.py").write_text(job, encoding="utf-8")
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("check", "check.py", "pre-push", always_run=True)],
        ".basicly/core/hooks",
    )
    (repo / "file.txt").write_text("value", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "--no-verify", "-m", "c")
    head = _git(repo, "rev-parse", "HEAD")
    return subprocess.run(
        [sys.executable, str(repo / ".basicly/core/hooks/lefthook-runtime.py"), "git", "pre-push"],
        cwd=repo,
        input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _core_worktree(repo: Path) -> str:
    return subprocess.run(
        ["git", "config", "--file", str(repo / ".git/config"), "--get", "core.worktree"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()


def test_a_pre_push_job_runs_without_the_hook_git_variables(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    result = _push(repo, NO_GIT_VARIABLES)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_job_that_runs_git_init_elsewhere_leaves_the_repository_config_unchanged(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)

    result = _push(repo, INIT_ELSEWHERE)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _core_worktree(repo) == ""


def test_a_hook_run_that_changes_the_config_core_worktree_is_refused_with_the_repair(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)

    result = _push(repo, WRITES_WORKTREE)

    assert result.returncode == 1
    assert "changed core.worktree" in result.stderr
    assert f"git config --file {repo / '.git' / 'config'} --unset core.worktree" in result.stderr
