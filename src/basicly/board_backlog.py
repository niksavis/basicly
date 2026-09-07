"""Every record the snapshot lists, grouped by the feature it belongs to.

Owner, 2026-09-06, reading a wall whose lower half was empty: *"maybe we can have a way to
click somewhere on the board and see the full backlog list so we can plan what to do next
better?"* The wall drew four of 237 ready records - 1.7% of the set an operator plans from.

A page and not a bigger region. The wall is a glance surface and is right to cap a list; a plan
is done at a desk, wants every record, and wants the fields a person sorts on. Uncapped by
construction here: nothing in this module takes a slot count, so a cap cannot be reintroduced
without a signature changing (basicly-lc2bd3v.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_record, board_wall
from .board_record import BLOCKS, PARENT_CHILD

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from .board_schema import SnapshotVerdict

# What a record belonging to no feature is filed under. Named rather than dropped: 40 of them
# sit outside every parent, and a plan that cannot see them plans two thirds of the work.
NO_FEATURE = "not attached to any feature"

# Where a record carrying no priority sorts: after every ranked one, never silently first.
UNRANKED = 99

# The status the producer leaves out of its `blocked` figure. One spelling, so this page and
# the wall's footer cannot come to disagree about what a parked record is.
PARKED = frozenset({"deferred"})


@dataclass(frozen=True)
class Row:
    """One record as a plan reads it: what it is, where it is, and what holds it."""

    ident: str
    title: str
    priority: str
    status: str
    phase: str
    kind: str
    ready: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class Group:
    """One feature and the records under it; *ident* is empty for the unparented set."""

    ident: str
    title: str
    rows: tuple[Row, ...]
    ready: int


def _rows(document: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    """The object rows of *section*, or nothing where the producer emitted none."""
    held = document.get(section)
    return [row for row in held if isinstance(row, dict)] if isinstance(held, list) else []


def _edges(document: Mapping[str, Any], kind: str) -> list[tuple[str, str]]:
    """Every ``(from, to)`` of *kind*, in the direction the producer writes them."""
    graph = document.get("graph")
    held = graph.get("edges") if isinstance(graph, dict) else None
    return [
        (str(edge.get("from") or ""), str(edge.get("to") or ""))
        for edge in (held if isinstance(held, list) else [])
        if isinstance(edge, dict) and edge.get("kind") == kind
    ]


def _blockers(document: Mapping[str, Any], live: frozenset[str]) -> dict[str, list[str]]:
    """What each record waits on, keyed by the record that waits.

    ``from`` is the **blocked** record and ``to`` is the blocker, which is
    `board_record._edges`' reading and not a second one - the JSON reads the other way round
    at a glance, and two answers here would put a record under the wrong blocker on a page
    somebody plans from. Kept to *live* for that function's reason: an edge onto a record
    ``units`` no longer lists is a debt already paid.
    """
    held: dict[str, list[str]] = {}
    for blocked, blocker in _edges(document, BLOCKS):
        if blocked in live and blocker in live:
            held.setdefault(blocked, []).append(blocker)
    return held


def _parents(document: Mapping[str, Any], live: frozenset[str]) -> dict[str, str]:
    """Each record's parent, keyed by the child - the direction the decomposition writes."""
    return {
        child: owner
        for child, owner in _edges(document, PARENT_CHILD)
        if child in live and owner in live
    }


def _rank(row: Row) -> tuple[int, str]:
    """By priority, then by id, so one group reads the same way twice."""
    figure = row.priority[1:]
    return (
        int(figure) if row.priority[:1] == "P" and figure.isdigit() else UNRANKED,
        row.ident,
    )


def groups(document: Mapping[str, Any]) -> tuple[Group, ...]:
    """Every record ``units`` lists, under the feature that owns it.

    Ordered by how much of the group can be started: a reader choosing what to do next is
    looking for the feature with work available, and a group whose every member is blocked is
    not that. Ties fall back on the title, so the order is stable between two folds.
    """
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
    # The unparented set last whatever its ready count: it is a bucket, not a feature, and a
    # plan reads the features first.
    return tuple(sorted(built, key=lambda g: (not g.ident, -g.ready, g.title)))


def totals(drawn: tuple[Group, ...]) -> dict[str, int]:
    """The page's own counts, summed over what it drew rather than read from a section.

    Derived from the rows on the page so the header cannot claim a figure the body does not
    show - which is the whole defect this page exists to end, one surface up.
    """
    rows = [row for group in drawn for row in group.rows]
    return {
        "records": len(rows),
        "ready": sum(1 for row in rows if row.ready),
        # `blocked` is the producer's own population and not a fourth reading of it: the wall
        # prints `BLOCKED 57` and links here, so a page answering `61 not ready` sends a reader
        # from one number to another with no way to reconcile them. Measured on this repo, the
        # producer's figure is *not ready and not parked*, so the two are stated apart and
        # `ready + blocked + parked` sums to the record count a reader can check by eye.
        "blocked": sum(1 for row in rows if not row.ready and row.status not in PARKED),
        "parked": sum(1 for row in rows if row.status in PARKED),
        # A subset of `blocked`, never a synonym: only 32 of the 57 carry a `blocks` edge, so
        # the rest are unstartable for a reason no edge names - a parent not decomposed, a
        # Definition-of-Ready unmet. This is the part whose obstacle the page can point at.
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
    """Everything the backlog template draws.

    `note` carries the producer's reason where ``units`` is unreadable, for the reason the
    loop surface carries one: an empty page and an unreadable section are not one fact.
    """
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
