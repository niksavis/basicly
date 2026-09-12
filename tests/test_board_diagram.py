from __future__ import annotations

from datetime import timedelta

from basicly import board_diagram, board_loop, board_wall, config
from tests.test_board_wall import REPO_ROOT, STAMPED, readings

SCHEMAS = REPO_ROOT / ".basicly" / "core" / "schemas"


def _reads(name: str, **override: object) -> board_wall.Readings:
    reads = board_wall.Readings(readings(name))
    for key, value in override.items():
        reads[key] = board_wall.Reading(key, board_wall.BY_KEY[board_wall.RENDERABLE], "", value)
    return reads


def _absent(name: str, reads: board_wall.Readings) -> board_wall.Readings:
    dropped = board_wall.Readings(reads)
    dropped[name] = board_wall.Reading(name, board_wall.BY_KEY[board_wall.ABSENT], "absent")
    return dropped


def _lane(ident: str, phase: str, *, ago_s: float = 2.0, state: str = "running") -> dict:
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
    return {"wait_id": "w", "issue": "basicly-x", "subject": subject, "waiting_s": waiting_s}


def _drawn(name: str = "wall-v1.json", **override: object) -> board_diagram.Diagram:
    return board_diagram.diagram(_reads(name, **override), STAMPED)


def test_the_shape_is_the_engines_own_loop_and_not_this_modules_opinion_of_it() -> None:
    assert board_diagram.CHAIN[0] == board_diagram.HOPPER == config.LOOP_PHASES[0]
    assert tuple(config.LOOP_PHASES[1:]) == board_diagram.STATIONS
    assert board_diagram.CHAIN[-1] == board_diagram.SINK == "done"
    assert frozenset(config.CHECKPOINTS) == board_diagram.CHECKPOINTS
    assert board_diagram.PHASES == config.LOOP_PHASES, "the two phase lists have diverged"


def test_every_artifact_the_diagram_names_is_a_schema_the_repo_ships() -> None:

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
    flows = {(flow.frm, flow.to): flow for flow in _drawn().flows}
    assert len(flows) == len(board_diagram.CHAIN) - 1 == 7
    assert flows[("classify", "decompose")].artifact.startswith("classification")
    assert flows[("build", "verify")].artifact == "change-summary"
    assert flows[("ship", "done")].artifact == "release-record", (
        "the terminal edge was lost, so the release-record is drawn nowhere"
    )
    assert not flows[("intake", "classify")].artifact


def test_the_three_human_checkpoints_sit_on_the_edges_they_gate() -> None:
    marked = {flow.frm for flow in _drawn().flows if flow.checkpoint}
    assert marked == frozenset(config.CHECKPOINTS) == {"classify", "decompose", "ship"}


def test_only_the_one_merge_carries_the_landing_verdict() -> None:

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

    silent = _absent("gates", _reads("wall-v1.json"))
    assert not any(flow.verdict for flow in board_diagram.diagram(silent, STAMPED).flows)


def test_a_failing_gate_that_names_no_check_still_says_it_is_red() -> None:
    flows = _drawn(gates={"passed": False}).flows
    assert next(flow.verdict for flow in flows if flow.merge) == "red"


def test_a_dispatched_lane_is_a_dot_and_never_also_a_resting_count() -> None:
    units = [{"id": f"u-{n}", "phase": "build"} for n in range(4)]
    lanes = [_lane("u-1", "build"), _lane("u-2", "build")]
    build = next(s for s in _drawn(units=units, lanes=lanes).stations if s.name == "build")
    assert build.count == 2, "a unit an agent is inside was counted as resting as well"
    assert len(build.lanes) == 2
    assert build.count + len(build.lanes) == len(units)


def test_a_lane_whose_id_is_in_no_units_row_is_still_drawn() -> None:
    reads = _reads("wall-v1.json")
    known = {row.get("id") for row in reads["units"].dicts}
    assert not {row.get("id") for row in reads["lanes"].dicts} & known
    drawn = board_diagram.diagram(reads, STAMPED)
    assert sum(len(station.lanes) for station in drawn.stations) == len(reads["lanes"].dicts)


def test_a_lane_that_moved_this_beat_is_marked_and_a_wedged_one_is_marked_apart() -> None:

    window = board_loop.beat(_reads("wall-v1.json"))
    lanes = [
        _lane("a", "build", ago_s=1.0),
        _lane("b", "build", ago_s=window * 100),
        _lane("c", "build", ago_s=1.0, state="refused"),
    ]
    build = next(s for s in _drawn(lanes=lanes).stations if s.name == "build")
    assert [(lane.moved, lane.stuck) for lane in build.lanes] == [
        (True, False),
        (False, False),
        (True, True),
    ]


def test_a_station_counts_the_crew_it_cannot_name() -> None:
    lanes = [_lane(f"l-{n}", "build") for n in range(board_diagram.LANE_MARKS + 2)]
    build = next(s for s in _drawn(lanes=lanes).stations if s.name == "build")
    assert len(build.lanes) == board_diagram.LANE_MARKS, "the dots are still bounded"
    assert build.crew == "5 agents", "every lane is counted, not the three that got a dot"
    alone = next(s for s in _drawn(lanes=[_lane("solo", "build")]).stations if s.name == "build")
    assert alone.crew.startswith("claude"), "one lane is named rather than counted as one"
    assert len(alone.crew) <= board_diagram.AGENT_MAX, "and the name fits the box it sits under"


def test_the_crew_gives_its_row_to_a_person_who_is_waiting() -> None:
    quiet = {s.name: s for s in _drawn(lanes=[_lane("solo", "build")]).stations}
    assert quiet["build"].crew_row == 0
    both = _drawn(lanes=[_lane("solo", "build")], asks=[_ask("build", 60.0)])
    stations = {s.name: s for s in both.stations}
    assert stations["build"].waiting, "the ask still reaches the station it names"
    assert stations["build"].crew_row == 1, "the wait takes the first line and says so"


def test_a_person_blocking_a_station_is_drawn_on_it_with_an_age() -> None:
    stations = {s.name: s for s in _drawn(asks=[_ask("ship", 7200.0)]).stations}
    assert stations["ship"].waiting
    assert stations["ship"].waiting != stations["ship"].waiting.upper(), (
        "the diagram shouts an age in the watch band's own register"
    )
    assert not any(s.waiting for s in stations.values() if s.name != "ship")


def test_an_ask_that_pins_to_no_station_is_reported_rather_than_attached() -> None:
    drawn = _drawn(asks=[_ask("ship"), _ask("a-record-id"), _ask("")])
    assert "2 ask(s) name no phase" in drawn.note
    assert sum(1 for station in drawn.stations if station.waiting) == 1


def test_intake_is_the_hopper_and_never_a_station() -> None:
    drawn = _drawn()
    assert board_diagram.HOPPER not in {station.name for station in drawn.stations}
    assert drawn.hopper.name == "intake"
    assert drawn.hopper.count is not None


def test_the_sink_draws_a_whole_tally_and_the_head_it_drained_to() -> None:
    drawn = _drawn()
    assert isinstance(drawn.sink.count, int)
    assert "main" in drawn.sink.detail


def test_a_section_the_producer_withheld_costs_its_own_half_and_never_the_drawing() -> None:
    reads = _absent("units", _absent("lanes", _reads("wall-v1.json")))
    drawn = board_diagram.diagram(reads, STAMPED)
    assert len(drawn.stations) == len(board_diagram.STATIONS)
    assert all(station.count is None for station in drawn.stations)
    assert all(not station.lanes for station in drawn.stations)
    assert "units" in drawn.note and "lanes" in drawn.note
    assert len(drawn.flows) == 7, "the shape went away with the data"


def test_the_drawing_always_says_it_cannot_track_an_artifact() -> None:
    assert "the contract carries no artifact state" in _drawn().note


def test_a_terminal_detail_cannot_reach_past_the_surface() -> None:

    drawn = _drawn()
    for end in (drawn.hopper, drawn.sink):
        assert len(end.detail) <= board_diagram.DETAIL_MAX, f"{end.name} runs off the surface"
    assert board_diagram.DETAIL_MAX * 5.5 <= board_diagram.SLOT, (
        "the bound itself is wider than the surface a centred detail is given"
    )


def test_every_placement_is_inside_the_drawing_surface() -> None:
    drawn = _drawn()
    half_w, half_h = board_diagram.BOX_W / 2, board_diagram.BOX_H / 2
    for node in (*drawn.stations, drawn.hopper, drawn.sink):
        assert half_w <= node.x <= drawn.width - half_w, f"{node.name} is off the surface"
        assert half_h <= node.y <= drawn.height - half_h, f"{node.name} is off the surface"
    nodes = (*drawn.stations, drawn.hopper, drawn.sink)
    assert len({(node.x, node.y) for node in nodes}) == len(nodes), (
        "two drawn nodes share a centre, so one is painted over the other"
    )
    assert (drawn.sink.x, drawn.sink.y) == board_diagram._place(len(board_diagram.CHAIN) - 1)
    assert (drawn.hopper.x, drawn.hopper.y) == board_diagram._place(0)


def test_a_station_bar_is_measured_against_the_loop_and_not_the_backlog() -> None:
    units = [{"id": f"u-{n}", "phase": "intake"} for n in range(200)]
    units.append({"id": "u-build", "phase": "build"})
    stations = {s.name: s for s in _drawn(units=units).stations}
    assert stations["build"].fill == board_diagram.BOX_W - board_diagram.BAR_INSET * 2, (
        "the only unit inside the loop fills its bar, whatever the hopper holds"
    )
    assert all(s.fill == 0.0 for s in stations.values() if s.name != "build")
