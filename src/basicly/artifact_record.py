from __future__ import annotations

import json
from pathlib import Path

from . import comment_rows, tracker

MARKER = "[harness-artifact]"
_KIND_PREFIX = "kind="


def recorded_payload(text: str, kind: str) -> object | None:

    if not text.startswith(MARKER):
        return None
    rest = text[len(MARKER) :].strip()
    if not rest.startswith(_KIND_PREFIX):
        return None
    kind_field, _, encoded = rest[len(_KIND_PREFIX) :].partition(" ")
    if kind_field != kind:
        return None
    try:
        return json.loads(encoded)
    except ValueError:
        return encoded


def _marker_payload(repo_root: Path, issue_id: str, kind: str) -> object | None:

    found = None
    for comment in tracker.read_comments(repo_root, issue_id):
        payload = recorded_payload(str(comment.get(tracker.COMMENT_TEXT_KEY, "")).strip(), kind)
        if payload is not None:
            found = payload
    return found


def cut_violation(repo_root: Path, issue_id: str, kind: str, payload: object) -> str | None:

    for row in tracker.read_comments(repo_root, issue_id):
        if comment_rows.TRUNCATED_KEY not in row:
            continue
        stored = str(row.get(tracker.COMMENT_TEXT_KEY, ""))
        if recorded_payload(stored.strip(), kind) != payload:
            continue
        return (
            "the recorded body was truncated by the event text cap to "
            f"{len(stored.encode('utf-8'))} bytes of {row[comment_rows.ORIGINAL_LENGTH_KEY]} "
            "and cannot be recovered from the append-only log; re-record the artifact "
            "from the producing state"
        )
    return None


def read(repo_root: Path, issue_id: str, kind: str) -> object | None:

    recorded = tracker.read_artifacts(repo_root, issue_id).get(kind)
    if recorded is not None:
        return recorded
    return _marker_payload(repo_root, issue_id, kind)


def write(repo_root: Path, issue_id: str, kind: str, payload: dict) -> None:

    tracker.add_artifact(repo_root, issue_id, kind, payload)
