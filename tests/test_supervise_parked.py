"""How a pass ends when a parked lane cannot be advanced (basicly-u2hl.55).

Its own module rather than a block in ``test_supervise.py``: that file is the tree's largest
at roughly fifteen times the size cap, so the ratchet allows it to shrink and not to grow.

The defect these pin was invisible for the same reason it was cheap — a lane blocked at a
downstream checkpoint was reported as ``merged``, which reads as progress, so the standing
loop re-adopted it every round. Measured at 257 rounds over 49 minutes with no dispatch, no
tokens, and no decision queued for the human to answer.
"""

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
    """Stub one parked lane advancing *was* -> *now*, recording what was enqueued.

    ``AdvanceResult.progressed`` is derived from the phases rather than declared, so a
    landing is modelled by moving one and a stall by repeating it. Stubbing a `progressed`
    flag instead would assert against a field that does not exist.
    """
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
    """The spin: a lane that did not move must not be reported as a landing.

    ``test_supervise.py`` already pins this for the ``build`` branch. The ``else`` branch —
    every phase downstream of build — had no such check.
    """
    _parked(monkeypatch, was="verify", now="verify")

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [r.route for r in routed] == ["lane-blocked"]
    assert not supervise.should_continue(routed)


def test_a_lane_blocked_downstream_queues_the_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ending the pass is not enough: the human is owed the question that ended it."""
    enqueued = _parked(monkeypatch, was="verify", now="verify")

    supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [(item[0], item[1]) for item in enqueued] == [("epic.1", "escalation")]
    assert enqueued[0][2] == supervise.PARKED_LANE_QUESTION
    assert "verify" in enqueued[0][3]


def test_a_lane_that_moved_downstream_is_still_a_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The discriminator: the new refusal is on progress, not on the phase being downstream.

    Without this, routing every downstream lane as ``lane-blocked`` would also pass the two
    assertions above while ending every pass that lands anything.
    """
    enqueued = _parked(monkeypatch, was="build", now="verify")

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [r.route for r in routed] == ["merged"]
    assert supervise.should_continue(routed)
    assert enqueued == []
