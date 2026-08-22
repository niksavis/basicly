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

from collections import Counter
from typing import TYPE_CHECKING, Any

from .board_wall import (
    ABSENT,
    ABSENT_TEXT,
    BY_KEY,
    CALM,
    DOT,
    LIVE,
    NOTE_MAX,
    PARENT_CHILD,
    RENDERABLE,
    TITLE_MAX,
    UNATTACHED,
    UNKNOWN,
    WAITING,
    WITHHELD,
    Band,
    Card,
    Cell,
    Group,
    Item,
    Listing,
    Phase,
    bar,
    cell,
    clip,
    coarse,
    duration,
    feature_of,
    joined,
    more,
    number,
    numeric,
    since,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
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

# How many items a capped region draws before it reports the rest. The running row no longer
# reserves empty frames: a dashed placeholder announcing nothing three times is 40% of a wall
# spent on the state that costs one token, so an empty row collapses to a line and the ready
# list takes the width. The cap survives because six live lanes still have to fit.
FLIGHT_SLOTS = 6
READY_SLOTS = 8

# What the ready list draws once the running row has collapsed and handed it the page, and how
# long a title may be there. The eight rows and 62-character titles of the narrow column leave
# two thirds of a 1080px screen blank and still truncate every line.
#
# **Both figures are the shortest wall this layout claims, not the roomiest.** Measured off the
# rendered page: 1440x900 gives the reclaimed region 373px of content box, because the status
# bar, the backlog line and the section roster each take a second line at that width, so 14
# rows of 24.1px under an 18.2px heading is what fits - against 26 at 1920x1080. A count taken
# at the roomiest width is the defect this repository already paid for once: the gate-name
# overlap reproduced at 1200 through 1800 and was absent at 1920, which is why it passed review.
READY_SLOTS_WIDE = 14
READY_TITLE_WIDE = 110

# One ask on the band, not two. The age is the headline now, and an age belongs to exactly one
# ask - the one that has waited longest. A second detail line under a 44px headline is what
# pushes the alarm past the height the design gives it; the dropped count names the rest.
BAND_ASKS = 1

QUESTION_MAX = 70


def head(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:
    """The status bar's tree half: which checkout, and what the run is allowed to spend.

    Two cells, not four. The grant is one cell rather than a name beside a token count
    because the bar already carries the share and the raw figure is a debugging view; the
    producer's name and version moved beside the freshness sentence, which is the sentence
    it is the producer *of*.
    """
    session = reads["session"]
    spent = session.fields.get("spent_tokens")
    return (
        cell(reads["repo"], "repo", ("name", "branch", "head")),
        Cell(
            "run",
            joined(session.fields, ("root", "root_status", "grant_level"))
            if session.drawn
            else session.note,
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
    """The alarm, which reads five ways and no more.

    Four of them are the ask verdict and exactly one holds: **withheld**, then **absent**,
    then **waiting**, then **calm**, in that precedence - a section that could not be read
    must never be reported as a quiet room. The fifth is the **stale** marker, appended to
    whichever of the four holds rather than replacing it, so a frozen screen says it is
    frozen and still shows the last values it knew.

    **While anybody is waiting the headline is the age**, in the coarsest unit that is still
    true, because that is what a wall ranks by; the id and the kind go beneath it and the
    exact elapsed phrase goes with them. An ask nobody could date keeps the alarm and says so
    rather than borrowing a number.
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
        return Band(read.state, headline, "", (clip(read.note, NOTE_MAX * 2),), stale)
    asks = sorted(read.dicts, key=lambda ask: _waited(ask, now) or -1.0, reverse=True)
    if not asks:
        calm = ("no checkpoint and no decision is pending",)
        return Band(BY_KEY[CALM], "NOTHING IS WAITING", "", calm, stale)
    waited = _waited(asks[0], now)
    lines = [_ask_line(ask, now) for ask in asks[:BAND_ASKS]]
    dropped = more(len(asks) - BAND_ASKS, "waiting")
    return Band(
        BY_KEY[WAITING],
        coarse(waited) if waited is not None else "WAITING",
        f"{len(asks)} waiting on a person",
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
    # The whole this row is a share *of* is the phased population and never the units section's
    # length: an unphased unit is in no phase, so counting it would make every bar short of its
    # own row by the same wrong amount. Both terms come from the one map, so `bar` refuses the
    # ratio exactly when the count is unmeasured.
    population = sum(counts.values())
    row = tuple(
        _phase(name, counts.get(name, 0) if measured else None, name in here, population)
        for name in list(PHASES) + sorted(set(counts) - set(PHASES))
    )
    return row, _loop_note(units, lanes, counts, unphased)


def _phase(name: str, count: int | None, here: bool, population: int) -> Phase:
    """One phase box: its count, its mark, and its share of the phased population."""
    return Phase(name, count, here, bar(count, population))


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
    """The running cards, what was dropped, and the one line that replaces them.

    **No empty slots.** The row used to reserve :data:`FLIGHT_SLOTS` dashed frames so its
    shape held at one lane and at six; on this repository's own wall that is 40% of the
    screen announcing nothing three times, while the ready list beside it truncated every
    title. A green state costs one token, so no lane means no card and the caller collapses
    the row to its note. The cap still holds because six live lanes still have to fit.
    """
    read = reads["lanes"]
    lanes = read.dicts if read.drawn else []
    cards = tuple(
        Card(
            clip(lane.get("id", UNKNOWN), TITLE_MAX),
            _phase_of(lane) or UNKNOWN,
            BY_KEY[LIVE] if lane.get("live") else BY_KEY[ABSENT],
            clip(lane.get("note") or lane.get("status") or "", NOTE_MAX),
            _lane_cells(lane),
        )
        for lane in lanes[:FLIGHT_SLOTS]
    )
    note = read.note if not read.drawn else "" if lanes else "no lane is dispatched"
    return cards, more(len(lanes) - FLIGHT_SLOTS, "lanes"), note


def _rank(unit: Mapping[str, Any]) -> tuple[str, str]:
    """The ready set's order: priority label first, then id, so it is stable to read."""
    return str(unit.get("priority") or "\N{TILDE}"), str(unit.get("id") or "")


def _feature_names(
    reads: Mapping[str, Reading], units: Sequence[Mapping[str, Any]], ready: Sequence[Any]
) -> list[str]:
    """The root feature each *ready* unit serves, in the order they rank.

    Edges and titles both come off the document this tick already carries, so naming a row's
    feature costs no read of its own. A producer may omit ``graph``: that leaves no parent
    edges, folds every row into the unattached group, and still draws the wall.
    """
    read = reads.get("graph")
    edges = read.held.get("edges", ()) if read is not None and read.drawn else ()
    parents = {
        str(edge["from"]): str(edge["to"])
        for edge in edges
        if edge.get("kind") == PARENT_CHILD and edge.get("from") and edge.get("to")
    }
    titles = {str(u["id"]): str(u["title"]) for u in units if u.get("id") and u.get("title")}
    return [feature_of(str(u.get("id", UNKNOWN)), parents, titles) or UNATTACHED for u in ready]


def grouped(rows: Sequence[Item], names: Sequence[str]) -> tuple[Group, ...]:
    """*rows* under one heading per feature, each counted over the whole ready set.

    *names* is the feature of every ready unit in rank order and *rows* is the leading slice
    the region has the height to draw, so the two zip and a count outruns its rows on purpose.
    Counting the drawn slice would have put 6 on the unattached heading where the document
    holds 41; the region's own ``more`` reconciles the pair. Group order follows its best row,
    so the ranking the wall already computed decides the page, and the unattached group sorts
    last because a row belonging to no feature is a filing gap rather than urgent work.
    """
    totals = Counter(names)
    order: list[str] = []
    held: dict[str, list[Item]] = {}
    for row, name in zip(rows, names, strict=False):
        if name not in held:
            held[name] = []
            order.append(name)
        held[name].append(row)
    order.sort(key=lambda name: name == UNATTACHED)
    return tuple(Group(name, str(totals[name]), tuple(held[name])) for name in order)


def next_up(reads: Mapping[str, Reading], *, wide: bool = False) -> Listing:
    """The ready set, ranked, with priority, id and title on each row.

    Three absences are distinguished and not one of them is a zero: the section not emitted,
    the section emitted with no ``ready`` flag on any row, and a flagged set with nothing in
    it. The middle one is the case a count would have reported as "0 ready".

    *wide* is the shape the list takes when no lane is dispatched and the running row gave it
    the width: more rows, and a title bound that fits them. Two shapes rather than one because
    a cap is a promise about a rendered width, and the list has two.
    """
    read = reads["units"]
    if not read.drawn:
        return Listing(read.state, note=read.note)
    units = read.dicts
    flagged = [unit for unit in units if isinstance(unit.get("ready"), bool)]
    if not flagged:
        return Listing(BY_KEY[ABSENT], note=f"ready {ABSENT_TEXT} on any of the {len(units)} units")
    slots = READY_SLOTS_WIDE if wide else READY_SLOTS
    bound = READY_TITLE_WIDE if wide else TITLE_MAX
    ready = sorted((unit for unit in flagged if unit["ready"]), key=_rank)
    names = _feature_names(reads, units, ready)
    # A heading spends a slot, a slot promising rendered height: six over fourteen rows ran
    # the region 137px past its box at 1440x900. The floor covers every top row being its own
    # feature, and half the slots carry at most half a slot of heading, so it cannot overrun.
    slots = max(slots // 2, slots - len(set(names[:slots])))
    rows = tuple(
        Item(
            str(unit.get("priority") or UNKNOWN),
            clip(unit.get("id", UNKNOWN), TITLE_MAX),
            clip(unit.get("title") or UNKNOWN, bound),
        )
        for unit in ready[:slots]
    )
    note = "" if ready else f"nothing is ready of the {len(flagged)} units emitted"
    groups = grouped(rows, names)
    return Listing(BY_KEY[RENDERABLE], rows, more(len(ready) - slots, "ready"), note, groups)
