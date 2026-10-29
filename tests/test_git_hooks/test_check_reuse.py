from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

COUNT = (
    "import sys; from pathlib import Path; p = Path(sys.argv[1]); "
    "p.write_text(str(int(p.read_text() or 0) + 1) if p.exists() else '1'); "
    "sys.exit(int(sys.argv[2]))"
)


def _runner():
    path = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "check_runner.py"
    spec = importlib.util.spec_from_file_location("check_runner_reuse_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # nosec B603 B607


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text(".basicly/usage/\n", encoding="utf-8")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    monkeypatch.chdir(root)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("BASICLY_SNAPSHOT_TREE", "any")
    return root


def _configure(repo: Path, counter: Path, code: int = 0) -> None:
    command = [sys.executable, "-c", COUNT, str(counter), str(code)]
    (repo / "basicly.toml").write_text(
        f'[[verify.checks]]\nname = "counted"\ncommand = {json.dumps(command)}\nmodes = ["fast"]\n',
        encoding="utf-8",
    )
    _git(repo, "add", "basicly.toml")


def _runs(counter: Path) -> int:
    return int(counter.read_text()) if counter.exists() else 0


def test_a_check_that_passed_for_this_tree_is_not_run_again(repo: Path, tmp_path: Path) -> None:
    counter = tmp_path / "runs"
    _configure(repo, counter)

    assert _runner().run_checks(repo, "fast") == 0
    assert _runner().run_checks(repo, "fast") == 0

    assert _runs(counter) == 1


def test_a_changed_tree_runs_the_check_again(repo: Path, tmp_path: Path) -> None:
    counter = tmp_path / "runs"
    _configure(repo, counter)
    assert _runner().run_checks(repo, "fast") == 0

    (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    assert _runner().run_checks(repo, "fast") == 0

    assert _runs(counter) == 2


def test_a_failed_check_is_never_reused(repo: Path, tmp_path: Path) -> None:
    counter = tmp_path / "runs"
    _configure(repo, counter, code=3)

    assert _runner().run_checks(repo, "fast") == 1
    assert _runner().run_checks(repo, "fast") == 1

    assert _runs(counter) == 2


def test_an_untracked_file_prevents_recording_a_pass(repo: Path, tmp_path: Path) -> None:
    counter = tmp_path / "runs"
    _configure(repo, counter)
    (repo / "draft.py").write_text("UNSTAGED = 1\n", encoding="utf-8")

    assert _runner().run_checks(repo, "fast") == 0
    assert _runner().run_checks(repo, "fast") == 0

    assert _runs(counter) == 2


@pytest.mark.parametrize("unset", ["outside a hook snapshot", "in CI"])
def test_reuse_is_off_outside_a_local_hook_snapshot(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset: str
) -> None:
    counter = tmp_path / "runs"
    _configure(repo, counter)
    if unset == "in CI":
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("BASICLY_SNAPSHOT_TREE")

    assert _runner().run_checks(repo, "fast") == 0
    assert _runner().run_checks(repo, "fast") == 0

    assert _runs(counter) == 2
