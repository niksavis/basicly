"""The footer: whether the work is moving, whether the tree is green, and what it cost.

One region below the fold of the wall, and it reads five of the twelve sections - `backlog`
and `graph` on the left, then `gates`, `spend` and `health` as one strip, then `events` as
the only prose on the page, then :func:`inventory`.

:func:`inventory` is why the four question regions above are safe to write. It draws the
verdict's whole roster with a state on each name, so a section that no region reads still
reports itself, and :func:`legend` spells what those states mean - an absent one reads
:data:`board_wall.ABSENT_TEXT` and never a nought. Without the pair, a change of layout
could silently drop a section the schema declares.

A sibling of :mod:`basicly.board_regions` rather than part of it: the two share the
vocabulary in :mod:`basicly.board_wall` and neither reads the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .board_wall import (
    ABSENT,
    ABSENT_TEXT,
    BY_KEY,
    DOT,
    FAIL,
    RENDERABLE,
    UNKNOWN,
    WITHHELD,
    Cell,
    Panel,
    bar,
    clip,
    joined,
    more,
    number,
    numeric,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .board_wall import Reading

# How many event lines the ticker draws before it reports the rest.
EVENT_LINES = 3
NAME_MAX = 32
LINE_MAX = 180

# The gate strip's capacity, as the grid it reserves rather than as a maximum. The check count
# is the producer's own and it stood at 12 in the fixture this layout was built against and 36
# in the tree that drew it, so a strip that took the room its cells needed took it from the
# region below. Six rows is what the footer has for it; six columns is what fits *beside* it,
# and not what fits a name - measured at six widths, six columns hold the tree's longest check
# name (`projection-permissions`, 22 characters) only at 1920px, and no column count holds it
# at every width this layout claims. So the name is truncated by the template rather than
# wrapped: a wrapped name took a second line the row height had already allotted to the check
# below and was painted across it (basicly-uvpu6b). :data:`NAME_MAX` bounds what the model
# hands over, never what the column can show.
GATE_COLUMNS = 6
GATE_ROWS = 6
GATE_SLOTS = GATE_COLUMNS * GATE_ROWS

# The footer's other two open-ended populations. `health` draws one line per agent, and
# `by_priority` is keyed by the producer's own label vocabulary, which the schema declines to
# close - "a fixed set would silently drop a label" - so neither has a length the page can
# assume. Health reserves its grid for the same reason the gate strip does: four agents where
# the last snapshot had two would otherwise take the height off the region above.
HEALTH_COLUMNS = 2
HEALTH_ROWS = 2
HEALTH_SLOTS = HEALTH_COLUMNS * HEALTH_ROWS
PRIORITY_SLOTS = 8

_BACKLOG_KEYS = ("total", "active", "ready", "blocked", "in_progress", "closed")
_SPEND_KEYS = ("scope", "lifetime_usd", "largest_dispatch_usd", "input_tokens", "output_tokens")
_HEALTH_KEYS = ("runs", "score", "failure_rate", "drift")
_GATE_STATE = {"pass": RENDERABLE, "fail": FAIL, "not_run": ABSENT}
# What the gate strip's caption spells, and the word it spells each key as.
_GATE_CAPTION = {"mode": "mode", "recorded_at": "recorded"}


def backlog(reads: Mapping[str, Reading]) -> Panel:
    """The backlog counts, the closed bar, and the edge count that explains a blocked one.

    ``graph`` is read here rather than given a region because the schema says what it is for:
    edges are "the answer to why an item is not ready - the one question a count of blocked
    items raises and cannot settle", so the count belongs beside that question.
    """
    read, edges = reads["backlog"], reads["graph"]
    if not read.drawn:
        return Panel("backlog", read.state, read.note)
    held = read.fields
    closed = bar(held.get("closed"), held.get("total"))
    cells = [
        Cell(key.replace("_", " "), number(held.get(key)), bar=closed if key == "closed" else None)
        for key in _BACKLOG_KEYS
    ]
    held_edges = edges.fields.get("edges")
    counted = number(len(held_edges)) if isinstance(held_edges, list) else UNKNOWN
    cells.append(Cell("dep edges", counted if edges.drawn else edges.note, edges.state))
    return Panel("backlog", read.state, "", tuple(cells))


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


def _gates(read: Reading) -> Panel:
    """The gate strip: the mode and the stamp above it, then a glyph per check, capped.

    The mode and the stamp are the caption rather than two cells because the checks below are
    a reserved grid of one-line names, and a two-line cell among them would take a row the
    grid had allotted to a check. Neither carries a glyph: the strip's own title already holds
    the verdict, and a second badge saying the same thing below it is the duplication this
    render removed.
    """
    if not read.drawn:
        return Panel("gates", read.state, read.note)
    held = read.fields
    passed = held.get("passed")
    state = BY_KEY[RENDERABLE if passed else FAIL] if isinstance(passed, bool) else read.state
    # `gates` is an object carrying a `checks` array, not an array section, so the list comes
    # out of the fields: reading it as `Reading.rows` is how the edge count came back a zero.
    checks = held.get("checks")
    rows = [
        check for check in (checks if isinstance(checks, list) else []) if isinstance(check, dict)
    ]
    cells = tuple(
        Cell(
            clip(check.get("name", UNKNOWN), NAME_MAX),
            "",
            BY_KEY[_GATE_STATE.get(str(check.get("status")), ABSENT)],
        )
        for check in rows[:GATE_SLOTS]
    )
    caption = DOT.join(
        f"{word} {held[key]}" for key, word in _GATE_CAPTION.items() if held.get(key)
    )
    return Panel(
        "gates",
        state,
        "",
        cells,
        caption=clip(caption, LINE_MAX) if caption else UNKNOWN,
        more=more(len(rows) - GATE_SLOTS, "checks"),
        columns=GATE_COLUMNS,
        rows=GATE_ROWS,
    )


def _spend(read: Reading) -> Panel:
    """The spend strip. ``scope`` is drawn verbatim: machine-local is not a team total."""
    if not read.drawn:
        return Panel("spend", read.state, read.note)
    cells = tuple(
        Cell(
            key.replace("_", " "),
            str(read.fields.get(key, UNKNOWN)) if key == "scope" else number(read.fields.get(key)),
        )
        for key in _SPEND_KEYS
    )
    return Panel("spend", read.state, "", cells)


def _health(read: Reading) -> Panel:
    """The health strip, one line per agent, capped. The agent names the row, never its index."""
    if not read.drawn:
        return Panel("health", read.state, read.note)
    agents = read.dicts
    if not agents:
        return Panel("health", read.state, "no run in the producer's window")
    cells = tuple(
        Cell(
            clip(agent.get("agent", UNKNOWN), NAME_MAX),
            DOT.join(f"{key.replace('_', ' ')} {number(agent.get(key))}" for key in _HEALTH_KEYS),
        )
        for agent in agents[:HEALTH_SLOTS]
    )
    return Panel(
        "health",
        read.state,
        "",
        cells,
        more=more(len(agents) - HEALTH_SLOTS, "agents"),
        columns=HEALTH_COLUMNS,
        rows=HEALTH_ROWS,
    )


def strips(reads: Mapping[str, Reading]) -> tuple[Panel, ...]:
    """Gates, spend and health as one footer strip, in that order."""
    return (_gates(reads["gates"]), _spend(reads["spend"]), _health(reads["health"]))


def events(reads: Mapping[str, Reading]) -> tuple[tuple[str, ...], str]:
    """The last few events newest first, and how many older ones were not drawn.

    The only region that reads as prose, and the dropped count is returned beside the lines
    rather than appended to them: the ticker's row height is fixed at :data:`EVENT_LINES`, so
    a fourth line would be the content the marker exists to account for.
    """
    read = reads["events"]
    if not read.drawn:
        return (f"events {read.note}",), ""
    rows = read.dicts
    if not rows:
        return ("no event recorded",), ""
    lines = tuple(
        joined(row, ("at", "issue", "kind", "text"), LINE_MAX)
        for row in reversed(rows[-EVENT_LINES:])
    )
    return lines, more(len(rows) - EVENT_LINES, "events")


def inventory(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:
    """Every section the verdict named, glyphed, what draws first and what is absent last.

    This is the accounting that makes the four question regions safe to write: a section no
    region reads still reports itself here, so nothing the schema declares can be silently
    dropped by a change of layout. What each glyph stands for is :func:`legend`'s.
    """
    return tuple(Cell(read.name, "", read.state) for read in reads.values())


def legend() -> tuple[Cell, ...]:
    """What the roster's three glyphs mean, spelled once rather than twelve times.

    Twelve copies of :data:`board_wall.ABSENT_TEXT` do not fit the strip's fixed height at
    1920px, and a region that actually reads an absent section still prints the phrase in
    full - this only names what a roster glyph carries.
    """
    return (
        Cell("", "renders", BY_KEY[RENDERABLE]),
        Cell("", "withheld, and says why", BY_KEY[WITHHELD]),
        Cell("", ABSENT_TEXT, BY_KEY[ABSENT]),
    )
