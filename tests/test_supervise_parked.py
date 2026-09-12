from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import loop, loop_state, supervise

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _lane(issue_id: str) -> supervise.AdoptedLane:
    return supervise.AdoptedLane(
        issue_id=issue_id,
        status="in_progress",
        binding=loop_state.WorktreeBinding(issue_id, f"harness/{issue_id}"),
        live=True,
    )


def _session(*lanes: supervise.AdoptedLane) -> supervise.SessionState:
    return supervise.SessionState(
        root_issue="epic",
        root_status="open",
        children=tuple((lane.issue_id, lane.status) for lane in lanes),
        adopted=lanes,
    )


def _parked(monkeypatch: pytest.MonkeyPatch, *, was: str, now: str) -> list[tuple]:

    enqueued: list[tuple] = []
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: now)
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: False)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: False)
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue_id, kind, question, detail="", **_k: enqueued.append((
            issue_id,
            kind,
            question,
            detail,
        )),
    )
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda _r, issue_id, **_k: [
            loop.AdvanceResult(issue_id, was, now, "blocked", "ship checkpoint")
        ],
    )
    return enqueued


def test_a_lane_blocked_downstream_ends_the_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _parked(monkeypatch, was="verify", now="verify")

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [r.route for r in routed] == ["lane-blocked"]
    assert not supervise.should_continue(routed)


def test_a_lane_blocked_downstream_queues_the_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    enqueued = _parked(monkeypatch, was="verify", now="verify")

    supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [(item[0], item[1]) for item in enqueued] == [("epic.1", "escalation")]
    assert enqueued[0][2] == supervise.PARKED_LANE_QUESTION
    assert "verify" in enqueued[0][3]


def test_a_lane_that_moved_downstream_is_still_a_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    enqueued = _parked(monkeypatch, was="build", now="verify")

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [r.route for r in routed] == ["merged"]
    assert supervise.should_continue(routed)
    assert enqueued == []
