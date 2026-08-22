"""The wall's vocabulary: what a state is, when a bar may be drawn, and what absence says.

Three modules draw this page and this is the bottom one. It holds the shapes every region is
built out of and the four honesty rules the design turns on, in one place so no region can
quietly keep its own version of them:

* **Every value carries the document's age**, and :func:`age` is the single reading of it -
  drawn once for the whole page rather than once per panel.
* **A bar needs both of its numbers.** :func:`bar` returns None where either term is absent,
  unmeasured or zero, and the caller then prints the raw number. This repo has already
  shipped a wrong ``context_window``; a bar against a wrong ceiling reads as reassurance.
* **An absent section reads :data:`ABSENT_TEXT`, never a zero.** :class:`Reading` is what a
  region is handed, and it carries the verdict's state rather than a bare value.
* **Every state is encoded three ways** - a Unicode glyph, a border style, and colour -
  so the page survives a monochrome projector and a colour-blind reader.

:mod:`basicly.board_regions` builds the regions on top of this and
:mod:`basicly.board_render` draws them. Nothing here reads engine state: the input is one
already-parsed ``harness-board/v1`` document, the verdict ruled on it, and an injected
instant.

**Orange belongs to the watch band and to nothing else.** ``site/index.html`` ships no red,
so ``--orange`` is this page's alarm channel and the band is its only site; a failing gate
and an over-budget bar take ``--amber`` and their own glyphs instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import board_fields

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_schema import SnapshotVerdict

# What an absent section says, verbatim. A zero or an empty box claims the producer measured
# nothing; this says it did not measure.
ABSENT_TEXT = "not emitted by this producer"

# No value on this page is drawn as bare text - `-` reads as a dash in a number column.
UNKNOWN = "not measured"

RENDERABLE = "renderable"
WITHHELD = "withheld"
ABSENT = "absent"
LIVE = "live"
STALE = "stale"
WAITING = "waiting"
CALM = "calm"
FAIL = "fail"


@dataclass(frozen=True)
class State:
    """One state and the three channels it is encoded on."""

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
    State(WAITING, "\N{BLACK UP-POINTING TRIANGLE}", "solid", "var(--orange)"),
    State(CALM, "\N{BULLSEYE}", "solid", "var(--green)"),
    State(FAIL, "\N{MULTIPLICATION X}", "double", "var(--amber)"),
)

# Public because two modules above resolve a state by name, and a leading underscore reached
# across a module boundary is a private surface with extra steps.
BY_KEY: Mapping[str, State] = {state.key: state for state in STATES}

_FULL = 100.0
_MINUTE = 60
_HOUR = 3600
_DAY = 86400

# The units a headline may be spelled in, coarsest first. Six metres reads a magnitude, not a
# figure, so the first unit with a whole count in it wins and the exact elapsed phrase stays on
# the detail line beneath for the reader who needs it.
_COARSE: tuple[tuple[int, str], ...] = ((_DAY, "DAY"), (_HOUR, "HOUR"), (_MINUTE, "MINUTE"))

DOT = " \N{MIDDLE DOT} "
_CLIP = "\N{HORIZONTAL ELLIPSIS}"

TITLE_MAX = 62
NOTE_MAX = 90


@dataclass(frozen=True)
class Bar:
    """A proportional bar, drawn only where both of its terms were measured."""

    width: float
    label: str
    over: bool


@dataclass(frozen=True)
class Cell:
    """One labelled value, with the bar and the state channel it earned."""

    label: str
    value: str
    state: State | None = None
    bar: Bar | None = None


@dataclass(frozen=True)
class Card:
    """One in-flight lane, or an empty slot holding the row's shape."""

    title: str
    phase: str
    state: State
    note: str
    cells: tuple[Cell, ...] = ()


@dataclass(frozen=True)
class Phase:
    """One loop phase: its name, its count, its share, and whether work sits here now.

    ``share`` is what makes 213 against 1 visible at six metres: seven numbers in seven
    identical boxes rank by nothing, so the bar carries the magnitude and the digits carry
    the value. It is None wherever :func:`bar` refuses one.
    """

    name: str
    count: int | None
    here: bool
    share: Bar | None = None


@dataclass(frozen=True)
class Item:
    """One ranked ready unit, as the next-up list prints it."""

    priority: str
    ident: str
    title: str


@dataclass(frozen=True)
class Band:
    """The alarm: how long it has waited, what it is, and the stale marker.

    ``headline`` is the **age** while anybody is waiting and the verdict otherwise, because
    that is the one figure the band exists to rank by; ``kicker`` says how many are waiting
    and ``lines`` say which. A green state costs one token here too - a quiet room is a
    headline and no kicker.
    """

    state: State
    headline: str
    kicker: str
    lines: tuple[str, ...]
    stale: str


@dataclass(frozen=True)
class Group:
    """One feature's ready rows, headed by the feature they serve."""

    name: str
    count: str
    rows: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Listing:
    """A ranked region: what it drew, what it dropped, and why it drew nothing."""

    state: State
    rows: tuple[Item, ...] = ()
    more: str = ""
    note: str = ""
    groups: tuple[Group, ...] = ()


# 41 of the 187 ready units on 2026-08-22, so the count under this heading is a finding in
# its own right rather than a leftover bucket.
UNATTACHED = "Not attached to any feature"

# The edge kind naming the feature a unit serves. Spelled here rather than imported from
# `loop_state`, which declares it too but which the layer contract puts above this module.
PARENT_CHILD = "parent-child"


def feature_of(ident: str, parents: Mapping[str, str], titles: Mapping[str, str]) -> str:
    """The title of the root feature *ident* serves, or ``""`` where none is reachable.

    The *root* rather than the immediate parent, so a unit two levels down is filed under the
    epic a reader recognises. Four endings share one answer because a reader can act on none
    of them: no parent edge, a chain leaving the map before a titled record, a titleless root,
    and a cycle. The second is reachable once an intermediate ancestor closes, the served
    graph being filtered to edges touching the drawn set. Meeting a seen id abandons the walk
    rather than taking the title where it stopped: a unit feeding a cycle is not in one.
    """
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
    """How old the document is, phrased for six metres and stamped for an auditor."""

    generated_at: str
    phrase: str
    state: State
    stale_after: str


@dataclass(frozen=True)
class Reading:
    """One section as the verdict left it: its state, its note, and its value or None."""

    name: str
    state: State
    note: str
    held: Any = None

    @property
    def rows(self) -> Sequence[Any]:
        """The section's items where it is a drawn list, else nothing."""
        return self.held if isinstance(self.held, list) else ()

    @property
    def fields(self) -> Mapping[str, Any]:
        """The section's keys where it is a drawn object, else nothing."""
        return self.held if isinstance(self.held, dict) else {}

    @property
    def drawn(self) -> bool:
        """True when the producer emitted this section and it conforms."""
        return self.state.key == RENDERABLE

    @property
    def dicts(self) -> list[Mapping[str, Any]]:
        """The section's items that are objects, which is what a region iterates."""
        return [row for row in self.rows if isinstance(row, dict)]


class Readings(dict[str, Reading]):
    """The verdict's readings, plus an absent one for a section it never named.

    A dict subclass rather than a lookup helper because every region indexes a section by
    name, and a key missing here is the one absence a *document* cannot produce - only an
    installed contract that dropped a property the code still reads can. That is an absence
    like any other, so it reads as one instead of raising. Nothing is inserted, so iterating
    this is still the verdict's own roster and never a second inventory.
    """

    def __missing__(self, name: str) -> Reading:
        """The reading a section this contract does not declare gets."""
        return Reading(name, BY_KEY[ABSENT], ABSENT_TEXT)


def readings(document: Mapping[str, Any], verdict: SnapshotVerdict) -> Readings:
    """Every section the verdict named, as ``name -> Reading``, what draws first.

    The verdict's inventory and never a second list: a section it did not name gets no entry,
    and one it named as absent carries :data:`ABSENT_TEXT` rather than an empty value. Every
    section it *did* name gets one, which is what lets a region index a name directly - a
    document whose required part failed withholds each of its sections rather than dropping
    it, so there is no arrangement of a snapshot that leaves a region without a reading.
    """
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
    """*value* as a float where it is a real number, else None. Booleans are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def bar(part: object, whole: object) -> Bar | None:
    """A bar for *part* of *whole*, or None where either term is absent or unmeasured.

    None on a zero whole too: a percentage of nothing is not a small bar, it is no ratio at
    all. The caller then prints the raw number, which is what the absence actually supports.
    """
    top, bottom = numeric(part), numeric(whole)
    if top is None or bottom is None or bottom <= 0:
        return None
    share = top / bottom * _FULL
    return Bar(min(share, _FULL), f"{share:.0f}%", share > _FULL)


def elapsed(seconds: float) -> str:
    """*seconds* as the coarsest phrase that still answers "is this screen frozen"."""
    if seconds < _MINUTE:
        return f"{int(seconds)}s"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)}m {int(seconds % _MINUTE)}s"
    return f"{int(seconds // _HOUR)}h {int(seconds % _HOUR // _MINUTE)}m"


def coarse(seconds: float) -> str:
    """*seconds* as the coarsest unit that is still true, spelled for a headline.

    ``6 DAYS``, never ``148h 52m``: a reader six metres back ranks by magnitude and cannot
    divide. Truncating rather than rounding keeps it honest - 6 days and 20 hours is still
    ``6 DAYS``, and the exact phrase :func:`elapsed` gives is drawn beneath it.
    """
    for bound, unit in _COARSE:
        count = int(seconds // bound)
        if count:
            return f"{count} {unit}" if count == 1 else f"{count} {unit}S"
    return f"{int(seconds)} SECONDS" if int(seconds) != 1 else "1 SECOND"


def day(stamp: object) -> str:
    """The UTC date of an RFC3339 *stamp*, or an empty string where it will not parse.

    A caller comparing two of these has to reject the empty one first: two unparseable
    stamps compare equal, and a row must not land in "today" because nobody could date it.
    """
    written = board_fields.instant(stamp) if isinstance(stamp, str) else None
    return "" if written is None else written.date().isoformat()


def duration(value: object) -> str:
    """*value* as an elapsed phrase, or :data:`UNKNOWN` where it is not a number."""
    seconds = numeric(value)
    return UNKNOWN if seconds is None else elapsed(seconds)


def since(stamp: object, now: datetime) -> float | None:
    """Seconds from the RFC3339 *stamp* to *now*, or None where it will not parse.

    The one datetime subtraction the page makes, and the reason it is one function: the
    wall-clock gate counts this shape per module, and a second site would be a second
    interval nobody proved was taken against an injected instant.
    """
    written = board_fields.instant(stamp) if isinstance(stamp, str) else None
    if written is None:
        return None
    return max(0.0, (now - written).total_seconds())


def age(document: Mapping[str, Any], now: datetime) -> Age:
    """The document's freshness, read once for the whole page.

    An unparseable stamp is :data:`STALE` rather than a blank: a viewer that cannot date the
    file it is drawing has no grounds to call it live.
    """
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
    """A value as the wall prints it, or :data:`UNKNOWN` where the producer gave none."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return UNKNOWN


def clip(value: object, limit: int) -> str:
    """*value* as text, truncated with a visible marker rather than by CSS overflow."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + _CLIP


def more(dropped: int, noun: str) -> str:
    """What a fixed-height region says about the items it did not draw, or nothing."""
    return f"+{dropped} more {noun}" if dropped > 0 else ""


def joined(held: Mapping[str, Any], keys: Sequence[str], limit: int = NOTE_MAX) -> str:
    """The named keys of *held* that are present, joined for one line."""
    parts = [str(held[key]) for key in keys if held.get(key) not in (None, "")]
    return clip(DOT.join(parts), limit) if parts else UNKNOWN


def cell(read: Reading, label: str, keys: Sequence[str]) -> Cell:
    """One cell from an object section, or the section's own state and note instead."""
    if not read.drawn:
        return Cell(label, read.note, read.state)
    return Cell(label, joined(read.fields, keys), read.state)
