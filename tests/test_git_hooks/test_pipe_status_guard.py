"""Tests for the pipe-status Claude Code PreToolUse guard (basicly-xkqxp9).

The positive control is the reproduction the record itself states, kept verbatim. The
two commands from the 2026-08-20 session were not recorded anywhere this checkout can
read, so they are **not** reconstructed here: a guard written against a paraphrase is a
guard against the paraphrase, and this repo has already paid for an invented fixture
once. What is asserted instead is the shape the record names plus the background shape
the earlier "exit code 0" incident recorded.

The negative half is the larger half on purpose. `head` and `tail` are the 1st and 3rd
most-used tools in this repo at 16394 and 13960 invocations, so a guard that fires on
the idiom would cry wolf tens of thousands of times and get switched off.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "pipe-status-guard.py"
)

# The reproduction the record states, step 1: a failing gate piped into tail, whose `$?`
# is then read and is 0.
THE_RECORDED_ONE = "uv run pytest -q tests/some_failing.py | tail -3; echo $?"


def _load_module():
    spec = importlib.util.spec_from_file_location("pipe_status_guard_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module():
    """The guard loaded once by path, the way the host runs it."""
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


def _bash(command: str, *, background: bool = False) -> dict:
    tool_input: dict = {"command": command}
    if background:
        tool_input["run_in_background"] = True
    return {"tool_name": "Bash", "tool_input": tool_input}


def test_the_reproduction_the_record_states_is_refused() -> None:
    """Positive control: the record's own step 1, blocked with the filter named."""
    result = _run_hook(_bash(THE_RECORDED_ONE))
    assert result.returncode == 2
    assert "`tail`" in result.stderr


def test_the_refusal_names_the_three_alternatives_rather_than_saying_be_careful() -> None:
    """A bare prohibition leaves the agent stuck; prose is what already failed here."""
    stderr = _run_hook(_bash(THE_RECORDED_ONE)).stderr
    assert "> out.txt" in stderr
    assert "PIPESTATUS" in stderr
    assert "pipefail" in stderr
    assert "pass/fail summary line" in stderr


@pytest.mark.parametrize(
    ("command", "background"),
    [
        ("uv run pytest -q tests/x.py | tail -3; echo $?", False),
        ("uv run basicly board validate | tail -5 && git commit", False),
        ("uv run ruff check | head -20 || echo failed", False),
        ("if uv run pytest -q | tail -1; then echo ok; fi", False),
        ("while cmd | tail -1; do sleep 1; done", False),
        # The recorded background incident: the only status the caller sees is the pipe's.
        ("uv run pytest -q | tail -3", True),
    ],
)
def test_a_status_actually_read_after_a_filter_is_refused(
    module, command: str, background: bool
) -> None:
    """Each of the four ways the one command string can branch on the pipe's status."""
    assert module.unread_pipe_filters(command, background=background) != ()


@pytest.mark.parametrize(
    "command",
    [
        # The idiom itself: 30354 recorded invocations must not become 30354 refusals.
        "uv run pytest -q | tail -3",
        "git log --oneline | head -20",
        "cat notes.txt | wc -l",
        # grep's status IS the assertion; refusing it is the cry-wolf case.
        "cat f | grep -q PASS && echo found",
        "cmd | grep -v skip; echo $?",
        # Already handled by the caller, both documented remedies.
        "set -o pipefail; uv run pytest -q | tail -3; echo $?",
        "uv run pytest -q | tail -3; echo ${PIPESTATUS[0]}",
        # The form the advice steers toward: no pipe carries the status at all.
        "uv run pytest -q > out.txt 2>&1; echo $?; tail -5 out.txt",
        # `&&` before the pipeline, not after it — nothing reads the pipe's status.
        "echo hi && ls | head -3",
        # A single-stage command is not a pipeline.
        "tail -5 out.txt; echo $?",
        # An operator inside a quoted argument is text, not a pipeline boundary.
        "git commit -m 'fix: tail x; echo $?' && git log | head -1",
    ],
)
def test_a_pipeline_whose_status_is_never_read_is_left_alone(module, command: str) -> None:
    """The acceptance criterion's second half: refuse the defect and not the idiom."""
    assert module.unread_pipe_filters(command) == ()


def test_a_heredoc_body_is_not_read_as_a_pipeline(module) -> None:
    """Text written into a file is data; counting it would refuse writing a test."""
    assert module.unread_pipe_filters("cat <<'EOF' > f\nx | tail -1; echo $?\nEOF") == ()


def test_the_grep_family_is_absent_from_the_fire_set_on_purpose(module) -> None:
    """Stated as an assertion so widening the set has to face this test."""
    assert "grep" not in module.PASS_THROUGH
    assert "rg" not in module.PASS_THROUGH
    assert {"head", "tail"} <= module.PASS_THROUGH


def test_the_guard_fails_open_on_anything_it_cannot_read() -> None:
    """A bug here must never lock an agent out of running commands."""
    for payload in ("not json at all", "{}", json.dumps({"tool_name": "Edit"})):
        assert _run_hook(payload).returncode == 0
