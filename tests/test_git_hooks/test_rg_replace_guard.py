from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "rg-replace-guard.py"
)

RECORDED_TRAPS = [
    ("rg -rn 'rank1_floor' src/basicly/config.py", "-rn"),
    ("rg -ril 'deepseek' --glob '!.basicly/ledger/**' .", "-ril"),
    ("rg -rl 'Knowledge Priming' .basicly/core/", "-rl"),
    ('rg -rln "headroom" .basicly/core/', "-rln"),
    ("rg -rc --glob '!*.jsonl' -- \"tracker/cli\" .", "-rc"),
]

SAFE = [
    ("rg -n \"docs_claims\" -r '' -l .", "the deliberate form, `-r` as its own token"),
    ("rg -o '^#{2,4} (D[0-9]+)' -r '$1' docs/f.md", "a real replacement"),
    ("rg -n --replace '' pattern .", "the long form"),
    ("rg -n pattern src/", "no r at all"),
    ("rg -l pattern src/", "a cluster with no r"),
    ("rg -in pattern src/", "case-insensitive plus line numbers"),
    ("grep -rn pattern src/", "grep, where -r really is recursion"),
    ("echo 'rg -rn x' > note.txt", "the string is data, not an invocation"),
]


def _load_module():
    spec = importlib.util.spec_from_file_location("rg_replace_guard_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(("command", "cluster"), RECORDED_TRAPS)
def test_recorded_trap_is_refused(command: str, cluster: str) -> None:
    assert _load_module().swallowed_replacements(command) == (cluster,)


@pytest.mark.parametrize(("command", "why"), SAFE)
def test_safe_shape_is_allowed(command: str, why: str) -> None:
    assert _load_module().swallowed_replacements(command) == (), why


def test_a_later_pipeline_stage_is_judged_too() -> None:
    assert _load_module().swallowed_replacements("cat f | rg -rn pat") == ("-rn",)


def test_end_to_end_refuses_with_exit_two_and_names_the_fix() -> None:
    result = _run({"tool_name": "Bash", "tool_input": {"command": "rg -rn pat src/"}})
    assert result.returncode == 2
    assert "`-rn`" in result.stderr
    assert "recurses by default" in result.stderr


def test_end_to_end_allows_the_deliberate_control() -> None:
    result = _run({"tool_name": "Bash", "tool_input": {"command": "rg -n pat -r '' src/"}})
    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Read", "tool_input": {"command": "rg -rn pat"}},
        {"tool_name": "Bash", "tool_input": {}},
        {},
    ],
)
def test_it_fails_open_on_anything_it_cannot_judge(payload: dict) -> None:
    assert _run(payload).returncode == 0


def test_malformed_stdin_never_blocks() -> None:
    result = subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input="not json at all",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
