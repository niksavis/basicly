from __future__ import annotations

from basicly import board_regions, board_wall
from tests.test_board_regions import _absent, _reads
from tests.test_board_wall import STAMPED


def test_the_running_row_draws_one_card_per_lane_and_names_what_it_dropped() -> None:

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

    cards, _, _ = board_regions.flight(_reads("wall-v1.json"))
    paired = next(card for card in cards if card.title == "basicly-rbnz49")
    lonely = next(card for card in cards if card.title == "basicly-4t9z")
    assert next(cell.bar for cell in paired.cells if cell.label == "context") is not None
    assert [cell.label for cell in lonely.cells if cell.label == "context"] == []
    assert board_wall.UNKNOWN not in [cell.value for cell in lonely.cells]


def test_a_lane_that_is_not_live_is_marked_on_two_channels() -> None:
    cards, _, _ = board_regions.flight(_reads("wall-v1.json"))
    live = next(card for card in cards if card.title == "basicly-rbnz49").state
    last_known = next(card for card in cards if card.title == "basicly-4t9z").state
    assert (live.glyph, live.border_style) != (last_known.glyph, last_known.border_style)


def test_a_lane_with_no_measurable_figure_still_names_the_lane_and_its_phase() -> None:

    lanes = [{"id": "basicly-bare", "phase": "intake", "live": True}]
    reads = _reads("wall-v1.json", lanes=lanes)
    cards, _, _ = board_regions.flight(reads)
    assert len(cards) == 1
    card = cards[0]
    assert (card.title, card.phase) == ("basicly-bare", "intake")
    assert [(cell.label, cell.value) for cell in card.cells] == [("id", "basicly-bare")]


def test_only_a_lane_reporting_a_live_stream_is_marked_as_working() -> None:

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
    assert [card.state.key for card in cards] == ["live", "live"]


def test_a_running_cards_primary_state_is_phase_and_started_ago_never_status() -> None:
    cards, _, _ = board_regions.flight(_reads("wall-v1.json"), now=STAMPED)
    running = next(card for card in cards if card.title == "basicly-rbnz49")
    assert running.phase == f"build{board_wall.DOT}started 24m 52s ago"
    assert "in_progress" not in running.phase, "the record's own status leaked onto the card"


def test_the_card_title_is_the_units_join_with_the_id_demoted_to_a_cell() -> None:
    lanes = [{"id": "basicly-x1", "phase": "build", "live": True}]
    units = [{"id": "basicly-x1", "title": "fix the flaky merge gate"}]
    reads = _reads("wall-v1.json", lanes=lanes, units=units)
    card = board_regions.flight(reads, now=STAMPED)[0][0]
    assert card.title == "fix the flaky merge gate"
    assert any(cell.label == "id" and cell.value == "basicly-x1" for cell in card.cells)

    unjoined = _reads("wall-v1.json", lanes=lanes, units=[])
    assert board_regions.flight(unjoined, now=STAMPED)[0][0].title == "basicly-x1"


def test_the_activity_note_is_carried_in_full_for_a_card_to_expand_to() -> None:
    long_note = "gates running: " + "g" * 150
    lanes = [{"id": "basicly-x2", "phase": "build", "live": True, "note": long_note}]
    reads = _reads("wall-v1.json", lanes=lanes, units=[])
    assert board_regions.flight(reads, now=STAMPED)[0][0].note == long_note


def test_a_lane_nobody_confirms_is_live_says_so_instead_of_looking_busy() -> None:
    lanes = [{"id": "basicly-x3", "phase": "ship", "live": False, "tokens": 999, "note": "old"}]
    reads = _reads("wall-v1.json", lanes=lanes, units=[])
    card = board_regions.flight(reads, now=STAMPED)[0][0]
    assert card.phase == "ship", "no duration is owed to a lane nobody confirms is live"
    assert card.note == f"not confirmed live{board_wall.DOT}old"
    assert card.working is False


def _lane(state: str, **extra: object) -> dict[str, object]:
    return {
        "id": "basicly-oqspon",
        "phase": "build",
        "state": state,
        "state_since": "2026-08-21T16:40:42Z",
        **extra,
    }


def _one(state: str, **extra: object) -> board_wall.Card:
    reads = _reads("wall-v1.json", lanes=[_lane(state, **extra)])
    return board_regions.flight(reads, now=STAMPED)[0][0]


def test_each_pass_state_leads_the_headline_with_its_own_word_and_no_glyph() -> None:

    drawn = {state: _one(state) for state in board_regions.LANE_MARKS}
    assert [card.phase for card in drawn.values()] == [
        "running \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "landing \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "waits to land \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "landed \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "queued \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "refused \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
        "parked \N{MIDDLE DOT} 2m 10s \N{MIDDLE DOT} build",
    ]
    assert drawn["landing"].state.colour != drawn["refused"].state.colour
    assert drawn["waits-to-land"].state.border_style == "solid"
    assert drawn["refused"].state.border_style == "double"


def test_a_finished_lane_says_where_it_is_in_the_landing_queue_not_that_it_is_idle() -> None:

    card = _one("waits-to-land", state_detail="2 of 3 in the landing queue")
    assert card.phase.startswith("waits to land")
    assert card.note == "2 of 3 in the landing queue"
    assert "not confirmed live" not in card.note


def test_a_refused_lane_carries_the_bound_that_refused_it_in_place() -> None:
    card = _one("refused", state_detail="downstream WIP bound 5 reached; 0 units past build")
    assert card.phase.startswith("refused")
    assert card.note == "downstream WIP bound 5 reached; 0 units past build"


def test_a_lane_naming_no_state_renders_exactly_as_it_did_before_the_key_existed() -> None:

    quiet = board_regions.flight(
        _reads("wall-v1.json", lanes=[{"id": "a", "phase": "build", "note": "reading the gate"}]),
        now=STAMPED,
    )[0][0]
    assert quiet.phase == "build"
    assert quiet.note == "not confirmed live \N{MIDDLE DOT} reading the gate"
    assert quiet.state.key == board_wall.ABSENT


def test_the_region_says_what_the_pass_waits_for_and_stays_silent_while_it_moves() -> None:

    def note(**over: object) -> str:
        return board_regions.flight(_reads("wall-v1.json", **over), now=STAMPED)[2]

    assert note(lanes=[_lane("landing")]) == ""
    assert note(lanes=[_lane("running")]) == ""
    assert note(lanes=[_lane("refused")]).startswith("waits on a person")
    held = note(lanes=[_lane("refused"), _lane("waits-to-land")], asks=[], backlog={})
    assert held == "waits for the next pass - 2 lane(s) waits to land, refused"
    only_blocked = note(lanes=[], asks=[], backlog={"blocked": 61})
    assert only_blocked == "waits on a blocker - 61 record(s) have an unmet dependency"


def test_a_board_of_only_parked_lanes_does_not_claim_a_pass_waits_for_them() -> None:

    reads = _reads("wall-v1.json", lanes=[_lane("parked"), _lane("landed")], asks=[])
    line = board_regions._waiting_on(reads, list(reads["lanes"].dicts))
    assert "waits for the next pass" not in line
    assert "ready to start" in line, "a reader is owed what they can start instead"

    queued = _reads("wall-v1.json", lanes=[_lane("queued")], asks=[])
    assert "waits for the next pass" in board_regions._waiting_on(
        queued, list(queued["lanes"].dicts)
    )
