from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".basicly/core/skills/swarm/scripts/swarm.py"
_spec = importlib.util.spec_from_file_location("swarm_script", SCRIPT)
assert _spec is not None and _spec.loader is not None
swarm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = swarm
_spec.loader.exec_module(swarm)


def _reply(directory: Path, lens: str, payload: dict | None, prose: str = "Done.\n") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    block = "" if payload is None else f"```json\n{json.dumps(payload)}\n```\n"
    (directory / f"{lens}.md").write_text(prose + block, encoding="utf-8")


def _finding(severity: str, evidence: str, claim: str = "c") -> dict:
    return {"severity": severity, "claim": claim, "evidence": evidence, "fix": "f"}


def test_a_missing_reply_is_not_run_and_fails_the_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _reply(tmp_path, "correctness", {"lens": "correctness", "findings": []})
    assert swarm.main(["check", "--lenses", "correctness,security", "--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "| security | not run: no security.md |" in out
    assert "incomplete: security not run or malformed" in out


def test_a_reply_without_a_json_block_is_malformed(tmp_path: Path) -> None:
    _reply(tmp_path, "facts", None, prose="I think it is fine.\n")
    assert swarm.read_report(tmp_path, "facts").status == "malformed"


def test_a_verdict_other_than_pass_or_fail_is_malformed(tmp_path: Path) -> None:
    _reply(tmp_path, "facts", {"verdict": "maybe", "findings": []})
    assert swarm.read_report(tmp_path, "facts").problem == "verdict must be pass or fail"


def test_an_unknown_severity_is_malformed(tmp_path: Path) -> None:
    _reply(tmp_path, "facts", {"findings": [_finding("critical", "a.py:1")]})
    report = swarm.read_report(tmp_path, "facts")
    assert (report.status, "severity must be one of" in report.problem) == ("malformed", True)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("src/a.py:12", True),
        ("$ pytest -q\n3 passed", True),
        ("ran: rg foo", True),
        ("I believe so", False),
    ],
)
def test_evidence_needs_a_location_or_a_command(evidence: str, expected: bool) -> None:
    assert swarm.verified({"evidence": evidence}) is expected


def test_two_lenses_on_one_line_note_each_other(tmp_path: Path) -> None:
    _reply(tmp_path, "a", {"findings": [_finding("nit", "src/x.py:3")]})
    _reply(tmp_path, "b", {"findings": [_finding("important", "src/x.py:3")]})
    merged = swarm.merge([swarm.read_report(tmp_path, "a"), swarm.read_report(tmp_path, "b")])
    assert [(m["lens"], m["severity"], m["also"]) for m in merged] == [
        ("b", "important", ["a"]),
        ("a", "nit", ["b"]),
    ]


def test_findings_citing_a_shared_line_stay_separate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shared = "src/registry.py:18"
    findings = [
        _finding("important", shared, "x: delete"),
        _finding("important", shared, "y: keep"),
    ]
    _reply(tmp_path, "usage", {"findings": findings})
    swarm.main(["check", "--lenses", "usage", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "- [important] usage: x: delete\n" in out
    assert "- [important] usage: y: keep\n" in out


def test_command_evidence_never_links_findings(tmp_path: Path) -> None:
    _reply(tmp_path, "a", {"findings": [_finding("important", "ran: pytest a", "one")]})
    _reply(tmp_path, "b", {"findings": [_finding("nit", "ran: ruff", "two")]})
    merged = swarm.merge([swarm.read_report(tmp_path, "a"), swarm.read_report(tmp_path, "b")])
    assert [m["also"] for m in merged] == [[], []]


def test_the_verdict_is_shown_in_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _reply(tmp_path, "a", {"verdict": "fail", "findings": []})
    swarm.main(["check", "--lenses", "a", "--dir", str(tmp_path)])
    assert "| a | ok | fail |" in capsys.readouterr().out


def test_an_unverified_finding_is_marked_in_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _reply(tmp_path, "a", {"findings": [_finding("important", "trust me", claim="bad")]})
    assert swarm.main(["check", "--lenses", "a", "--dir", str(tmp_path)]) == 0
    assert "- [important UNVERIFIED] a: bad" in capsys.readouterr().out


def _vote(directory: Path, voter: str, vote: str) -> None:
    _reply(directory, voter, {"vote": vote, "reason": "r", "strongest_counter": f"against {vote}"})


def test_a_majority_of_all_voters_decides_and_shows_the_dissent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _vote(tmp_path, "user", "a")
    _vote(tmp_path, "safety", "a")
    _vote(tmp_path, "skeptic", "b")
    assert (
        swarm.main([
            "tally",
            "--voters",
            "user,safety,skeptic",
            "--options",
            "a,b",
            "--dir",
            str(tmp_path),
        ])
        == 0
    )
    out = capsys.readouterr().out
    assert "decision: a" in out
    assert "dissent (skeptic): against b" in out


def test_a_missing_vote_counts_against_every_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _vote(tmp_path, "user", "a")
    _vote(tmp_path, "safety", "b")
    assert (
        swarm.main([
            "tally",
            "--voters",
            "user,safety,skeptic",
            "--options",
            "a,b",
            "--dir",
            str(tmp_path),
        ])
        == 2
    )
    out = capsys.readouterr().out
    assert "skeptic: not run (no skeptic.md); counts against every option" in out
    assert "the user decides" in out


def test_a_vote_outside_the_options_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _vote(tmp_path, "user", "a")
    _vote(tmp_path, "safety", "a")
    _vote(tmp_path, "skeptic", "A")
    assert (
        swarm.main([
            "tally",
            "--voters",
            "user,safety,skeptic",
            "--options",
            "a,b",
            "--dir",
            str(tmp_path),
        ])
        == 0
    )
    out = capsys.readouterr().out
    assert "skeptic: not run (vote 'A' is not one of a, b)" in out
    assert "dissent (skeptic): did not vote" in out


def test_an_even_council_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="odd number of voters"):
        swarm.main(["tally", "--voters", "a,b", "--options", "x,y", "--dir", str(tmp_path)])


def test_a_property_with_a_space_is_refused_with_the_hyphen_form(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"e\.g\. done-states"):
        swarm.main([
            "climb-init",
            "--state",
            str(tmp_path / "s.json"),
            "--properties",
            "done states",
        ])


def test_a_lens_named_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="named twice"):
        swarm.main(["check", "--lenses", "a,a", "--dir", str(tmp_path)])


def _grade(directory: Path, lens: str, grades: dict, severity: str | None = None) -> None:
    findings = [_finding(severity, f"plan.md:{len(grades)}")] if severity else []
    _reply(directory, lens, {"grades": grades, "findings": findings})


def _climb(tmp_path: Path) -> Path:
    state = tmp_path / "climb.json"
    assert swarm.main(["climb-init", "--state", str(state), "--properties", "x,y"]) == 0
    return state


def _round(state: Path, directory: Path, lenses: str) -> str:
    swarm.main(["climb-round", "--state", str(state), "--lenses", lenses, "--dir", str(directory)])
    return swarm.decide(json.loads(state.read_text(encoding="utf-8")))


def test_the_lowest_grade_is_the_property_score(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _grade(tmp_path / "r1", "a", {"x": 4, "y": 9}, "important")
    _grade(tmp_path / "r1", "b", {"x": 6, "y": 7})
    assert _round(state, tmp_path / "r1", "a,b").startswith("continue")
    scores = json.loads(state.read_text(encoding="utf-8"))["rounds"][0]["scores"]
    assert scores == {"x": 4, "y": 7}


def test_no_rise_of_a_full_point_stops_the_climb(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _grade(tmp_path / "r1", "a", {"x": 5, "y": 5}, "important")
    _round(state, tmp_path / "r1", "a")
    _grade(tmp_path / "r2", "b", {"x": 5.5, "y": 5.9}, "important")
    assert _round(state, tmp_path / "r2", "b") == "stop: no property rose 1.0 since the last round"


def test_two_clean_rounds_stop_the_climb(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _grade(tmp_path / "r1", "a", {"x": 5, "y": 5})
    _round(state, tmp_path / "r1", "a")
    _grade(tmp_path / "r2", "b", {"x": 7, "y": 7})
    assert _round(state, tmp_path / "r2", "b").startswith("stop: 2 rounds in a row")


def test_the_cap_stops_a_climb_that_keeps_rising(tmp_path: Path) -> None:
    state = tmp_path / "climb.json"
    swarm.main(["climb-init", "--state", str(state), "--properties", "x", "--cap", "2"])
    _grade(tmp_path / "r1", "a", {"x": 1}, "important")
    _round(state, tmp_path / "r1", "a")
    _grade(tmp_path / "r2", "b", {"x": 5}, "important")
    assert _round(state, tmp_path / "r2", "b") == "stop: the cap of 2 rounds is reached"


def test_a_property_no_reviewer_graded_refuses_the_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _climb(tmp_path)
    _grade(tmp_path / "r1", "a", {"x": 5})
    assert (
        swarm.main([
            "climb-round",
            "--state",
            str(state),
            "--lenses",
            "a",
            "--dir",
            str(tmp_path / "r1"),
        ])
        == 1
    )
    assert "round refused: no reviewer graded y" in capsys.readouterr().out


def test_a_partial_reviewer_still_counts_for_what_it_graded(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    _grade(tmp_path / "r1", "a", {"x": 5, "y": 6})
    _grade(tmp_path / "r1", "b", {"x": 3})
    _round(state, tmp_path / "r1", "a,b")
    round_one = json.loads(state.read_text(encoding="utf-8"))["rounds"][0]
    assert (round_one["scores"], round_one["highs"]) == ({"x": 3, "y": 6}, {"x": 5, "y": 6})


def test_a_round_after_the_stop_needs_the_past_stop_flag(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    for name, grades in (("r1", {"x": 5, "y": 5}), ("r2", {"x": 5, "y": 5})):
        _grade(tmp_path / name, "a", grades, "important")
        _round(state, tmp_path / name, "a")
    _grade(tmp_path / "r3", "b", {"x": 9, "y": 9})
    args = ["climb-round", "--state", str(state), "--lenses", "b", "--dir", str(tmp_path / "r3")]
    with pytest.raises(SystemExit, match="add --past-stop only when the user asked"):
        swarm.main(args)
    assert swarm.main([*args, "--past-stop"]) == 0
    assert json.loads(state.read_text(encoding="utf-8"))["rounds"][-1]["past_stop"] is True


def test_a_second_climb_on_the_same_state_is_refused(tmp_path: Path) -> None:
    state = _climb(tmp_path)
    with pytest.raises(SystemExit, match="one state file per climb"):
        swarm.main(["climb-init", "--state", str(state), "--properties", "x"])
