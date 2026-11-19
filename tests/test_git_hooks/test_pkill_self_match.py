from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "pkill-self-match.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("pkill_self_match_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module():
    return _load_module()


def _run_hook(payload: object) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "command",
    [
        "pkill -f margin_plugin",
        "pkill -9 -f 'uv run pytest'",
        "sleep 1; pgrep -af loop.sh | head -3",
        "cd x && /usr/bin/pkill --full worker; echo done",
        "pkill -fl runner",
    ],
)
def test_a_full_line_pattern_without_a_bracket_class_is_refused(module, command: str) -> None:
    assert module.self_matching(command) != ()


@pytest.mark.parametrize(
    "command",
    [
        "pkill -f '[m]argin_plugin'",
        "pgrep -af '[l]oop.sh'",
        "pkill margin",
        "pgrep -l python",
        "kill 4242",
        "echo pkill -f is refused",
        "git commit -m 'pkill -f x'",
    ],
)
def test_a_bracket_class_a_name_match_or_a_pid_is_left_alone(module, command: str) -> None:
    assert module.self_matching(command) == ()


def test_the_refusal_names_the_bracket_form_and_kill_by_pid() -> None:
    result = _run_hook({"tool_name": "Bash", "tool_input": {"command": "pkill -f margin"}})
    assert result.returncode == 2
    assert "[m]argin" in result.stderr
    assert "kill <pid>" in result.stderr
    assert "Exit code 144" in result.stderr


def test_the_guard_fails_open_on_anything_it_cannot_read() -> None:
    for payload in ("not json", "{}", json.dumps({"tool_name": "Edit"})):
        assert _run_hook(payload).returncode == 0
    unbalanced = {"tool_name": "Bash", "tool_input": {"command": "pkill -f 'open"}}
    assert _run_hook(unbalanced).returncode == 0
