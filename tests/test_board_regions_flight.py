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
from tests.test_board_wall import STAMPED


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

    The empty-list case now names *what the pass waits for* rather than stopping at the
    absence (basicly-ncday7, AC4), so it is driven twice - once with the fixture's own
    pending asks, once with none - and only the second reaches the bare absence.
    """
    reads = _reads("wall-v1.json")
    for empty, expected in (
        (_reads("wall-v1.json", lanes=[]), "waits on a person - 3 checkpoint or decision pending"),
        (
            _reads("wall-v1.json", lanes=[], asks=[], backlog={}),
            "no lane is dispatched",
        ),
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


def test_a_lane_with_no_measurable_figure_still_names_the_lane_and_its_phase() -> None:
    """Zero rows is a legitimate card, not a missing one: the id and phase still draw.

    A lane just dispatched can hold nothing but its id and phase for a beat - no agent, no
    tokens, nothing the run has reported yet. That is still a lane that is running, so the
    card keeps its identity and draws no `not measured` row rather than the six placeholders
    this record removed. The one cell it does draw is the id, demoted to the address line
    (basicly-0xtzf1): a cell with a value is not a placeholder.
    """
    lanes = [{"id": "basicly-bare", "phase": "intake", "live": True}]
    reads = _reads("wall-v1.json", lanes=lanes)
    cards, _, _ = board_regions.flight(reads)
    assert len(cards) == 1
    card = cards[0]
    assert (card.title, card.phase) == ("basicly-bare", "intake")
    assert [(cell.label, cell.value) for cell in card.cells] == [("id", "basicly-bare")]


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


def test_a_running_cards_primary_state_is_phase_and_started_ago_never_status() -> None:
    """basicly-0xtzf1: six working lanes must not read as six idle tracker records."""
    cards, _, _ = board_regions.flight(_reads("wall-v1.json"), now=STAMPED)
    running = next(card for card in cards if card.title == "basicly-rbnz49")
    assert running.phase == f"build{board_wall.DOT}started 24m 52s ago"
    assert "in_progress" not in running.phase, "the record's own status leaked onto the card"


def test_the_card_title_is_the_units_join_with_the_id_demoted_to_a_cell() -> None:
    """`lanes[].id` joins `units[].title`; the id survives beside the agent and branch."""
    lanes = [{"id": "basicly-x1", "phase": "build", "live": True}]
    units = [{"id": "basicly-x1", "title": "fix the flaky merge gate"}]
    reads = _reads("wall-v1.json", lanes=lanes, units=units)
    card = board_regions.flight(reads, now=STAMPED)[0][0]
    assert card.title == "fix the flaky merge gate"
    assert any(cell.label == "id" and cell.value == "basicly-x1" for cell in card.cells)

    unjoined = _reads("wall-v1.json", lanes=lanes, units=[])
    assert board_regions.flight(unjoined, now=STAMPED)[0][0].title == "basicly-x1"


def test_the_activity_note_is_carried_in_full_for_a_card_to_expand_to() -> None:
    """A tighter clip at render time is what left a card unable to expand past its summary."""
    long_note = "gates running: " + "g" * 150
    lanes = [{"id": "basicly-x2", "phase": "build", "live": True, "note": long_note}]
    reads = _reads("wall-v1.json", lanes=lanes, units=[])
    assert board_regions.flight(reads, now=STAMPED)[0][0].note == long_note


def test_a_lane_nobody_confirms_is_live_says_so_instead_of_looking_busy() -> None:
    """A parked lane can still carry a finished dispatch's tokens and note; it must not pulse."""
    lanes = [{"id": "basicly-x3", "phase": "ship", "live": False, "tokens": 999, "note": "old"}]
    reads = _reads("wall-v1.json", lanes=lanes, units=[])
    card = board_regions.flight(reads, now=STAMPED)[0][0]
    assert card.phase == "ship", "no duration is owed to a lane nobody confirms is live"
    assert card.note == f"not confirmed live{board_wall.DOT}old"
    assert card.working is False


def _lane(state: str, **extra: object) -> dict[str, object]:
    """One lane row in *state*, entered two minutes and ten seconds before `STAMPED`."""
    return {
        "id": "basicly-oqspon",
        "phase": "build",
        "state": state,
        "state_since": "2026-08-21T16:40:42Z",
        **extra,
    }


def _one(state: str, **extra: object) -> board_wall.Card:
    """The card the region draws for a single lane in *state*."""
    reads = _reads("wall-v1.json", lanes=[_lane(state, **extra)])
    return board_regions.flight(reads, now=STAMPED)[0][0]


def test_each_pass_state_leads_the_headline_with_its_own_word_and_no_glyph() -> None:
    """basicly-ncday7 AC5: the word, the colour and the border, and never a glyph.

    Every one of these read `build` before, with `live` true or false as the only other
    channel - so a lane whose agent had exited, a lane the supervisor was landing and a
    lane the WIP bound had refused were one rendering. The phase is still there and still
    behind the state: it answers a different question and it did not become wrong.
    """
    drawn = {state: _one(state) for state in board_regions.LANE_MARKS}
    assert [card.phase for card in drawn.values()] == [
        "running \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "landing \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "waits to land \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "queued \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "refused \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "parked \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
    ]
    assert drawn["landing"].state.colour != drawn["refused"].state.colour
    assert drawn["waits-to-land"].state.border_style == "solid"
    assert drawn["refused"].state.border_style == "double"


def test_a_finished_lane_says_where_it_is_in_the_landing_queue_not_that_it_is_idle() -> None:
    """AC1: `waits to land` with its position, never `build` and `not confirmed live`.

    The position is the fact a duration cannot replace - it says how many landings, each of
    them 3 to 8 minutes, stand between this lane and its own.
    """
    card = _one("waits-to-land", state_detail="2 of 3 in the landing queue")
    assert card.phase.startswith("waits to land")
    assert card.note == "2 of 3 in the landing queue"
    assert "not confirmed live" not in card.note


def test_a_refused_lane_carries_the_bound_that_refused_it_in_place() -> None:
    """AC3: the refusal and its reason on the card, and not as an idle build."""
    card = _one("refused", state_detail="downstream WIP bound 5 reached; 0 units past build")
    assert card.phase.startswith("refused")
    assert card.note == "downstream WIP bound 5 reached; 0 units past build"


def test_a_lane_naming_no_state_renders_exactly_as_it_did_before_the_key_existed() -> None:
    """The compatibility half: a producer that omits `state` loses nothing it had.

    The schema admits a new key within a major, so every snapshot written before this one
    still has to draw - and `not confirmed live` was always reporting an absence rather
    than an idle lane, which is why it survives here and nowhere else.
    """
    quiet = board_regions.flight(
        _reads("wall-v1.json", lanes=[{"id": "a", "phase": "build", "note": "reading the gate"}]),
        now=STAMPED,
    )[0][0]
    assert quiet.phase == "build"
    assert quiet.note == "not confirmed live \N{MIDDLE DOT} reading the gate"
    assert quiet.state.key == board_wall.ABSENT


def test_the_region_says_what_the_pass_waits_for_and_stays_silent_while_it_moves() -> None:
    """AC4: one sentence, ordered by what a reader can do, and none while work is running.

    A running or landing lane needs no sentence at all: the cards are the answer, and a
    line above them would be a second one.
    """

    def note(**over: object) -> str:
        return board_regions.flight(_reads("wall-v1.json", **over), now=STAMPED)[2]

    assert note(lanes=[_lane("landing")]) == ""
    assert note(lanes=[_lane("running")]) == ""
    assert note(lanes=[_lane("refused")]).startswith("waits on a person")
    held = note(lanes=[_lane("refused"), _lane("waits-to-land")], asks=[], backlog={})
    assert held == "waits for the next pass - 2 lane(s) waits to land, refused"
    only_blocked = note(lanes=[], asks=[], backlog={"blocked": 61})
    assert only_blocked == "waits on a blocker - 61 record(s) have an unmet dependency"
