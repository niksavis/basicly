from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "discard-guard.py"
)

REFUSED = [
    "git checkout -- src/app.py",
    "git checkout HEAD -- src/app.py",
    "git restore src/app.py",
    "git restore --staged --worktree src/app.py",
    "git stash",
    "git stash push -m keep",
    "git stash pop",
    "git stash drop",
    "git reset --hard",
    "git reset --hard origin/main",
    "git clean -fd",
    "git clean -f",
    "cd lane && git checkout HEAD -- src/app.py",
    "git -C ../lane restore .",
    "git status; git stash",
]

ALLOWED = [
    "git restore --staged src/app.py",
    "git restore -S src/app.py",
    "git stash list",
    "git stash show -p",
    "git checkout main",
    "git checkout -b feature",
    "git reset --soft HEAD~1",
    "git reset src/app.py",
    "git clean -n",
    "git clean --dry-run -d",
    "echo 'git reset --hard' > note.txt",
    "rg -n 'git stash' docs",
]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("discard_guard_hook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("command", REFUSED)
def test_a_command_that_discards_work_is_refused(command: str) -> None:
    assert _load().discarding(command), command


@pytest.mark.parametrize("command", ALLOWED)
def test_a_command_that_keeps_work_is_allowed(command: str) -> None:
    assert _load().discarding(command) == [], command


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_refusal_names_the_command_and_the_remedy() -> None:
    done = _run({"tool_name": "Bash", "tool_input": {"command": "git checkout HEAD -- a.py"}})

    assert done.returncode == 2
    assert "git checkout HEAD -- a.py" in done.stderr
    assert "ask-first item" in done.stderr
    assert "editing the file back" in done.stderr


def test_another_tool_passes() -> None:
    assert _run({"tool_name": "Read", "tool_input": {"command": "git stash"}}).returncode == 0


NOW = datetime(2026, 11, 19, 16, 0, tzinfo=UTC)


def _stamp(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%MZ")


def test_a_named_approval_under_an_hour_old_lets_the_command_through() -> None:
    command = f"BASICLY_APPROVAL=dev-j6oqo@{_stamp(10)} git stash"

    assert _load().discarding(command, NOW) == [
        ("BASICLY_APPROVAL=dev-j6oqo@" + _stamp(10) + " git stash", "dev-j6oqo@" + _stamp(10))
    ]


@pytest.mark.parametrize("minutes_ago", [61, -30])
def test_an_expired_or_future_approval_is_refused(minutes_ago: int) -> None:
    command = f"BASICLY_APPROVAL=dev-j6oqo@{_stamp(minutes_ago)} git stash"

    assert _load().discarding(command, NOW)[0][1] is None


def test_an_approval_names_only_its_own_segment() -> None:
    command = f"BASICLY_APPROVAL=dev-j6oqo@{_stamp(5)} git stash list; git reset --hard"

    assert [approval for _, approval in _load().discarding(command, NOW)] == [None]


def test_an_accepted_use_is_logged_and_passes(tmp_path: Path) -> None:
    (tmp_path / ".basicly").mkdir()
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    command = f"BASICLY_APPROVAL=dev-j6oqo@{stamp} git reset --hard"

    done = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})

    assert done.returncode == 0, done.stderr
    logged = (tmp_path / ".basicly/usage/approvals.jsonl").read_text(encoding="utf-8")
    assert f"dev-j6oqo@{stamp}" in logged
    assert "git reset --hard" in logged


def test_an_accepted_use_without_a_place_to_log_is_refused(tmp_path: Path) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    command = f"BASICLY_APPROVAL=dev-j6oqo@{stamp} git stash"

    done = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})

    assert done.returncode == 2
