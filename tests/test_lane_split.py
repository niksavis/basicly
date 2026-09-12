from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import cli, lane_split

if TYPE_CHECKING:
    import pytest


def _turn(tokens: int, tools: list[str] | None = None) -> dict:
    line = {"seq": 0, "type": "assistant", "tokens": tokens, "text": ""}
    return line if tools is None else {**line, "tools": tools}


def _write(root: Path, issue: str, turns: list[dict]) -> Path:
    directory = root / lane_split.LANE_LOGS_DIR / "session"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{issue}.jsonl"
    path.write_text("".join(json.dumps(turn) + "\n" for turn in turns), encoding="utf-8")
    return path


def test_the_split_attributes_a_turn_to_the_tools_its_predecessor_emitted() -> None:

    turns = [_turn(10, ["Read"]), _turn(100, ["Edit"]), _turn(50, [])]

    outcome = lane_split.split_events(turns)

    assert not isinstance(outcome, str)
    assert outcome.tokens[lane_split.ACQUISITION] == 100
    assert outcome.tokens[lane_split.IMPLEMENTATION] == 50
    assert outcome.tokens[lane_split.UNATTRIBUTED] == 10


def test_the_split_reports_shares_that_survive_the_denomination() -> None:
    turns = [_turn(0, ["Read"]), _turn(300, ["Edit"]), _turn(100, [])]

    outcome = lane_split.split_events(turns)

    assert not isinstance(outcome, str)
    assert outcome.share(lane_split.ACQUISITION) == 0.75
    assert outcome.share(lane_split.IMPLEMENTATION) == 0.25


def test_classify_reads_a_read_tool_as_acquisition_and_a_write_as_implementation() -> None:
    assert lane_split.classify(["Read"]) == lane_split.ACQUISITION
    assert lane_split.classify(["Grep", "Glob"]) == lane_split.ACQUISITION
    assert lane_split.classify(["Edit"]) == lane_split.IMPLEMENTATION
    assert lane_split.classify(["Write", "NotebookEdit"]) == lane_split.IMPLEMENTATION


def test_classify_refuses_to_guess_a_tool_that_is_neither() -> None:

    assert lane_split.classify(["Bash"]) == lane_split.UNCLASSIFIED
    assert lane_split.classify(["Task"]) == lane_split.UNCLASSIFIED
    assert lane_split.classify(["Read", "Edit"]) == lane_split.UNCLASSIFIED
    assert lane_split.classify([]) == lane_split.UNATTRIBUTED


def test_a_transcript_predating_the_tool_field_is_unclassifiable_not_implementation() -> None:

    outcome = lane_split.split_events([_turn(10), _turn(100)])

    assert isinstance(outcome, str)
    assert "before the tool name field" in outcome


def test_a_lane_with_no_transcript_is_missing_rather_than_a_zero_split(tmp_path: Path) -> None:
    assert lane_split.lane_splits(tmp_path) == []


def test_a_missing_transcript_directory_reports_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "basicly.toml").write_text("", encoding="utf-8")

    assert cli.main(["usage", "lane-split"]) == 0

    assert "no lane transcript is persisted" in capsys.readouterr().out


def test_the_report_names_its_family_and_its_denomination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

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
    monkeypatch.chdir(tmp_path)
    (tmp_path / "basicly.toml").write_text("", encoding="utf-8")
    _write(tmp_path, "basicly-old", [_turn(10), _turn(100)])

    assert cli.main(["usage", "lane-split"]) == 0

    assert "basicly-old: unclassifiable" in capsys.readouterr().out


def test_a_usage_free_event_between_a_call_and_its_answer_does_not_break_the_chain() -> None:

    turns = [_turn(10, ["Read"]), _turn(0, []), _turn(100, [])]

    outcome = lane_split.split_events(turns)

    assert not isinstance(outcome, str)
    assert outcome.tokens[lane_split.ACQUISITION] == 100
    assert (
        lane_split.UNATTRIBUTED not in outcome.tokens
        or outcome.tokens[lane_split.UNATTRIBUTED] == 10
    )
