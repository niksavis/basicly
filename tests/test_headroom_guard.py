"""Tests for the headroom PreToolUse hook (basicly-zq9i2m.4).

Covers `.basicly/core/hooks/headroom-guard.py`. The two questions worth asserting are the
ones the hook exists to answer: a tight module's figures reach the *model* rather than a
debug log, and every other input is allowed in silence. The decision shape is asserted
against the documented field names, because a hook that emits a key Claude Code does not
read is indistinguishable from one that says nothing.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "headroom-guard.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("headroom_guard", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


def _run(payload: object) -> tuple[int, str]:
    """The hook as Claude Code runs it: payload on stdin, decision on stdout."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload) if not isinstance(payload, str) else payload,
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    return proc.returncode, proc.stdout


def _edit(path: str) -> dict[str, object]:
    return {"session_id": "s", "tool_input": {"file_path": str(REPO_ROOT / path)}}


def test_a_tight_module_puts_its_figures_where_the_model_reads_them() -> None:
    """The decision allows the call and carries the figures as `additionalContext`.

    `src/basicly/merge.py` is the subject because it stands at its frozen baseline with 0
    tokens left, so it is tight by measurement rather than by a fixture.
    """
    code, out = _run(_edit("src/basicly/merge.py"))

    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "allow"
    assert "src/basicly/merge.py" in decision["additionalContext"]
    assert "tokens" in decision["additionalContext"]


def test_a_module_with_room_says_nothing() -> None:
    """Silence is the whole reason this is affordable on the write path."""
    code, out = _run(_edit(".basicly/core/hooks/headroom-guard.py"))

    assert code == 0
    assert out == ""


def test_a_path_outside_python_is_not_measured() -> None:
    """Only `.py` is ratcheted by these two gates, so nothing else pays the 1.1s."""
    assert _run(_edit("README.md")) == (0, "")


def test_a_malformed_payload_allows_the_call() -> None:
    """Fails open: a guard that can refuse on its own bug is worse than no guard."""
    assert _run("not json") == (0, "")


def test_a_payload_naming_no_file_allows_the_call() -> None:
    """Every non-write tool reaching this matcher lands here."""
    assert _run({"session_id": "s", "tool_input": {}}) == (0, "")


def test_the_decision_omits_the_context_key_when_there_is_none() -> None:
    """An empty `additionalContext` would be a claim about headroom; absence is not."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        hook.allow()

    decision = json.loads(buffer.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "allow"
    assert "additionalContext" not in decision
