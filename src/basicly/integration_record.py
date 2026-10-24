from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import tracker

PREFIX = "[harness-policy] integration "
EVENTS = frozenset({"implementation-completed", "queued", "started", "completed", "waiting"})


def read(repo_root: Path, issue_id: str) -> tuple[dict[str, Any], ...]:
    found = []
    for row in tracker.read_comments(repo_root, issue_id):
        text = str(row.get("text", ""))
        if text.startswith(PREFIX):
            try:
                payload = json.loads(text[len(PREFIX) :])
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid integration progress on {issue_id}: {exc.msg}") from exc
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("event"), str)
                or payload["event"] not in EVENTS
                or any(not isinstance(payload.get(key), str) for key in ("at", "id", "detail"))
                or not payload["id"].strip()
            ):
                raise ValueError(f"invalid integration progress on {issue_id}")
            try:
                stamp = datetime.fromisoformat(payload["at"])
            except ValueError as exc:
                raise ValueError(f"invalid integration timestamp on {issue_id}") from exc
            if stamp.tzinfo is None or (
                payload["event"] == "waiting" and not payload["detail"].strip()
            ):
                raise ValueError(f"invalid integration progress on {issue_id}")
            found.append(payload)
    return tuple(found)


def record(repo_root: Path, issue_id: str, event: str, detail: str = "") -> None:
    if event not in EVENTS:
        raise ValueError(f"unknown integration event {event!r}")
    if event == "waiting" and not detail.strip():
        raise ValueError("integration waiting requires a concrete reason")
    tracker.add_comment(
        repo_root,
        issue_id,
        PREFIX
        + json.dumps({
            "event": event,
            "at": datetime.now(UTC).isoformat(),
            "id": secrets.token_hex(16),
            "detail": detail,
        }),
    )


def enqueue(repo_root: Path, issue_id: str, detail: str) -> None:
    if not any(row["event"] == "implementation-completed" for row in read(repo_root, issue_id)):
        record(
            repo_root,
            issue_id,
            "implementation-completed",
            "completion observed at queue admission",
        )
    record(repo_root, issue_id, "queued", detail)
