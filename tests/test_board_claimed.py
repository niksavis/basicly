"""Work the page implied and work it could not name, which were the same defect twice.

The owner read the board and reported *"i have no idea do we see what task are being worked
on."* Three figures said one — `build 1`, `BUILD 1`, `IN PROGRESS 1` — and they were three
different populations: a *deferred* record holding a stale worktree, a record resting at
`intake`, and no lane at all. None was named, and nothing was being worked on.

So two assertions run through this file. **Name what is claimed**, because a count no reader
can act on is not information. And **do not imply a cause you do not have**: the region's
note gave the blocked count as the reason nothing was running, on a page that also printed
231 ready.

Split from `tests/test_board_regions.py` when its own size cap refused the sixth case.
"""

from __future__ import annotations

from basicly import board_regions
from tests.test_board_regions import _reads
from tests.test_board_wall import STAMPED


def _unit(ident: str, phase: str, status: str = "open", **over: object) -> dict:
    """One units[] row, at *phase* with *status*."""
    return {"id": ident, "phase": phase, "status": status, "ready": True, **over}


def test_a_claimed_record_no_lane_holds_is_named_and_not_merely_counted() -> None:
    """The owner's report: `IN PROGRESS 1` three times over and no id anywhere.

    The three figures were three populations. The one the footer counted was
    `basicly-n5jvhh` resting at `intake`; the one the diagram drew at `build` was a
    *deferred* record; and `lanes[]` was empty. None of them was named.
    """
    units = [
        _unit("basicly-n5jvhh", "intake", "in_progress", title="a pass that changes nothing"),
        _unit("basicly-open", "decompose"),
    ]
    rows, dropped = board_regions.claimed(_reads("wall-v1.json", units=units, lanes=[]))
    assert [row["id"] for row in rows] == ["basicly-n5jvhh"]
    assert rows[0]["phase"] == "intake", "the phase it is actually at, not the one assumed"
    assert rows[0]["title"].startswith("a pass that changes nothing")
    assert not dropped


def test_a_claim_a_lane_already_holds_is_not_drawn_twice() -> None:
    """The card is the answer for a dispatched lane; a row beside it would be a second one."""
    units = [_unit("basicly-x", "build", "in_progress")]
    lanes = [{"id": "basicly-x", "phase": "build"}]
    assert board_regions.claimed(_reads("wall-v1.json", units=units, lanes=lanes))[0] == ()
    # The control: with no lane the same record is drawn.
    assert board_regions.claimed(_reads("wall-v1.json", units=units, lanes=[]))[0]


def test_the_claimed_region_is_bounded_and_reports_what_it_dropped() -> None:
    """More than a few claims with no lane is a filing problem, not a busy factory."""
    many = [
        _unit(f"basicly-{n}", "build", "in_progress")
        for n in range(board_regions.CLAIMED_SLOTS + 3)
    ]
    rows, dropped = board_regions.claimed(_reads("wall-v1.json", units=many, lanes=[]))
    assert len(rows) == board_regions.CLAIMED_SLOTS
    assert "3" in dropped and "claimed" in dropped


def test_nothing_claimed_draws_nothing() -> None:
    """No standing heading: a region that is always there is one nobody reads."""
    units = [_unit("basicly-a", "intake"), _unit("basicly-b", "decompose", "blocked")]
    assert board_regions.claimed(_reads("wall-v1.json", units=units, lanes=[])) == ((), "")


def test_a_blocked_count_is_not_a_cause_while_anything_is_ready() -> None:
    """The note said `waits on a blocker` on an idle factory with 231 records ready.

    Read plainly that says blockers are why nothing runs. They were not: the true answer was
    `no pass is running`, drawn under the diagram in the smallest type on the page.
    """
    idle = _reads("wall-v1.json", lanes=[], asks=[], backlog={"ready": 231, "blocked": 56})
    _cards, _more, note = board_regions.flight(idle, now=STAMPED)
    assert "waits on a blocker" not in note
    assert note == "no pass is running - 231 record(s) are ready to start"


def test_a_blocked_count_is_a_cause_when_nothing_at_all_is_ready() -> None:
    """The rung earns its place exactly here, so it is kept rather than deleted."""
    starved = _reads("wall-v1.json", lanes=[], asks=[], backlog={"ready": 0, "blocked": 56})
    _cards, _more, note = board_regions.flight(starved, now=STAMPED)
    assert note == "waits on a blocker - 56 record(s) have an unmet dependency"
