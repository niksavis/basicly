from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from basicly import integration_record, tracker
from tests import flipped_tracker


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(root, "basicly-task", title="bounded task")
    return root


def _lines(repo: Path) -> set[bytes]:
    return {
        line
        for pattern in ("events-*.jsonl", "pending-*.jsonl")
        for path in tracker.ledger_dir(repo).glob(pattern)
        for line in path.read_bytes().splitlines()
    }


def test_progress_retains_completion_start_and_wait_facts_without_rewriting_history(
    repo: Path,
) -> None:
    before = _lines(repo)
    integration_record.record(repo, "basicly-task", "implementation-completed")
    integration_record.enqueue(repo, "basicly-task", "waiting for the active batch")
    integration_record.record(repo, "basicly-task", "started")
    integration_record.record(repo, "basicly-task", "waiting", "required verification failed")
    integration_record.record(repo, "basicly-task", "started")
    integration_record.record(repo, "basicly-task", "completed", "merged and verified")
    facts = integration_record.read(repo, "basicly-task")
    assert [row["event"] for row in facts] == [
        "implementation-completed",
        "queued",
        "started",
        "waiting",
        "started",
        "completed",
    ]
    assert facts[3]["detail"] == "required verification failed"
    assert len({row["id"] for row in facts}) == len(facts)
    assert all(datetime.fromisoformat(row["at"]).tzinfo is not None for row in facts)
    assert before <= _lines(repo)


def test_a_carried_commit_records_when_completion_was_observed(repo: Path) -> None:
    integration_record.enqueue(repo, "basicly-task", "committed branch ready")
    facts = integration_record.read(repo, "basicly-task")
    assert facts[0]["event"] == "implementation-completed"
    assert "observed" in facts[0]["detail"]
    assert facts[1]["event"] == "queued"


def test_waiting_without_a_reason_is_refused_without_a_write(repo: Path) -> None:
    before = _lines(repo)
    with pytest.raises(ValueError, match="concrete reason"):
        integration_record.record(repo, "basicly-task", "waiting")
    assert _lines(repo) == before


@pytest.mark.parametrize("body", ["not json", "{}", '{"event":"completed"}'])
def test_corrupt_progress_is_refused_by_name(repo: Path, body: str) -> None:
    tracker.add_comment(repo, "basicly-task", integration_record.PREFIX + body)
    with pytest.raises(ValueError, match="integration progress on basicly-task"):
        integration_record.read(repo, "basicly-task")
