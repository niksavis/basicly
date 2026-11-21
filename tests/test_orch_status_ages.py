from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests.test_orch_status import at, never_dead, saved, workspace

status = importlib.import_module("orch_state.status")


@pytest.mark.parametrize(
    ("minutes", "stored", "shown"),
    [
        (29, "idle", "quiet"),
        (31, "idle", "nudge"),
        (45, "working", "nudge"),
        (121, "idle", "stale"),
    ],
)
def test_a_wait_over_thirty_minutes_is_a_nudge_and_over_two_hours_is_stale(
    tmp_path: Path, minutes: float, stored: str, shown: str
) -> None:
    base, _repo = workspace(tmp_path)
    record = saved(base, "s1", 0, name="dev", state=stored)

    assert status.state(record, at(minutes), never_dead) == shown


def test_dead_sessions_are_folded_into_one_count_line(tmp_path: Path) -> None:
    base, _repo = workspace(tmp_path)
    live = saved(base, "s1", 0, name="dev", state="working", pid=1, pid_start=1)
    gone = [saved(base, f"s{n}", 0, name=f"old{n}", pid=n, pid_start=n) for n in (2, 3)]

    lines = status.render([live, *gone], [], at(1), lambda pid, _b: pid == 1)

    assert len(lines) == 2
    assert lines[0].startswith("dev")
    assert lines[1] == "2 dead session(s) not shown"
