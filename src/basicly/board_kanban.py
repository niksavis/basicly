"""The loop as a column per phase, each naming the records that sit in it.

Owner, 2026-09-06: *"the states have numbers, but are not interactive and i cannot see which
items are in classify, intake, decompose, build, and so on"*. The loop region draws seven
boxes carrying a count each, and nothing in one is reachable.

This reverses `basicly-a68ggd` deliberately and only here. That finding took the region *away*
from listing the backlog, because a wall read from across a room should show the running pass.
The owner has since redefined the board as an instrument worked at a desk, where the members
are the point. The old concern survives as :data:`CARD_SLOTS`: `intake` holds hundreds, and a
column that draws them all is the defect `a68ggd` closed (basicly-lc2bd3v.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_record, board_wall
from .board_loop import PHASES, working_phase

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_schema import SnapshotVerdict

# How many cards a column draws before it says how many it kept back.
CARD_SLOTS = 12

# Where a record carrying no priority sorts: after every ranked one, never silently first.
UNRANKED = 99


@dataclass(frozen=True)
class Card:
    """One record in a column: what it is, and whether an agent is inside it."""

    ident: str
    title: str
    priority: str
    kind: str
    held: bool


@dataclass(frozen=True)
class Column:
    """One phase: its name, how many rest there, the cards drawn, and what was kept back."""

    name: str
    count: int
    cards: tuple[Card, ...]
    more: str


def _rows(document: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    """The object rows of *section*, or nothing where the producer emitted none."""
    held = document.get(section)
    return [row for row in held if isinstance(row, dict)] if isinstance(held, list) else []


def _held(document: Mapping[str, Any]) -> frozenset[str]:
    """Every record a lane row names, so a card can say a worktree is inside it."""
    return frozenset(
        ident for row in _rows(document, "lanes") if (ident := str(row.get("id") or ""))
    )


def _rank(card: Card) -> tuple[int, int, str]:
    """Held first, then by priority, then by id.

    Held first so the work actually in flight cannot be the thing a full column drops: a
    reader looking for what an agent is doing must not have to widen the cap to find it.
    """
    figure = card.priority[1:]
    return (
        0 if card.held else 1,
        int(figure) if card.priority[:1] == "P" and figure.isdigit() else UNRANKED,
        card.ident,
    )


def columns(document: Mapping[str, Any], *, slots: int = CARD_SLOTS) -> tuple[Column, ...]:
    """One column per phase the engine declares, in the loop's own order.

    Every phase draws, including an empty one. A column that disappears as it empties makes
    the surface change shape while a reader watches, and a phase added to `board_loop.PHASES`
    later would otherwise be invisible until something reached it.

    Binned by `working_phase` and not by `phase`, so a parked record is not drawn as work at
    a phase - the two populations disagreeing about `deferred` is how one number on a page
    contradicts another one region over (basicly-5jkxqk).
    """
    inside = _held(document)
    binned: dict[str, list[Card]] = {name: [] for name in PHASES}
    for row in _rows(document, "units"):
        name = working_phase(row)
        ident = str(row.get("id") or "")
        if not ident or name not in binned:
            continue
        binned[name].append(
            Card(
                ident,
                str(row.get("title") or ""),
                str(row.get("priority") or ""),
                str(row.get("type") or ""),
                ident in inside,
            )
        )
    return tuple(_column(name, binned[name], slots) for name in PHASES)


def _column(name: str, cards: Sequence[Card], slots: int) -> Column:
    """*cards* ordered and capped into one column, which states what it kept back."""
    ordered = sorted(cards, key=_rank)
    kept = len(ordered) - slots
    return Column(name, len(ordered), tuple(ordered[:slots]), f"+{kept} more" if kept > 0 else "")


def context(
    document: Mapping[str, Any],
    verdict: SnapshotVerdict,
    now: datetime,
    *,
    back: str = ".",
    slots: int = CARD_SLOTS,
) -> dict[str, Any]:
    """Everything the kanban template draws.

    *back* is the caller's own link to the wall, for the reason `board_record.context` takes
    one: only the caller knows whether the wall is a file beside this page or the server root.

    `note` carries the producer's own reason where `units` is unreadable. Without it every
    column draws a truthful-looking zero, and "no record is at classify" is indistinguishable
    from "this board could not read the section that would say".
    """
    reads = board_wall.readings(document, verdict)
    return {
        "back": back,
        "age": board_wall.age(document, now),
        "note": reads["units"].note,
        "columns": columns(document, slots=slots),
        "record_dir": board_record.HREF_DIR,
        "record_ext": board_record.HREF_SUFFIX,
        "linkable": frozenset(board_record.ids(document)),
        "schema": document.get("schema", board_wall.UNKNOWN),
    }
