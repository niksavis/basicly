"""Tests for the unsplit-loop Claude Code PreToolUse guard (basicly-m2g3).

The positive control is the real command from the session that filed the bead, kept
verbatim: a guard written from a paraphrase of a defect is a guard against the paraphrase.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "unsplit-loop-guard.py"
)

# The command as it was actually run on 2026-08-09. It looped once over the whole string,
# touched none of the 15 files, and exited 0.
THE_REAL_ONE = (
    'MOVE="tool-ast-grep tool-curl tool-fd tool-git tool-jq tool-ripgrep tool-sd '
    'tool-shellcheck tool-tmux tool-tree tool-typos tool-uv tool-wget tool-xh tool-yq" && '
    "for s in $MOVE; do\n"
    '  f=".basicly/core/skills/$s/skill.yaml"\n'
    '  grep -q \'^invocation: model$\' "$f" || { echo "MISS $s"; continue; }\n'
    "  sd '^invocation: model$' 'invocation: user' \"$f\"\n"
    "done"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("unsplit_loop_guard_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_hook(payload: object) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_the_command_that_filed_this_bead_is_refused() -> None:
    """Positive control: the real defect, verbatim, blocked with the variable named."""
    result = _run_hook(_bash(THE_REAL_ONE))

    assert result.returncode == 2
    assert "$MOVE" in result.stderr
    assert "runs ONCE" in result.stderr


def test_zsh_really_does_run_that_loop_only_once() -> None:
    """The defect is a property of the shell, not a story about it.

    If a future shell splits this, the guard is refusing something harmless and should be
    reconsidered — this test is how anyone would find out.
    """
    script = 'V="a b c"\nn=0\nfor x in $V; do n=$((n+1)); done\necho "$n"'
    for shell in ("zsh", "bash"):
        if not subprocess.run(  # nosec B603 B607
            ["which", shell], capture_output=True, check=False
        ).stdout:
            continue
        out = subprocess.run(  # nosec B603
            [shell, "-c", script], capture_output=True, text=True, check=False
        ).stdout.strip()
        expected = "1" if shell == "zsh" else "3"
        assert out == expected, f"{shell} produced {out!r}, expected {expected!r}"


def test_the_corrected_forms_are_all_allowed() -> None:
    """The three forms the guard's own advice recommends must not trip it.

    A gate that refuses its own remedy teaches nothing and gets switched off.
    """
    allowed = (
        "for x in a b c; do echo $x; done",
        'arr=(a b c); for x in "${arr[@]}"; do echo $x; done',
        'V="a b c"; printf "%s\\n" $V | xargs -n1 echo',
    )
    for command in allowed:
        result = _run_hook(_bash(command))
        assert result.returncode == 0, f"{command!r} was refused: {result.stderr}"
        assert result.stderr == ""


def test_an_array_assignment_is_not_a_scalar() -> None:
    """Zsh splits an array, so looping over one unquoted is correct and must pass."""
    module = _load_module()
    assert module.unsplit_loop_names("arr=(a b c)\nfor x in $arr; do echo $x; done") == ()


def test_a_single_word_scalar_loops_once_correctly() -> None:
    """One word in, one iteration out — the loop does what it looks like it does."""
    module = _load_module()
    assert module.unsplit_loop_names('V="solo"\nfor x in $V; do echo $x; done') == ()


def test_a_quoted_expansion_is_an_explicit_choice() -> None:
    """`"$V"` passes one word on purpose; refusing it would be refusing correct code."""
    module = _load_module()
    assert module.unsplit_loop_names('V="a b"\nfor x in "$V"; do echo $x; done') == ()


def test_command_substitution_is_left_alone() -> None:
    """Also unsplit under zsh, but idiomatic — see the module docstring for why not here."""
    module = _load_module()
    assert module.unsplit_loop_names('V="a b"\nfor x in $(ls); do echo $x; done') == ()


def test_the_two_halves_are_only_a_defect_together() -> None:
    """Either half alone is ordinary shell; the intersection is the whole check."""
    module = _load_module()
    assert module.unsplit_loop_names('V="a b c"; echo "$V"') == ()
    assert module.unsplit_loop_names("for x in $UNSET_ELSEWHERE; do echo $x; done") == ()
    assert module.unsplit_loop_names('V="a b c"; for x in $V; do echo $x; done') == ("V",)


def test_every_broken_name_is_reported_not_just_the_first() -> None:
    """A caller fixing one and re-running would otherwise pay a round trip per variable."""
    module = _load_module()
    command = 'A="1 2"\nB="3 4"\nfor i in $A; do :; done\nfor j in ${B}; do :; done'
    assert module.unsplit_loop_names(command) == ("A", "B")


def test_the_braced_form_is_caught_too() -> None:
    """`${V}` is the same expansion wearing braces."""
    module = _load_module()
    assert module.unsplit_loop_names('V="a b"\nfor x in ${V}; do echo $x; done') == ("V",)


def test_it_fails_open_on_anything_it_cannot_read() -> None:
    """A bug in the guard must never be able to stop an agent running commands."""
    for payload in ("", "not json", "[]", '"a string"', json.dumps({}), json.dumps({"a": 1})):
        result = _run_hook(payload)
        assert result.returncode == 0, f"{payload!r} blocked"

    assert _run_hook({"tool_name": "Bash", "tool_input": {}}).returncode == 0
    assert _run_hook({"tool_name": "Bash", "tool_input": {"command": 7}}).returncode == 0
    assert _run_hook({"tool_name": "Bash", "tool_input": "not a dict"}).returncode == 0


def test_an_ordinary_command_passes_silently() -> None:
    """The common case writes nothing at all — a guard that chatters gets muted."""
    result = _run_hook(_bash("uv run pytest -q"))
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""
