"""Durable lane transcripts and the pass narrative (basicly-rrah).

The claim under test is that a supervised pass leaves evidence of what its agents
did, not merely of what they cost. So every test here asserts against a file on
disk after the thing that wrote it has ended — a killed dispatch, a finished pass,
a rotation — because "the record survives the process" is the whole requirement.
"""

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
    """A spec whose "agent CLI" is *body*, declaring the claude event stream."""
    return RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", "import json, os, sys, time\n" + body, PROMPT_PLACEHOLDER),
        usage_format=CLAUDE_STREAM_JSON,
    )


def _turn(text: str) -> str:
    """A child that emits one assistant turn saying *text*, then sleeps past its kill."""
    message = {"content": [{"type": "text", "text": text}], "usage": {"output_tokens": 3}}
    return (
        f"turn = {{'type': 'assistant', 'message': {message!r}}}\n"
        "sys.stdout.write(json.dumps(turn) + '\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )


def _narrative(repo: Path) -> Path:
    """Where :data:`SESSION`'s pass narrative is written under *repo*."""
    return repo / lane_log.LANE_LOGS_DIR / "basicly-u2hl-bc7cc925" / lane_log.NARRATIVE_FILE


def _lines(path: Path) -> list[dict]:
    """The transcript at *path*, parsed."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _dispatch(repo: Path, body: str, issue_id: str = "basicly-u2hl.18") -> Path:
    """Run a stubbed dispatch to its kill with the transcript attached; its path.

    Killed on the *event*, never on a clock: the child emits one turn and then
    sleeps past any bound, and the stop predicate fires once that turn has been
    recorded. A short wall-clock kill would instead race the child's interpreter
    start-up, so the assertion would hold or not according to how loaded the box
    was — and this suite runs beside the rest of the gate suite.
    """
    seen: list[runner.StreamEvent] = []
    with lane_log.lane_transcript(repo, SESSION, issue_id) as transcript:
        result = runner.run(
            _spec(body),
            "go",
            repo,
            capture_usage=True,
            on_event=lane_log.fanout(transcript, seen.append),
            timeout=60.0,
            bounds=runner.DispatchBounds(
                stop_when=lambda: runner.StopReason("test", "one event recorded") if seen else None
            ),
        )
    assert result.stopped is not None, "the dispatch must be killed, not left to exit"
    return repo / lane_log.LANE_LOGS_DIR / "basicly-u2hl-bc7cc925" / f"{issue_id}.jsonl"


def test_a_killed_dispatch_leaves_the_events_it_had_already_emitted(tmp_path: Path) -> None:
    """AC: the transcript exists whichever way the dispatch ended, kill included.

    The kill is the case that decides it. Anything written at the end of a run is
    written only by the runs that reach their end, and a lane stopped on a bound is
    exactly the lane whose behaviour nobody can otherwise account for — so the file
    is asserted after a hard kill, and it has to hold what the lane said before it.
    """
    path = _dispatch(tmp_path, _turn("reading AGENTS.md"))

    assert path.exists(), "a killed lane must still have a transcript"
    records = _lines(path)
    assert [record["text"] for record in records] == ["reading AGENTS.md"]
    assert records[0]["type"] == "assistant"
    assert records[0]["seq"] == 1
    assert records[0]["tokens"] == 3


def test_the_transcript_path_names_the_bead_and_the_session(tmp_path: Path) -> None:
    """AC: a transcript is addressable by the two facts that identify the run.

    Both, not either: a bead is dispatched in many sessions and a session runs many
    beads, so only the pair says which run's narrative this is. The session id's
    ``:`` cannot be one of them — it is illegal in a Windows path component — which
    is why the directory is the id with the separator folded away rather than the
    id itself.
    """
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
    """An id reaching here came from a command line, so it is not trusted as a path.

    A fact about a string, asserted on every platform rather than on the one whose
    separator it names: ``..`` is a component that walks *out* of the directory the
    transcripts are filed under, and a root issue is whatever the operator typed
    after ``basicly loop supervise``.
    """
    assert lane_log._dir_name(session_id) == expected


def test_a_secret_the_agent_echoed_is_redacted_in_the_transcript(tmp_path: Path) -> None:
    """AC: the persisted copy carries the agent's words, never its credentials.

    Redaction already ran before the sink saw the line (``runner._emit``), and this
    is the assertion that the durable copy is downstream of it rather than of the
    raw stream. Both halves matter: the token is gone *and* the sentence around it
    survives, since a transcript redacted to nothing would pass a "no secret here"
    check while recording no behaviour at all.
    """
    token = "ghp_" + "c" * 30
    path = _dispatch(tmp_path, _turn(f"exported GITHUB_TOKEN={token} for the push"))

    written = path.read_text(encoding="utf-8")
    assert token not in written
    assert "redacted" in written
    assert "for the push" in _lines(path)[0]["text"]


def test_a_transcript_records_a_plain_line_the_stream_interleaved(tmp_path: Path) -> None:
    """A CLI's non-JSON progress line is prose too, and is the only text it carries.

    The stream is JSONL by contract and interleaves plain lines anyway. Such an
    event has no ``text`` field to read, so the transcript falls back to the line
    itself rather than filing a record that says an event happened and nothing else.
    """
    body = "sys.stdout.write('warming up the cache\\n'); sys.stdout.flush()\ntime.sleep(60)\n"
    path = _dispatch(tmp_path, body)

    record = _lines(path)[0]
    assert record["type"] == lane_log.RAW_EVENT
    assert record["text"] == "warming up the cache"


def test_a_raising_sink_does_not_cost_the_other_sinks_the_event() -> None:
    """The live meter and the durable transcript observe the same stream independently.

    They are composed, so containment is what keeps one from silencing the other:
    without it the transcript's first write error would also stop the spend bound
    reading the lane, which is a terminal control.
    """
    seen: list[runner.StreamEvent] = []

    def angry(_event: runner.StreamEvent) -> None:
        raise RuntimeError("sink is unhappy")

    lane_log.fanout(angry, seen.append, angry)(runner.StreamEvent(line="{}"))

    assert len(seen) == 1


def test_the_rotation_keeps_the_most_recently_written_sessions(tmp_path: Path) -> None:
    """AC: the directory is bounded, and what it dropped is reported.

    Ordered by mtime rather than by name, so "old" means least recently written —
    a long session still being appended to is not stale because it started first.
    The session being opened counts against the bound and is held out of the
    ordering, so ``keep=2`` leaves it plus the one most recent survivor.
    """
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
    """A bound of zero is still a bound on *old* sessions, not on this one.

    The degenerate configuration is the one that would silently reintroduce the
    defect: a pass whose own narrative is deleted the moment it is opened records
    nothing and looks exactly like a pass that never wrote one.
    """
    (tmp_path / lane_log.LANE_LOGS_DIR / "earlier").mkdir(parents=True)

    log = lane_log.open_pass(tmp_path, SESSION, keep=0)
    log.append("routed:   basicly-u2hl.18 -> shipped")
    log.close()

    assert "shipped" in _narrative(tmp_path).read_text(encoding="utf-8")
    assert log.rotated == ("earlier",)


def test_the_narrative_redacts_a_secret_a_routed_detail_carried(tmp_path: Path) -> None:
    """A routed outcome's detail is whatever br, git and the gates printed.

    So the narrative is redacted on the way to disk for the same reason the lane
    transcript is: neither is a place a credential may come to rest.
    """
    token = "ghp_" + "d" * 30
    log = lane_log.open_pass(tmp_path, SESSION, keep=5)
    log.append(f"routed:   basicly-u2hl.18 -> error - push failed with {token} in the url")
    log.close()

    written = _narrative(tmp_path).read_text(encoding="utf-8")
    assert token not in written
    assert "push failed with" in written


class _Heartbeat:
    """A heartbeat that does nothing, so the pass under test owns no timing."""

    def __init__(self, *_args: object) -> None:
        """Accept the lock and session id the command constructs it with."""

    def start(self) -> None:
        """Started and stopped by the command; there is nothing to beat."""

    def check(self) -> None:
        """The lock is never contended in a test, so this never raises."""

    def stop(self) -> None:
        """Nothing to join."""


def test_a_two_lane_pass_leaves_every_routed_outcome_in_its_narrative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC: how a completed pass routed is recoverable by grepping the directory.

    The narrative was previously the supervisor's stdout and nothing else, so a
    claim about a landing rested on the operator having redirected the pane by
    hand. Both routes are asserted on disk *and* on stdout: the terminal copy is
    what an operator watches, and dropping it to gain the file would trade one
    half of the record for the other.
    """
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


# --- Which tool the turn called (basicly-ejdm.1) -----------------------------


def test_a_transcript_line_names_the_tools_its_turn_called(tmp_path: Path) -> None:
    """The split basicly-ejdm needs starts here: a turn that read vs one that wrote."""
    path = tmp_path / "lane.jsonl"
    transcript = lane_log.LaneTranscript(path)
    transcript(runner.StreamEvent(line="{}", data={"type": "assistant"}, tools=("Read", "Edit")))
    transcript.close()

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["tools"] == ["Read", "Edit"]


def test_a_turn_that_called_nothing_records_an_empty_list(tmp_path: Path) -> None:
    """Called-nothing is a measurement; it must not read as unmeasured."""
    path = tmp_path / "lane.jsonl"
    transcript = lane_log.LaneTranscript(path)
    transcript(runner.StreamEvent(line="{}", data={"type": "assistant"}))
    transcript.close()

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["tools"] == []


def test_a_line_written_before_the_field_reads_as_unknown_not_as_no_tools(
    tmp_path: Path,
) -> None:
    """Every transcript on disk today predates this field.

    A reader that defaults a missing key to `[]` would count every historical lane
    as having called no tools, which is indistinguishable from a lane that only
    thought — and would silently classify the whole existing corpus as pure
    implementation. The absent key must stay absent so a consumer can tell.
    """
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"seq": 0, "type": "assistant", "tokens": 5}) + "\n")

    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "tools" not in line
    assert line.get("tools") is None
