from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "orch"
sys.path.insert(0, str(PACKAGE))

store = importlib.import_module("orch_state.store")
status = importlib.import_module("orch_state.status")
reporter = importlib.import_module("orch_state.reporter")
cli = importlib.import_module("orch_lessons.cli")
commands = importlib.import_module("orch_state.commands")
log = importlib.import_module("orch_lessons.log")

NOTICE = {"hook_event_name": "Notification", "notification_type": "permission_prompt"}
T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def at(minutes: float = 0) -> datetime:
    return T0 + timedelta(minutes=minutes)


def workspace(tmp_path: Path) -> tuple[Path, Path]:
    lessons = tmp_path / ".orchestrator" / "lessons"
    lessons.mkdir(parents=True)
    (lessons / log.MARKER).write_text("", encoding="utf-8")
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)
    return tmp_path / ".orchestrator", repo


def never_dead(_pid: int, _began: int) -> bool | None:
    return True


def saved(base: Path, key: str, minutes: float, **changes: object) -> dict[str, object]:
    return store.save(base, key, changes, lambda: at(minutes))


def test_a_save_merges_fields_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    base, _repo = workspace(tmp_path)
    saved(base, "s1", 0, name="dev", now="rehearsal", now_at=at(0).isoformat())
    record = saved(base, "s1", 5, state="working")

    assert record["name"] == "dev"
    assert record["now"] == "rehearsal"
    assert record["updated_at"] == at(5).isoformat(timespec="seconds")
    assert sorted(p.name for p in (base / "sessions").iterdir()) == ["s1.json"]


@pytest.mark.parametrize(
    ("minutes", "stored", "alive", "shown"),
    [
        (1, "working", True, "working"),
        (20, "working", True, "quiet"),
        (5, "idle", True, "quiet"),
        (5, "needs-you", True, "needs you"),
        (130, "working", True, "stale"),
        (1, "working", False, "dead"),
        (1, "working", None, "working"),
    ],
)
def test_status_derives_the_five_states_at_read_time(
    tmp_path: Path, minutes: float, stored: str, alive: bool | None, shown: str
) -> None:
    base, _repo = workspace(tmp_path)
    record = saved(base, "s1", 0, name="dev", state=stored, pid=10, pid_start=20)

    assert status.state(record, at(minutes), lambda _p, _b: alive) == shown


@pytest.mark.parametrize(
    "name", ["dev", "a-very-long-session-name-for-a-lane", "会話の名前会話の名前"]
)
def test_every_row_fits_forty_columns_and_shows_its_age(tmp_path: Path, name: str) -> None:
    base, _repo = workspace(tmp_path)
    record = saved(
        base, "s1", 0, name=name, state="working", now="x" * 90, now_at=at(0).isoformat()
    )

    line = status.row(record, at(3), never_dead)

    assert status.width(line) <= status.WIDTH
    assert " 3m " in line


def test_an_old_now_is_never_printed_as_current(tmp_path: Path) -> None:
    base, _repo = workspace(tmp_path)
    saved(base, "s1", 0, name="dev", now="old work", now_at=at(0).isoformat())
    record = saved(base, "s1", 170, state="working")

    line = status.row(record, at(171), never_dead)

    assert "2h ago: old work" in line
    assert "working" in line


def test_the_first_line_counts_only_open_asks_younger_than_a_day(tmp_path: Path) -> None:
    base, _repo = workspace(tmp_path)
    store.ask(base, "r1", "Release now?", "go|wait", lambda: at(0))
    store.ask(base, "r2", "Cut the lane?", None, lambda: at(0))
    store.ask(base, "r3", "Old question?", None, lambda: at(-60 * 25))
    assert store.close_ask(base, "r2", "approved", lambda: at(1))

    lines = status.render([], store.asks(base), at(2), never_dead)

    assert lines == ["1 decision waits for you", "1 old ask(s) are stale (24 h)"]


def test_orch_state_set_then_status_shows_the_session(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", str(status.WIDTH))
    _base, repo = workspace(tmp_path)
    env = {"ORCH_SESSION_ID": "abc", "ORCH_MEMBER": "dev"}

    assert (
        cli.run(["state", "set", "--now", "builds the overview", "--state", "working"], repo, env)
        == 0
    )
    assert cli.run(["status"], repo, env) == 0

    shown = capsys.readouterr().out.splitlines()
    assert shown[0] == "state saved for dev"
    assert shown[1].startswith("dev       working")
    assert shown[1].endswith("builds the overv")
    assert status.width(shown[1]) == status.WIDTH


def test_orch_ask_opens_and_orch_approve_closes_the_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, repo = workspace(tmp_path)
    assert cli.run(["ask", "--record", "dev-1", "--question", "Ship it?"], repo, {}) == 0
    assert store.asks(base)[0]["record"] == "dev-1"

    assert cli.run(["ask", "--withdraw", "dev-1"], repo, {}) == 0
    assert store.asks(base)[0]["closed_by"] == "withdrawn"
    capsys.readouterr()


def test_state_set_without_a_store_names_the_remedy(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)

    with pytest.raises(log.RefusedError, match="orch lessons init"):
        cli.run(["state", "set", "--now", "x", "--session", "s"], repo, {})


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"hook_event_name": "SessionStart", "session_title": "dev"}, "idle"),
        ({"hook_event_name": "UserPromptSubmit"}, "working"),
        ({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, "needs-you"),
        (
            {"hook_event_name": "Notification", "notification_type": "permission_prompt"},
            "needs-you",
        ),
        ({"hook_event_name": "Stop"}, "idle"),
    ],
)
def test_hooks_write_the_session_state_without_herdr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], event: dict[str, str], expected: str
) -> None:
    base, repo = workspace(tmp_path)
    payload = {**event, "session_id": "sess-1", "cwd": str(repo)}

    reporter.run(json.dumps(payload), {"PATH": "/usr/bin:/bin"}, lambda: at(0))

    record = json.loads((base / "sessions" / "sess-1.json").read_text(encoding="utf-8"))
    assert record["state"] == expected
    assert record["project"] == "project"
    assert capsys.readouterr().out == ""


def test_an_idle_notification_and_a_missing_store_write_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)
    idle = {"hook_event_name": "Notification", "notification_type": "idle_prompt"}
    start = {"hook_event_name": "SessionStart"}

    for event in (idle, start):
        reporter.run(json.dumps({**event, "session_id": "s", "cwd": str(repo)}), {}, lambda: at(0))

    assert not (tmp_path / ".orchestrator").exists()


def test_a_report_updates_now_and_an_approval_closes_its_ask(tmp_path: Path) -> None:
    base, repo = workspace(tmp_path)
    env = {"ORCH_SESSION_ID": "orch-1"}
    store.ask(base, "dev-9", "Ship?", None, lambda: at(0))
    report = argparse.Namespace(group="report", state="blocked", record="dev-9", item=None)
    approve = argparse.Namespace(group="approve", state=None, record="dev-9", item="release")

    commands.after_relay(report, repo, env, lambda: at(1))
    commands.after_relay(approve, repo, env, lambda: at(2))

    record = store.read(base / "sessions" / "orch-1.json")
    assert record["now"] == "relayed approval dev-9"
    assert record["waiting_on"] == "orchestrator"
    assert store.asks(base)[0]["closed_by"] == "approved"


def test_every_file_carries_the_format_version_and_the_platform(tmp_path: Path) -> None:
    base, _repo = workspace(tmp_path)
    record = store.save(base, "s1", {"name": "dev"}, lambda: at(0), "linux")
    store.ask(base, "r1", "Ship?", None, lambda: at(0))
    store.close_ask(base, "r1", "approved", lambda: at(1))

    assert record["v"] == store.FORMAT
    assert record["platform"] == "linux"
    assert store.asks(base)[0]["v"] == store.FORMAT


@pytest.mark.parametrize(
    ("platform", "fields", "kept"),
    [
        ("linux", {"pid": 7, "pid_start": 9}, True),
        ("linux", {"pid": 7}, False),
        ("darwin", {"pid": 7, "pid_start": 9}, False),
        ("win32", {"pid": 7, "pid_start": 9}, False),
    ],
)
def test_pid_and_its_start_come_as_a_pair_and_only_on_linux(
    tmp_path: Path, platform: str, fields: dict[str, int], kept: bool
) -> None:
    base, _repo = workspace(tmp_path)

    record = store.save(base, "s1", dict(fields), lambda: at(0), platform)

    assert ("pid" in record and "pid_start" in record) is kept
    assert ("pid" in record) == ("pid_start" in record)
    assert record["platform"] == platform


def transcript(tmp_path: Path, *entries: dict[str, str]) -> Path:
    path = tmp_path / "t.jsonl"
    filler = json.dumps({"type": "assistant", "message": "x" * 200})
    lines = [*(json.dumps(e) for e in entries), *([filler] * 3)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_hook_fills_a_missing_name_from_the_transcript(tmp_path: Path) -> None:
    base, repo = workspace(tmp_path)
    path = transcript(
        tmp_path,
        {"type": "custom-title", "customTitle": "old", "sessionId": "s"},
        {"type": "custom-title", "customTitle": "basicly", "sessionId": "s"},
    )
    event = {"hook_event_name": "Stop", "session_id": "s", "cwd": str(repo)}

    reporter.run(json.dumps({**event, "transcript_path": str(path)}), {}, lambda: at(0))

    assert store.read(store.session_path(base, "s"))["name"] == "basicly"


def test_a_stored_name_is_kept_and_an_agent_name_is_the_fallback(tmp_path: Path) -> None:
    base, repo = workspace(tmp_path)
    path = transcript(tmp_path, {"type": "agent-name", "agentName": "repair", "sessionId": "s"})
    event = {"hook_event_name": "Stop", "session_id": "s", "cwd": str(repo)}
    payload = json.dumps({**event, "transcript_path": str(path)})

    reporter.run(payload, {}, lambda: at(0))
    store.save(base, "s", {"name": "kept"}, lambda: at(1))
    reporter.run(payload, {}, lambda: at(2))

    assert store.read(store.session_path(base, "s"))["name"] == "kept"


def test_a_missing_or_unreadable_transcript_leaves_no_name(tmp_path: Path) -> None:
    base, repo = workspace(tmp_path)
    event = {"hook_event_name": "Stop", "session_id": "s", "cwd": str(repo)}

    reporter.run(
        json.dumps({**event, "transcript_path": str(tmp_path / "none")}), {}, lambda: at(0)
    )

    assert "name" not in store.read(store.session_path(base, "s"))


def test_a_wide_terminal_shows_more_of_now_and_a_narrow_one_keeps_forty(tmp_path: Path) -> None:
    base, _repo = workspace(tmp_path)
    stamp = at(0).isoformat()
    record = saved(base, "s1", 0, name="dev", state="working", now="x" * 90, now_at=stamp)

    wide = status.row(record, at(1), never_dead, 100)
    narrow = status.row(record, at(1), never_dead, 20)

    assert status.width(wide) == 100
    assert status.width(narrow) == status.WIDTH
