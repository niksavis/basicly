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
from dataclasses import dataclass
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

# The edge kind that means "belongs to", spelled `from` the child `to` the parent. A page that
# rendered only `BLOCKS` left an epic saying nothing open waited on it while fourteen children
# were open, and gave a child no way back to the epic that explains it.
PARENT_CHILD = "parent-child"

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


def _family(document: Mapping[str, Any], record_id: str) -> tuple[str, tuple[str, ...]]:
    """The parent this record belongs to, and the children still listed under it.

    Kept to records ``units`` still lists, the cut :func:`_edges` makes for the same reason:
    the page is the active population, so a closed child is a debt already paid. The parent
    is a single edge by construction - a record has one - so it is returned as one id rather
    than a tuple, and is empty where the record is a root or its parent has closed.
    """
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
    """What only the caller knows about a record page: where it sits, and what starts it.

    Two fields rather than two parameters, because both come from the tier above this one and
    neither is derivable here. `back` is the path to the wall, which differs between the
    written directory and the served root. `start_command` is built by
    `board_actions.ACTIONS`, which this module sits below and may not reach.
    """

    back: str = ".."
    start_command: str = ""
    # The record's own description. Beside the document rather than inside it: 304 bodies is a
    # snapshot nobody can serve, and a record page is one record (basicly-lc2bd3v.2).
    body: str = ""


def start_form(document: Mapping[str, Any], record_id: str) -> dict[str, str]:
    """The fields `board_actions`' start action needs, read off the document.

    The record's own type and the parent whose grant covers it. Pressed without them the
    detached child reached intake and stopped - `classify needs an agent-proposed work type;
    no active grant on <id> to delegate it under` - so a start that carries only the id starts
    a process that halts one step in. Measured by pressing it (basicly-fiow1sr).

    Neither value is decided here: `type` is the producer's own field and the parent is a
    `parent-child` edge the graph already carries. A missing one is omitted, never guessed.
    """
    unit = _find(_rows(document, "units"), record_id) or {}
    parent, _children = _family(document, record_id)
    return {"issue": record_id, "work_type": str(unit.get("type") or ""), "root": parent}


def startable(unit: Mapping[str, Any], lane: Mapping[str, Any] | None) -> bool:
    """Whether this record may be offered a start, on three conditions.

    Ready, unheld, and owing nothing. `ready` is the producer's own field - the tracker's
    answer to whether anything *blocks* it - and a lane row means a worktree already exists,
    whatever state that lane is in. Offering a start on either would begin a second lane on
    work already in flight (basicly-fiow1sr).

    `owes` is the third, and the dependency walk cannot stand in for it: on the commit that
    added the trigger gate the walk called 245 records ready and the gate would refuse 244
    of them, so a start control drawn on `ready` alone is a control that cannot work
    (basicly-lc2bd3v.9).
    """
    return bool(unit.get("ready")) and lane is None and not unit.get("owes")


def context(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    record_id: str,
    now: datetime,
    *,
    page: PageFacts | None = None,
) -> dict[str, Any] | None:
    """Everything the record template draws, or None where this document has no such record.

    None rather than an empty page: a page drawn for an id the snapshot never listed would
    report every field as absent, which reads as a record the producer knows nothing about
    rather than as one it never mentioned. *back* is the caller's own link to the wall,
    because only the caller knows what the wall is called - a file beside this one under
    ``--out``, and the origin's root under the server.
    """
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
        "parent": parent,
        "children": children,
        # Supplied by a caller that may reach `board_actions`, and shown only where the
        # record is startable. This module sits below that table in the tier contract, and
        # spelling the argv here instead would be a second answer to what the button runs -
        # which is exactly how the refused version of this shipped (basicly-fiow1sr).
        "start_command": page.start_command if startable(unit, lane) else "",
        "body": page.body,
        # Every id this page prints, so the tree and the edge lists name a record rather than
        # address one. The owner's standing rule, and the ready list already keeps it - this
        # page was the surface that did not (basicly-lc2bd3v.2, basicly-lc2bd3v.8).
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
