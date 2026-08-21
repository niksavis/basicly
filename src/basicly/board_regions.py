"""One region per question, because the layout is the acceptance criterion.

The board answers four questions from across a room and each has a region of its own: the
**watch band** says whether anybody is waiting on a person and for how long, the **loop row**
says where the work is phase by phase and where it is now, **in flight** and **next up** say
what is running and what is next, and the **footer** says whether we are making progress,
whether the tree is green, and what it cost.

Every region is a **fixed-height row that truncates with a visible marker**, which is the
defect this module was written against: the render it replaces gave each schema key a box of
its own that its content overflowed, so all four answers sat below a scrollbar nobody on a
wall display can reach. A region therefore caps what it draws at a slot count, says
``+N more`` naming what it dropped, and clips a string with an ellipsis of its own rather
than letting CSS hide the end of it.

**A layout count is not a section count.** One region may read several sections - the footer
reads five - or none of the twelve. :func:`inventory` is what makes that safe: it draws the
verdict's whole roster, so a section no region reads still reports itself and nothing the
schema declares can be dropped by a change of layout.

The vocabulary, the honesty rules and the shapes are :mod:`basicly.board_wall`'s;
:mod:`basicly.board_render` draws what this module returns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .board_wall import (
    ABSENT,
    ABSENT_TEXT,
    BY_KEY,
    CALM,
    DOT,
    LIVE,
    NOTE_MAX,
    RENDERABLE,
    TITLE_MAX,
    UNKNOWN,
    WAITING,
    WITHHELD,
    Band,
    Card,
    Cell,
    Item,
    Listing,
    Phase,
    bar,
    cell,
    clip,
    duration,
    joined,
    more,
    number,
    numeric,
    since,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from .board_wall import Age, Reading

# The harness's own lifecycle, in order, and the order is the load-bearing part: the palette
# ships four hues and there are seven phases, so a phase is carried by its **position** in
# this row and by its label rather than by a colour that would have to repeat.
PHASES: tuple[str, ...] = (
    "intake",
    "classify",
    "decompose",
    "build",
    "verify",
    "validate",
    "ship",
)

# How many items a fixed-height region draws before it reports the rest. Slots rather than a
# maximum: the in-flight row keeps its shape at one lane and at six, so an empty frame reads
# as spare capacity instead of as a layout that moved when a number changed.
FLIGHT_SLOTS = 6
READY_SLOTS = 8
BAND_ASKS = 2
EVENT_LINES = 3

QUESTION_MAX = 70


def head(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:
    """The header strip: which tree, which run, and what the run is allowed to spend.

    The spend bar sits at the top rather than in the footer because "are we about to spend
    money we did not agree to" is the one number that has to be legible before anything else.
    """
    session = reads["session"]
    spent = session.fields.get("spent_tokens")
    return (
        cell(reads["repo"], "repo", ("name", "branch", "head")),
        cell(reads["session"], "run", ("root", "root_status", "grant_level")),
        cell(reads["generator"], "producer", ("tool", "version")),
        Cell(
            "tokens",
            number(spent) if session.drawn else session.note,
            session.state,
            bar(spent, session.fields.get("token_budget")),
        ),
    )


def _waited(ask: Mapping[str, Any], now: datetime) -> float | None:
    """How long *ask* has waited: its own figure where it gave one, else from its stamp."""
    given = numeric(ask.get("waiting_s"))
    return given if given is not None else since(ask.get("requested_at"), now)


def _ask_line(ask: Mapping[str, Any], now: datetime) -> str:
    """One pending ask: who is waiting, on what, for how long, and in whose words."""
    question = ask.get("question")
    asked = f' "{clip(question, QUESTION_MAX)}"' if question else ""
    named = joined(ask, ("issue", "kind", "subject"))
    return f"{named}{DOT}{duration(_waited(ask, now))}{asked}"


def band(reads: Mapping[str, Reading], drawn: Age, now: datetime) -> Band:
    """The watch band, which reads five ways and no more.

    Four of them are the ask verdict and exactly one holds: **withheld**, then **absent**,
    then **waiting**, then **calm**, in that precedence - a section that could not be read
    must never be reported as a quiet room. The fifth is the **stale** marker, appended to
    whichever of the four holds rather than replacing it, so a frozen screen says it is
    frozen and still shows the last values it knew.
    """
    read = reads["asks"]
    stale = (
        ""
        if drawn.state.key == LIVE
        else f"STALE \N{EM DASH} {drawn.phrase}, bound {drawn.stale_after}"
        f" \N{EM DASH} the values below are the last known"
    )
    if not read.drawn:
        headline = "ASKS WITHHELD" if read.state.key == WITHHELD else "ASKS NOT EMITTED"
        return Band(read.state, headline, (clip(read.note, NOTE_MAX * 2),), stale)
    asks = sorted(read.dicts, key=lambda ask: _waited(ask, now) or -1.0, reverse=True)
    if not asks:
        calm = ("no checkpoint and no decision is pending",)
        return Band(BY_KEY[CALM], "NOTHING IS WAITING", calm, stale)
    lines = [_ask_line(ask, now) for ask in asks[:BAND_ASKS]]
    dropped = more(len(asks) - BAND_ASKS, "waiting")
    return Band(
        BY_KEY[WAITING],
        f"{len(asks)} WAITING ON A PERSON",
        tuple(lines + ([dropped] if dropped else [])),
        stale,
    )


def _phase_of(row: Mapping[str, Any]) -> str:
    """The phase *row* declares, or an empty string where it declares none."""
    phase = row.get("phase")
    return phase if isinstance(phase, str) and phase else ""


def _loop_note(units: Reading, lanes: Reading, counts: Mapping[str, int], unphased: int) -> str:
    """Why the row is not fully populated, in the producer's terms rather than as a zero."""
    parts = []
    if not units.drawn:
        parts.append(f"units {units.note}")
    elif not counts:
        parts.append(f"phase {ABSENT_TEXT} on any of the {len(units.dicts)} units")
    elif unphased:
        parts.append(f"{unphased} of {len(units.dicts)} units carry no phase")
    if not lanes.drawn:
        parts.append(f"current position: lanes {lanes.note}")
    return DOT.join(parts)


def loop(reads: Mapping[str, Reading]) -> tuple[tuple[Phase, ...], str]:
    """The loop row: a count per phase, and a mark where work currently sits.

    The counts are the units section's and the mark is the lanes section's, because the
    schema separates those two facts on purpose - ``units.phase`` says where a unit stopped,
    ``lanes.phase`` says where one is running. Either being absent costs its own half of the
    row and never the row, and an absent count is None rather than a nought.
    """
    units, lanes = reads["units"], reads["lanes"]
    counts: dict[str, int] = {}
    unphased = 0
    for row in units.dicts:
        name = _phase_of(row)
        if name:
            counts[name] = counts.get(name, 0) + 1
        else:
            unphased += 1
    here = {_phase_of(row) for row in lanes.dicts} - {""}
    measured = units.drawn and bool(counts)
    row = tuple(
        Phase(name, counts.get(name, 0) if measured else None, name in here)
        for name in list(PHASES) + sorted(set(counts) - set(PHASES))
    )
    return row, _loop_note(units, lanes, counts, unphased)


def _lane_cells(lane: Mapping[str, Any]) -> tuple[Cell, ...]:
    """The six figures a lane card carries, each with its own absence.

    The context bar is the rule's sharpest case: the two terms travel together or not at all,
    so a producer knowing only the occupancy draws the number and no bar.
    """
    used = lane.get("context_used")
    attempt = lane.get("rework_attempt")
    rework = (
        UNKNOWN
        if attempt is None
        else f"{number(attempt)} of {number(lane.get('rework_allowance'))}"
    )
    return (
        Cell("agent", joined(lane, ("agent", "model"))),
        Cell("running", duration(lane.get("elapsed_s"))),
        Cell("tokens", number(lane.get("tokens"))),
        Cell("cost usd", number(lane.get("cost_usd"))),
        Cell("context", number(used), bar=bar(used, lane.get("context_window"))),
        Cell("rework", rework),
    )


def flight(reads: Mapping[str, Reading]) -> tuple[tuple[Card, ...], str, str]:
    """The in-flight cards, what was dropped, and why there is nothing to draw."""
    read = reads["lanes"]
    lanes = read.dicts if read.drawn else []
    cards = [
        Card(
            clip(lane.get("id", UNKNOWN), TITLE_MAX),
            _phase_of(lane) or UNKNOWN,
            BY_KEY[LIVE] if lane.get("live") else BY_KEY[ABSENT],
            clip(lane.get("note") or lane.get("status") or "", NOTE_MAX),
            _lane_cells(lane),
        )
        for lane in lanes[:FLIGHT_SLOTS]
    ]
    cards += [Card("", "", BY_KEY[ABSENT], "")] * (FLIGHT_SLOTS - len(cards))
    note = read.note if not read.drawn else "" if lanes else "no lane is running"
    return tuple(cards), more(len(lanes) - FLIGHT_SLOTS, "lanes"), note


def _rank(unit: Mapping[str, Any]) -> tuple[str, str]:
    """The ready set's order: priority label first, then id, so it is stable to read."""
    return str(unit.get("priority") or "\N{TILDE}"), str(unit.get("id") or "")


def next_up(reads: Mapping[str, Reading]) -> Listing:
    """The ready set, ranked, with priority, id and title on each row.

    Three absences are distinguished and not one of them is a zero: the section not emitted,
    the section emitted with no ``ready`` flag on any row, and a flagged set with nothing in
    it. The middle one is the case a count would have reported as "0 ready".
    """
    read = reads["units"]
    if not read.drawn:
        return Listing(read.state, note=read.note)
    units = read.dicts
    flagged = [unit for unit in units if isinstance(unit.get("ready"), bool)]
    if not flagged:
        return Listing(BY_KEY[ABSENT], note=f"ready {ABSENT_TEXT} on any of the {len(units)} units")
    ready = sorted((unit for unit in flagged if unit["ready"]), key=_rank)
    rows = tuple(
        Item(
            str(unit.get("priority") or UNKNOWN),
            clip(unit.get("id", UNKNOWN), TITLE_MAX),
            clip(unit.get("title") or UNKNOWN, TITLE_MAX),
        )
        for unit in ready[:READY_SLOTS]
    )
    note = "" if ready else f"nothing is ready of the {len(flagged)} units emitted"
    return Listing(BY_KEY[RENDERABLE], rows, more(len(ready) - READY_SLOTS, "ready"), note)
