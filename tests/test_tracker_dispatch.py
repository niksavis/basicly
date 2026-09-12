from __future__ import annotations

from pathlib import Path

import pytest

from basicly import run_record, tracker
from tests import flipped_tracker

LANE = "lane-0001"


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    flipped_tracker.refuse_spawn(monkeypatch)


def _lane(tmp_path: Path) -> Path:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, LANE)
    return repo


def _kinds(repo: Path) -> list[str]:
    return [event.kind for event in flipped_tracker.ledger_events(repo) if event.record == LANE]


def _totals(repo: Path) -> tuple[int, int]:
    kit = tracker.kit(repo)
    totals = kit.events.fold(flipped_tracker.ledger_events(repo)).records[LANE].totals
    return totals.spend_micros, totals.attempts


def _run(tokens: int, at: str = "2026-08-07T00:00:00+00:00") -> run_record.RunRecord:
    return run_record.RunRecord(
        agent="claude",
        outcome=run_record.EXECUTED,
        returncode=0,
        duration_s=1.0,
        command=("claude", "-p", run_record.REDACTED_PROMPT),
        timestamp=at,
        tokens=tokens,
        estimated=False,
        prompt_sha256="ab" * 32,
        phase="build",
    )


def test_a_dispatch_event_carries_its_spend_into_the_records_totals(tmp_path: Path) -> None:

    repo = _lane(tmp_path)

    tracker.add_dispatch(repo, LANE, {tracker.DISPATCH_SPEND_KEY: 1234, "phase": "build"})

    kit = tracker.kit(repo)
    assert _totals(repo) == (1234, 1)
    assert _kinds(repo) == ["status", kit.events.KIND_DISPATCH]
    assert kit.events.KIND_DISPATCH in kit.events.KNOWN_KINDS
    assert kit.events.classify_kind(kit.events.KIND_DISPATCH) == kit.events.APPLIED


def test_one_dispatch_completion_recorded_twice_is_one_spend_event(tmp_path: Path) -> None:

    repo = _lane(tmp_path)
    reading = {tracker.DISPATCH_SPEND_KEY: 500, "at": "2026-08-07T00:00:00+00:00"}

    tracker.add_dispatch(repo, LANE, dict(reading))
    tracker.add_dispatch(repo, LANE, dict(reading))
    tracker.add_dispatch(repo, LANE, {**reading, "at": "2026-08-07T00:05:00+00:00"})

    assert _kinds(repo).count(tracker.kit(repo).events.KIND_DISPATCH) == 2
    assert _totals(repo) == (1000, 2)


def test_a_dispatch_spend_for_a_record_the_ledger_lacks_is_refused(tmp_path: Path) -> None:

    repo = _lane(tmp_path)

    with pytest.raises(tracker.TrackerDivergenceError):
        tracker.add_dispatch(repo, "lane-9999", {tracker.DISPATCH_SPEND_KEY: 1})


def test_a_dispatch_spend_write_is_refused_inside_a_read_only_section(tmp_path: Path) -> None:

    repo = _lane(tmp_path)

    with tracker.read_only("a pre-flight gate"), pytest.raises(tracker.TrackerWriteRefusedError):
        tracker.add_dispatch(repo, LANE, {tracker.DISPATCH_SPEND_KEY: 1})
    with tracker.read_only("a pre-flight gate"), pytest.raises(tracker.TrackerWriteRefusedError):
        run_record.record_dispatch_event(repo, LANE, _run(7))
    assert _kinds(repo) == ["status"]


def test_a_recorded_dispatch_lands_both_its_marker_and_its_typed_spend(tmp_path: Path) -> None:

    repo = _lane(tmp_path)

    ident = run_record.record_marker(repo, LANE, _run(1234))

    assert ident is not None
    assert _totals(repo) == (1234, 1)
    assert [entry["tokens"] for entry in run_record.tracker_history(repo)[LANE]] == [1234]
