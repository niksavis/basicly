from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_fields

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_schema import SnapshotVerdict

ABSENT_TEXT = "not emitted by this producer"

UNKNOWN = "not measured"

RENDERABLE = "renderable"
WITHHELD = "withheld"
ABSENT = "absent"
LIVE = "live"
STALE = "stale"
WAITING = "waiting"
STUCK = "stuck"
CALM = "calm"
FAIL = "fail"


@dataclass(frozen=True)
class State:
    key: str
    glyph: str
    border_style: str
    colour: str


STATES: tuple[State, ...] = (
    State(RENDERABLE, "\N{BLACK CIRCLE}", "solid", "var(--green)"),
    State(WITHHELD, "\N{WHITE DIAMOND}", "double", "var(--amber)"),
    State(ABSENT, "\N{WHITE CIRCLE}", "dashed", "var(--text-dim)"),
    State(LIVE, "\N{BLACK RIGHT-POINTING TRIANGLE}", "solid", "var(--green)"),
    State(STALE, "\N{BLACK DIAMOND}", "double", "var(--amber)"),
    State(WAITING, "\N{BLACK UP-POINTING TRIANGLE}", "solid", "var(--amber)"),
    State(STUCK, "\N{BLACK UP-POINTING TRIANGLE}", "double", "var(--orange)"),
    State(CALM, "\N{BULLSEYE}", "solid", "var(--green)"),
    State(FAIL, "\N{MULTIPLICATION X}", "double", "var(--amber)"),
)

BY_KEY: Mapping[str, State] = {state.key: state for state in STATES}

_FULL = 100.0
_MINUTE = 60
_HOUR = 3600
_DAY = 86400

_COARSE: tuple[tuple[int, str], ...] = ((_DAY, "DAY"), (_HOUR, "HOUR"), (_MINUTE, "MINUTE"))

DOT = " \N{MIDDLE DOT} "
_CLIP = "\N{HORIZONTAL ELLIPSIS}"

TITLE_MAX = 62
NOTE_MAX = 90


@dataclass(frozen=True)
class Bar:
    width: float
    label: str
    over: bool


@dataclass(frozen=True)
class Cell:
    label: str
    value: str
    state: State | None = None
    bar: Bar | None = None


@dataclass(frozen=True)
class Card:
    title: str
    phase: str
    state: State
    note: str
    cells: tuple[Cell, ...] = ()
    working: bool = False
    ident: str = ""


@dataclass(frozen=True)
class Phase:
    name: str
    count: int | None
    here: bool
    share: Bar | None = None
    moved: bool = False


@dataclass(frozen=True)
class Item:
    priority: str
    ident: str
    title: str


@dataclass(frozen=True)
class Band:
    state: State
    headline: str
    kicker: str
    lines: tuple[str, ...]
    stale: str


@dataclass(frozen=True)
class Group:
    name: str
    count: str
    rows: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Listing:
    state: State
    rows: tuple[Item, ...] = ()
    more: str = ""
    note: str = ""
    groups: tuple[Group, ...] = ()


UNATTACHED = "Not attached to any feature"

PARENT_CHILD = "parent-child"


def feature_of(ident: str, parents: Mapping[str, str], titles: Mapping[str, str]) -> str:

    seen: set[str] = set()
    at = ident
    while at in parents:
        if at in seen:
            return ""
        seen.add(at)
        at = parents[at]
    return titles.get(at, "") if at != ident else ""


@dataclass(frozen=True)
class Age:
    generated_at: str
    phrase: str
    state: State
    stale_after: str


@dataclass(frozen=True)
class Reading:
    name: str
    state: State
    note: str
    held: Any = None

    @property
    def rows(self) -> Sequence[Any]:
        return self.held if isinstance(self.held, list) else ()

    @property
    def fields(self) -> Mapping[str, Any]:
        return self.held if isinstance(self.held, dict) else {}

    @property
    def drawn(self) -> bool:
        return self.state.key == RENDERABLE

    @property
    def dicts(self) -> list[Mapping[str, Any]]:
        return [row for row in self.rows if isinstance(row, dict)]


class Readings(dict[str, Reading]):
    def __missing__(self, name: str) -> Reading:
        return Reading(name, BY_KEY[ABSENT], ABSENT_TEXT)


def readings(document: Mapping[str, Any], verdict: SnapshotVerdict) -> Readings:

    drawn = set(verdict.renderable)
    ruled = Readings(
        (name, Reading(name, BY_KEY[RENDERABLE], "", document.get(name)))
        for name in verdict.renderable
    )
    for section in verdict.sections:
        if section.name not in drawn:
            note = "; ".join(section.violations) or "; ".join(verdict.violations)
            ruled[section.name] = Reading(section.name, BY_KEY[WITHHELD], note or UNKNOWN)
    for name in verdict.absent:
        ruled[name] = Reading(name, BY_KEY[ABSENT], ABSENT_TEXT)
    return ruled


def numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def bar(part: object, whole: object) -> Bar | None:

    top, bottom = numeric(part), numeric(whole)
    if top is None or bottom is None or bottom <= 0:
        return None
    share = top / bottom * _FULL
    return Bar(min(share, _FULL), f"{share:.0f}%", share > _FULL)


def elapsed(seconds: float) -> str:
    if seconds < _MINUTE:
        return f"{int(seconds)}s"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)}m {int(seconds % _MINUTE)}s"
    return f"{int(seconds // _HOUR)}h {int(seconds % _HOUR // _MINUTE)}m"


def coarse(seconds: float) -> str:

    for bound, unit in _COARSE:
        count = int(seconds // bound)
        if count:
            return f"{count} {unit}" if count == 1 else f"{count} {unit}S"
    return f"{int(seconds)} SECONDS" if int(seconds) != 1 else "1 SECOND"


def day(stamp: object) -> str:

    written = board_fields.instant(stamp) if isinstance(stamp, str) else None
    return "" if written is None else written.date().isoformat()


def duration(value: object) -> str:
    seconds = numeric(value)
    return UNKNOWN if seconds is None else elapsed(seconds)


def since(stamp: object, now: datetime) -> float | None:

    written = board_fields.instant(stamp) if isinstance(stamp, str) else None
    if written is None:
        return None
    return max(0.0, (now - written).total_seconds())


def age(document: Mapping[str, Any], now: datetime) -> Age:

    stamp = document.get("generated_at")
    fresh = document.get("freshness")
    bound = numeric(fresh.get("stale_after_s")) if isinstance(fresh, dict) else None
    seconds = since(stamp, now)
    if seconds is None:
        return Age(str(stamp or UNKNOWN), "age unknown", BY_KEY[STALE], UNKNOWN)
    state = STALE if bound is not None and seconds > bound else LIVE
    return Age(
        str(stamp),
        f"{elapsed(seconds)} ago",
        BY_KEY[state],
        f"{bound:g}s" if bound is not None else UNKNOWN,
    )


def number(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return UNKNOWN


def clip(value: object, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + _CLIP


def more(dropped: int, noun: str) -> str:
    return f"+{dropped} more {noun}" if dropped > 0 else ""


def joined(held: Mapping[str, Any], keys: Sequence[str], limit: int = NOTE_MAX) -> str:
    parts = [str(held[key]) for key in keys if held.get(key) not in (None, "")]
    return clip(DOT.join(parts), limit) if parts else UNKNOWN


def cell(read: Reading, label: str, keys: Sequence[str]) -> Cell:
    if not read.drawn:
        return Cell(label, read.note, read.state)
    return Cell(label, joined(read.fields, keys), read.state)
