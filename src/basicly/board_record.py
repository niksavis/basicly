"""One record's own page, read from the same snapshot the wall is drawn from.

The boundary is *one record* against *the population*: :mod:`basicly.board_regions` and
:mod:`basicly.board_footer` draw every unit at the width a wall has, and this draws one unit
at the depth a developer asks for. Nothing here reads engine state - the input is a parsed
``harness-board/v1`` document and the verdict ruled on it (C12), so a reader sees exactly
what the producer published and never a value this layer went and fetched.

**The link is relative, and that is what makes one href work in both modes.**
``record/<id>.html`` resolves to ``<out-dir>/record/<id>.html`` beside a ``--out`` page and
to ``/record/<id>.html`` under the server, so the wall carries one spelling rather than one
per mode. :mod:`basicly.board_serve` answers ``/record/<id>`` as well, which is the route
the acceptance names.

**A field the snapshot does not carry renders as absent.** The three sections this page adds
to the wall's - the unit row, the detail row and the lane - each report their own state, so
a producer that emits no ``detail`` yields a page saying so rather than a page of empty
values (:data:`basicly.board_wall.ABSENT_TEXT`).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import board_wall
from .board_wall import ABSENT, ABSENT_TEXT, BY_KEY, RENDERABLE, Cell

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_schema import SnapshotVerdict

# The directory the pages sit in, under the wall's own. One constant, read by the href below,
# by the server's route and by the writer in `board_cli`.
HREF_DIR = "record"
HREF_SUFFIX = ".html"

# What a record id may hold to become a file name. Ids are the producer's, not this
# harness's, so a foreign one carrying a separator or a dot-dot segment would otherwise name
# a path outside the output directory. A refused id keeps its wall row and gets no page.
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

# The edge kind that means "waits on"; `board_graph` spells the same constant for the wall's
# frontier count, and the schema leaves the vocabulary open.
BLOCKS = "blocks"

NO_LANE = "no lane holds this record"
NO_EDGES = "nothing waits on this record and it waits on nothing"


def href(record_id: str) -> str:
    """The link the wall prints for *record_id*, relative to the page it is printed on."""
    return f"{HREF_DIR}/{record_id}{HREF_SUFFIX}"


def writable(record_id: str) -> bool:
    """True when *record_id* may become a file name under :data:`HREF_DIR`."""
    return bool(SAFE_ID.match(record_id)) and ".." not in record_id


def _rows(document: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    """The object rows of *section*, or nothing where the producer emitted none."""
    held = document.get(section)
    return [row for row in held if isinstance(row, dict)] if isinstance(held, list) else []


def ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Every record the document lists a unit row for, which is what a page can be built for."""
    return tuple(
        ident for row in _rows(document, "units") if isinstance(ident := row.get("id"), str)
    )


def _find(rows: Sequence[Mapping[str, Any]], record_id: str) -> Mapping[str, Any] | None:
    """The first row of *rows* whose ``id`` is *record_id*."""
    return next((row for row in rows if row.get("id") == record_id), None)


def _value(held: Mapping[str, Any], key: str) -> str:
    """*key* off *held*, or :data:`ABSENT_TEXT` where the producer omitted it.

    Absent rather than empty: `units` omits a title it does not hold, and an empty cell
    would read as a record with no title. A string passes through and everything else goes
    through the wall's own formatting - `board_wall.number` reports a string as unmeasured,
    which is what it is for a figure and a lie for a status.
    """
    if key not in held:
        return ABSENT_TEXT
    value = held[key]
    return value if isinstance(value, str) else board_wall.number(value)


def _cell(held: Mapping[str, Any], label: str, key: str) -> Cell:
    """One labelled field of *held*, carrying whether the producer emitted it."""
    present = key in held
    return Cell(label, _value(held, key), BY_KEY[RENDERABLE if present else ABSENT])


def _unit_cells(unit: Mapping[str, Any]) -> tuple[Cell, ...]:
    """What the wall's row carries about this record, unclipped."""
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
    """The binding this record is being built in."""
    return tuple(
        _cell(detail, label, key) for label, key in (("worktree", "worktree"), ("branch", "branch"))
    )


def _checkpoints(detail: Mapping[str, Any]) -> tuple[tuple[str, bool], ...]:
    """Each checkpoint the producer named, and whether it is held.

    Held and missing are read as two lists rather than one subtracted from a roster: the
    roster is the producer's own, and this consumer does not know it.
    """
    held = [name for name in detail.get("checkpoints_held", []) if isinstance(name, str)]
    missing = [name for name in detail.get("checkpoints_missing", []) if isinstance(name, str)]
    return tuple([(name, True) for name in held] + [(name, False) for name in missing])


def _rework(detail: Mapping[str, Any]) -> tuple[Cell, ...]:
    """One cell per gate the producer counted attempts for."""
    counts = detail.get("rework")
    if not isinstance(counts, dict):
        return ()
    return tuple(
        Cell(str(gate), board_wall.number(count), BY_KEY[RENDERABLE])
        for gate, count in sorted(counts.items())
    )


def _lane_cells(lane: Mapping[str, Any], now: datetime) -> tuple[Cell, ...]:
    """Who is running this record, since when, and what it has spent.

    ``started`` is drawn as an interval as well as a stamp: the stamp answers "which
    dispatch" and only the interval answers "is this lane stuck", which is the question a
    developer opened the page with.
    """
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
    """What this record waits on, and what waits on it - each kept to records still listed.

    An edge onto a record ``units`` no longer lists is a debt already paid, the cut
    :func:`basicly.board_graph.queue` makes for the same reason: the section is the active
    population, so a closed blocker holds nothing.
    """
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


def context(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    record_id: str,
    now: datetime,
    *,
    back: str = "..",
) -> dict[str, Any] | None:
    """Everything the record template draws, or None where this document has no such record.

    None rather than an empty page: a page drawn for an id the snapshot never listed would
    report every field as absent, which reads as a record the producer knows nothing about
    rather than as one it never mentioned. *back* is the caller's own link to the wall,
    because only the caller knows what the wall is called - a file beside this one under
    ``--out``, and the origin's root under the server.
    """
    reads = board_wall.readings(document, verdict)
    unit = _find(_rows(document, "units"), record_id)
    if unit is None:
        return None
    detail = _find(_rows(document, "detail"), record_id)
    lane = _find(_rows(document, "lanes"), record_id)
    blockers, dependents = _edges(document, record_id)
    return {
        "record": record_id,
        "back": back,
        # The template marks a value absent by comparing against this rather than by a second
        # spelling of it: the wording is `board_wall`'s and one page may not reword it.
        "absent_text": ABSENT_TEXT,
        "href_suffix": HREF_SUFFIX,
        "age": board_wall.age(document, now),
        "title": _value(unit, "title"),
        "facts": _unit_cells(unit),
        "detail_note": "" if detail is not None else reads["detail"].note or ABSENT_TEXT,
        "binding": _detail_cells(detail) if detail is not None else (),
        "checkpoints": _checkpoints(detail) if detail is not None else (),
        "rework": _rework(detail) if detail is not None else (),
        # The command is the producer's own remedy text, printed and never composed here.
        "next_command": (detail or {}).get("next_command", ""),
        "lane": _lane_cells(lane, now) if lane is not None else (),
        "lane_note": "" if lane is not None else (reads["lanes"].note or NO_LANE),
        "lane_absent": lane is None,
        "lane_progress": str((lane or {}).get("note", "")),
        "blockers": blockers,
        "dependents": dependents,
        "edges_note": NO_EDGES if not blockers and not dependents else "",
        "schema": document.get("schema", board_wall.UNKNOWN),
    }
