from __future__ import annotations

from basicly import board_regions
from tests.test_board_regions import _reads
from tests.test_board_wall import STAMPED


def _unit(ident: str, phase: str, status: str = "open", **over: object) -> dict:
    return {"id": ident, "phase": phase, "status": status, "ready": True, **over}


def test_a_claimed_record_no_lane_holds_is_named_and_not_merely_counted() -> None:

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
    units = [_unit("basicly-x", "build", "in_progress")]
    lanes = [{"id": "basicly-x", "phase": "build"}]
    assert board_regions.claimed(_reads("wall-v1.json", units=units, lanes=lanes))[0] == ()
    assert board_regions.claimed(_reads("wall-v1.json", units=units, lanes=[]))[0]


def test_the_claimed_region_is_bounded_and_reports_what_it_dropped() -> None:
    many = [
        _unit(f"basicly-{n}", "build", "in_progress")
        for n in range(board_regions.CLAIMED_SLOTS + 3)
    ]
    rows, dropped = board_regions.claimed(_reads("wall-v1.json", units=many, lanes=[]))
    assert len(rows) == board_regions.CLAIMED_SLOTS
    assert "3" in dropped and "claimed" in dropped


def test_nothing_claimed_draws_nothing() -> None:
    units = [_unit("basicly-a", "intake"), _unit("basicly-b", "decompose", "blocked")]
    assert board_regions.claimed(_reads("wall-v1.json", units=units, lanes=[])) == ((), "")


def test_a_blocked_count_is_not_a_cause_while_anything_is_ready() -> None:

    idle = _reads("wall-v1.json", lanes=[], asks=[], backlog={"ready": 231, "blocked": 56})
    _cards, _more, note = board_regions.flight(idle, now=STAMPED)
    assert "waits on a blocker" not in note
    assert note == "no pass is running - 231 record(s) are ready to start"


def test_a_blocked_count_is_a_cause_when_nothing_at_all_is_ready() -> None:
    starved = _reads("wall-v1.json", lanes=[], asks=[], backlog={"ready": 0, "blocked": 56})
    _cards, _more, note = board_regions.flight(starved, now=STAMPED)
    assert note == "waits on a blocker - 56 record(s) have an unmet dependency"
