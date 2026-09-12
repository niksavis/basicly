from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_wall
from .board_wall import ABSENT, ABSENT_TEXT, BY_KEY, RENDERABLE, Cell

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_schema import SnapshotVerdict

HREF_DIR = "record"
HREF_SUFFIX = ".html"

SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

BLOCKS = "blocks"

PARENT_CHILD = "parent-child"

NO_LANE = "no lane holds this record"
NO_EDGES = "nothing waits on this record and it waits on nothing"


def href(record_id: str) -> str:
    return f"{HREF_DIR}/{record_id}{HREF_SUFFIX}"


def writable(record_id: str) -> bool:
    return bool(SAFE_ID.match(record_id)) and ".." not in record_id


def _rows(document: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    held = document.get(section)
    return [row for row in held if isinstance(row, dict)] if isinstance(held, list) else []


def ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        ident for row in _rows(document, "units") if isinstance(ident := row.get("id"), str)
    )


def _find(rows: Sequence[Mapping[str, Any]], record_id: str) -> Mapping[str, Any] | None:
    return next((row for row in rows if row.get("id") == record_id), None)


def _value(held: Mapping[str, Any], key: str) -> str:

    if key not in held:
        return ABSENT_TEXT
    value = held[key]
    return value if isinstance(value, str) else board_wall.number(value)


def _cell(held: Mapping[str, Any], label: str, key: str) -> Cell:
    present = key in held
    return Cell(label, _value(held, key), BY_KEY[RENDERABLE if present else ABSENT])


def _unit_cells(unit: Mapping[str, Any]) -> tuple[Cell, ...]:
    return tuple(
        _cell(unit, label, key)
        for label, key in (
            ("status", "status"),
            ("priority", "priority"),
            ("type", "type"),
            ("phase", "phase"),
            ("ready", "ready"),
        )
    )


def _detail_cells(detail: Mapping[str, Any]) -> tuple[Cell, ...]:
    return tuple(
        _cell(detail, label, key) for label, key in (("worktree", "worktree"), ("branch", "branch"))
    )


def _checkpoints(detail: Mapping[str, Any]) -> tuple[tuple[str, bool], ...]:

    held = [name for name in detail.get("checkpoints_held", []) if isinstance(name, str)]
    missing = [name for name in detail.get("checkpoints_missing", []) if isinstance(name, str)]
    return tuple([(name, True) for name in held] + [(name, False) for name in missing])


def _rework(detail: Mapping[str, Any]) -> tuple[Cell, ...]:
    counts = detail.get("rework")
    if not isinstance(counts, dict):
        return ()
    return tuple(
        Cell(str(gate), board_wall.number(count), BY_KEY[RENDERABLE])
        for gate, count in sorted(counts.items())
    )


def _lane_cells(lane: Mapping[str, Any], now: datetime) -> tuple[Cell, ...]:

    cells = [
        _cell(lane, label, key)
        for label, key in (
            ("agent", "agent"),
            ("model", "model"),
            ("state", "state"),
            ("branch", "branch"),
            ("started", "started_at"),
            ("tokens", "tokens"),
            ("cost usd", "cost_usd"),
        )
    ]
    running = board_wall.since(lane.get("started_at"), now)
    if running is not None:
        cells.append(Cell("running for", board_wall.elapsed(running), BY_KEY[RENDERABLE]))
    return tuple(cells)


def _edges(document: Mapping[str, Any], record_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:

    graph = document.get("graph")
    edges = graph.get("edges") if isinstance(graph, dict) else None
    live = frozenset(ids(document))
    blockers, dependents = [], []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict) or edge.get("kind") != BLOCKS:
            continue
        blocked, blocker = str(edge.get("from") or ""), str(edge.get("to") or "")
        if blocked == record_id and blocker in live:
            blockers.append(blocker)
        if blocker == record_id and blocked in live:
            dependents.append(blocked)
    return tuple(sorted(set(blockers))), tuple(sorted(set(dependents)))


def _family(document: Mapping[str, Any], record_id: str) -> tuple[str, tuple[str, ...]]:

    graph = document.get("graph")
    edges = graph.get("edges") if isinstance(graph, dict) else None
    live = frozenset(ids(document))
    parent, children = "", []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict) or edge.get("kind") != PARENT_CHILD:
            continue
        child, owner = str(edge.get("from") or ""), str(edge.get("to") or "")
        if child == record_id and owner in live:
            parent = owner
        if owner == record_id and child in live:
            children.append(child)
    return parent, tuple(sorted(set(children)))


@dataclass(frozen=True)
class PageFacts:
    back: str = ".."
    start_command: str = ""
    body: str = ""


def start_form(document: Mapping[str, Any], record_id: str) -> dict[str, str]:

    unit = _find(_rows(document, "units"), record_id) or {}
    parent, _children = _family(document, record_id)
    return {"issue": record_id, "work_type": str(unit.get("type") or ""), "root": parent}


def startable(unit: Mapping[str, Any], lane: Mapping[str, Any] | None) -> bool:

    return bool(unit.get("ready")) and lane is None and not unit.get("owes")


def context(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    record_id: str,
    now: datetime,
    *,
    page: PageFacts | None = None,
) -> dict[str, Any] | None:

    page = page or PageFacts()
    reads = board_wall.readings(document, verdict)
    unit = _find(_rows(document, "units"), record_id)
    if unit is None:
        return None
    detail = _find(_rows(document, "detail"), record_id)
    lane = _find(_rows(document, "lanes"), record_id)
    blockers, dependents = _edges(document, record_id)
    parent, children = _family(document, record_id)
    return {
        "record": record_id,
        "back": page.back,
        "absent_text": ABSENT_TEXT,
        "href_suffix": HREF_SUFFIX,
        "age": board_wall.age(document, now),
        "title": _value(unit, "title"),
        "facts": _unit_cells(unit),
        "detail_note": "" if detail is not None else reads["detail"].note or ABSENT_TEXT,
        "binding": _detail_cells(detail) if detail is not None else (),
        "checkpoints": _checkpoints(detail) if detail is not None else (),
        "rework": _rework(detail) if detail is not None else (),
        "next_command": (detail or {}).get("next_command", ""),
        "lane": _lane_cells(lane, now) if lane is not None else (),
        "lane_note": "" if lane is not None else (reads["lanes"].note or NO_LANE),
        "lane_absent": lane is None,
        "lane_progress": str((lane or {}).get("note", "")),
        "parent": parent,
        "children": children,
        "start_command": page.start_command if startable(unit, lane) else "",
        "body": page.body,
        "titles": {
            str(row["id"]): str(row["title"])
            for row in _rows(document, "units")
            if row.get("id") and row.get("title")
        },
        "blockers": blockers,
        "dependents": dependents,
        "edges_note": NO_EDGES if not blockers and not dependents else "",
        "schema": document.get("schema", board_wall.UNKNOWN),
    }
