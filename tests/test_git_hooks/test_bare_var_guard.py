"""Tests for the bare-`$VAR` Claude Code PreToolUse guard (basicly-gmdvdwo).

The positive controls are the four commands the guard was measured against, taken
verbatim from this repository's own recorded Bash calls rather than paraphrased: a
guard written against a paraphrase is a guard against the paraphrase.

The negative half is the larger half on purpose. Of 17358 recorded calls, 4 carry the
trap and 54 carry a **quoted** `"$VAR"` head that is deliberate — an executable path
holding a space. A guard that fired on those would be wrong thirteen times out of
fourteen and would be switched off, taking its true positives with it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "bare-var-guard.py"
)

# The four recorded calls the guard fires on, abbreviated to the part that decides.
RECORDED_TRAPS = [
    'W="uv run basicly tracker write --"; $W dep add basicly-hymq99 basicly-1bsfx3 -t blocks',
    'B=/repo; R="uv run --project $B --directory /tmp/s basicly tracker write --"; $R update wpc-1',
    'S=/tmp/s; G="git -C $S/consumer -c user.email=t@example.invalid"; $G commit -qam "attr"',
    'C=/tmp/s/consumer; G="git -C $C -c user.name=t"; $G merge -q --no-edit agent-b',
]

# Shapes that must never fire, each drawn from the same corpus.
SAFE = [
    ('CHROME="/mnt/c/Program Files/Google/Chrome/chrome.exe"; "$CHROME" --headless', "quoted path"),
    ("G=git; $G status", "a single-word value needs no splitting"),
    ("$EDITOR notes.md", "an environment variable this command never assigns"),
    ('D=$(mktemp -d) && cat > "$D/t.py"', "the expansion is an argument, not a head"),
    ('OLD=$(git show HEAD:f | python3 -c "print(1)")', "inside a command substitution"),
    ("jq -rs '.[] | select(.record as $ids)' f.json", "inside a single-quoted program"),
    ("uv run pytest -q", "no expansion at all"),
]


def _load_module():
    spec = importlib.util.spec_from_file_location("bare_var_guard_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    """Drive the hook the way the host does: one JSON payload on stdin."""
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("command", RECORDED_TRAPS)
def test_recorded_trap_is_refused(command: str) -> None:
    """Every command the corpus recorded failing is refused."""
    assert _load_module().unsplit_command_vars(command)


@pytest.mark.parametrize(("command", "why"), SAFE)
def test_safe_shape_is_allowed(command: str, why: str) -> None:
    """The 54 quoted heads and the other safe shapes stay silent."""
    assert _load_module().unsplit_command_vars(command) == (), why


def test_it_names_the_variable_it_refuses() -> None:
    """The refusal names the variable, so the fix is obvious."""
    module = _load_module()
    assert module.unsplit_command_vars('W="uv run x --"; $W a b') == ("W",)


def test_braced_expansion_is_the_same_defect() -> None:
    """`${W}` splits no better than `$W`."""
    module = _load_module()
    assert module.unsplit_command_vars('W="uv run x --"; ${W} a b') == ("W",)


def test_end_to_end_refuses_with_exit_two_and_names_the_fix() -> None:
    """Exit 2 is what the host reads as a block."""
    result = _run({"tool_name": "Bash", "tool_input": {"command": 'W="uv run x --"; $W a b'}})
    assert result.returncode == 2
    assert "`$W`" in result.stderr
    assert "word-split" in result.stderr


def test_end_to_end_allows_the_quoted_control() -> None:
    """The control that must never fire, driven through stdin."""
    command = 'CHROME="/a b/chrome.exe"; "$CHROME" --headless'
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Read", "tool_input": {"command": 'W="a b"; $W c'}},
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash"},
        {},
    ],
)
def test_it_fails_open_on_anything_it_cannot_judge(payload: dict) -> None:
    """A payload this guard cannot judge never blocks a call."""
    assert _run(payload).returncode == 0


def test_malformed_stdin_never_blocks() -> None:
    """A bug here must not lock an agent out of the shell."""
    result = subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input="not json at all",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
