from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "orch"
sys.path.insert(0, str(PACKAGE))

guard = importlib.import_module("orch_state.relay_guard")
store = importlib.import_module("orch_state.store")
doctor = importlib.import_module("orch_lessons.doctor")
log = importlib.import_module("orch_lessons.log")
messages = importlib.import_module("orch_relay.messages")
cli = importlib.import_module("orch_lessons.cli")
start_steps = importlib.import_module("orch_state.start_steps")
reporter = importlib.import_module("orch_state.reporter")

ORCH_ID = "0a1b2c3d-orchestrator-session"
WORKER_ID = "9f8e7d6c-worker-session"


def _clock() -> datetime:
    return datetime(2026, 11, 19, 20, 30, tzinfo=UTC)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / ".orchestrator"
    (root / "lessons").mkdir(parents=True)
    (root / "lessons" / log.MARKER).write_text("", encoding="utf-8")
    sessions = root / store.SESSIONS
    sessions.mkdir()
    for session, name in ((ORCH_ID, "Orchestrator"), (WORKER_ID, "dev")):
        (sessions / f"{session}.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    return tmp_path


def _calls(workspace: Path) -> list[dict[str, object]]:
    path = workspace / ".orchestrator" / guard.CALLS
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_orchestrator_session_is_accepted_and_logged(workspace: Path) -> None:
    line = guard.check("approve", workspace, {guard.HARNESS_ENV: ORCH_ID}, _clock)

    assert line == f"Session: Orchestrator ({ORCH_ID[:8]})"
    assert _calls(workspace)[-1] == {
        "accepted": True,
        "at": "2026-11-19T20:30:00+00:00",
        "group": "approve",
        "name": "Orchestrator",
        "session": ORCH_ID,
    }


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {guard.HARNESS_ENV: WORKER_ID},
        {guard.HARNESS_ENV: "unknown-session"},
    ],
)
def test_any_other_caller_is_refused_and_logged(workspace: Path, environ: dict[str, str]) -> None:
    with pytest.raises(log.RefusedError, match="runs only in the session named Orchestrator"):
        guard.check("decide", workspace, environ, _clock)

    last = _calls(workspace)[-1]
    assert last["accepted"] is False
    assert last["group"] == "decide"


def test_an_orch_session_id_alone_is_ignored(workspace: Path) -> None:
    with pytest.raises(log.RefusedError, match="an unregistered session"):
        guard.check("approve", workspace, {"ORCH_SESSION_ID": ORCH_ID}, _clock)


def test_orch_state_set_cannot_claim_the_orchestrator_name(workspace: Path) -> None:
    commands = importlib.import_module("orch_state.commands")
    args = argparse.Namespace(
        name="orchestrator", now=None, next=None, waiting=None, state=None, session=None
    )

    with pytest.raises(log.RefusedError, match="cannot give or change the name Orchestrator"):
        commands.set_state(args, workspace, {guard.HARNESS_ENV: WORKER_ID}, _clock)


def test_a_hook_event_names_the_orchestrator_only_at_session_start(workspace: Path) -> None:
    reporter = importlib.import_module("orch_state.reporter")
    root = workspace / ".orchestrator"
    base = {"session_id": "new-session", "cwd": str(workspace), "session_title": "Orchestrator"}

    reporter.write_state({**base, "hook_event_name": "UserPromptSubmit"}, {}, _clock)
    assert store.read(store.session_path(root, "new-session")).get("name") is None

    reporter.write_state({**base, "hook_event_name": "SessionStart"}, {}, _clock)
    assert store.read(store.session_path(root, "new-session")).get("name") == "Orchestrator"


def test_no_store_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(log.RefusedError, match="needs the orchestrator store"):
        guard.check("approve", tmp_path, {guard.HARNESS_ENV: ORCH_ID}, _clock)


class FakeTracker:
    def __init__(self, entries: dict[str, list[str]]) -> None:
        self.entries = entries

    def matching(self, query: str) -> list[str]:
        return [name for name, texts in self.entries.items() if any(query in t for t in texts)]

    def comments(self, record: str) -> list[str]:
        return self.entries[record]


def test_doctor_reports_an_entry_not_written_by_the_orchestrator(workspace: Path) -> None:
    good = f"[approval 2026-11-19T20:00:00+00:00] relayed\nSession: Orchestrator ({ORCH_ID[:8]})"
    forged = "[decision 2026-11-19T20:10:00+00:00] decided\nRecord: dev-1"
    tracker = FakeTracker({"dev-1": [good, forged, "an ordinary comment"]})

    lines, failed = doctor.relay_lines(workspace, {}, tracker)

    assert failed
    assert lines[0] == "relay entries: 2 approval or decision entries checked"
    assert lines[1:] == [
        "relay entries: dev-1: [decision 2026-11-19T20:10:00+00:00] decided "
        "was not written by the Orchestrator session"
    ]


def test_doctor_passes_when_every_entry_carries_the_orchestrator_session(
    workspace: Path,
) -> None:
    good = f"[decision 2026-11-19T20:00:00+00:00] decided\nSession: Orchestrator ({ORCH_ID[:8]})"

    lines, failed = doctor.relay_lines(workspace, {}, FakeTracker({"dev-1": [good]}))

    assert not failed
    assert lines == ["relay entries: 1 approval or decision entries checked"]


def test_the_relayed_text_carries_the_session_line() -> None:
    written: list[tuple[str, str]] = []

    class Recorder:
        def comment(self, record: str, text: str) -> None:
            written.append((record, text))

    line = "Session: Orchestrator (0a1b2c3d)"
    tracker: Any = Recorder()
    writer = messages.Writer(tracker, lambda _text: None, _clock, line)
    act = messages.Action("deploy", "dev-1", "basicly", "publish the basicly pages")

    writer.approve(act, "go")

    assert written[0][1].splitlines()[1] == "Session: Orchestrator (0a1b2c3d)"


def test_orch_approve_from_a_worker_is_refused_before_any_tracker_write(workspace: Path) -> None:
    argv = [
        "approve",
        "--item",
        "deploy",
        "--record",
        "dev-1",
        "--repo",
        "basicly",
        "--action",
        "publish the basicly pages",
        "--words",
        "go",
    ]

    with pytest.raises(log.RefusedError, match="runs only in the session named Orchestrator"):
        cli.run(argv, workspace, {guard.HARNESS_ENV: WORKER_ID})

    assert _calls(workspace)[-1]["name"] == "dev"


def _live(pid: int, _began: int) -> bool:
    return pid == 4242


def test_a_forged_session_start_cannot_add_a_second_live_orchestrator(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter = importlib.import_module("orch_state.reporter")
    root = workspace / ".orchestrator"
    orchestrator = store.session_path(root, ORCH_ID)
    orchestrator.write_text(
        json.dumps({"name": "Orchestrator", "pid": 4242, "pid_start": 7}), encoding="utf-8"
    )
    monkeypatch.setattr(store, "alive", _live)
    event = {
        "hook_event_name": "SessionStart",
        "session_title": "Orchestrator",
        "session_id": WORKER_ID,
        "cwd": str(workspace),
    }

    reporter.write_state(event, {}, _clock)

    assert store.read(store.session_path(root, WORKER_ID)).get("name") == "dev"
    assert _calls(workspace)[-1]["reason"] == "second Orchestrator refused"


def test_doctor_reports_two_live_orchestrators(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace / ".orchestrator"
    for session in (ORCH_ID, WORKER_ID):
        store.session_path(root, session).write_text(
            json.dumps({"name": "Orchestrator", "pid": 4242, "pid_start": 7}), encoding="utf-8"
        )
    monkeypatch.setattr(store, "alive", _live)

    lines, failed = doctor.relay_lines(workspace, {}, FakeTracker({}))

    assert failed
    assert lines[1].startswith("relay entries: 2 live sessions are named Orchestrator")


ORCH_ENV = {guard.HARNESS_ENV: ORCH_ID}
OPEN_GROUPS = ["status", "state", "record", "accept", "brief", "lesson", "lessons", "doctor"]


@pytest.mark.parametrize("group", OPEN_GROUPS)
def test_an_orchestrator_before_its_start_steps_keeps_every_read_and_brief(
    workspace: Path, group: str
) -> None:
    start_steps.gate(group, workspace, ORCH_ENV)


def test_orch_status_runs_for_an_orchestrator_before_its_start_steps(workspace: Path) -> None:
    assert cli.run(["status"], workspace, ORCH_ENV) == 0


@pytest.mark.parametrize("group", sorted(start_steps.GATED))
def test_an_orchestrator_before_orch_brief_is_refused_by_name(workspace: Path, group: str) -> None:
    with pytest.raises(
        log.RefusedError, match=r"Run orch brief first.*orch status, orch state set"
    ):
        start_steps.gate(group, workspace, ORCH_ENV)
    start_steps.gate(group, workspace, {guard.HARNESS_ENV: WORKER_ID})
    start_steps.gate(group, workspace, {})


def test_orch_send_is_refused_before_orch_brief_and_runs_after_it(workspace: Path) -> None:
    argv = ["send", "--order", "--to", "dev", "--record", "dev-1"]
    argv += ["--goal", "ship", "--limits", "no push", "--proof", "tests pass"]
    with pytest.raises(log.RefusedError, match=r"orch send refuses: this Orchestrator session"):
        cli.run(argv, workspace, ORCH_ENV)
    start_steps.mark(workspace, ORCH_ENV, _clock)
    assert cli.run(argv, workspace, ORCH_ENV) == 0


def test_a_new_session_start_asks_for_orch_brief_again(workspace: Path) -> None:
    start_steps.mark(workspace, ORCH_ENV, _clock)
    start_steps.gate("decide", workspace, ORCH_ENV)

    def later() -> datetime:
        return datetime(2026, 11, 19, 21, 0, tzinfo=UTC)

    event = {"hook_event_name": "SessionStart", "session_id": ORCH_ID, "cwd": str(workspace)}
    reporter.write_state(event, {}, later)
    with pytest.raises(log.RefusedError, match=r"Run orch brief first"):
        start_steps.gate("decide", workspace, ORCH_ENV)


def test_each_owner_turn_names_the_missing_start_steps_until_orch_brief(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    turn = {"hook_event_name": "UserPromptSubmit", "cwd": str(workspace)}
    reporter.remind({**turn, "session_id": ORCH_ID}, {})
    assert capsys.readouterr().out == start_steps.PROMPT_LINE + "\n"
    reporter.remind({**turn, "session_id": WORKER_ID}, {})
    assert capsys.readouterr().out == ""
    start_steps.mark(workspace, ORCH_ENV, _clock)
    reporter.remind({**turn, "session_id": ORCH_ID}, {})
    assert capsys.readouterr().out == ""
