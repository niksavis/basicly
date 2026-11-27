from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import pytest

HOOKS = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks"

REFUSED = [
    "uv run pytest",
    "uv run pytest -q",
    "pytest tests -q",
    "uv run pytest tests/test_git_hooks -q -n 4",
    "uv run pytest tests/test_a.py tests/test_b.py -q",
    "cd lane && uv run pytest -x",
    "npm test",
    "npm run test",
    "pnpm test",
    "uv run basicly verify --mode full",
    "basicly verify --mode=full --ci",
    "uv run basicly consumer-proof --record x-1",
    "python -m pytest -q",
    "uv run pytest tests 2>&1",
    "uv run pytest -q > out.txt",
    "uv run pytest tests/test_a.py tests/test_b.py -q 2>/dev/null",
]

ALLOWED = [
    "uv run pytest tests/test_a.py -q",
    "uv run pytest tests/test_a.py::test_one -q",
    "uv run pytest -k name tests/test_a.py",
    "uv run pytest tests/test_delivery_pr.py -q -p no:randomly 2>&1",
    "uv run pytest tests/test_a.py -q > out.txt 2>&1",
    "uv run pytest tests/test_a.py -q 2> err.txt",
    "uv run pytest tests/test_a.py -q &>out.txt",
    "uv run pytest tests/test_a.py -q >>out.txt",
    "npm test -- src/a.test.ts",
    "uv run basicly verify --mode staged --affected",
    "rg -n pytest docs",
    "echo 'npm test' > note.txt",
    "git commit -m 'run pytest later'",
]


def _rule() -> Any:
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("local_runs_rule", HOOKS / "local_runs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("command", REFUSED)
def test_a_local_test_set_is_refused_by_name(command: str) -> None:
    assert _rule().refusal(command) is not None


@pytest.mark.parametrize("command", ALLOWED)
def test_one_test_file_and_the_staged_checks_pass(command: str) -> None:
    assert _rule().refusal(command) is None


def test_several_test_files_pass_only_when_git_shows_each_as_changed() -> None:
    command = "uv run pytest tests/test_a.py tests/test_b.py -q"
    rule = _rule()

    assert rule.refusal(command, frozenset({"tests/test_a.py", "tests/test_b.py"})) is None
    assert "1 of them unchanged" in str(rule.refusal(command, frozenset({"tests/test_a.py"})))


def test_the_hook_blocks_a_bash_call_and_prints_the_allowed_command(tmp_path: Path) -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "uv run pytest"},
        "cwd": str(tmp_path),
    }

    done = subprocess.run(  # nosec B603
        [sys.executable, str(HOOKS / "local-test-guard.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert done.returncode == 2
    assert "pytest with no test file runs the whole suite" in done.stderr
    assert "uv run basicly verify --mode staged --affected" in done.stderr
