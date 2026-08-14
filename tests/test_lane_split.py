"""Tests for the acquisition/implementation split over a lane transcript (basicly-ejdm.2).

The bead's four criteria and the three measured caveats recorded on it, which are the
reason a naive version of this would produce a number that means nothing:

* **The pairing rule.** A tool_use turn's usage is the cost of *emitting* the call; the
  result lands in the next turn. So attribution runs one turn late, and a test that
  summed tokens on tool turns would pass against the wrong arithmetic.
* **Shares, never absolutes.** Asserted as shares, because per-turn stream usage
  over-reports the run record by 1.46x-1.79x and this repo has already killed a lane by
  mixing those denominations.
* **Unknown is not none.** A transcript predating the tool field is unclassifiable, never
  fully implementation.

The transcripts are built as line dicts rather than by driving a runner: the observable
behaviour is what a given transcript produces, and a live dispatch would test the stream
parser instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import cli, lane_split

if TYPE_CHECKING:
    import pytest


def _turn(tokens: int, tools: list[str] | None = None) -> dict:
    """One transcript line. Omitting *tools* spells a line written before the field."""
    line = {"seq": 0, "type": "assistant", "tokens": tokens, "text": ""}
    return line if tools is None else {**line, "tools": tools}


def _write(root: Path, issue: str, turns: list[dict]) -> Path:
    """Persist *turns* as *issue*'s transcript under one session directory."""
    directory = root / lane_split.LANE_LOGS_DIR / "session"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{issue}.jsonl"
    path.write_text("".join(json.dumps(turn) + "\n" for turn in turns), encoding="utf-8")
    return path


def test_the_split_attributes_a_turn_to_the_tools_its_predecessor_emitted() -> None:
    """The pairing rule, which is the whole arithmetic.

    Turn 2's 100 tokens are the cost of the Read that turn 1 asked for, and turn 3's 50
    are the cost of the Edit turn 2 asked for. Summing tokens on the turns that *called*
    the tools would attribute 10 to acquisition and 100 to implementation - the exact
    inversion the caveat on the bead warns about.
    """
    turns = [_turn(10, ["Read"]), _turn(100, ["Edit"]), _turn(50, [])]

    outcome = lane_split.split_events(turns)

    assert not isinstance(outcome, str)
    assert outcome.tokens[lane_split.ACQUISITION] == 100
    assert outcome.tokens[lane_split.IMPLEMENTATION] == 50
    assert outcome.tokens[lane_split.UNATTRIBUTED] == 10  # the first turn has no predecessor


def test_the_split_reports_shares_that_survive_the_denomination() -> None:
    """Shares are the number that compares; the tokens beside them are not."""
    turns = [_turn(0, ["Read"]), _turn(300, ["Edit"]), _turn(100, [])]

    outcome = lane_split.split_events(turns)

    assert not isinstance(outcome, str)
    assert outcome.share(lane_split.ACQUISITION) == 0.75
    assert outcome.share(lane_split.IMPLEMENTATION) == 0.25


def test_classify_reads_a_read_tool_as_acquisition_and_a_write_as_implementation() -> None:
    """The declared classification, in the one place a reader can inspect it."""
    assert lane_split.classify(["Read"]) == lane_split.ACQUISITION
    assert lane_split.classify(["Grep", "Glob"]) == lane_split.ACQUISITION
    assert lane_split.classify(["Edit"]) == lane_split.IMPLEMENTATION
    assert lane_split.classify(["Write", "NotebookEdit"]) == lane_split.IMPLEMENTATION


def test_classify_refuses_to_guess_a_tool_that_is_neither() -> None:
    """`Bash` runs `git status` and `mv` alike, so bucketing it would invent the number.

    Mixed classes go the same way. A majority rule would look reasonable and would put a
    guess inside the measurement `ejdm.4` is judged by.
    """
    assert lane_split.classify(["Bash"]) == lane_split.UNCLASSIFIED
    assert lane_split.classify(["Task"]) == lane_split.UNCLASSIFIED
    assert lane_split.classify(["Read", "Edit"]) == lane_split.UNCLASSIFIED
    assert lane_split.classify([]) == lane_split.UNATTRIBUTED


def test_a_transcript_predating_the_tool_field_is_unclassifiable_not_implementation() -> None:
    """Absent is unknown, never "called nothing".

    Collapsing the two would report every lane written before `basicly-ejdm.1` as fully
    implementation, which is a confident wrong answer rather than a missing one.
    """
    outcome = lane_split.split_events([_turn(10), _turn(100)])

    assert isinstance(outcome, str)
    assert "before the tool name field" in outcome


def test_a_lane_with_no_transcript_is_missing_rather_than_a_zero_split(tmp_path: Path) -> None:
    """No transcript is not a lane that spent nothing on acquisition."""
    assert lane_split.lane_splits(tmp_path) == []


def test_a_missing_transcript_directory_reports_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command says so, which is the fourth criterion at the consumer surface."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "basicly.toml").write_text("", encoding="utf-8")

    assert cli.main(["usage", "lane-split"]) == 0

    assert "no lane transcript is persisted" in capsys.readouterr().out


def test_the_report_names_its_family_and_its_denomination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two caveats a reader cannot recover from the numbers, so the report carries them.

    Codex emits no per-tool event this stack parses, and the tokens are stream-denominated
    against a grant metered from the run record. A report stating neither would imply
    coverage it does not have and invite a comparison that has already cost a lane.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "basicly.toml").write_text("", encoding="utf-8")
    _write(tmp_path, "basicly-a", [_turn(10, ["Read"]), _turn(90, [])])

    assert cli.main(["usage", "lane-split"]) == 0

    out = capsys.readouterr().out
    assert "claude only" in out
    assert "1.46x-1.79x" in out
    assert "acquisition 90%" in out


def test_an_unclassifiable_lane_is_named_in_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It is reported beside the lanes that did split, never dropped from the listing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "basicly.toml").write_text("", encoding="utf-8")
    _write(tmp_path, "basicly-old", [_turn(10), _turn(100)])

    assert cli.main(["usage", "lane-split"]) == 0

    assert "basicly-old: unclassifiable" in capsys.readouterr().out


def test_a_usage_free_event_between_a_call_and_its_answer_does_not_break_the_chain() -> None:
    """Found by the demonstration, not by this file, and that is the point.

    A real claude transcript forwards the tool result as a `user` event carrying no usage,
    so it sits between the `tool_use` turn and the assistant turn that pays for the
    answer. Pairing against the immediately preceding *line* attributed every real lane to
    `unattributed` - a confident 100% that measured nothing. The pairing is against the
    last tools emitted before a turn that carries usage.
    """
    turns = [_turn(10, ["Read"]), _turn(0, []), _turn(100, [])]

    outcome = lane_split.split_events(turns)

    assert not isinstance(outcome, str)
    assert outcome.tokens[lane_split.ACQUISITION] == 100
    assert (
        lane_split.UNATTRIBUTED not in outcome.tokens
        or outcome.tokens[lane_split.UNATTRIBUTED] == 10
    )
