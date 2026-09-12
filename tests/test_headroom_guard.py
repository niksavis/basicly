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


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hook = _load_module(SCRIPT, "headroom_guard")


def _run(payload: object) -> tuple[int, str]:
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


def _a_tight_module() -> str:
    read = _load_module(REPO_ROOT / ".scripts" / "headroom.py", "headroom_read")
    tight = [room.path for room in read.measure(REPO_ROOT) if read.is_tight(room)]
    assert tight, "the positive control: some module must be near a bound for this to test"
    return tight[0]


def test_a_tight_module_puts_its_figures_where_the_model_reads_them() -> None:
    path = _a_tight_module()

    code, out = _run(_edit(path))

    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "allow"
    assert path in decision["additionalContext"]
    assert "tokens" in decision["additionalContext"]


def test_a_module_with_room_says_nothing() -> None:
    code, out = _run(_edit(".basicly/core/hooks/headroom-guard.py"))

    assert code == 0
    assert out == ""


def test_a_path_outside_python_is_not_measured() -> None:
    assert _run(_edit("README.md")) == (0, "")


def test_a_malformed_payload_allows_the_call() -> None:
    assert _run("not json") == (0, "")


def test_a_payload_naming_no_file_allows_the_call() -> None:
    assert _run({"session_id": "s", "tool_input": {}}) == (0, "")


def test_the_decision_omits_the_context_key_when_there_is_none() -> None:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        hook.allow()

    decision = json.loads(buffer.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "allow"
    assert "additionalContext" not in decision
