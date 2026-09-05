"""The drawn loop, asserted against the questions a histogram could not answer.

The owner's report was that the page carries no diagram of the factory - so each test here
is one thing a diagram says and seven counted boxes cannot:

* the **shape** is the loop's own, and the vocabulary is the engine's rather than this
  module's opinion of it - the phases, the three human checkpoints, the one merge, and the
  artifact each transition produces, each pinned to the file the engine reads;
* a **lane an agent is inside** is distinguishable from a unit merely resting at a phase,
  and the two counts are disjoint rather than nested;
* a **person blocking a station** is drawn on the station they block, with an age, and an
  ask that pins to nothing is reported rather than attached to the nearest box;
* the **merge edge** carries the landing verdict and no other edge does, because a red gate
  named seven times is named nowhere;
* what the contract **cannot** say is said out loud instead of implied.
"""

from __future__ import annotations

from datetime import timedelta

from basicly import board_diagram, board_loop, board_wall, config
from tests.test_board_wall import REPO_ROOT, STAMPED, readings

SCHEMAS = REPO_ROOT / ".basicly" / "core" / "schemas"


def _reads(name: str, **override: object) -> board_wall.Readings:
    """A fixture's readings, with named sections replaced by hand-built ones."""
    reads = board_wall.Readings(readings(name))
    for key, value in override.items():
        reads[key] = board_wall.Reading(key, board_wall.BY_KEY[board_wall.RENDERABLE], "", value)
    return reads


def _absent(name: str, reads: board_wall.Readings) -> board_wall.Readings:
    """*reads* with *name* replaced by the reading a section the producer omitted gets."""
    dropped = board_wall.Readings(reads)
    dropped[name] = board_wall.Reading(name, board_wall.BY_KEY[board_wall.ABSENT], "absent")
    return dropped


def _lane(ident: str, phase: str, *, ago_s: float = 2.0, state: str = "running") -> dict:
    """One lane at *phase*, having entered *state* *ago_s* seconds before `STAMPED`."""
    stamp = STAMPED - timedelta(seconds=ago_s)
    return {
        "id": ident,
        "phase": phase,
        "state": state,
        "agent": "claude",
        "model": "claude-opus-5",
        "state_since": stamp.isoformat().replace("+00:00", "Z"),
    }


def _ask(subject: str, waiting_s: float = 90.0) -> dict:
    """One pending ask naming *subject*, which the diagram tries to pin to a station."""
    return {"wait_id": "w", "issue": "basicly-x", "subject": subject, "waiting_s": waiting_s}


def _drawn(name: str = "wall-v1.json", **override: object) -> board_diagram.Diagram:
    """The diagram a fixture produces, against the fixture's own instant."""
    return board_diagram.diagram(_reads(name, **override), STAMPED)


def test_the_shape_is_the_engines_own_loop_and_not_this_modules_opinion_of_it() -> None:
    """A diagram whose stations drift from `config.LOOP_PHASES` teaches a wrong workflow."""
    assert board_diagram.CHAIN[0] == board_diagram.HOPPER == config.LOOP_PHASES[0]
    assert tuple(config.LOOP_PHASES[1:]) == board_diagram.STATIONS
    assert board_diagram.CHAIN[-1] == board_diagram.SINK == "done"
    assert frozenset(config.CHECKPOINTS) == board_diagram.CHECKPOINTS
    assert board_diagram.PHASES == config.LOOP_PHASES, "the two phase lists have diverged"


def test_every_artifact_the_diagram_names_is_a_schema_the_repo_ships() -> None:
    """The edge labels are the engine's artifact vocabulary, pinned to the files it reads.

    `intake` names none on purpose: nothing is produced by sitting in the backlog. Asserted
    as a set rather than per-edge so an artifact added to the engine and not to the diagram
    is caught by the second half.
    """
    shipped = {path.name.removesuffix(".schema.json") for path in SCHEMAS.glob("*.schema.json")}
    assert shipped, "the positive control is empty, so this probe proves nothing"
    named = {
        part.strip() for label in board_diagram.ARTIFACTS.values() for part in label.split("+")
    }
    assert named <= shipped, (
        f"the diagram names an artifact the repo ships no schema for: {named - shipped}"
    )
    assert board_diagram.HOPPER not in board_diagram.ARTIFACTS
    assert set(board_diagram.ARTIFACTS) == set(board_diagram.STATIONS)


def test_every_edge_carries_the_artifact_its_source_produces() -> None:
    """The artifact travels the edge *leaving* the phase that made it, never the one into it."""
    flows = {(flow.frm, flow.to): flow for flow in _drawn().flows}
    assert len(flows) == len(board_diagram.CHAIN) - 1 == 7
    assert flows[("classify", "decompose")].artifact.startswith("classification")
    assert flows[("build", "verify")].artifact == "change-summary"
    assert flows[("ship", "done")].artifact == "release-record", (
        "the terminal edge was lost, so the release-record is drawn nowhere"
    )
    assert not flows[("intake", "classify")].artifact


def test_the_three_human_checkpoints_sit_on_the_edges_they_gate() -> None:
    """A checkpoint gates *leaving* its phase, so it belongs to the outgoing edge."""
    marked = {flow.frm for flow in _drawn().flows if flow.checkpoint}
    assert marked == frozenset(config.CHECKPOINTS) == {"classify", "decompose", "ship"}


def test_only_the_one_merge_carries_the_landing_verdict() -> None:
    """`build -> verify` is the loop's only merge and the only place the landing gate binds.

    A red gate painted on seven edges is painted nowhere, so the verdict is asserted to be
    absent from the other six rather than merely present on this one.
    """
    failing = {
        "passed": False,
        "checks": [{"name": "pytest", "status": "fail"}, {"name": "ruff", "status": "pass"}],
    }
    flows = _drawn(gates=failing).flows
    merges = [flow for flow in flows if flow.merge]
    assert [(flow.frm, flow.to) for flow in merges] == [("build", "verify")]
    assert merges[0].verdict == "pytest", "the merge edge does not name what will fail"
    assert not any(flow.verdict for flow in flows if not flow.merge)

    green = _drawn(gates={"passed": True, "checks": []}).flows
    assert next(flow.verdict for flow in green if flow.merge) == "green"

    # A producer that reports no verdict draws none, rather than a reassuring green.
    silent = _absent("gates", _reads("wall-v1.json"))
    assert not any(flow.verdict for flow in board_diagram.diagram(silent, STAMPED).flows)


def test_a_failing_gate_that_names_no_check_still_says_it_is_red() -> None:
    """`passed: false` is the fact; the check list is detail a producer need not hold."""
    flows = _drawn(gates={"passed": False}).flows
    assert next(flow.verdict for flow in flows if flow.merge) == "red"


def test_a_dispatched_lane_is_a_dot_and_never_also_a_resting_count() -> None:
    """The record asks that the two be distinguishable; nesting them makes one unit two."""
    units = [{"id": f"u-{n}", "phase": "build"} for n in range(4)]
    lanes = [_lane("u-1", "build"), _lane("u-2", "build")]
    build = next(s for s in _drawn(units=units, lanes=lanes).stations if s.name == "build")
    assert build.count == 2, "a unit an agent is inside was counted as resting as well"
    assert len(build.lanes) == 2
    assert build.count + len(build.lanes) == len(units)


def test_a_lane_whose_id_is_in_no_units_row_is_still_drawn() -> None:
    """The shipped fixture is that shape already, so a join would have drawn no lane at all."""
    reads = _reads("wall-v1.json")
    known = {row.get("id") for row in reads["units"].dicts}
    assert not {row.get("id") for row in reads["lanes"].dicts} & known
    drawn = board_diagram.diagram(reads, STAMPED)
    assert sum(len(station.lanes) for station in drawn.stations) == len(reads["lanes"].dicts)


def test_a_lane_that_moved_this_beat_is_marked_and_a_wedged_one_is_marked_apart() -> None:
    """Two marks, because a lane between steps is quiet and is not a wedged lane.

    Collapsing them would report every working lane as wedged in the beats it did not move,
    which is the alarm-that-is-always-on shape.
    """
    window = board_loop.beat(_reads("wall-v1.json"))
    lanes = [
        _lane("a", "build", ago_s=1.0),
        _lane("b", "build", ago_s=window * 100),
        _lane("c", "build", ago_s=1.0, state="refused"),
    ]
    build = next(s for s in _drawn(lanes=lanes).stations if s.name == "build")
    # By position, not keyed by label: all three carry the same agent, and a dict would
    # collapse them into one and pass on whichever happened to be last.
    assert [(lane.moved, lane.stuck) for lane in build.lanes] == [
        (True, False),
        (False, False),
        (True, True),
    ]


def test_a_station_reports_the_lanes_it_had_no_room_to_draw() -> None:
    """Three dots read at six metres and a fourth is a smudge, so the rest is a count."""
    lanes = [_lane(f"l-{n}", "build") for n in range(board_diagram.LANE_MARKS + 2)]
    build = next(s for s in _drawn(lanes=lanes).stations if s.name == "build")
    assert len(build.lanes) == board_diagram.LANE_MARKS
    assert build.dropped == 2


def test_a_person_blocking_a_station_is_drawn_on_it_with_an_age() -> None:
    """The record's own words: the node a human blocks carries it, with its age."""
    stations = {s.name: s for s in _drawn(asks=[_ask("ship", 7200.0)]).stations}
    assert stations["ship"].waiting
    assert stations["ship"].waiting != stations["ship"].waiting.upper(), (
        "the diagram shouts an age in the watch band's own register"
    )
    assert not any(s.waiting for s in stations.values() if s.name != "ship")


def test_an_ask_that_pins_to_no_station_is_reported_rather_than_attached() -> None:
    """The pin is `subject` matching a phase name - this producer's habit, not a contract."""
    drawn = _drawn(asks=[_ask("ship"), _ask("a-record-id"), _ask("")])
    assert "2 ask(s) name no phase" in drawn.note
    assert sum(1 for station in drawn.stations if station.waiting) == 1


def test_intake_is_the_hopper_and_never_a_station() -> None:
    """It is the derivation's default rung, so its count is untouched backlog, not a stage."""
    drawn = _drawn()
    assert board_diagram.HOPPER not in {station.name for station in drawn.stations}
    assert drawn.hopper.name == "intake"
    assert drawn.hopper.count is not None


def test_the_sink_draws_a_whole_tally_and_the_head_it_drained_to() -> None:
    """A closed count is a tally; `770.0` is a float that escaped a numeric coercion."""
    drawn = _drawn()
    assert isinstance(drawn.sink.count, int)
    assert "main" in drawn.sink.detail


def test_a_section_the_producer_withheld_costs_its_own_half_and_never_the_drawing() -> None:
    """A diagram that blanked on one absent section would hide the six facts it still has."""
    reads = _absent("units", _absent("lanes", _reads("wall-v1.json")))
    drawn = board_diagram.diagram(reads, STAMPED)
    assert len(drawn.stations) == len(board_diagram.STATIONS)
    assert all(station.count is None for station in drawn.stations)
    assert all(not station.lanes for station in drawn.stations)
    assert "units" in drawn.note and "lanes" in drawn.note
    assert len(drawn.flows) == 7, "the shape went away with the data"


def test_the_drawing_always_says_it_cannot_track_an_artifact() -> None:
    """The contract carries no artifact state, and a named artifact looks like a tracked one."""
    assert "the contract carries no artifact state" in _drawn().note


def test_every_placement_is_inside_the_drawing_surface() -> None:
    """A station centred past the viewBox is clipped in silence; there is no scrollbar."""
    drawn = _drawn()
    half_w, half_h = board_diagram.BOX_W / 2, board_diagram.BOX_H / 2
    for node in (*drawn.stations, drawn.hopper, drawn.sink):
        assert half_w <= node.x <= drawn.width - half_w, f"{node.name} is off the surface"
        assert half_h <= node.y <= drawn.height - half_h, f"{node.name} is off the surface"
    assert len({(node.x, node.y) for node in drawn.stations}) == len(drawn.stations), (
        "two stations share a centre, so one is drawn over the other"
    )
