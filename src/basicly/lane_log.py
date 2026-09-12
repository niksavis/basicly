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

NARRATIVE_FILE = "pass.log"

DEFAULT_RETAINED_SESSIONS = 20

RAW_EVENT = "raw"


def _dir_name(name: str) -> str:

    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in name)
    return safe.strip(".") or "session"


def _session_dir(repo_root: Path, session_id: str) -> Path:
    path = repo_root / LANE_LOGS_DIR / _dir_name(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record(event: StreamEvent, seq: int) -> dict[str, object]:

    kind = event.data.get("type") if event.data else None
    return {
        "seq": seq,
        "at": datetime.now(UTC).isoformat(),
        "type": kind if isinstance(kind, str) and kind else RAW_EVENT,
        "subagent": event.subagent,
        "tokens": event.usage.tokens if event.usage is not None else 0,
        "tools": list(event.tools),
        "text": event.text or ("" if event.data else event.line),
    }


class LaneTranscript:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._handle = path.open("a", encoding="utf-8")
        self._seq = 0

    def __call__(self, event: StreamEvent) -> None:
        with self._lock:
            if self._handle.closed:
                return
            self._seq += 1
            self._handle.write(json.dumps(_record(event, self._seq), sort_keys=True) + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


class PassLog:
    def __init__(self, path: Path, rotated: tuple[str, ...] = ()) -> None:
        self._lock = threading.Lock()
        self._handle = path.open("a", encoding="utf-8")
        self.rotated = rotated

    def append(self, line: str) -> None:
        with self._lock:
            if self._handle.closed:
                return
            self._handle.write(redact_secrets(line.rstrip("\n")) + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


def fanout(*sinks: EventSink) -> EventSink:

    def emit(event: StreamEvent) -> None:
        for sink in sinks:
            with contextlib.suppress(Exception):
                sink(event)

    return emit


def _rotate(repo_root: Path, *, keep: int, protect: str) -> tuple[str, ...]:

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
    directory = _session_dir(repo_root, session_id)
    transcript = LaneTranscript(directory / f"{_dir_name(issue_id)}.jsonl")
    try:
        yield transcript
    finally:
        transcript.close()


def open_pass(repo_root: Path, session_id: str, *, keep: int) -> PassLog:

    directory = _session_dir(repo_root, session_id)
    dropped = _rotate(repo_root, keep=keep, protect=directory.name)
    return PassLog(directory / NARRATIVE_FILE, dropped)
