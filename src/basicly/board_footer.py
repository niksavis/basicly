"""The accounting: whether the tree is green, whether the work is moving, and what it cost.

Six of the twelve sections, each reduced to what a wall can rank. **A green state costs one
token; an exception expands** - :func:`gates` is the sharpest case, where a passing set reads
`GREEN` and only a failing or unrun check spells its own name, which is why the check-name
overlap it replaces cannot recur: there is no grid of names left to collide.

:func:`inventory` is why the regions above are safe to write. It names every section that did
not draw, with the word for why, so a section no region reads still reports itself and a
change of layout cannot silently drop one the schema declares. Only the exceptions: naming the
twelve that drew spent a standing row saying twelve things are normal.

A sibling of :mod:`basicly.board_regions`: the two share :mod:`basicly.board_wall`'s
vocabulary and neither reads the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .board_wall import (
    ABSENT,
    BY_KEY,
    DOT,
    FAIL,
    RENDERABLE,
    UNKNOWN,
    Cell,
    bar,
    clip,
    day,
    joined,
    more,
    number,
    numeric,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .board_wall import Reading

# One line. A wall answers "is anything happening" and the newest event answers it; the two
# behind it were a scrolling log at six metres, and the dropped count still says they exist.
EVENT_LINES = 1
NAME_MAX = 32
LINE_MAX = 180

# How many exception names the gate token spells before it stops naming them. Not a layout
# capacity - there is no grid left to overflow - but a bound on the one arrangement that would
# put all 36 names back on the wall: a tree where everything failed.
GATE_NAMES = 4

# The two open-ended populations that remain: one cell per agent, and `by_priority` keyed by the
# producer's own label vocabulary, which the schema declines to close - "a fixed set would
# silently drop a label". Neither has a length the page can assume, so each says what it dropped.
HEALTH_SLOTS = 4
PRIORITY_SLOTS = 8

# The event kind a producer writes a lifecycle change under, and the status counted. Both are
# the producer's vocabulary: `events[].kind` is an open string, so a producer that records no
# status change supplies no throughput and the figure is absent rather than nought.
STATUS_KIND = "status"
CLOSED_STATUS = "closed"

_BACKLOG_KEYS = ("total", "active", "ready", "blocked", "in_progress", "closed")
# Each spend figure and its unit: four bare numbers in a row is a quantity nobody can name.
# `scope` is not here, because it is drawn first and verbatim.
_SPEND_UNITS = {
    "lifetime_usd": "usd",
    "largest_dispatch_usd": "usd largest",
    "input_tokens": "in",
    "output_tokens": "out",
}
_HEALTH_KEYS = ("runs", "score", "failure_rate", "drift")
# The producer's word for a check result, per state. One direction only: the token names the
# exceptions, so nothing looks a passing check up.
_STATUS_WORD = {FAIL: "fail", ABSENT: "not_run"}
# What the gate token's caption spells, and the word it spells each key as.
_GATE_CAPTION = {"mode": "mode", "recorded_at": "recorded"}

# board_wall.ABSENT_TEXT names the schema's vocabulary; this module's own reading stays
# reachable at board-snapshot.json, the sidecar `basicly board` writes beside the page.
_NOT_IN_SNAPSHOT = "not in this snapshot"


def _say(read: Reading) -> str:
    """The word a cell shows for a reading that did not draw, in a reader's vocabulary."""
    return _NOT_IN_SNAPSHOT if read.state.key == ABSENT else read.note


def backlog(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:
    """The backlog counts on one line, the closed bar, and the edge count beside them.

    ``graph`` is read here rather than given a region because the schema says what it is for:
    edges are "the answer to why an item is not ready - the one question a count of blocked
    items raises and cannot settle". A section that could not be read is one cell carrying its
    own note, the shape :func:`spend` and :func:`health` also take.
    """
    read, edges = reads["backlog"], reads["graph"]
    if not read.drawn:
        return (Cell("backlog", _say(read), read.state),)
    held = read.fields
    closed = bar(held.get("closed"), held.get("total"))
    cells = [
        Cell(key.replace("_", " "), number(held.get(key)), bar=closed if key == "closed" else None)
        for key in _BACKLOG_KEYS
    ]
    held_edges = edges.fields.get("edges")
    counted = number(len(held_edges)) if isinstance(held_edges, list) else UNKNOWN
    cells.append(Cell("dep edges", counted if edges.drawn else _say(edges), edges.state))
    return tuple(cells)


def priorities(reads: Mapping[str, Reading]) -> tuple[tuple[Cell, ...], str]:
    """The per-priority histogram, sorted by label, and how many labels it did not draw.

    Both terms come from the same map, so the ratio is one the producer actually measured; a
    label whose count is not a number draws the raw value and no bar. The vocabulary is the
    producer's and the schema keeps it open, so the row is capped at :data:`PRIORITY_SLOTS`
    and reports the rest rather than running off the end of the column.
    """
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
    """The names of the checks recorded at *status*, in the producer's own order."""
    return [
        clip(check.get("name", UNKNOWN), NAME_MAX)
        for check in rows
        if str(check.get("status")) == status
    ]


def _verdict(rows: Sequence[Mapping[str, Any]], passed: object) -> tuple[str, str]:
    """The whole check set as one token, and the state key that token is drawn in.

    A failing check names itself, an unrun one names itself, and a set with neither reads
    `GREEN`. ``passed`` wins over the rows: a producer that says it failed while emitting no
    failing row is reporting something these names cannot show, and `FAILING` with no name is
    the honest reading of that.
    """
    for status, word in ((FAIL, "FAILING"), (ABSENT, "NOT RUN")):
        named = _named(rows, _STATUS_WORD[status])
        if named:
            dropped = more(len(named) - GATE_NAMES, "checks")
            spelled = DOT.join([*named[:GATE_NAMES], *([dropped] if dropped else [])])
            return f"{len(named)} {word}: {spelled}", status
    return ("GREEN", RENDERABLE) if passed is not False else ("FAILING", FAIL)


def gates(reads: Mapping[str, Reading]) -> tuple[Cell, str]:
    """The whole gate set as one token, and the run that produced it beneath.

    The caption is the mode and the stamp, because a token saying `GREEN` is worth nothing
    without which suite said so and when. `gates` is an object carrying a `checks` array, not
    an array section, so the list comes out of the fields: reading it as
    :attr:`board_wall.Reading.rows` is how the edge count came back a zero.
    """
    read = reads["gates"]
    if not read.drawn:
        return Cell("gates", _say(read), read.state), ""
    held = read.fields
    checks = held.get("checks")
    rows = [
        check for check in (checks if isinstance(checks, list) else []) if isinstance(check, dict)
    ]
    token, state = _verdict(rows, held.get("passed"))
    caption = DOT.join(
        f"{word} {held[key]}" for key, word in _GATE_CAPTION.items() if held.get(key)
    )
    return Cell("gates", token, BY_KEY[state]), clip(caption, LINE_MAX) if caption else UNKNOWN


def compact(value: object) -> str:
    """A large count as a reader compares it: 616,122,594 becomes 616M.

    Nine digits are what pushed the spend line past its bound and clipped the figure beside
    it; nobody compares token counts digit by digit.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return number(value)
    for bound, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if abs(value) >= bound:
            return f"{value / bound:.1f}".rstrip("0").rstrip(".") + suffix
    return number(value)


def spend(reads: Mapping[str, Reading]) -> Cell:
    """What this machine has been billed, as one status-bar cell.

    ``scope`` is drawn verbatim and first: machine-local is not a team total, and a currency
    figure with no scope beside it reads as one.
    """
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
    """One cell per agent, capped, and what the cap dropped. The agent names its own cell."""
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
    """How many units the producer recorded closed on *today*, or that it cannot say.

    The one figure that answers "is the factory improving" rather than "how big is the pile".
    Distinct records, not rows: a unit closed twice is one unit closed.

    **Absent, never nought.** A producer that records no :data:`STATUS_KIND` row at all has not
    measured this and the cell says so; one that records them and closed nothing today is a
    measured zero. An undateable row is in no day, so it cannot fall into this one.
    """
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


def events(reads: Mapping[str, Reading]) -> tuple[tuple[str, ...], str]:
    """The newest events, and how many older ones were not drawn.

    The only region that reads as prose, and the dropped count is returned beside the lines
    rather than appended to them: the ticker's row height is fixed at :data:`EVENT_LINES`, so
    one more line would be the content the marker exists to account for.
    """
    read = reads["events"]
    if not read.drawn:
        return (f"events {_say(read)}",), ""
    rows = read.dicts
    if not rows:
        return ("no event recorded",), ""
    lines = tuple(
        joined(row, ("at", "issue", "kind", "text"), LINE_MAX)
        for row in reversed(rows[-EVENT_LINES:])
    )
    return lines, more(len(rows) - EVENT_LINES, "events")


def inventory(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:
    """Only the sections that did **not** draw, each with the word for why.

    The accounting that makes the four question regions safe to write is unchanged: a
    section no region reads still reports itself here, so nothing the schema declares can be
    silently dropped by a change of layout. What changed is which half is spoken. Naming all
    twelve spent a standing row of an operator's dashboard saying that twelve things are
    normal, and a mark that is almost always present carries no information. The exceptions
    are the half worth a reader's attention, and an empty tuple is the statement that there
    are none. A withheld section spells `_say`'s note, not the bare state name.
    """
    return tuple(
        Cell(read.name, _say(read), read.state)
        for read in reads.values()
        if read.state.key != RENDERABLE
    )
