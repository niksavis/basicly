"""The dispatch spend write path: one completion, one summable event (basicly-0eexh5).

A grant's spend lived only in `.basicly/usage/run-records.json`, which is self-ignored, so
a clone read every grant's spend as unknown. What makes the figure travel is the ledger's
own `dispatch` kind: `events.accumulate` sums its `spend_micros` into the record's carried
totals, and the ledger is committed.

Split from `test_tracker_seam.py`, which is at the module-size cap: what is asserted here
is one write path rather than the flip, so it is the responsibility that splits cleanly.
Every test runs with a spawn made fatal, for that file's reason — a call site that
degraded to writing nothing would satisfy a weaker assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import run_record, tracker
from tests import flipped_tracker

LANE = "lane-0001"


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spawn fails the test rather than falling back to a store that no longer exists."""
    flipped_tracker.refuse_spawn(monkeypatch)


def _lane(tmp_path: Path) -> Path:
    """A flipped checkout whose ledger already holds :data:`LANE`, open."""
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, LANE)
    return repo


def _kinds(repo: Path) -> list[str]:
    return [event.kind for event in flipped_tracker.ledger_events(repo) if event.record == LANE]


def _totals(repo: Path) -> tuple[int, int]:
    """:data:`LANE`'s carried spend and attempts, as the fold reports them."""
    kit = tracker.kit(repo)
    totals = kit.events.fold(flipped_tracker.ledger_events(repo)).records[LANE].totals
    return totals.spend_micros, totals.attempts


def _run(tokens: int, at: str = "2026-08-07T00:00:00+00:00") -> run_record.RunRecord:
    """One completed dispatch, stamped so two of them can be told apart."""
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
    """The write path's whole claim: a completed dispatch's spend is in the ledger.

    Asserted off the fold rather than off the payload, because the payload is only half of
    it — `spend_micros` is summed by name, so a key spelled anything else stores a number
    the carried totals never see and every reader of them still answers zero. The kind is
    held to the closed set beside it: a kind of its own would come back unknown from
    `classify_kind` and fold to no state at all.
    """
    repo = _lane(tmp_path)

    tracker.add_dispatch(repo, LANE, {tracker.DISPATCH_SPEND_KEY: 1234, "phase": "build"})

    kit = tracker.kit(repo)
    assert _totals(repo) == (1234, 1)
    assert _kinds(repo) == ["status", kit.events.KIND_DISPATCH]
    assert kit.events.KIND_DISPATCH in kit.events.KNOWN_KINDS
    assert kit.events.classify_kind(kit.events.KIND_DISPATCH) == kit.events.APPLIED


def test_one_dispatch_completion_recorded_twice_is_one_spend_event(tmp_path: Path) -> None:
    """Idempotent by the content digest, which is what makes a re-record safe to attempt.

    The failure this closes is silent and one-directional: an append-only log has no undo,
    so a second copy of one completion doubles a lane's spend for good and the grant it is
    read against refuses on a figure nothing spent. A dispatch that really is a second one
    differs in its reading — `record_dispatch_event` puts the timestamp there — and the
    first pair below is the same completion twice.
    """
    repo = _lane(tmp_path)
    reading = {tracker.DISPATCH_SPEND_KEY: 500, "at": "2026-08-07T00:00:00+00:00"}

    tracker.add_dispatch(repo, LANE, dict(reading))
    tracker.add_dispatch(repo, LANE, dict(reading))
    tracker.add_dispatch(repo, LANE, {**reading, "at": "2026-08-07T00:05:00+00:00"})

    assert _kinds(repo).count(tracker.kit(repo).events.KIND_DISPATCH) == 2
    assert _totals(repo) == (1000, 2)


def test_a_dispatch_spend_for_a_record_the_ledger_lacks_is_refused(tmp_path: Path) -> None:
    """Telemetry attached to nothing folds a mistyped id into existence, so it is refused.

    `owned_write.refuse_a_write_to_an_absent_record`'s rule, reached through the second
    write path that states itself as something other than an argv — the guard it was
    written for covered `add_artifact` and nothing was holding this one to it.
    """
    repo = _lane(tmp_path)

    with pytest.raises(tracker.TrackerDivergenceError):
        tracker.add_dispatch(repo, "lane-9999", {tracker.DISPATCH_SPEND_KEY: 1})


def test_a_dispatch_spend_write_is_refused_inside_a_read_only_section(tmp_path: Path) -> None:
    """A gate that promised to write nothing may not record a dispatch either.

    The soft caller is asserted beside it: `record_dispatch_event` swallows a store that
    cannot answer and deliberately does not swallow this, which is the same split
    `tracker.try_write` draws.
    """
    repo = _lane(tmp_path)

    with tracker.read_only("a pre-flight gate"), pytest.raises(tracker.TrackerWriteRefusedError):
        tracker.add_dispatch(repo, LANE, {tracker.DISPATCH_SPEND_KEY: 1})
    with tracker.read_only("a pre-flight gate"), pytest.raises(tracker.TrackerWriteRefusedError):
        run_record.record_dispatch_event(repo, LANE, _run(7))
    assert _kinds(repo) == ["status"]


def test_a_recorded_dispatch_lands_both_its_marker_and_its_typed_spend(tmp_path: Path) -> None:
    """The wiring: what the dispatch site already calls now writes the summable form too.

    Both halves are asserted because different consumers read them — the calibration
    readers parse the `[harness-run]` marker, and the spend bound reads the carried total —
    and dropping either while D-34's migration runs loses one of them.
    """
    repo = _lane(tmp_path)

    ident = run_record.record_marker(repo, LANE, _run(1234))

    assert ident is not None
    assert _totals(repo) == (1234, 1)
    assert [entry["tokens"] for entry in run_record.tracker_history(repo)[LANE]] == [1234]
