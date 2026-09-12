from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_record, board_wall
from .board_loop import PHASES, working_phase

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_schema import SnapshotVerdict

CARD_SLOTS = 12

UNRANKED = 99


@dataclass(frozen=True)
class Card:
    ident: str
    title: str
    priority: str
    kind: str
    held: bool


@dataclass(frozen=True)
class Column:
    name: str
    count: int
    cards: tuple[Card, ...]
    more: str


def _rows(document: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    held = document.get(section)
    return [row for row in held if isinstance(row, dict)] if isinstance(held, list) else []


def _held(document: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        ident for row in _rows(document, "lanes") if (ident := str(row.get("id") or ""))
    )


def _rank(card: Card) -> tuple[int, int, str]:

    figure = card.priority[1:]
    return (
        0 if card.held else 1,
        int(figure) if card.priority[:1] == "P" and figure.isdigit() else UNRANKED,
        card.ident,
    )


def columns(document: Mapping[str, Any], *, slots: int = CARD_SLOTS) -> tuple[Column, ...]:

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
