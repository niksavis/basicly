"""The advance ask, asserted against the false calm it exists to end.

The owner asked *"there is 1 in ship - we do not know why it is not drained"* while the band
read `NOTHING IS WAITING`. Both were true: the record's ship checkpoint had been approved
eleven days earlier, so no checkpoint was pending, and nothing had run `loop advance`.

So the cases here are the three conditions and the two ways each can be wrong. A row drawn
for work a lane or a supervisor is about to do is noise on the one region a person reads for
"does it need me?", and a row *not* drawn is the eleven days.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from basicly import board_actions, board_advance, board_asks

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ELEVEN_DAYS_AGO = "2026-08-25T12:00:00Z"


def _states(**over: tuple[str, bool, str]) -> dict[str, tuple[str, bool, str]]:
    """A population where one record is stalled at ship and the rest are not."""
    held = {
        "basicly-b2n2": ("ship", True, "in_progress"),
        "basicly-busy": ("build", False, "in_progress"),
        "basicly-shut": ("done", False, "closed"),
    }
    return {**held, **over}


def _asks(states: dict[str, tuple[str, bool, str]] | None = None, **kw: Any) -> list[dict]:
    """The asks *states* produces, with the three conditions defaulted to the stalled case."""
    settings: dict[str, Any] = {"lanes": None, "supervised": False, "last_event": None}
    settings.update(kw)
    return board_advance.asks(states if states is not None else _states(), **settings)


def test_a_record_one_allowed_advance_from_moving_is_an_ask() -> None:
    """The eleven days. Nothing was requested, so no checkpoint ask could ever cover it."""
    (ask,) = _asks()
    assert ask["issue"] == "basicly-b2n2"
    assert ask["kind"] == board_advance.KIND == "advance"
    assert ask["subject"] == "ship"
    assert "nothing is scheduled to advance it" in str(ask["question"])


def test_the_ask_names_the_exact_command_that_moves_it() -> None:
    """The remedy is the row's whole point: a reader who cannot act on it learns nothing."""
    (ask,) = _asks()
    assert ask["actions"] == [{"offer": "basicly loop advance basicly-b2n2"}]


def test_the_ask_offers_no_runnable_verb_and_that_is_deliberate() -> None:
    """`loop advance` at `build` dispatches an agent and spends a budget.

    So the offer carries no `basicly` key and the board draws no button for it - the schema's
    own "drawn without a button rather than refused". Asserted against the closed table, not
    against a literal, so adding the verb there would fail here and be a decision someone
    took rather than a default that leaked.
    """
    (ask,) = _asks()
    offers = ask["actions"]
    assert isinstance(offers, list)
    assert all("basicly" not in offer for offer in offers)
    assert board_asks.pending([ask], "a-token") == ((), 0)
    assert "loop-advance" not in board_actions.ACTIONS


def test_a_lane_holding_the_record_draws_no_ask() -> None:
    """A dispatched lane is going to advance it; a row would be noise on the alarm region."""
    assert _asks(lanes=[{"id": "basicly-b2n2", "phase": "ship"}]) == []
    # The control: a lane on some *other* record leaves the ask standing.
    assert len(_asks(lanes=[{"id": "basicly-other", "phase": "build"}])) == 1
    # A lane row with no id cannot hold anything, and must not swallow the population.
    assert len(_asks(lanes=[{"phase": "ship"}, "not a row"])) == 1


def test_a_supervisor_refuses_the_whole_population_rather_than_filtering_it() -> None:
    """This producer cannot know a supervisor's selector, so it cannot filter by it."""
    assert _asks(supervised=True) == []
    assert len(_asks(supervised=False)) == 1


def test_a_closed_or_deferred_record_is_not_waiting_on_anyone() -> None:
    """`can_advance` can read true on a record nobody owes anything; the status settles it."""
    for status in ("closed", "deferred"):
        assert _asks(_states(**{"basicly-b2n2": ("ship", True, status)})) == []
    assert len(_asks(_states(**{"basicly-b2n2": ("ship", True, "open")}))) == 1


def test_a_record_already_done_names_a_command_that_would_do_nothing() -> None:
    """`derive_phase` has no transition out of `done`, so an ask there is a dead remedy."""
    assert _asks(_states(**{"basicly-b2n2": ("done", True, "in_progress")})) == []


def test_a_blocked_advance_is_not_an_ask_however_old() -> None:
    """The whole discrimination: `can_advance` false means the engine is not waiting on us."""
    assert _asks(_states(**{"basicly-b2n2": ("ship", False, "in_progress")})) == []


def test_the_ask_is_dated_from_the_records_own_last_event_and_aged_by_the_consumer() -> None:
    """The stamp, never a computed interval.

    `board_regions._waited` ages an ask from its own figure or from its stamp, through the
    single function the wall-clock gate counts per module. A `waiting_s` computed here would
    be a second site for that shape and change nothing the band draws -
    `test_board_regions` asserts the eleven days arriving at the alarm from this stamp alone.
    """
    (ask,) = _asks(last_event={"basicly-b2n2": ELEVEN_DAYS_AGO})
    assert ask["requested_at"] == ELEVEN_DAYS_AGO
    assert "waiting_s" not in ask


def test_an_undated_ask_carries_no_stamp_rather_than_this_instant() -> None:
    """Dating it now would report a record stalled for days as one that just arrived."""
    (ask,) = _asks(last_event={})
    assert "requested_at" not in ask
    (unreadable,) = _asks(last_event={"basicly-b2n2": "not a stamp"})
    assert "requested_at" not in unreadable


def test_the_rows_are_ordered_so_the_page_does_not_move_between_folds() -> None:
    """A wall whose rows reorder on a beat is unreadable; the order is the record id's."""
    many = _states(**{f"basicly-{n}": ("ship", True, "open") for n in "cab"})
    assert [ask["issue"] for ask in _asks(many)] == [
        "basicly-a",
        "basicly-b",
        "basicly-b2n2",
        "basicly-c",
    ]


def test_an_empty_population_is_an_empty_answer_and_never_a_guess() -> None:
    """A producer that cannot fold the log emits no advance ask, not an all-clear."""
    assert _asks({}) == []
