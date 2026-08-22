"""The running-lane cards: what a card carries, and what it refuses to carry.

Split from `test_board_regions.py` under the `test_<module>_<aspect>.py` convention, because
that module is at its size ratchet and the card grew two properties it did not have - a row
is drawn only where the producer held a value, and a lane reporting a live stream is marked
as working.

The operator's report was that the cards said "not measured" four to six times each while
the one field that says whether a lane is stuck was clipped, and that a lane parked four
hours earlier looked identical to one an agent was inside.
"""

from __future__ import annotations

from basicly import board_regions, board_wall
from tests.test_board_regions import _absent, _reads


def test_the_running_row_draws_one_card_per_lane_and_names_what_it_dropped() -> None:
    """One card per lane, no reserved frames, and the cap still holds above six.

    The reserved slot is the thing being removed: it kept the row's shape at one lane and at
    six by announcing nothing in the other five, which on this repository's own wall is 40% of
    the screen. The cap survives because six live lanes still have to fit the row.
    """
    cards, dropped, note = board_regions.flight(_reads("wall-v1.json"))
    assert [card.title for card in cards] == [
        "basicly-rbnz49",
        "basicly-f3tked",
        "basicly-7bur",
        "basicly-4t9z",
    ], "the row drew a slot the producer gave it no lane for"
    assert not dropped and not note

    lanes = [{"id": f"lane-{index}", "phase": "build"} for index in range(9)]
    cards, dropped, _ = board_regions.flight(_reads("wall-v1.json", lanes=lanes))
    assert len(cards) == board_regions.FLIGHT_SLOTS
    assert dropped == f"+{9 - board_regions.FLIGHT_SLOTS} more lanes"


def test_no_lane_dispatched_draws_no_card_and_says_which_of_its_two_silences_it_is() -> None:
    """Three ways to have nothing running, and only one of them is a producer that measured.

    The note is what the collapsed row prints in place of the cards, so it has to carry the
    difference: a producer that omitted `lanes` said nothing about what is running, while one
    that emitted an empty list said nothing is.
    """
    reads = _reads("wall-v1.json")
    for empty, expected in (
        (_reads("wall-v1.json", lanes=[]), "no lane is dispatched"),
        (_absent("lanes", reads), board_wall.ABSENT_TEXT),
    ):
        cards, dropped, note = board_regions.flight(empty)
        assert cards == (), "a collapsed row still reserved a card"
        assert not dropped
        assert note == expected


def test_a_lane_card_draws_a_context_bar_only_when_both_of_its_terms_are_there() -> None:
    """`context_used` and `context_window` travel together, and one alone draws nothing.

    The pair used to draw a labelled row saying it had no value. A card no longer spends a row
    on an absence, so the same property now reads as the cell being gone rather than present
    and empty - and the paired case is the control that the cell is drawn when it can be.
    """
    cards, _, _ = board_regions.flight(_reads("wall-v1.json"))
    paired = next(card for card in cards if card.title == "basicly-rbnz49")
    lonely = next(card for card in cards if card.title == "basicly-4t9z")
    assert next(cell.bar for cell in paired.cells if cell.label == "context") is not None
    assert [cell.label for cell in lonely.cells if cell.label == "context"] == []
    assert board_wall.UNKNOWN not in [cell.value for cell in lonely.cells]


def test_a_lane_that_is_not_live_is_marked_on_two_channels() -> None:
    """A dashed border and a different glyph, not only a colour."""
    cards, _, _ = board_regions.flight(_reads("wall-v1.json"))
    live = next(card for card in cards if card.title == "basicly-rbnz49").state
    last_known = next(card for card in cards if card.title == "basicly-4t9z").state
    assert (live.glyph, live.border_style) != (last_known.glyph, last_known.border_style)


def test_only_a_lane_reporting_a_live_stream_is_marked_as_working() -> None:
    """A still card and a wedged card look identical, so the page carries this as motion.

    `state` answers whether the worktree exists, which a parked lane also satisfies - it is
    the field that let a lane adopted four hours earlier render under a heading saying
    running now. `working` answers whether an agent is inside it. This test is also what
    holds the field against the dead-code gate, which cannot see a template read.
    """
    lanes = [
        {"id": "busy", "phase": "build", "live": True, "tokens": 5, "note": "reading the gate"},
        {"id": "parked", "phase": "build", "live": True, "agent": "claude"},
    ]
    reads = _reads("wall-v1.json")
    reads["lanes"] = board_wall.Reading(
        "lanes", board_wall.BY_KEY[board_wall.RENDERABLE], "", lanes
    )
    cards, _, _ = board_regions.flight(reads)
    assert [card.working for card in cards] == [True, False]
    # The control: both are `live`, so a check reading that field alone cannot tell them apart.
    assert [card.state.key for card in cards] == ["live", "live"]
