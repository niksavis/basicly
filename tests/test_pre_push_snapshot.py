from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from basicly import hooks, lefthook_config
from tests.native_hooks import repository


@pytest.fixture(autouse=True)
def isolated_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")


def _push_check(repo: Path, committed: str, working: str) -> subprocess.CompletedProcess:
    script = repo / ".basicly/core/hooks/check.py"
    script.write_text('from pathlib import Path\nassert Path("file.txt").read_text() == "valid"\n')
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("check", "check.py", "pre-push", always_run=True)],
        ".basicly/core/hooks",
    )
    target = repo / "file.txt"
    target.write_text(committed)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "--no-verify", "-m", "c"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    target.write_text(working)
    return subprocess.run(
        [sys.executable, str(repo / ".basicly/core/hooks/lefthook-runtime.py"), "git", "pre-push"],
        cwd=repo,
        input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_pre_push_refuses_a_pushed_commit_the_working_file_would_pass(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    result = _push_check(repo, committed="invalid", working="valid")

    assert result.returncode != 0
    assert (repo / "file.txt").read_text() == "valid"


def test_pre_push_ignores_an_unfinished_working_edit(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    result = _push_check(repo, committed="valid", working="invalid")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "file.txt").read_text() == "invalid"
