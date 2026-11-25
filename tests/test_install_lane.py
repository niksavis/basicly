from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import cli, install_lane, scaffolds

_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _git(where: Path, *args: str) -> None:
    subprocess.run(  # nosec B603 B607
        ["git", "-C", str(where), *_ID, *args],
        capture_output=True,
        check=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "start")
    return root


def _dirty_lane(repo: Path, lane: str) -> Path:
    path = repo.parent / f"{repo.name}.worktrees" / lane
    _git(repo, "worktree", "add", "-q", "-b", f"harness/{lane}", str(path))
    (path / "left.txt").write_text("unsaved\n", encoding="utf-8")
    return path


def test_a_dirty_lane_is_refused_by_name_with_the_inspect_command(repo: Path) -> None:
    path = _dirty_lane(repo, "kit")

    refused = install_lane.refusal(repo, "kit")

    assert refused is not None
    assert str(path) in refused
    assert f"git -C {path} status" in refused


def test_a_clean_or_absent_lane_is_not_refused(repo: Path) -> None:
    assert install_lane.refusal(repo, "kit") is None


def test_the_dry_run_plan_names_the_lane_and_the_release_wheel(repo: Path) -> None:
    lines = install_lane.plan_lines(repo, "kit")

    assert any(str(repo.parent / "app.worktrees" / "kit") in line for line in lines)
    assert any(scaffolds.DIST_SOURCE in line for line in lines)


def test_install_with_a_dirty_lane_exits_1_before_it_writes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _dirty_lane(repo, "kit")
    monkeypatch.setattr(cli, "_repo_root", lambda: repo)
    args = argparse.Namespace(lane="kit", dry_run=True, technologies=None)

    assert cli.cmd_install(args) == 1

    assert "uncommitted changes" in capsys.readouterr().err
    assert not (repo / ".basicly").exists()
