from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import codex_permissions, merge, release, worktree

REPO_ROOT = Path(__file__).resolve().parents[1]
APPROVED_DEFAULTS = b'approval_policy = "on-request"\ndefault_permissions = ":danger-full-access"\n'


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
        capture_output=True,
        text=True,
        check=True,
    )


def _repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    target = root / codex_permissions.CONFIG_PATH
    target.parent.mkdir()
    target.write_bytes((REPO_ROOT / codex_permissions.CONFIG_PATH).read_bytes())
    _git(root, "add", ".codex/config.toml")
    _git(root, "commit", "-qm", "chore: record repository defaults")
    return root


def _basicly(root: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "basicly.cli", *args],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src"), "CODEX_HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_repository_commits_the_approved_codex_defaults() -> None:
    assert (REPO_ROOT / codex_permissions.CONFIG_PATH).read_bytes() == APPROVED_DEFAULTS


@pytest.mark.parametrize(
    "command", [("permissions-build",), ("install", "--technologies", "python")]
)
def test_cleanup_and_install_preserve_repository_defaults_and_user_settings(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    root = tmp_path / "consumer"
    target = root / codex_permissions.CONFIG_PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(APPROVED_DEFAULTS)
    home = tmp_path / "codex-home"
    home.mkdir()
    user = home / "config.toml"
    user_settings = b'approval_policy = "never"\ndefault_permissions = ":read-only"\n'
    user.write_bytes(user_settings)

    for _ in range(2):
        result = _basicly(root, home, *command)
        assert result.returncode == 0, result.stdout + result.stderr
        assert target.read_bytes() == APPROVED_DEFAULTS
        assert user.read_bytes() == user_settings
        checked = _basicly(root, home, "permissions-check")
        assert checked.returncode == 0, checked.stdout + checked.stderr


def test_a_provisioned_worktree_receives_the_committed_repository_defaults(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path / "consumer")

    created = worktree.create("defaults", repo_root=root)

    assert (created.path / codex_permissions.CONFIG_PATH).read_bytes() == APPROVED_DEFAULTS
    assert _git(root, "status", "--porcelain").stdout == ""


def test_dirty_repository_defaults_still_refuse_integration_and_release(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path / "consumer")
    created = worktree.create("change", repo_root=root)
    (created.path / "result.txt").write_text("delivered\n", encoding="utf-8")
    _git(created.path, "add", "result.txt")
    _git(created.path, "commit", "-qm", "chore: deliver a change")
    plan = release.ReleasePlan("0.26.11", "0.26.12", "2026-10-24", ())
    clean_reasons = release.blocking_reasons(root, plan, issue_id="app-test")
    assert not any("working tree is not clean" in reason for reason in clean_reasons)
    target = root / codex_permissions.CONFIG_PATH
    changed = APPROVED_DEFAULTS + b"\n"
    target.write_bytes(changed)

    with pytest.raises(SystemExit, match="base checkout has uncommitted changes"):
        merge.merge_worktree(root, created.name, bead="app-test")
    dirty_reasons = release.blocking_reasons(root, plan, issue_id="app-test")

    assert any(
        "working tree is not clean" in reason and ".codex/config.toml" in reason
        for reason in dirty_reasons
    )
    assert target.read_bytes() == changed
    assert not (root / "result.txt").exists()
