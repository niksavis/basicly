from __future__ import annotations

import importlib
import json
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "packages" / "basicly-orch"
sys.path.insert(0, str(PACKAGE))

herdr = importlib.import_module("orch_state.herdr")
reporter = importlib.import_module("orch_state.reporter")

PANE = "w9:pT"


def fake_herdr(tmp_path: Path, body: str = "") -> tuple[Path, Path]:
    log = tmp_path / "calls.jsonl"
    script = tmp_path / "herdr"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys, time\n"
        f"{body}\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


def environ(script: Path | None, **extra: str) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "HERDR_ENV": "1", "HERDR_PANE_ID": PANE}
    if script is not None:
        env["HERDR_BIN_PATH"] = str(script)
    env.update(extra)
    return env


def hook(stdin: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from orch_state.reporter import main; raise SystemExit(main())"
    )
    return subprocess.run(  # nosec B603
        [sys.executable, "-I", "-c", code, str(PACKAGE)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    )


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def event(name: str, **fields: object) -> str:
    return json.dumps({"hook_event_name": name, **fields})


def test_argv_puts_the_pane_id_first_and_names_the_source_and_seq() -> None:
    command = herdr.argv("herdr", PANE, {"blocked": "needs you: Bash git"}, 600_000, seq=7)
    assert command == [
        "herdr",
        "pane",
        "report-metadata",
        PANE,
        "--source",
        "basicly-orch",
        "--state-label",
        "blocked=needs you: Bash git",
        "--ttl-ms",
        "600000",
        "--seq",
        "7",
    ]


def test_argv_clears_the_labels_when_none_are_given() -> None:
    assert herdr.argv("herdr", PANE, None, None, seq=1)[4:] == [
        "--source",
        "basicly-orch",
        "--clear-state-labels",
        "--seq",
        "1",
    ]


def test_argv_seq_rises_between_two_calls() -> None:
    first = int(herdr.argv("h", PANE, None, None)[-1])
    second = int(herdr.argv("h", PANE, None, None)[-1])
    assert second > first


def test_label_holds_the_tool_and_the_first_word_only() -> None:
    action = reporter.plan(
        json.loads(
            event(
                "PermissionRequest",
                tool_name="Bash",
                tool_input={"command": "git push origin main --token secret"},
            )
        )
    )
    assert action is not None
    assert action.labels == {"blocked": "needs you: Bash git"}


def test_label_drops_control_characters_and_stops_at_80_characters() -> None:
    text = herdr.clean("a\x1b[31mb\tc\n" + "x" * 200)
    assert "\x1b" not in text and "\n" not in text and "\t" not in text
    assert len(text) == herdr.LABEL_MAX


def test_label_names_no_path_for_a_tool_without_a_command() -> None:
    action = reporter.plan(
        json.loads(
            event(
                "PermissionRequest",
                tool_name="Edit",
                tool_input={"file_path": "/private/x.py"},
            )
        )
    )
    assert action is not None
    assert action.labels == {"blocked": "needs you: Edit"}


def test_label_paused_counts_background_work_at_stop() -> None:
    action = reporter.plan(
        json.loads(
            event(
                "Stop",
                background_tasks=[{"type": "shell"}, {"type": "monitor"}],
                session_crons=[],
            )
        )
    )
    assert action is not None
    assert action.labels == {"idle": "paused: 2 background", "done": "paused: 2 background"}
    assert action.ttl_ms == reporter.PAUSED_TTL_MS


@pytest.mark.parametrize("name", ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"])
def test_label_is_cleared_on_the_clearing_events(name: str) -> None:
    assert reporter.plan(json.loads(event(name))) == reporter.CLEAR


def test_label_is_left_alone_on_an_unknown_event() -> None:
    assert reporter.plan(json.loads(event("Notification"))) is None


def test_stdout_stays_empty_on_permission_request(tmp_path: Path) -> None:
    script, log = fake_herdr(tmp_path)
    done = hook(
        event("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}), environ(script)
    )
    assert done.returncode == 0
    assert done.stdout == ""
    assert calls(log)[0][:4] == ["pane", "report-metadata", PANE, "--source"]


def test_fault_herdr_missing_exits_zero(tmp_path: Path) -> None:
    done = hook(event("Stop"), environ(tmp_path / "absent", PATH=str(tmp_path)))
    assert (done.returncode, done.stdout) == (0, "")


def test_fault_herdr_hanging_is_cut_off_within_the_timeout(tmp_path: Path) -> None:
    script, _ = fake_herdr(tmp_path, "time.sleep(5)")
    start = time.monotonic()
    done = hook(event("Stop"), environ(script))
    assert (done.returncode, done.stdout) == (0, "")
    assert time.monotonic() - start < 3


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '{"hook_event_name": 3}'])
def test_fault_bad_input_exits_zero_and_reports_nothing(tmp_path: Path, stdin: str) -> None:
    script, log = fake_herdr(tmp_path)
    done = hook(stdin, environ(script))
    assert (done.returncode, done.stdout) == (0, "")
    assert calls(log) == []


@pytest.mark.parametrize("pane", ["", "w2:p;rm", "../x", "w2 pP", "-x:y"])
def test_fault_bad_pane_id_reports_nothing(tmp_path: Path, pane: str) -> None:
    script, log = fake_herdr(tmp_path)
    done = hook(event("Stop"), environ(script, HERDR_PANE_ID=pane))
    assert (done.returncode, done.stdout) == (0, "")
    assert calls(log) == []


def test_no_herdr_env_runs_no_process(tmp_path: Path) -> None:
    script, log = fake_herdr(tmp_path)
    env = environ(script)
    del env["HERDR_ENV"]
    done = hook(event("PermissionRequest", tool_name="Bash"), env)
    assert (done.returncode, done.stdout) == (0, "")
    assert calls(log) == []
