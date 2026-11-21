from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import runner
from tests.test_runner import _claude_spec, _HungProc, _ready_tracker_memory, _streaming_spec

if TYPE_CHECKING:
    import pytest

__all__ = ["_ready_tracker_memory"]


def _notes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(
        runner.orch_note, "note", lambda _cwd, **kw: seen.append(kw["last"]) or True
    )
    return seen


def test_a_lane_past_its_runner_timeout_is_noted_and_never_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hung = _HungProc()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_a, **_k: hung)
    killed: list[int] = []
    monkeypatch.setattr(runner, "_kill_tree", lambda proc: killed.append(proc.pid))
    seen = _notes(monkeypatch)

    result = runner.run(_claude_spec(), "go", Path("/work"), timeout=1.0, keep_on_timeout=True)

    assert killed == []
    assert result.timed_out is False
    assert "partial" in result.stdout
    assert seen == ["stalled: claude ran past 1s"]


def test_a_quiet_streaming_lane_is_noted_and_runs_to_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _notes(monkeypatch)
    body = (
        "time.sleep(0.8)\n"
        "res = {'type':'result','result':'ok','usage':{'input_tokens':1,'output_tokens':0}}\n"
        "sys.stdout.write(json.dumps(res) + '\\n')\n"
    )

    result = runner.run(
        _streaming_spec(body),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=lambda _e: None,
        timeout=0.3,
        bounds=runner.DispatchBounds(quiet_after=0.2),
        keep_on_timeout=True,
    )

    assert result.stopped is None
    assert result.timed_out is False
    assert result.returncode == 0
    assert any("stalled" in note for note in seen)


def test_a_helper_with_no_timeout_still_gets_a_stall_note_and_keeps_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hung = _HungProc()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_a, **_k: hung)
    monkeypatch.setattr(runner, "DEFAULT_QUIET_AFTER", 0.2)
    killed: list[int] = []
    monkeypatch.setattr(runner, "_kill_tree", lambda proc: killed.append(proc.pid))
    seen = _notes(monkeypatch)

    result = runner.run(_claude_spec(), "go", Path("/work"), timeout=None, keep_on_timeout=True)

    assert killed == []
    assert result.timed_out is False
    assert seen == ["stalled: claude ran past 0.2s"]
