from __future__ import annotations

from pathlib import Path

import pytest

from tests.swarm_fixtures import _reply, swarm


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
    assert "dissent (skeptic, b): r" in out


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


@pytest.mark.parametrize(
    ("voters", "options"), [("a,b", "x,y"), ("a", "x,y"), ("a,b,c,d,e,f,g", "x,y")]
)
def test_a_council_of_the_wrong_size_is_refused(tmp_path: Path, voters: str, options: str) -> None:
    with pytest.raises(SystemExit, match="use 3 voters, or 5"):
        swarm.main(["tally", "--voters", voters, "--options", options, "--dir", str(tmp_path)])


def test_a_duplicate_option_label_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="named twice"):
        swarm.main(["tally", "--voters", "a,b,c", "--options", "x,x", "--dir", str(tmp_path)])


@pytest.mark.parametrize("options", ["x", "v,w,x,y,z"])
def test_a_council_needs_two_to_four_options(tmp_path: Path, options: str) -> None:
    with pytest.raises(SystemExit, match="give 2 to 4 options"):
        swarm.main(["tally", "--voters", "a,b,c", "--options", options, "--dir", str(tmp_path)])


def test_a_plurality_without_a_majority_of_all_voters_decides_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _vote(tmp_path, "a", "x")
    _vote(tmp_path, "b", "x")
    _vote(tmp_path, "c", "y")
    args = ["tally", "--voters", "a,b,c,d,e", "--options", "x,y", "--dir", str(tmp_path)]
    assert swarm.main(args) == 2
    assert "the user decides" in capsys.readouterr().out


def test_a_tally_with_an_unlisted_reply_is_refused(tmp_path: Path) -> None:
    for voter in ("a", "b", "c", "x"):
        _vote(tmp_path, voter, "p")
    with pytest.raises(SystemExit, match=r"x\.md, not named in --voters"):
        swarm.main(["tally", "--voters", "a,b,c", "--options", "p,q", "--dir", str(tmp_path)])


def test_a_vote_that_is_not_text_does_not_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _reply(tmp_path, "a", {"vote": 3})
    _vote(tmp_path, "b", "p")
    _vote(tmp_path, "c", "p")
    swarm.main(["tally", "--voters", "a,b,c", "--options", "p,q", "--dir", str(tmp_path)])
    assert "a: not run (no vote field)" in capsys.readouterr().out
