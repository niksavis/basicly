from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_record, board_wall
from .board_record import BLOCKS, PARENT_CHILD

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from .board_schema import SnapshotVerdict

NO_FEATURE = "not attached to any feature"

UNRANKED = 99

PARKED = frozenset({"deferred"})


@dataclass(frozen=True)
class Row:
    ident: str
    title: str
    priority: str
    status: str
    phase: str
    kind: str
    ready: bool
    blockers: tuple[str, ...]
    owes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    ident: str
    title: str
    rows: tuple[Row, ...]
    ready: int


def _rows(document: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    held = document.get(section)
    return [row for row in held if isinstance(row, dict)] if isinstance(held, list) else []


def _edges(document: Mapping[str, Any], kind: str) -> list[tuple[str, str]]:
    graph = document.get("graph")
    held = graph.get("edges") if isinstance(graph, dict) else None
    return [
        (str(edge.get("from") or ""), str(edge.get("to") or ""))
        for edge in (held if isinstance(held, list) else [])
        if isinstance(edge, dict) and edge.get("kind") == kind
    ]


def _blockers(document: Mapping[str, Any], live: frozenset[str]) -> dict[str, list[str]]:

    held: dict[str, list[str]] = {}
    for blocked, blocker in _edges(document, BLOCKS):
        if blocked in live and blocker in live:
            held.setdefault(blocked, []).append(blocker)
    return held


def _parents(document: Mapping[str, Any], live: frozenset[str]) -> dict[str, str]:
    return {
        child: owner
        for child, owner in _edges(document, PARENT_CHILD)
        if child in live and owner in live
    }


def _rank(row: Row) -> tuple[int, str]:
    figure = row.priority[1:]
    return (
        int(figure) if row.priority[:1] == "P" and figure.isdigit() else UNRANKED,
        row.ident,
    )


def groups(document: Mapping[str, Any]) -> tuple[Group, ...]:

    units = _rows(document, "units")
    live = frozenset(board_record.ids(document))
    waits = _blockers(document, live)
    owner_of = _parents(document, live)
    titles = {str(unit.get("id") or ""): str(unit.get("title") or "") for unit in units}
    held: dict[str, list[Row]] = {}
    for unit in units:
        ident = str(unit.get("id") or "")
        if not ident:
            continue
        held.setdefault(owner_of.get(ident, ""), []).append(
            Row(
                ident,
                str(unit.get("title") or ""),
                str(unit.get("priority") or ""),
                str(unit.get("status") or ""),
                str(unit.get("phase") or ""),
                str(unit.get("type") or ""),
                bool(unit.get("ready")),
                tuple(sorted(waits.get(ident, ()))),
                tuple(str(name) for name in unit.get("owes") or ()),
            )
        )
    built = [
        Group(
            parent,
            titles.get(parent, "") or NO_FEATURE if parent else NO_FEATURE,
            tuple(sorted(rows, key=_rank)),
            sum(1 for row in rows if row.ready),
        )
        for parent, rows in held.items()
    ]
    return tuple(sorted(built, key=lambda g: (not g.ident, -g.ready, g.title)))


def totals(drawn: tuple[Group, ...]) -> dict[str, int]:

    rows = [row for group in drawn for row in group.rows]
    return {
        "records": len(rows),
        "ready": sum(1 for row in rows if row.ready),
        "dispatchable": sum(1 for row in rows if row.ready and not row.owes),
        "blocked": sum(1 for row in rows if not row.ready and row.status not in PARKED),
        "parked": sum(1 for row in rows if row.status in PARKED),
        "waiting": sum(1 for row in rows if row.blockers),
        "features": sum(1 for group in drawn if group.ident),
    }


def context(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    now: datetime,
    *,
    back: str = ".",
) -> dict[str, Any]:

    reads = board_wall.readings(document, verdict)
    drawn = groups(document)
    return {
        "back": back,
        "age": board_wall.age(document, now),
        "note": reads["units"].note,
        "groups": drawn,
        "totals": totals(drawn),
        "record_dir": board_record.HREF_DIR,
        "record_ext": board_record.HREF_SUFFIX,
        "linkable": frozenset(board_record.ids(document)),
        "schema": document.get("schema", board_wall.UNKNOWN),
    }
