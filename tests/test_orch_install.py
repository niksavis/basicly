from __future__ import annotations

import importlib
import os
import stat
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".basicly" / "core" / "kit" / "orch"))

install = importlib.import_module("orch_relay.install")
runner = importlib.import_module("runner")
log = importlib.import_module("orch_lessons.log")
cli = importlib.import_module("orch_lessons.cli")

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
HOOK = """#!/bin/sh
grep -Eq '^chore\\(basicly\\): install the released kit \\(app-[a-z0-9]+\\)$' "$1" && exit 0
echo "commit-msg: the subject names no claimed app record" >&2
exit 1
"""


def _git(where: Path, *args: str) -> str:
    return subprocess.run(  # nosec B603 B607
        ["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
        env=CLEAN_ENV,
    ).stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    home = tmp_path / "development"
    app = home / "app"
    app.mkdir(parents=True)
    _git(app, "init", "-q", "-b", "main")
    _git(app, "config", "user.name", "t")
    _git(app, "config", "user.email", "t@t")
    _git(app, "commit", "-q", "--allow-empty", "-m", "start")
    hook = app / ".git" / "hooks" / "commit-msg"
    hook.write_text(HOOK, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    return home


def fake(argv: list[str], cwd: Path) -> object:
    if argv[0] != "uvx":
        done = subprocess.run(  # nosec B603
            argv, cwd=cwd, capture_output=True, text=True, check=False, env=CLEAN_ENV
        )
        return runner.Done(done.returncode, done.stdout, done.stderr)
    args = argv[argv.index("basicly") + 1 :]
    if args[:2] == ["worktree", "create"]:
        lane = cwd.parent / f"{cwd.name}.worktrees" / args[2]
        _git(cwd, "worktree", "add", "-q", "-b", f"harness/{args[2]}", str(lane))
    elif args == ["install"]:
        (cwd / "kit.txt").write_text("0.49.4\n", encoding="utf-8")
    return runner.Done(0, "would write kit.txt\n" if "--dry-run" in args else "", "")


def _lane(home: Path) -> Path:
    return home / "app.worktrees" / "kit-0494"


def test_install_commits_in_the_lane_through_the_hook_and_prints_the_commit(
    workspace: Path,
) -> None:
    lines = list(install.run(workspace, "0.49.4", ["app=app-x1y2"], False, fake))

    head = _git(_lane(workspace), "rev-parse", "HEAD")
    assert lines == [
        f"app: lane kit-0494 commit {head} chore(basicly): install the released kit (app-x1y2)"
    ]
    assert _git(_lane(workspace), "status", "--porcelain") == ""
    assert not (workspace / "app" / "kit.txt").exists()


def test_a_hook_refusal_stops_with_the_hook_text_and_names_the_lane(workspace: Path) -> None:
    with pytest.raises(log.RefusedError) as refused:
        list(install.run(workspace, "0.49.4", ["app=wrong-x1y2"], False, fake))

    text = str(refused.value)
    assert "git commit failed" in text
    assert "left as it is" in text
    assert "names no claimed app record" in text
    assert str(_lane(workspace)) in text


def test_the_dry_run_names_lane_wheel_and_subject_and_writes_nothing(workspace: Path) -> None:
    lines = list(install.run(workspace, "0.49.4", ["app=app-x1y2"], True, fake))

    assert any("kit-0494" in line and "would be created" in line for line in lines)
    assert any(install.WHEEL.format("0.49.4") in line for line in lines)
    assert any(line == "app: would write kit.txt" for line in lines)
    assert lines[-1] == "dry run: nothing written"
    assert not _lane(workspace).exists()


def test_a_dirty_lane_is_refused_before_any_write(workspace: Path) -> None:
    _git(
        workspace / "app", "worktree", "add", "-q", "-b", "harness/kit-0494", str(_lane(workspace))
    )
    (_lane(workspace) / "left.txt").write_text("unsaved\n", encoding="utf-8")

    with pytest.raises(log.RefusedError) as refused:
        list(install.run(workspace, "0.49.4", ["app=app-x1y2"], False, fake))

    assert f"git -C {_lane(workspace)} status" in str(refused.value)
    assert not (_lane(workspace) / "kit.txt").exists()


def test_an_install_with_no_change_makes_no_commit(workspace: Path) -> None:
    def unchanged(argv: list[str], cwd: Path) -> object:
        return runner.Done(0, "", "") if argv[-1] == "install" else fake(argv, cwd)

    lines = list(install.run(workspace, "0.49.4", ["app=app-x1y2"], False, unchanged))

    assert lines == ["app: lane kit-0494 has no change from basicly 0.49.4; no commit"]
    assert _git(_lane(workspace), "log", "--oneline") == _git(workspace / "app", "log", "--oneline")


def test_the_workspace_itself_is_a_repo_by_its_own_name(workspace: Path) -> None:
    _git(workspace, "init", "-q", "-b", "main")

    chosen = install.targets(workspace, ["development=dev-j6oqo"], "0.49.4")

    assert chosen[0].root == workspace


@pytest.mark.parametrize(
    ("release", "spec", "named"),
    [
        ("0.49", "app=app-x1y2", "--release"),
        ("0.49.4", "app", "--repo"),
        ("0.49.4", "../app=app-x1y2", "--repo"),
        ("0.49.4", "missing=app-x1y2", "not a git repository"),
    ],
)
def test_wrong_input_is_refused_by_name_with_the_usage(
    workspace: Path, release: str, spec: str, named: str
) -> None:
    with pytest.raises(log.RefusedError) as refused:
        list(install.run(workspace, release, [spec], False, fake))

    assert named in str(refused.value)
    assert not (workspace / "app.worktrees").exists()


def test_a_failed_dry_run_says_no_lane_was_made(workspace: Path) -> None:
    def broken(argv: list[str], cwd: Path) -> object:
        return (
            runner.Done(1, "", "markdownlint is missing")
            if "--dry-run" in argv
            else fake(argv, cwd)
        )

    with pytest.raises(log.RefusedError) as refused:
        list(install.run(workspace, "0.49.4", ["app=app-x1y2"], True, broken))

    assert "no lane kit-0494 was made" in str(refused.value)
    assert "markdownlint is missing" in str(refused.value)


def test_orch_install_without_a_store_is_refused(tmp_path: Path) -> None:
    with pytest.raises(log.RefusedError) as refused:
        cli.run(["install", "--release", "0.49.4", "--repo", "app=app-x1y2"], tmp_path, {})

    assert "orch lessons init" in str(refused.value)
