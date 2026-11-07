from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.swarm_fixtures import _finding, _reply, swarm


def test_a_property_with_a_space_is_refused_with_the_hyphen_form(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"e\.g\. done-states"):
        swarm.main([
            "climb-init",
            "--state",
            str(tmp_path / "s.json"),
            "--properties",
            "done states",
        ])


LENSES = ("a,b,c", "b,c,d", "c,d,e", "d,e,f", "e,f,g")


def _graded(directory: Path, lenses: str, grades: dict, severity: str | None = None) -> None:
    for lens in lenses.split(","):
        findings = (
            [_finding(severity, f"plan.md:{lens}1")] if severity and lens == lenses[0] else []
        )
        _reply(directory, lens, {"grades": grades, "findings": findings})


def _climb(tmp_path: Path, properties: str = "x,y", cap: int = 5) -> Path:
    state = tmp_path / "climb.json"
    args = ["climb-init", "--state", str(state), "--properties", properties, "--cap", str(cap)]
    assert swarm.main(args) == 0
    return state


def _round(state: Path, directory: Path, lenses: str, *extra: str) -> int:
    args = ["climb-round", "--state", str(state), "--lenses", lenses, "--dir", str(directory)]
    return swarm.main([*args, *extra])


def _decision(state: Path) -> str:
    return swarm.decide(json.loads(state.read_text(encoding="utf-8")))


def test_the_lowest_grade_is_the_property_score(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", "a,b", {"x": 4, "y": 9}, "important")
    _reply(tmp_path / "r1", "c", {"grades": {"x": 6, "y": 7}, "findings": []})
    assert _round(state, tmp_path / "r1", "a,b,c") == 0
    assert _decision(state).startswith("continue")
    scores = json.loads(state.read_text(encoding="utf-8"))["rounds"][0]["scores"]
    assert scores == {"x": 4, "y": 7}


def test_no_rise_of_a_full_point_stops_the_climb(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", LENSES[0], {"x": 5, "y": 5}, "important")
    _round(state, tmp_path / "r1", LENSES[0])
    _graded(tmp_path / "r2", LENSES[1], {"x": 5.5, "y": 5.9}, "important")
    _round(state, tmp_path / "r2", LENSES[1])
    assert _decision(state) == "stop: no property rose 1.0 since the last round"


def test_two_clean_rounds_stop_the_climb(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", LENSES[0], {"x": 5, "y": 5})
    _round(state, tmp_path / "r1", LENSES[0])
    _graded(tmp_path / "r2", LENSES[1], {"x": 7, "y": 7})
    _round(state, tmp_path / "r2", LENSES[1])
    assert _decision(state).startswith("stop: 2 rounds in a row")


def test_the_cap_stops_a_climb_that_keeps_rising(tmp_path: Path) -> None:
    state = _climb(tmp_path, "x", cap=2)
    _graded(tmp_path / "r1", LENSES[0], {"x": 1}, "important")
    _round(state, tmp_path / "r1", LENSES[0])
    _graded(tmp_path / "r2", LENSES[1], {"x": 5}, "important")
    _round(state, tmp_path / "r2", LENSES[1])
    assert _decision(state) == "stop: the cap of 2 rounds is reached"


def test_a_property_no_reviewer_graded_refuses_the_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", LENSES[0], {"x": 5})
    assert _round(state, tmp_path / "r1", LENSES[0]) == 1
    assert "round refused: no reviewer graded y" in capsys.readouterr().out


def test_a_partial_reviewer_still_counts_for_what_it_graded(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", "a,b", {"x": 5, "y": 6})
    _reply(tmp_path / "r1", "c", {"grades": {"x": 3}, "findings": []})
    _round(state, tmp_path / "r1", "a,b,c")
    round_one = json.loads(state.read_text(encoding="utf-8"))["rounds"][0]
    assert (round_one["scores"], round_one["highs"]) == ({"x": 3, "y": 6}, {"x": 5, "y": 6})


def test_a_round_after_the_stop_needs_the_past_stop_flag(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    for index, grades in enumerate(({"x": 5, "y": 5}, {"x": 5, "y": 5})):
        _graded(tmp_path / f"r{index}", LENSES[index], grades, "important")
        _round(state, tmp_path / f"r{index}", LENSES[index])
    _graded(tmp_path / "r3", LENSES[2], {"x": 9, "y": 9})
    with pytest.raises(SystemExit, match="add --past-stop only when the user asked"):
        _round(state, tmp_path / "r3", LENSES[2])
    assert _round(state, tmp_path / "r3", LENSES[2], "--past-stop") == 0
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["rounds"][-1]["past_stop"] is True
    assert swarm.decide(after).startswith("stop: this round ran past the stop rule")


def test_past_stop_on_a_running_climb_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", LENSES[0], {"x": 5, "y": 5})
    with pytest.raises(SystemExit, match="only for a round after a stop"):
        _round(state, tmp_path / "r1", LENSES[0], "--past-stop")


def test_a_missing_reviewer_reply_refuses_the_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", "a,b", {"x": 5, "y": 5})
    assert _round(state, tmp_path / "r1", "a,b,c") == 1
    assert "- c: not run (no c.md)" in capsys.readouterr().out
    assert json.loads(state.read_text(encoding="utf-8"))["rounds"] == []


@pytest.mark.parametrize(
    ("lenses", "message"),
    [("a,b", "use 3 to 6 lenses"), ("a,b,c,d,e,f,g", "use 3 to 6 lenses")],
)
def test_a_round_needs_three_to_six_lenses(tmp_path: Path, lenses: str, message: str) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", lenses, {"x": 5, "y": 5})
    with pytest.raises(SystemExit, match=message):
        _round(state, tmp_path / "r1", lenses)


def test_a_round_reusing_the_last_lenses_or_folder_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", LENSES[0], {"x": 5, "y": 5}, "important")
    _round(state, tmp_path / "r1", LENSES[0])
    _graded(tmp_path / "r2", LENSES[0], {"x": 7, "y": 7})
    with pytest.raises(SystemExit, match="at least one lens not used in the last round"):
        _round(state, tmp_path / "r2", LENSES[0])
    _graded(tmp_path / "r1", LENSES[1], {"x": 7, "y": 7})
    with pytest.raises(SystemExit, match="already recorded"):
        _round(state, tmp_path / "r1", LENSES[1])


def test_refuted_findings_do_not_count_as_important(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", LENSES[0], {"x": 5, "y": 5}, "important")
    assert _round(state, tmp_path / "r1", LENSES[0], "--refuted", "1") == 0
    assert json.loads(state.read_text(encoding="utf-8"))["rounds"][0]["important"] == 0
    _graded(tmp_path / "r2", LENSES[1], {"x": 5, "y": 5})
    with pytest.raises(SystemExit, match="--refuted must be 0 to 0"):
        _round(state, tmp_path / "r2", LENSES[1], "--refuted", "1")


def test_a_second_climb_on_the_same_state_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    with pytest.raises(SystemExit, match="one state file per climb"):
        swarm.main(["climb-init", "--state", str(state), "--properties", "x"])


@pytest.mark.parametrize("bad", [11, -1, True])
def test_a_grade_outside_zero_to_ten_does_not_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: object
) -> None:
    state = _climb(tmp_path, "x")
    _graded(tmp_path / "r1", LENSES[0], {"x": bad})
    assert _round(state, tmp_path / "r1", LENSES[0]) == 1
    out = capsys.readouterr().out
    assert "no 0-10 grade for x" in out
    assert "round refused: no reviewer graded x" in out


def test_a_negative_refuted_count_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", LENSES[0], {"x": 5, "y": 5}, "important")
    with pytest.raises(SystemExit, match="--refuted must be 0 to"):
        _round(state, tmp_path / "r1", LENSES[0], "--refuted", "-1")


def test_a_climb_reply_without_grades_refuses_the_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _climb(tmp_path)
    for lens in ("a", "b", "c"):
        _reply(tmp_path / "r1", lens, {"findings": []})
    assert _round(state, tmp_path / "r1", "a,b,c") == 1
    assert "the json block has no 'grades' field" in capsys.readouterr().out


def test_a_round_whose_lenses_are_a_subset_of_the_last_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _graded(tmp_path / "r1", "a,b,c,d", {"x": 5, "y": 5}, "important")
    _round(state, tmp_path / "r1", "a,b,c,d")
    _graded(tmp_path / "r2", "a,b,c", {"x": 7, "y": 7})
    with pytest.raises(SystemExit, match="at least one lens not used"):
        _round(state, tmp_path / "r2", "a,b,c")
