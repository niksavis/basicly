"""The loop region, asserted against the one question it exists to answer.

The defect this module was written against is that a region headed `the loop` binned the
whole backlog, so its total equalled `backlog.active` by construction (basicly-a68ggd). The
first test is that arithmetic held to the fixture, because the population - not the wording,
not the layout - is what was wrong. The rest follow from it:

* the pass row counts what **this pass selected** and never what the backlog holds;
* it says a pass is not running rather than drawing a histogram under a heading that claims
  one, and the two absences are different facts a caller must be able to tell apart;
* movement carries its own mark, because a still picture of positions cannot tell a lane
  that arrived at `build` this beat from one wedged there since morning;
* the backlog census survives, under its own label and its own denominator.
"""

from __future__ import annotations

from datetime import timedelta

from basicly import board_loop, board_wall
from tests.test_board_wall import STAMPED, readings


def _reads(name: str, **override: object) -> board_wall.Readings:
    """A fixture's readings, with named sections replaced by hand-built ones.

    A `Readings` and never a plain dict: the production type answers a section it was never
    given rather than raising, and a helper that flattened it would make every region look
    like it read a key it does not.
    """
    reads = board_wall.Readings(readings(name))
    for key, value in override.items():
        reads[key] = board_wall.Reading(key, board_wall.BY_KEY[board_wall.RENDERABLE], "", value)
    return reads


def _absent(name: str, reads: board_wall.Readings) -> board_wall.Readings:
    """*reads* with *name* replaced by the reading a section the producer omitted gets."""
    dropped = board_wall.Readings(reads)
    dropped[name] = board_wall.Reading(name, board_wall.BY_KEY[board_wall.ABSENT], "absent")
    return dropped


def _lane(phase: str, ago_s: float | None = None) -> dict[str, object]:
    """One lane at *phase*, having entered its state *ago_s* seconds before `STAMPED`."""
    lane: dict[str, object] = {"id": f"lane-{phase}", "phase": phase}
    if ago_s is not None:
        stamp = STAMPED - timedelta(seconds=ago_s)
        lane["state_since"] = stamp.isoformat().replace("+00:00", "Z")
    return lane


def test_the_pass_row_counts_the_lanes_and_never_the_whole_backlog() -> None:
    """The regression test, and it is arithmetic rather than wording.

    `basicly-a68ggd` was reported because the region's phase total equalled `backlog.active`
    exactly - the same population counted twice under a heading that claimed a running pass.
    So the assertion is that the two totals now *differ*, and that the smaller one is the
    lane count. A rename of the heading would leave this red.
    """
    reads = _reads("wall-v1.json")
    phases, _, running = board_loop.loop(reads, STAMPED)
    assert running
    counted = sum(phase.count or 0 for phase in phases)
    assert counted == len(reads["lanes"].dicts) == 4
    active = reads["backlog"].fields.get("active")
    assert counted != active, "the pass row still totals the whole active backlog"
    # The fixture's lanes sit at build, verify, build and ship - the units section's own
    # phases are a different population and must not appear here.
    assert {phase.name: phase.count for phase in phases if phase.count} == {
        "build": 2,
        "verify": 1,
        "ship": 1,
    }


def test_a_lane_id_absent_from_the_units_section_is_still_counted() -> None:
    """Nothing is joined: `lanes[].phase` is the lane's own and the units row need not exist.

    The shipped fixture is already this shape - its four lane ids appear in no `units[]` row
    - so a join would have drawn an empty pass row against four running lanes.
    """
    reads = _reads("wall-v1.json")
    known = {row.get("id") for row in reads["units"].dicts}
    assert not {row.get("id") for row in reads["lanes"].dicts} & known
    phases, _, _ = board_loop.loop(reads, STAMPED)
    assert sum(phase.count or 0 for phase in phases) == 4


def test_no_pass_running_says_so_rather_than_drawing_a_histogram() -> None:
    """The second half of the defect: an idle factory drew seven bars off the backlog."""
    phases, note, running = board_loop.loop(readings("no-phase-v1.json"), STAMPED)
    assert not running
    assert phases == (), "an idle factory still draws a phase row"
    assert note == "no pass is running"


def test_a_session_with_no_lane_yet_is_still_a_running_pass() -> None:
    """The schema says an absent `session` is the statement, not an empty `lanes`.

    A pass that has selected nothing so far is a different fact from no pass at all, and a
    reader who cannot tell them apart reads a starting supervisor as an idle machine.
    """
    reads = _reads("wall-v1.json", lanes=[])
    assert board_loop.running(reads)
    phases, note, running = board_loop.loop(reads, STAMPED)
    assert running and phases and all(phase.count is None for phase in phases)
    assert "no lane moved" not in note, "an empty pass is reported as a wedged one"

    # And the mirror: lanes held by a producer that emits no session is also a pass.
    assert board_loop.running(_absent("session", _reads("wall-v1.json")))


def test_neither_section_present_is_not_a_pass() -> None:
    """Only the absence of both settles it, which is the one case that reads as idle."""
    reads = _absent("lanes", _absent("session", _reads("wall-v1.json")))
    assert not board_loop.running(reads)


def test_a_lane_that_moved_this_beat_is_marked_and_a_still_row_says_so() -> None:
    """Working against wedged, which a picture of positions alone cannot distinguish."""
    beat = board_loop.BEAT_FALLBACK_S
    moving = _reads("wall-v1.json", lanes=[_lane("build", 2.0), _lane("ship", beat * 40)])
    phases, note, _ = board_loop.loop(moving, STAMPED)
    marks = {phase.name for phase in phases if phase.moved}
    assert marks == {"build"}, "the mark does not separate the lane that moved"
    assert "no lane moved" not in note

    # Every lane still: the row is drawn, nothing is marked, and the note is the alarm.
    still = _reads("wall-v1.json", lanes=[_lane("build", 9999.0), _lane("ship", 9999.0)])
    phases, note, _ = board_loop.loop(still, STAMPED)
    assert not any(phase.moved for phase in phases)
    assert "no lane moved this beat" in note
    assert {phase.name for phase in phases if phase.here} == {"build", "ship"}, (
        "`here` collapsed into `moved`; a wedged phase must still show where work sits"
    )


def test_a_lane_with_no_state_stamp_is_never_marked_as_moving() -> None:
    """An absent stamp is an unknown, and an unknown drawn as movement is a false all-clear."""
    phases, note, _ = board_loop.loop(_reads("wall-v1.json", lanes=[_lane("build")]), STAMPED)
    assert not any(phase.moved for phase in phases)
    assert "no lane moved this beat" in note


def test_the_beat_is_the_producers_cadence_and_is_capped() -> None:
    """A producer free to declare a one-hour cadence would mark every lane as moving all day.

    An alarm that is always on carries no information, so the window a mark is taken over is
    bounded above whatever the document says, and an absent or null cadence falls back
    rather than guessing a number of its own.
    """
    assert board_loop.beat(_reads("wall-v1.json", freshness={"cadence_s": 3.0})) == 3.0
    capped = _reads("wall-v1.json", freshness={"cadence_s": 86400})
    assert board_loop.beat(capped) == board_loop.BEAT_CAP_S
    for absent in ({"cadence_s": None}, {}, {"cadence_s": 0}):
        assert board_loop.beat(_reads("wall-v1.json", freshness=absent)) == (
            board_loop.BEAT_FALLBACK_S
        )


def test_the_pass_row_shares_are_of_the_lane_population() -> None:
    """The denominator is stated and is the pass's own, never the backlog's."""
    lanes = [_lane(f"build{n}"[:5]) for n in range(3)] + [_lane("ship")]
    phases, _, _ = board_loop.loop(_reads("wall-v1.json", lanes=lanes), STAMPED)
    shares = {phase.name: phase.share for phase in phases}
    assert shares["build"] is not None and shares["build"].label == "75%"
    assert shares["ship"] is not None and shares["ship"].label == "25%"


def test_a_lane_carrying_no_phase_is_counted_in_the_note_rather_than_dropped() -> None:
    """A lane the pass holds and cannot place is a fact about the pass, not an empty slot."""
    lanes = [_lane("build"), {"id": "lane-nowhere"}]
    _, note, _ = board_loop.loop(_reads("wall-v1.json", lanes=lanes), STAMPED)
    assert "1 of 2 lanes carry no phase" in note


def test_the_backlog_census_keeps_the_histogram_under_its_own_denominator() -> None:
    """Kept rather than dropped: where each record stopped is real, the heading was not."""
    reads = _reads("wall-v1.json")
    phases, note = board_loop.backlog_phases(reads)
    assert [phase.name for phase in phases] == list(board_loop.PHASES)
    assert [phase.count for phase in phases] == [4, 2, 3, 5, 2, 1, 2]
    assert not note
    assert not any(phase.here or phase.moved for phase in phases), (
        "the census marks a position as if it were movement"
    )


def test_the_census_share_is_of_the_phased_population_and_not_the_section_length() -> None:
    """An unphased unit is in no phase; counting it makes every bar short by the same amount."""
    lopsided = [{"id": f"u-{n}", "phase": "intake"} for n in range(213)]
    lopsided.append({"id": "u-late", "phase": "ship"})
    lopsided.append({"id": "u-none"})
    phases, note = board_loop.backlog_phases(_reads("wall-v1.json", units=lopsided))
    shares = {phase.name: phase.share for phase in phases}
    assert shares["intake"] is not None and shares["intake"].label == "100%"
    assert shares["ship"] is not None and shares["ship"].label == "0%"
    assert shares["intake"].width > shares["ship"].width * 50, "the bar does not rank the counts"
    assert shares["build"] is not None and shares["build"].width == 0.0
    assert "1 of 215 units carry no phase" in note


def test_a_units_section_carrying_no_phase_says_so_rather_than_showing_noughts() -> None:
    """The producer's state at the time this was written, and a nought would have lied."""
    phases, note = board_loop.backlog_phases(readings("no-phase-v1.json"))
    assert [phase.count for phase in phases] == [None] * len(board_loop.PHASES)
    assert all(phase.share is None for phase in phases)
    assert f"phase {board_wall.ABSENT_TEXT}" in note
    assert "12 units" in note, "the note does not say how many units it looked at"


def test_a_phase_the_harness_does_not_declare_is_appended_rather_than_dropped() -> None:
    """The schema leaves `phase` an open string because a foreign harness names its own."""
    foreign = [{"id": "x-1", "phase": "triage"}, {"id": "x-2", "phase": "build"}]
    census, _ = board_loop.backlog_phases(_reads("wall-v1.json", units=foreign))
    assert [phase.name for phase in census][-1] == "triage"
    assert next(phase.count for phase in census if phase.name == "build") == 1

    pass_row, _, _ = board_loop.loop(_reads("wall-v1.json", lanes=foreign), STAMPED)
    assert [phase.name for phase in pass_row][-1] == "triage"


def test_the_two_rows_read_the_two_sections_and_do_not_cross() -> None:
    """The structural statement of the defect: neither population may reach the other's row."""
    reads = _absent("units", _reads("wall-v1.json"))
    phases, _, running = board_loop.loop(reads, STAMPED)
    assert running and sum(phase.count or 0 for phase in phases) == 4, (
        "the pass row went blank when the backlog census could not be read"
    )
    census, note = board_loop.backlog_phases(reads)
    assert all(phase.count is None for phase in census)
    assert "units" in note

    withheld = _absent("lanes", _absent("session", _reads("wall-v1.json")))
    census, _ = board_loop.backlog_phases(withheld)
    assert [phase.count for phase in census] == [4, 2, 3, 5, 2, 1, 2], (
        "the census went blank because no pass was running"
    )


def test_every_declared_phase_draws_a_box_even_at_nought() -> None:
    """The row is the lifecycle in order; a missing box would move every phase left of it."""
    phases, _, _ = board_loop.loop(_reads("wall-v1.json", lanes=[_lane("ship")]), STAMPED)
    assert [phase.name for phase in phases] == list(board_loop.PHASES)
    assert [phase.count for phase in phases] == [0, 0, 0, 0, 0, 0, 1]


# --- a parked record is not work at a phase (basicly-5jkxqk) ----------------


def test_a_deferred_record_is_not_counted_as_work_at_a_phase() -> None:
    """A parked record keeps the phase its worktree binding derives, and is not at it.

    `basicly-3iaw0x` was parked on 2026-09-01 and held a live worktree, so its phase derived
    to `build` and the census drew it as activity. The owner read `build 1` as work.
    """
    units = [
        {"id": "basicly-3iaw0x", "phase": "build", "status": "deferred"},
        {"id": "basicly-live", "phase": "build", "status": "open"},
    ]
    census, _note = board_loop.backlog_phases(_reads("wall-v1.json", units=units))
    assert next(p.count for p in census if p.name == "build") == 1
    assert board_loop.working_phase(units[0]) == ""
    assert board_loop.working_phase(units[1]) == "build"


def test_the_parked_set_is_named_rather_than_a_literal_in_two_places() -> None:
    """The census and the diagram both bin by phase; one spelling, so they cannot disagree."""
    assert "deferred" in board_loop.PARKED
    # A status the table does not name is work: `blocked` still sits at its phase, because a
    # blocked record is waiting on something rather than parked by a person.
    assert board_loop.working_phase({"phase": "build", "status": "blocked"}) == "build"
    assert board_loop.working_phase({"phase": "build"}) == "build"


def test_phase_of_still_answers_for_a_row_that_carries_no_status() -> None:
    """A row with no status is not parked, so the rule must not swallow a lane.

    `lanes[]` rows carry no status and `board_regions` reads their phase off the same helper.
    """
    assert board_loop.phase_of({"phase": "build"}) == "build"
    assert board_loop.working_phase({"phase": "build", "status": ""}) == "build"
