from __future__ import annotations

import importlib.util
import os
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / ".basicly" / "core" / "hooks" / "git-env-check.py"
)
CLEAN = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
KEPT = 'import subprocess\nsubprocess.run(\n    ["git", "init"],\n    cwd=x,\n    env=clean,\n)\n'


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("git_env_check_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=CLEAN, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "test_x.py").write_text(KEPT, encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "c")
    monkeypatch.chdir(tmp_path)
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    return tmp_path


def _stage(repo: Path, text: str) -> int:
    (repo / "test_x.py").write_text(text, encoding="utf-8")
    _git(repo, "add", ".")
    return _load().main(["test_x.py"])


def test_an_old_inherited_call_passes_when_another_line_is_added(repo: Path) -> None:
    old = 'import subprocess\nsubprocess.run(["git", "status"])\n'
    (repo / "test_x.py").write_text(old, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "old")

    assert _stage(repo, old + "VALUE = 1\n") == 0


def test_an_added_line_that_runs_git_with_the_inherited_variables_is_refused(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _stage(repo, KEPT + 'subprocess.run(["git", "status"])\n')

    assert code == 1
    assert "test_x.py:7" in capsys.readouterr().err


def test_deleting_the_env_line_of_a_call_is_refused(repo: Path) -> None:
    assert _stage(repo, KEPT.replace("    env=clean,\n", "")) == 1


def test_the_refusal_names_what_the_check_cannot_see(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stage(repo, KEPT + 'subprocess.run(["git", "status"], env={**os.environ})\n')

    assert "does not see an argv built by a function" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("line", "leaks"),
    [
        ('subprocess.run(["git", "init"])', True),
        ('subprocess.run(["git", "add"], env={**os.environ, "A": "b"})', True),
        ('subprocess.run("git init", shell=True)', True),
        ('subprocess.run(["git", "status"], env={"PATH": os.environ["PATH"]})', False),
        (
            'subprocess.run(["git", "commit"], '
            'env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})',
            False,
        ),
        ('subprocess.run(["ls"])', False),
    ],
)
def test_a_git_call_is_judged_by_its_env(line: str, leaks: bool) -> None:
    found = _load().leaking_calls(f"import os, subprocess\n{line}\n")
    assert bool(found) is leaks


def test_an_argv_held_in_a_name_is_seen() -> None:
    source = 'import subprocess\ncmd = ["git", "commit"]\nsubprocess.run(cmd)\n'
    assert _load().leaking_calls(source) == [(3, 3)]
