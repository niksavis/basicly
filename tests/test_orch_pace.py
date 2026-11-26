from __future__ import annotations

import argparse
import importlib
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "orch"
sys.path.insert(0, str(PACKAGE))

snapshot = importlib.import_module("orch_pace.snapshot")
plan = importlib.import_module("orch_pace.plan")
commands = importlib.import_module("orch_pace.commands")
record = importlib.import_module("orch_relay.record")
log = importlib.import_module("orch_lessons.log")

NOW = 1_800_000_000
FIVE_RESET = NOW + 2 * 3600
SEVEN_RESET = NOW + 3 * 24 * 3600
GLYPHS = b"model \xe2\x96\x88\xe2\x96\x91 42%\n"


def status(five: float = 40.0, seven: float = 20.0) -> dict[str, object]:
    return {
        "model": {"display_name": "Opus"},
        "rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": FIVE_RESET},
            "seven_day": {"used_percentage": seven, "resets_at": SEVEN_RESET},
        },
    }


def tap(stdin: bytes, env: dict[str, str], *command: str) -> subprocess.CompletedProcess[bytes]:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from orch_pace.tap import main; raise SystemExit(main(sys.argv[2:]))"
    )
    return subprocess.run(  # nosec B603
        [sys.executable, "-I", "-c", code, str(PACKAGE), *command],
        input=stdin,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", **env},
        timeout=20,
        check=False,
    )


def echo_child(tmp_path: Path, exit_code: int = 0) -> list[str]:
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\n"
        "data = sys.stdin.buffer.read()\n"
        f"sys.stdout.buffer.write({GLYPHS!r} + str(len(data)).encode())\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return ["--", sys.executable, str(script)]


def test_the_tap_passes_stdin_through_and_writes_the_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "state" / "statusline.json"
    stdin = json.dumps(status()).encode()
    done = tap(stdin, {snapshot.DIR_ENV: str(target.parent)}, *echo_child(tmp_path, 3))
    assert done.returncode == 3
    assert done.stdout == GLYPHS + str(len(stdin)).encode()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["format"] == snapshot.FORMAT
    assert written["version"] == 1
    assert written["writer"] == "statusline"
    assert written["windows"]["five_hour"]["used_percentage"] == 40.0
    assert written["windows"]["seven_day"]["resets_at"] == SEVEN_RESET
    assert list(target.parent.glob(".usage-snapshot.*")) == []


@pytest.mark.parametrize(
    "stdin",
    [b"", b"{not json", json.dumps({"model": {}}).encode(), b"\xff\xfe"],
    ids=["empty", "invalid-json", "no-rate-limits", "not-utf8"],
)
def test_the_tap_prints_the_output_when_the_input_has_no_usage(
    tmp_path: Path, stdin: bytes
) -> None:
    done = tap(stdin, {snapshot.DIR_ENV: str(tmp_path / "s")}, *echo_child(tmp_path))
    assert done.returncode == 0
    assert done.stdout == GLYPHS + str(len(stdin)).encode()
    assert not (tmp_path / "s").exists()


def test_the_tap_prints_the_output_when_the_snapshot_cannot_be_written(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    stdin = json.dumps(status()).encode()
    env = {snapshot.DIR_ENV: str(blocker / "snapshots")}
    done = tap(stdin, env, *echo_child(tmp_path))
    assert done.returncode == 0
    assert done.stdout == GLYPHS + str(len(stdin)).encode()


def test_the_tap_names_a_command_that_cannot_start(tmp_path: Path) -> None:
    target = tmp_path / "statusline.json"
    stdin = json.dumps(status()).encode()
    done = tap(stdin, {snapshot.DIR_ENV: str(tmp_path)}, "--", str(tmp_path / "missing"))
    assert done.returncode == 127
    assert b"cannot start" in done.stderr
    assert b"orch-statusline-tap -- COMMAND" in done.stderr
    assert target.is_file()


def test_the_tap_refuses_no_command_by_name(tmp_path: Path) -> None:
    done = tap(b"{}", {snapshot.DIR_ENV: str(tmp_path)}, "--")
    assert done.returncode == 2
    assert b"no status line command given" in done.stderr


def test_the_snapshot_folder_follows_the_documented_order(tmp_path: Path) -> None:
    named = tmp_path / "named"
    tail = Path("basicly") / "usage-snapshots"
    assert snapshot.folder({snapshot.DIR_ENV: str(named)}, "linux", tmp_path) == named
    xdg = {"XDG_STATE_HOME": str(tmp_path / "x")}
    assert snapshot.folder(xdg, "linux", tmp_path) == tmp_path / "x" / tail
    relative = {"XDG_STATE_HOME": "relative"}
    assert snapshot.folder(relative, "linux", tmp_path) == tmp_path / ".local" / "state" / tail
    local = {"LOCALAPPDATA": str(tmp_path / "L")}
    assert snapshot.folder(local, "win32", tmp_path) == tmp_path / "L" / tail
    with pytest.raises(ValueError, match="writer name"):
        snapshot.own_file({snapshot.DIR_ENV: str(named)}, "../x")


def merged(old: object, five: float, at: int, resets: int = FIVE_RESET) -> object:
    return snapshot.merge(old, {"five_hour": (five, resets)}, at, "test")


def five(document: object) -> dict[str, object]:
    return snapshot.windows(document)["five_hour"]


def test_an_older_writer_cannot_lower_the_percent_or_refresh_the_reading() -> None:
    first = merged(None, 50.0, NOW)
    assert merged(first, 45.0, NOW + 120) is None
    assert merged(first, 50.0, NOW + 120, resets=FIVE_RESET - 5 * 3600) is None
    assert five(first)["read_at"] == NOW


def test_a_reading_after_its_reset_time_is_dropped() -> None:
    assert merged(None, 10.0, FIVE_RESET) is None


def test_readings_keep_one_minute_steps_and_restart_at_a_new_window() -> None:
    doc = merged(None, 10.0, NOW)
    doc = merged(doc, 11.0, NOW + 30)
    doc = merged(doc, 12.0, NOW + 50)
    assert five(doc)["readings"] == [[NOW, 10.0], [NOW + 50, 12.0]]
    doc = merged(doc, 13.0, NOW + 70)
    assert five(doc)["readings"] == [[NOW, 10.0], [NOW + 50, 12.0], [NOW + 70, 13.0]]
    doc = merged(doc, 2.0, NOW + 200, resets=FIVE_RESET + 5 * 3600)
    assert five(doc)["readings"] == [[NOW + 200, 2.0]]


def test_a_window_missing_from_the_input_keeps_its_old_value() -> None:
    both = {"five_hour": (10.0, FIVE_RESET), "seven_day": (5.0, SEVEN_RESET)}
    doc = merged(snapshot.merge(None, both, NOW, "test"), 12.0, NOW + 100)
    assert snapshot.windows(doc)["seven_day"]["used_percentage"] == 5.0


def window(used: float, readings: list[list[float]], resets: int = FIVE_RESET) -> dict[str, object]:
    return {
        "used_percentage": used,
        "resets_at": resets,
        "read_at": readings[-1][0],
        "readings": readings,
    }


@pytest.mark.parametrize(
    ("start", "end", "advice"),
    [
        (30.0, 35.0, "start"),
        (40.0, 50.0, "hold"),
        (40.0, 70.0, "close idle"),
        (95.0, 95.0, "close idle"),
    ],
)
def test_the_projection_at_reset_picks_the_advice(start: float, end: float, advice: str) -> None:
    found = plan.pace("five_hour", window(end, [[NOW - 1800, start], [NOW, end]]), NOW)
    assert found.basis == plan.RECENT
    assert found.rate == pytest.approx((end - start) * 2)
    assert found.projected == pytest.approx(end + (end - start) * 4)
    assert found.advice == advice


def test_few_readings_fall_back_to_the_window_average() -> None:
    found = plan.pace("five_hour", window(30.0, [[NOW, 30.0]]), NOW)
    assert found.basis == plan.AVERAGE
    assert found.rate == pytest.approx(10.0)
    assert found.projected == pytest.approx(50.0)
    assert found.advice == "start"


def test_the_seven_day_window_uses_its_average_and_caps_the_advice() -> None:
    seven = window(60.0, [[NOW - 7200, 10.0], [NOW, 60.0]], resets=SEVEN_RESET)
    found = plan.pace("seven_day", seven, NOW)
    assert found.basis == plan.AVERAGE
    assert found.rate == pytest.approx(60.0 / (4 * 24))
    assert found.advice == "close idle"
    calm = plan.pace("five_hour", window(10.0, [[NOW - 1800, 9.0], [NOW, 10.0]]), NOW)
    assert calm.advice == "start"
    assert plan.overall([calm, found]) == "close idle"


def write_snapshot(target: Path, five_window: dict[str, object]) -> None:
    document = {
        "format": snapshot.FORMAT,
        "version": 1,
        "writer": target.stem,
        "written_at": NOW,
        "windows": {"five_hour": five_window},
    }
    snapshot.write(target, document)


def test_orch_pace_prints_both_windows_and_the_advice(tmp_path: Path) -> None:
    env = {snapshot.DIR_ENV: str(tmp_path)}
    assert snapshot.record(json.dumps(status(five=30.0)).encode(), env, NOW - 1800, "test")
    assert snapshot.record(json.dumps(status(five=35.0)).encode(), env, NOW, "test")
    args = argparse.Namespace(group="pace", action=None)
    shown = commands.run(args, tmp_path, env, lambda: NOW)
    assert "five_hour: used 35.0%" in shown
    assert "burn 10.0%/h (last 60 min), at reset 55.0%" in shown
    assert "seven_day: used 20.0%" in shown
    assert shown.endswith("advice: start")


def test_orch_pace_refuses_a_missing_stale_or_foreign_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "statusline.json"
    env = {snapshot.DIR_ENV: str(tmp_path)}
    args = argparse.Namespace(group="pace", action=None)
    with pytest.raises(log.RefusedError, match="no usage snapshot in"):
        commands.run(args, tmp_path, env, lambda: NOW)
    write_snapshot(target, window(30.0, [[NOW - 601, 30.0]]))
    with pytest.raises(log.RefusedError, match=r"statusline\.json: stale"):
        commands.run(args, tmp_path, env, lambda: NOW)
    target.write_text(json.dumps({"format": snapshot.FORMAT, "version": 2}), encoding="utf-8")
    with pytest.raises(log.RefusedError, match="version 2; this orch reads version 1"):
        commands.run(args, tmp_path, env, lambda: NOW)


def test_orch_pace_skips_a_torn_file_and_reads_the_valid_one(tmp_path: Path) -> None:
    write_snapshot(tmp_path / "statusline.json", window(30.0, [[NOW - 60, 30.0]]))
    torn = (tmp_path / "statusline.json").read_text(encoding="utf-8")
    (tmp_path / "handily-board.json").write_text(torn[: len(torn) // 2], encoding="utf-8")
    args = argparse.Namespace(group="pace", action=None)
    shown = commands.run(args, tmp_path, {snapshot.DIR_ENV: str(tmp_path)}, lambda: NOW)
    assert "fresh from: statusline" in shown
    assert "five_hour: used 30.0%" in shown


def test_orch_pace_takes_the_highest_fresh_reading_of_all_writers(tmp_path: Path) -> None:
    write_snapshot(tmp_path / "statusline.json", window(30.0, [[NOW - 60, 30.0]]))
    write_snapshot(tmp_path / "handily-board.json", window(34.0, [[NOW - 120, 34.0]]))
    write_snapshot(tmp_path / "old.json", window(90.0, [[NOW - 900, 90.0]]))
    args = argparse.Namespace(group="pace", action=None)
    shown = commands.run(args, tmp_path, {snapshot.DIR_ENV: str(tmp_path)}, lambda: NOW)
    assert "fresh from: handily-board, statusline" in shown
    assert "five_hour: used 34.0%" in shown


def test_a_young_window_projects_only_its_used_percent() -> None:
    young = plan.pace("five_hour", window(4.0, [[NOW, 4.0]], resets=NOW + 5 * 3600 - 660), NOW)
    assert young.rate is None
    assert young.projected == pytest.approx(4.0)
    assert young.advice == "start"
    week = window(16.0, [[NOW, 16.0]], resets=NOW + 7 * 24 * 3600 - 8 * 3600)
    early = plan.pace("seven_day", week, NOW)
    assert early.rate is None
    assert early.advice == "start"


class FakeLedger:
    def __init__(self) -> None:
        self.notes: list[str] = []

    def __call__(self, argv: list[str]) -> str:
        if argv[2] == "comment":
            self.notes.append(argv[-1])
            return "{}"
        entries = [{"text": text} for text in self.notes]
        return json.dumps({"comment_log": entries, "fields": {"issue_type": "task"}})


def tracker(tmp_path: Path, ledger: FakeLedger) -> object:
    cli = tmp_path / record.TRACKER_CLI
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("", encoding="utf-8")
    return record.Tracker(tmp_path, ledger)


def test_orch_pace_note_records_the_percent_change_of_an_item(tmp_path: Path) -> None:
    target = tmp_path / "statusline.json"
    env = {snapshot.DIR_ENV: str(tmp_path)}
    ledger = FakeLedger()
    found = tracker(tmp_path, ledger)
    write_snapshot(target, window(30.0, [[NOW, 30.0]]))
    start = argparse.Namespace(group="pace", action="note", record="x-1", moment="start")
    commands.note(start, tmp_path, env, NOW, found)
    write_snapshot(target, window(42.5, [[NOW + 600, 42.5]]))
    done = argparse.Namespace(group="pace", action="note", record="x-1", moment="done")
    commands.note(done, tmp_path, env, NOW + 600, found)
    assert ledger.notes == [
        f"[pace start] five_hour=30.0 resets_at={FIVE_RESET}",
        f"[pace done] five_hour=42.5 resets_at={FIVE_RESET} change=12.5 type=task",
    ]
    later = window(5.0, [[NOW + 900, 5.0]], resets=FIVE_RESET + 5 * 3600)
    write_snapshot(target, later)
    commands.note(done, tmp_path, env, NOW + 900, found)
    assert ledger.notes[-1].endswith("change=unknown_crossed_reset type=task")


def test_orch_pace_note_done_without_a_start_is_refused(tmp_path: Path) -> None:
    write_snapshot(tmp_path / "statusline.json", window(30.0, [[NOW, 30.0]]))
    done = argparse.Namespace(group="pace", action="note", record="x-2", moment="done")
    env = {snapshot.DIR_ENV: str(tmp_path)}
    with pytest.raises(log.RefusedError, match="orch pace note x-2 start"):
        commands.note(done, tmp_path, env, NOW, tracker(tmp_path, FakeLedger()))
