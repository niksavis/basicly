from __future__ import annotations

from pathlib import Path

import pytest

from basicly import tracker
from tests import flipped_tracker
from tests.tracker_process_fixture import recorded_review


def _ready(root: Path) -> None:
    flipped_tracker.flipped_repo(root)
    flipped_tracker.seed(
        root,
        "fx-1",
        title="complete the fixture",
        status="in_progress",
        assignee="fixture-holder",
        description=(
            "## Trigger\n\nWhen the fixture completes, I want admission checked, "
            "so I can preserve data before closing.\n"
        ),
        acceptance_criteria="When the fixture completes, its recorded result shall be confirmed.",
        requirements="Preserve actual confirmation before completion.",
    )
    recorded_review(tracker.kit(root, "commands"), tracker.ledger_dir(root), "fx-1", completed=True)
    tracker.write(root, ["update", "fx-1", "--status", "in_progress"])


def test_completion_admission_preserves_the_log_until_the_authoritative_close(
    tmp_path: Path,
) -> None:
    _ready(tmp_path)
    events = tracker.kit(tmp_path, "events")
    before = events.read_events(tracker.ledger_dir(tmp_path))[0]

    tracker.check_close(tmp_path, "fx-1", "fixture completed")
    tracker.check_close(tmp_path, "fx-1", "fixture completed")

    assert events.read_events(tracker.ledger_dir(tmp_path))[0] == before
    assert tracker.require_record(tmp_path, "fx-1")["status"] == "in_progress"
    tracker.write(tmp_path, ["close", "fx-1", "--reason", "fixture completed"])
    assert tracker.require_record(tmp_path, "fx-1")["status"] == "closed"


def test_completion_admission_refuses_stale_confirmation_without_a_partial_close(
    tmp_path: Path,
) -> None:
    _ready(tmp_path)
    tracker.write(
        tmp_path,
        [
            "update",
            "fx-1",
            "--acceptance-criteria",
            "When the fixture changes, the new result shall be confirmed.",
        ],
    )
    events = tracker.kit(tmp_path, "events")
    before = events.read_events(tracker.ledger_dir(tmp_path))[0]

    with pytest.raises(RuntimeError, match="completion admission"):
        tracker.check_close(tmp_path, "fx-1", "fixture completed")

    assert events.read_events(tracker.ledger_dir(tmp_path))[0] == before
    assert tracker.require_record(tmp_path, "fx-1")["status"] == "in_progress"
