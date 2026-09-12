from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from basicly import board_actions, board_advance, board_asks

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ELEVEN_DAYS_AGO = "2026-08-25T12:00:00Z"


def _states(**over: tuple[str, bool, str]) -> dict[str, tuple[str, bool, str]]:
    held = {
        "basicly-b2n2": ("ship", True, "in_progress"),
        "basicly-busy": ("build", False, "in_progress"),
        "basicly-shut": ("done", False, "closed"),
    }
    return {**held, **over}


def _asks(states: dict[str, tuple[str, bool, str]] | None = None, **kw: Any) -> list[dict]:
    settings: dict[str, Any] = {"lanes": None, "supervised": False, "last_event": None}
    settings.update(kw)
    return board_advance.asks(states if states is not None else _states(), **settings)


def test_a_record_one_allowed_advance_from_moving_is_an_ask() -> None:
    (ask,) = _asks()
    assert ask["issue"] == "basicly-b2n2"
    assert ask["kind"] == board_advance.KIND == "advance"
    assert ask["subject"] == "ship"
    assert "nothing is scheduled to advance it" in str(ask["question"])


def test_the_ask_names_the_exact_command_that_moves_it() -> None:
    (ask,) = _asks()
    assert ask["actions"] == [{"offer": "basicly loop advance basicly-b2n2"}]


def test_the_ask_offers_no_runnable_verb_and_that_is_deliberate() -> None:

    (ask,) = _asks()
    offers = ask["actions"]
    assert isinstance(offers, list)
    assert all("basicly" not in offer for offer in offers)
    assert board_asks.pending([ask], "a-token") == ((), 0)
    assert "loop-advance" not in board_actions.ACTIONS


def test_a_lane_holding_the_record_draws_no_ask() -> None:
    assert _asks(lanes=[{"id": "basicly-b2n2", "phase": "ship"}]) == []
    assert len(_asks(lanes=[{"id": "basicly-other", "phase": "build"}])) == 1
    assert len(_asks(lanes=[{"phase": "ship"}, "not a row"])) == 1


def test_a_supervisor_refuses_the_whole_population_rather_than_filtering_it() -> None:
    assert _asks(supervised=True) == []
    assert len(_asks(supervised=False)) == 1


def test_a_closed_or_deferred_record_is_not_waiting_on_anyone() -> None:
    for status in ("closed", "deferred"):
        assert _asks(_states(**{"basicly-b2n2": ("ship", True, status)})) == []
    assert len(_asks(_states(**{"basicly-b2n2": ("ship", True, "open")}))) == 1


def test_a_record_already_done_names_a_command_that_would_do_nothing() -> None:
    assert _asks(_states(**{"basicly-b2n2": ("done", True, "in_progress")})) == []


def test_a_blocked_advance_is_not_an_ask_however_old() -> None:
    assert _asks(_states(**{"basicly-b2n2": ("ship", False, "in_progress")})) == []


def test_the_ask_is_dated_from_the_records_own_last_event_and_aged_by_the_consumer() -> None:

    (ask,) = _asks(last_event={"basicly-b2n2": ELEVEN_DAYS_AGO})
    assert ask["requested_at"] == ELEVEN_DAYS_AGO
    assert "waiting_s" not in ask


def test_an_undated_ask_carries_no_stamp_rather_than_this_instant() -> None:
    (ask,) = _asks(last_event={})
    assert "requested_at" not in ask
    (unreadable,) = _asks(last_event={"basicly-b2n2": "not a stamp"})
    assert "requested_at" not in unreadable


def test_the_rows_are_ordered_so_the_page_does_not_move_between_folds() -> None:
    many = _states(**{f"basicly-{n}": ("ship", True, "open") for n in "cab"})
    assert [ask["issue"] for ask in _asks(many)] == [
        "basicly-a",
        "basicly-b",
        "basicly-b2n2",
        "basicly-c",
    ]


def test_an_empty_population_is_an_empty_answer_and_never_a_guess() -> None:
    assert _asks({}) == []
