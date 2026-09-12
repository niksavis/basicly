from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_fields
from .board_wall import (
    ABSENT,
    BY_KEY,
    DOT,
    FAIL,
    RENDERABLE,
    STALE,
    UNKNOWN,
    Cell,
    bar,
    clip,
    day,
    elapsed,
    joined,
    more,
    number,
    numeric,
    since,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .board_wall import Age, Reading

EVENT_LINES = 1
NAME_MAX = 32
LINE_MAX = 180

GATE_NAMES = 4

HEALTH_SLOTS = 4
PRIORITY_SLOTS = 8

COUNTED_KEY = "closed_today"

STATUS_KIND = "status"
CLOSED_STATUS = "closed"

_BACKLOG_KEYS = ("total", "active", "ready", "blocked", "in_progress", "closed")
_SPEND_UNITS = {
    "lifetime_usd": "usd lifetime",
    "largest_dispatch_usd": "usd largest lane",
}
_HEALTH_KEYS = ("runs", "score", "failure_rate", "drift")
_STATUS_WORD = {FAIL: "fail", ABSENT: "not_run"}
_GATE_CAPTION = {"mode": "mode", "recorded_at": "recorded"}

VERDICT_STALE_AFTER_S = 900.0

_NOT_IN_SNAPSHOT = "not in this snapshot"


def _say(read: Reading) -> str:
    return _NOT_IN_SNAPSHOT if read.state.key == ABSENT else read.note


def backlog(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:

    read = reads["backlog"]
    if not read.drawn:
        return (Cell("backlog", _say(read), read.state),)
    held = read.fields
    closed = bar(held.get("closed"), held.get("total"))
    cells = [
        Cell(key.replace("_", " "), number(held.get(key)), bar=closed if key == "closed" else None)
        for key in _BACKLOG_KEYS
    ]
    return tuple(cells)


def priorities(reads: Mapping[str, Reading]) -> tuple[tuple[Cell, ...], str]:

    read = reads["backlog"]
    held = read.fields.get("by_priority") if read.drawn else None
    if not isinstance(held, dict) or not held:
        return (), ""
    whole = sum(value for value in held.values() if numeric(value) is not None)
    labels = sorted(held)
    cells = tuple(
        Cell(str(label), number(held[label]), bar=bar(held[label], whole))
        for label in labels[:PRIORITY_SLOTS]
    )
    return cells, more(len(labels) - PRIORITY_SLOTS, "priorities")


def _named(rows: Sequence[Mapping[str, Any]], status: str) -> list[str]:
    return [
        clip(check.get("name", UNKNOWN), NAME_MAX)
        for check in rows
        if str(check.get("status")) == status
    ]


def _verdict(rows: Sequence[Mapping[str, Any]], passed: object) -> tuple[str, str]:

    for status, word in ((FAIL, "FAILING"), (ABSENT, "NOT RUN")):
        named = _named(rows, _STATUS_WORD[status])
        if named:
            dropped = more(len(named) - GATE_NAMES, "checks")
            spelled = DOT.join([*named[:GATE_NAMES], *([dropped] if dropped else [])])
            return f"{len(named)} {word}: {spelled}", status
    return ("GREEN", RENDERABLE) if passed is not False else ("FAILING", FAIL)


def gates(reads: Mapping[str, Reading], drawn: Age) -> tuple[Cell, str]:

    read = reads["gates"]
    if not read.drawn:
        return Cell("gates", _say(read), read.state), ""
    held = read.fields
    checks = held.get("checks")
    rows = [
        check for check in (checks if isinstance(checks, list) else []) if isinstance(check, dict)
    ]
    token, state = _verdict(rows, held.get("passed"))
    moment = board_fields.instant(drawn.generated_at)
    lag = since(held.get("recorded_at"), moment) if moment is not None else None
    if lag is not None and lag > VERDICT_STALE_AFTER_S:
        state = STALE
    caption = DOT.join([
        *(f"{word} {held[key]}" for key, word in _GATE_CAPTION.items() if held.get(key)),
        *([_lag_phrase(lag)] if lag is not None else []),
    ])
    return Cell("gates", token, BY_KEY[state]), clip(caption, LINE_MAX) if caption else UNKNOWN


def _lag_phrase(lag: float) -> str:

    return f"taken {elapsed(lag)} before this snapshot"


def compact(value: object) -> str:

    if isinstance(value, bool) or not isinstance(value, int):
        return number(value)
    for bound, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if abs(value) >= bound:
            return f"{value / bound:.1f}".rstrip("0").rstrip(".") + suffix
    return number(value)


def spend(reads: Mapping[str, Reading]) -> Cell:

    read = reads["spend"]
    if not read.drawn:
        return Cell("spend", _say(read), read.state)
    held = read.fields
    figures = [
        f"{(number if unit.startswith('usd') else compact)(held.get(key))} {unit}"
        for key, unit in _SPEND_UNITS.items()
    ]
    spelled = clip(DOT.join([str(held.get("scope", UNKNOWN)), *figures]), LINE_MAX)
    return Cell("spend", spelled, read.state)


def health(reads: Mapping[str, Reading]) -> tuple[tuple[Cell, ...], str]:
    read = reads["health"]
    if not read.drawn:
        return (Cell("agents", _say(read), read.state),), ""
    agents = read.dicts
    if not agents:
        return (Cell("agents", "no run in the producer's window", read.state),), ""
    cells = tuple(
        Cell(
            clip(agent.get("agent", UNKNOWN), NAME_MAX),
            DOT.join(f"{key.replace('_', ' ')} {number(agent.get(key))}" for key in _HEALTH_KEYS),
        )
        for agent in agents[:HEALTH_SLOTS]
    )
    return cells, more(len(agents) - HEALTH_SLOTS, "agents")


def throughput(reads: Mapping[str, Reading], today: str) -> Cell:

    counted = reads["backlog"]
    held = counted.fields.get(COUNTED_KEY) if counted.drawn else None
    if isinstance(held, int) and not isinstance(held, bool):
        return Cell("closed today", number(held), counted.state)
    read = reads["events"]
    rows = [row for row in read.dicts if str(row.get("kind")) == STATUS_KIND] if read.drawn else []
    if not rows or not today:
        return Cell("closed today", UNKNOWN, BY_KEY[ABSENT])
    closed = {
        str(row.get("issue", ""))
        for row in rows
        if CLOSED_STATUS in str(row.get("text", "")).split() and day(row.get("at")) == today
    }
    return Cell("closed today", number(len(closed)), read.state)


@dataclass(frozen=True)
class EventLine:
    ident: str
    text: str


def events(reads: Mapping[str, Reading]) -> tuple[tuple[EventLine, ...], str]:

    read = reads["events"]
    if not read.drawn:
        return (EventLine("", f"events {_say(read)}"),), ""
    rows = read.dicts
    if not rows:
        return (EventLine("", "no event recorded"),), ""
    lines = tuple(
        EventLine(str(row.get("issue") or ""), joined(row, ("at", "kind", "text"), LINE_MAX))
        for row in reversed(rows[-EVENT_LINES:])
    )
    return lines, more(len(rows) - EVENT_LINES, "events")


def inventory(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:

    return tuple(
        Cell(read.name, _say(read), read.state)
        for read in reads.values()
        if read.state.key != RENDERABLE
    )
