from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

from basicly import cli, lane_log, runner, supervise
from basicly.runner import CLAUDE_STREAM_JSON, HEADLESS, PROMPT_PLACEHOLDER, RunnerSpec

SESSION = "basicly-u2hl:bc7cc925"


def _spec(body: str) -> RunnerSpec:
    return RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", "import json, os, sys, time\n" + body, PROMPT_PLACEHOLDER),
        usage_format=CLAUDE_STREAM_JSON,
    )


def _turn(text: str) -> str:
    message = {"content": [{"type": "text", "text": text}], "usage": {"output_tokens": 3}}
    return (
        f"turn = {{'type': 'assistant', 'message': {message!r}}}\n"
        "sys.stdout.write(json.dumps(turn) + '\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )


def _narrative(repo: Path) -> Path:
    return repo / lane_log.LANE_LOGS_DIR / "basicly-u2hl-bc7cc925" / lane_log.NARRATIVE_FILE


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _dispatch(repo: Path, body: str, issue_id: str = "basicly-u2hl.18") -> Path:

    seen: list[runner.StreamEvent] = []
    with lane_log.lane_transcript(repo, SESSION, issue_id) as transcript:
        result = runner.run(
            _spec(body),
            "go",
            repo,
            capture_usage=True,
            on_event=lane_log.fanout(transcript, seen.append),
            timeout=60.0,
            bounds=runner.DispatchBounds(token_ceiling=1),
        )
    assert result.stopped is not None, "the dispatch must be killed, not left to exit"
    return repo / lane_log.LANE_LOGS_DIR / "basicly-u2hl-bc7cc925" / f"{issue_id}.jsonl"


def test_a_killed_dispatch_leaves_the_events_it_had_already_emitted(tmp_path: Path) -> None:

    path = _dispatch(tmp_path, _turn("reading AGENTS.md"))

    assert path.exists(), "a killed lane must still have a transcript"
    records = _lines(path)
    assert [record["text"] for record in records] == ["reading AGENTS.md"]
    assert records[0]["type"] == "assistant"
    assert records[0]["seq"] == 1
    assert records[0]["tokens"] == 3


def test_the_transcript_path_names_the_bead_and_the_session(tmp_path: Path) -> None:

    path = _dispatch(tmp_path, _turn("working"), issue_id="basicly-u2hl.20")

    assert path.parent.name == "basicly-u2hl-bc7cc925"
    assert path.name == "basicly-u2hl.20.jsonl"
    assert path.parent.parent == tmp_path / lane_log.LANE_LOGS_DIR


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("basicly-u2hl:bc7cc925", "basicly-u2hl-bc7cc925"),
        ("..", "session"),
        ("../../etc", "-..-etc"),
        ("basicly-u2hl.18", "basicly-u2hl.18"),
    ],
)
def test_a_directory_name_cannot_escape_the_lane_log_root(session_id: str, expected: str) -> None:

    assert lane_log._dir_name(session_id) == expected


def test_a_secret_the_agent_echoed_is_redacted_in_the_transcript(tmp_path: Path) -> None:

    token = "ghp_" + "c" * 30
    path = _dispatch(tmp_path, _turn(f"exported GITHUB_TOKEN={token} for the push"))

    written = path.read_text(encoding="utf-8")
    assert token not in written
    assert "redacted" in written
    assert "for the push" in _lines(path)[0]["text"]


def test_a_transcript_records_a_plain_line_the_stream_interleaved(tmp_path: Path) -> None:

    body = "sys.stdout.write('warming up the cache\\n'); sys.stdout.flush()\n" + _turn("done")
    path = _dispatch(tmp_path, body)

    record = _lines(path)[0]
    assert record["type"] == lane_log.RAW_EVENT
    assert record["text"] == "warming up the cache"


def test_a_raising_sink_does_not_cost_the_other_sinks_the_event() -> None:

    seen: list[runner.StreamEvent] = []

    def angry(_event: runner.StreamEvent) -> None:
        raise RuntimeError("sink is unhappy")

    lane_log.fanout(angry, seen.append, angry)(runner.StreamEvent(line="{}"))

    assert len(seen) == 1


def test_the_rotation_keeps_the_most_recently_written_sessions(tmp_path: Path) -> None:

    root = tmp_path / lane_log.LANE_LOGS_DIR
    for age, name in enumerate(("oldest", "middle", "newest")):
        directory = root / name
        directory.mkdir(parents=True)
        os.utime(directory, (1_000_000, 1_000_000 + (10 * age)))

    log = lane_log.open_pass(tmp_path, SESSION, keep=2)
    log.close()

    assert log.rotated == ("oldest", "middle")
    assert sorted(path.name for path in root.iterdir()) == ["basicly-u2hl-bc7cc925", "newest"]


def test_the_rotation_never_drops_the_session_it_is_making_room_for(tmp_path: Path) -> None:

    (tmp_path / lane_log.LANE_LOGS_DIR / "earlier").mkdir(parents=True)

    log = lane_log.open_pass(tmp_path, SESSION, keep=0)
    log.append("routed:   basicly-u2hl.18 -> shipped")
    log.close()

    assert "shipped" in _narrative(tmp_path).read_text(encoding="utf-8")
    assert log.rotated == ("earlier",)


def test_the_narrative_redacts_a_secret_a_routed_detail_carried(tmp_path: Path) -> None:

    token = "ghp_" + "d" * 30
    log = lane_log.open_pass(tmp_path, SESSION, keep=5)
    log.append(f"routed:   basicly-u2hl.18 -> error - push failed with {token} in the url")
    log.close()

    written = _narrative(tmp_path).read_text(encoding="utf-8")
    assert token not in written
    assert "push failed with" in written


class _Heartbeat:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def start(self) -> None:
        pass

    def check(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_a_two_lane_pass_leaves_every_routed_outcome_in_its_narrative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.supervise, "HeartbeatThread", _Heartbeat)
    monkeypatch.setattr(cli.supervise, "new_session_id", lambda _root: SESSION)
    states = iter([
        supervise.SessionState("epic", "open", (("epic.1", "open"), ("epic.2", "open")), ()),
        supervise.SessionState("epic", "closed", (), ()),
    ])
    monkeypatch.setattr(cli.supervise, "derive_session", lambda *_a, **_k: next(states))
    monkeypatch.setattr(
        cli,
        "_supervise_pass",
        lambda *_a, **_k: (
            supervise.RoutedOutcome("epic.1", "shipped", "landed and shipped"),
            supervise.RoutedOutcome("epic.2", "merged", "landed, ship pending"),
        ),
    )

    code = cli._cmd_loop_supervise(argparse.Namespace(issue="epic", label=None))

    out = capsys.readouterr().out
    assert code == 0, out
    narrative = _narrative(tmp_path).read_text(encoding="utf-8")
    for expected in (
        f"session:  {SESSION}",
        "routed:   epic.1 -> shipped - landed and shipped",
        "routed:   epic.2 -> merged - landed, ship pending",
        "done:     yes",
    ):
        assert expected in narrative, narrative
        assert expected in out


def test_a_transcript_line_names_the_tools_its_turn_called(tmp_path: Path) -> None:
    path = tmp_path / "lane.jsonl"
    transcript = lane_log.LaneTranscript(path)
    transcript(runner.StreamEvent(line="{}", data={"type": "assistant"}, tools=("Read", "Edit")))
    transcript.close()

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["tools"] == ["Read", "Edit"]


def test_a_turn_that_called_nothing_records_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "lane.jsonl"
    transcript = lane_log.LaneTranscript(path)
    transcript(runner.StreamEvent(line="{}", data={"type": "assistant"}))
    transcript.close()

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["tools"] == []


def test_a_line_written_before_the_field_reads_as_unknown_not_as_no_tools(
    tmp_path: Path,
) -> None:

    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"seq": 0, "type": "assistant", "tokens": 5}) + "\n")

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "tools" not in line
    assert line.get("tools") is None
