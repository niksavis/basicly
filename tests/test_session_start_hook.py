"""Tests for the session-start orientation hook (basicly-yru8eu).

The engine is injected, never spawned from PATH: `cli_command` is replaced with a
`python -c` argv, so the report, a slow fold and a failing exit are all test data instead
of properties of whichever `basicly` the runner happened to resolve. The one end-to-end
test runs the script as the hosts run it — as a file, with the real CLI.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / ".basicly" / "core" / "hooks" / "session-start.py"
)

REPORT = "ledger: 3 records, 1 ready, 0 blocked\nHandover (basicly-a, 2026-08-31) - read it"
CLAUDE_PAYLOAD = {"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}
COPILOT_PAYLOAD = {"sessionId": "s", "timestamp": 1, "cwd": ".", "source": "startup"}


def _load_module():
    spec = importlib.util.spec_from_file_location("session_start_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_cli(module, code: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "cli_command", lambda: [sys.executable, "-c", code])


def _run(module, payload: object, monkeypatch: pytest.MonkeyPatch) -> int:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    return module.main()


def test_a_claude_payload_receives_the_report_as_plain_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude Code injects a SessionStart hook's plain stdout, so it is not JSON-wrapped."""
    module = _load_module()
    _stub_cli(module, f"print({REPORT!r})", monkeypatch)

    assert _run(module, CLAUDE_PAYLOAD, monkeypatch) == 0

    captured = capsys.readouterr()
    assert captured.out == REPORT + "\n"
    assert captured.err == ""


def test_a_copilot_payload_receives_the_report_as_additional_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Copilot parses stdout as one JSON object; plain text would be dropped unread."""
    module = _load_module()
    _stub_cli(module, f"print({REPORT!r})", monkeypatch)

    assert _run(module, COPILOT_PAYLOAD, monkeypatch) == 0

    assert json.loads(capsys.readouterr().out) == {"additionalContext": REPORT}


def test_no_payload_at_all_takes_the_plain_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hand run has no stdin, and the readable form is the right default."""
    module = _load_module()
    _stub_cli(module, f"print({REPORT!r})", monkeypatch)

    assert _run(module, "", monkeypatch) == 0

    assert capsys.readouterr().out == REPORT + "\n"


def test_an_orientation_slower_than_the_bound_is_abandoned_in_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session open waits on this hook, so the fold is bounded rather than awaited."""
    module = _load_module()
    monkeypatch.setattr(module, "CLI_TIMEOUT_S", 0.2)
    _stub_cli(module, "import time; time.sleep(30)", monkeypatch)

    assert _run(module, CLAUDE_PAYLOAD, monkeypatch) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "session-start: orientation skipped, `basicly session start` outran 0.2s"
    ]


def test_a_failing_or_absent_engine_injects_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Never a gate: a broken orientation costs the report, never the session."""
    module = _load_module()
    _stub_cli(module, "raise SystemExit(3)", monkeypatch)

    assert _run(module, CLAUDE_PAYLOAD, monkeypatch) == 0
    first = capsys.readouterr()
    assert first.out == ""
    assert "exited 3" in first.err

    monkeypatch.setattr(module, "cli_command", lambda: None)
    assert _run(module, CLAUDE_PAYLOAD, monkeypatch) == 0
    assert capsys.readouterr() == ("", "")


def test_a_repository_with_no_owned_tracker_is_silent_end_to_end(tmp_path: Path) -> None:
    """The script as a host runs it: a file, the real CLI, and nothing to say.

    The second assertion is the positive control this empty stdout needs — it reads the
    line the command actually prints there and pins the hook's prefix against it, so the
    silence cannot come from a probe that never reached the tracker.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # nosec B603 B607
    hook = subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input="",
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (hook.returncode, hook.stdout) == (0, "")

    module = _load_module()
    command = module.cli_command()
    assert command is not None, "no runnable basicly, so the control cannot be read"
    report = subprocess.run(  # nosec B603
        command, cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert report.stdout.startswith(module.NO_TRACKER_PREFIX)
