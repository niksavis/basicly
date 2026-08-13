"""Durable lane transcripts: what each dispatched agent actually did (basicly-rrah).

The harness spent every lane's event stream on token accounting and then dropped it:
measured 2026-08-08 over session ``basicly-u2hl:bc7cc925``, 32 dispatches costing
$122.41 left records of what each one cost and nothing of what it did. This is the
durable half of the sink :func:`basicly.runner.run` already feeds — a JSONL file per
lane under a directory naming the session, plus the supervisor's narrative, which
until now was a terminal pane. Both sit in the self-ignored ``.basicly/usage/``
tree, so neither can enter a commit.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .redact import redact_secrets
from .run_record import USAGE_DIR

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .runner import EventSink, StreamEvent

LANE_LOGS_DIR = USAGE_DIR / "lane-logs"

# The supervisor's narrative, one per session. Named for its content rather than
# `PASS_LOG`, which reads to a secret scanner as a password (ruff S105).
NARRATIVE_FILE = "pass.log"

# Session directories a rotation keeps. Uncalibrated: no pass has ever left a
# transcript to size one from, which is why it is config (`[runner]
# lane_log_sessions`) — the figure to replace it with is the one this ships to find.
DEFAULT_RETAINED_SESSIONS = 20

# The `type` of a plain-text line the CLI interleaved into its JSON stream. Named
# rather than empty, which would index the same as a shape we failed to read.
RAW_EVENT = "raw"


def _dir_name(name: str) -> str:
    """*name* as one path component every platform accepts.

    A session id is ``<root issue>:<hex>``, and ``:`` is illegal in a Windows path
    component. Leading and trailing dots go too: these ids come from a command
    line, and ``..`` walks out of the directory they are filed under.
    """
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in name)
    return safe.strip(".") or "session"


def _session_dir(repo_root: Path, session_id: str) -> Path:
    """The directory *session_id*'s transcripts are filed under, made on demand."""
    path = repo_root / LANE_LOGS_DIR / _dir_name(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record(event: StreamEvent, seq: int) -> dict[str, object]:
    """One stream event as the object its transcript line holds.

    Ordered by ``seq``, not by the timestamp: the step sequence is what a reader
    reconstructs, and two events inside one millisecond are common. A line the
    stream emitted as prose rather than JSON *is* its own text.
    """
    kind = event.data.get("type") if event.data else None
    return {
        "seq": seq,
        "at": datetime.now(UTC).isoformat(),
        "type": kind if isinstance(kind, str) and kind else RAW_EVENT,
        "subagent": event.subagent,
        "tokens": event.usage.tokens if event.usage is not None else 0,
        "text": event.text or ("" if event.data else event.line),
    }


class LaneTranscript:
    """One dispatched lane's durable event log: a JSONL line per stream event.

    Written on the runner's reader thread, so it locks. An event arriving after
    :meth:`close` is dropped rather than raised on: that thread outlives a dispatch
    killed while it held the pipe (``runner.READER_JOIN_S``).
    """

    def __init__(self, path: Path) -> None:
        """Open *path* for appending, so a re-dispatched lane extends its history."""
        self._lock = threading.Lock()
        self._handle = path.open("a", encoding="utf-8")
        self._seq = 0

    def __call__(self, event: StreamEvent) -> None:
        """Append *event* to the transcript and flush it."""
        with self._lock:
            if self._handle.closed:
                return
            self._seq += 1
            self._handle.write(json.dumps(_record(event, self._seq), sort_keys=True) + "\n")
            # Per event, not at close: the dispatches this observes are the ones a
            # quiet bound, a spend ceiling or a kill stops, and the buffered tail is
            # the part a reader needs from a lane that never reached its own exit.
            self._handle.flush()

    def close(self) -> None:
        """Close the transcript; later events are dropped."""
        with self._lock:
            self._handle.close()


class PassLog:
    """The supervisor's own narrative, on disk beside the lanes it describes.

    Appended to from the pass's thread and from the dispatch pool's, so it locks
    too. Redacted, because a routed outcome's detail carries whatever ``br``,
    ``git`` and the gates printed.
    """

    def __init__(self, path: Path, rotated: tuple[str, ...] = ()) -> None:
        """Open *path* for appending, so a restarted pass extends its narrative."""
        self._lock = threading.Lock()
        self._handle = path.open("a", encoding="utf-8")
        # What the rotation dropped for this session, for the caller to report: a
        # bound that discards evidence silently reads like one that never fired.
        self.rotated = rotated

    def append(self, line: str) -> None:
        """Record one narrative line."""
        with self._lock:
            if self._handle.closed:
                return
            self._handle.write(redact_secrets(line.rstrip("\n")) + "\n")
            self._handle.flush()

    def close(self) -> None:
        """Close the narrative; later lines are dropped."""
        with self._lock:
            self._handle.close()


def fanout(*sinks: EventSink) -> EventSink:
    """One sink driving several, so a dispatch can be metered *and* recorded.

    Each contained on its own: the meter and the transcript are independent
    observers, and a raise from one must not cost the other its event.
    """

    def emit(event: StreamEvent) -> None:
        for sink in sinks:
            with contextlib.suppress(Exception):
                sink(event)

    return emit


def _rotate(repo_root: Path, *, keep: int, protect: str) -> tuple[str, ...]:
    """Delete every session directory past the *keep* most recently written.

    By mtime, so "oldest" is least recently written rather than first created — a
    session still being appended to is not old. *protect* is the session being
    opened, held out of the ordering rather than trusted to sort last: it was just
    created, so its mtime ties and the tiebreak is a name, on which a pass rotates
    away its own narrative. It still counts against *keep*.
    """
    root = repo_root / LANE_LOGS_DIR
    sessions = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name != protect),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    stale = sessions[: max(0, len(sessions) - max(0, keep - 1))]
    for path in stale:
        shutil.rmtree(path, ignore_errors=True)
    return tuple(path.name for path in stale)


@contextlib.contextmanager
def lane_transcript(repo_root: Path, session_id: str, issue_id: str) -> Iterator[LaneTranscript]:
    """This lane's durable sink, closed however its dispatch ends."""
    directory = _session_dir(repo_root, session_id)
    transcript = LaneTranscript(directory / f"{_dir_name(issue_id)}.jsonl")
    try:
        yield transcript
    finally:
        transcript.close()


def open_pass(repo_root: Path, session_id: str, *, keep: int) -> PassLog:
    """This pass's narrative, with the session directory provisioned and rotated.

    Not a context manager, deliberately: every line is flushed as it is appended, so
    a caller that dies without closing loses nothing.
    """
    directory = _session_dir(repo_root, session_id)
    dropped = _rotate(repo_root, keep=keep, protect=directory.name)
    return PassLog(directory / NARRATIVE_FILE, dropped)
