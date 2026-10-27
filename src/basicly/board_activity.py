from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import board_fields, tracker
from .loop_state import parse_worktree_ref

PREFIX = "[activity] "
LIMIT = 100


def root_query(query_string: str) -> str:
    query = parse_qs(query_string, keep_blank_values=True, max_num_fields=4)
    if set(query) - {"root"} or any(len(value) != 1 for value in query.values()):
        raise ValueError("invalid query")
    root = query.get("root", [""])[0]
    if len(root) > board_fields.ID_MAX:
        raise ValueError("invalid root")
    return root


def _report(text: str) -> dict[str, str]:
    if not text.startswith(PREFIX):
        return {}
    try:
        value = json.loads(text[len(PREFIX) :])
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        key: board_fields.text(value[key], 300)
        for key in ("action", "phase", "worktree", "blocker", "next", "evidence")
        if isinstance(value.get(key), str)
    }


def _purpose(fields: Mapping[str, Any]) -> str:
    body = str(fields.get("description") or "")
    match = re.search(r"(?mi)^## (?:Context|Trigger)\s*\n([^#]+)", body)
    return board_fields.text((match[1] if match else body).strip(), 300) or "Purpose not recorded"


@dataclass(frozen=True)
class _History:
    logs: dict[str, list[dict[str, str]]]
    reports: dict[str, dict[str, str]]
    actions: dict[str, dict[str, str]]
    started: dict[str, str]


def _history(ordered: list[Any], selected: set[str], kit: Any) -> _History:
    logs: dict[str, list[dict[str, str]]] = defaultdict(list)
    reports: dict[str, dict[str, str]] = {}
    actions: dict[str, dict[str, str]] = {}
    started: dict[str, str] = {}
    previous: dict[str, str] = {}
    for event in ordered:
        if event.record not in selected:
            continue
        if event.kind == kit.events.KIND_STATUS:
            status = str(event.payload.get("status") or "")
            if status == "in_progress" and previous.get(event.record) != status:
                started[event.record] = event.ts
            previous[event.record] = status
        if event.kind in kit.events.PROSE_KINDS:
            text = str(event.payload.get("text") or "")
            report = _report(text)
            if report:
                reports[event.record] = {**report, "at": event.ts, "writer": event.actor}
            logs[event.record].append({
                "at": event.ts,
                "writer": board_fields.text(event.actor, 100),
                "text": board_fields.text(text, 1000),
            })
            logs[event.record] = logs[event.record][-5:]
            if not text.startswith(("[model handoff]", "[session handover ")):
                actions[event.record] = logs[event.record][-1]
    return _History(logs, reports, actions, started)


def document(events: list[Any], kit: Any, root: str = "") -> dict[str, object]:
    ordered = kit.events.canonical_order(events)
    states = kit.events.fold(ordered).records
    views = kit.views_from_events(ordered)
    children = kit.children_of(views, kit.DEFAULT_VOCABULARY)
    if root and (root not in states or states[root].tombstoned):
        raise KeyError(root)
    selected = set(states) if not root else {root}
    frontier = [root] if root else []
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in selected:
                selected.add(child)
                frontier.append(child)
    history = _history(ordered, selected, kit)
    rows = []
    for ident in selected:
        state = states.get(ident)
        if state is None or state.tombstoned:
            continue
        report = history.reports.get(ident, {})
        log = history.logs.get(ident, [])
        blockers = [
            edge.target
            for edge in views[ident].dependencies
            if edge.type in kit.DEFAULT_VOCABULARY.blocking_types
            and edge.target in states
            and states[edge.target].status not in kit.DEFAULT_VOCABULARY.closed_statuses
            and not states[edge.target].tombstoned
        ]
        status = state.status or "unknown"
        latest = history.actions.get(ident, {})
        latest_report = _report(latest.get("text", ""))
        action = (
            latest_report.get("action", "Activity updated")
            if latest_report
            else re.sub(r"^\[[^\]]+\]\s*", "", latest.get("text", ""))
        )
        binding = parse_worktree_ref(str(state.fields.get("external_ref") or ""))
        rows.append({
            "id": ident,
            "title": board_fields.text(state.fields.get("title") or ident, 200),
            "purpose": _purpose(state.fields),
            "status": status,
            "runtime": "unknown",
            "runtime_reason": (
                "Interactive process activity is not instrumented. "
                "Task status is not proof of a running process."
            ),
            "owner": board_fields.text(state.fields.get("assignee") or "Unassigned", 100),
            "phase": report.get("phase", "Unknown"),
            "worktree": report.get("worktree") or (binding.name if binding else "Unknown"),
            "action": board_fields.text(action, 300),
            "action_at": latest.get("at"),
            "updated_at": state.dates.get("updated"),
            "started_at": history.started.get(ident),
            "blocker": report.get("blocker") or "None recorded",
            "dependencies": blockers,
            "next": report.get("next", "None recorded"),
            "evidence": report.get("evidence", "Open task history for recorded checks"),
            "logs": log,
        })
    rows.sort(
        key=lambda row: (
            str(row["action_at"] or row["started_at"] or ""),
            str(row["updated_at"] or ""),
            str(row["id"]),
        ),
        reverse=True,
    )
    return {
        "schema": "basicly.activity.v1",
        "root": root,
        "generated_at": board_fields.stamp(datetime.now(UTC)),
        "total": len(rows),
        "items": rows[:LIMIT],
        "limit": LIMIT,
        "observation": "Tracker events; runtime presence is unknown unless separately observed.",
    }


class Feed:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self._events: list[Any] = []
        self._kit: Any = None
        self._payloads: dict[str, tuple[bytes, str]] = {}

    def payload(self, root: str = "") -> tuple[bytes, str]:
        with self._lock:
            version = tracker.ledger_fingerprint(self.repo_root)
            if version != self._version:
                self._events, self._kit = tracker.activity_source(self.repo_root)
                self._payloads.clear()
                self._version = version
            if root not in self._payloads:
                body = json.dumps(
                    document(self._events, self._kit, root), ensure_ascii=False
                ).encode()
                if len(self._payloads) >= 8:
                    self._payloads.clear()
                self._payloads[root] = body, '"' + hashlib.sha256(body).hexdigest() + '"'
            return self._payloads[root]


def respond(handler: BaseHTTPRequestHandler, feed: Feed) -> None:
    try:
        root = root_query(urlsplit(handler.path).query)
    except ValueError:
        handler.send_error(HTTPStatus.BAD_REQUEST, "invalid activity request")
        return
    try:
        body, etag = feed.payload(root)
    except KeyError:
        handler.send_error(HTTPStatus.NOT_FOUND, "task not found")
        return
    except OSError, ValueError, tracker.TrackerDivergenceError:
        logging.getLogger(__name__).exception("Cannot read tracker activity")
        handler.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "activity unavailable")
        return
    status = (
        HTTPStatus.NOT_MODIFIED if handler.headers.get("If-None-Match") == etag else HTTPStatus.OK
    )
    handler.send_response(status)
    handler.send_header("ETag", etag)
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)) if status == HTTPStatus.OK else "0")
    handler.end_headers()
    if status == HTTPStatus.OK:
        handler.wfile.write(body)
