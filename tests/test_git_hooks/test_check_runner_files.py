from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

RECORD = "import json, sys; open(sys.argv[1], 'a').write(json.dumps(sys.argv[2:]) + '\\n')"


def _runner():
    path = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "check_runner.py"
    spec = importlib.util.spec_from_file_location("check_runner_files_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # nosec B603 B607
    for name in ("a.py", "b.py", "notes.txt"):
        (root / name).write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.delenv("BASICLY_SNAPSHOT_TREE", raising=False)
    return root


def _configure(repo: Path, log: Path) -> None:
    command = [sys.executable, "-c", RECORD, str(log)]
    (repo / "basicly.toml").write_text(
        f'[[verify.checks]]\nname = "typed"\ncommand = {json.dumps(command)}\n'
        'modes = ["fast"]\nstaged_suffix = ".py"\n',
        encoding="utf-8",
    )


def _calls(log: Path) -> list[list[str]]:
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [json.loads(line) for line in lines]


def test_a_suffixed_check_receives_only_the_changed_files_with_its_suffix(
    repo: Path, tmp_path: Path
) -> None:
    log = tmp_path / "calls"
    _configure(repo, log)

    assert _runner().run_checks(repo, "fast", changed=["a.py", "notes.txt", "gone.py"]) == 0

    assert _calls(log) == [["a.py"]]


def test_a_suffixed_check_is_skipped_when_no_changed_file_has_its_suffix(
    repo: Path, tmp_path: Path
) -> None:
    log = tmp_path / "calls"
    _configure(repo, log)

    assert _runner().run_checks(repo, "fast", changed=["notes.txt"]) == 0

    assert _calls(log) == []


def test_a_suffixed_check_sees_the_whole_project_when_no_change_list_is_known(
    repo: Path, tmp_path: Path
) -> None:
    log = tmp_path / "calls"
    _configure(repo, log)

    assert _runner().run_checks(repo, "fast") == 0

    assert _calls(log) == [[]]
