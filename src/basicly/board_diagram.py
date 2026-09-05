"""The factory loop as a drawn diagram, because seven counted boxes are not a workflow.

The owner's report (basicly-6c97zx) was that the page carries *"no actual diagramatic
visualization of the loop with artifacts and important information about the factory loop
and workflow"*. A histogram says how many units are at a phase. It cannot say what the
phases are, which transition a human gates, what each one produces, or which edge will fail
next - and those are the questions a person watching a factory asks.

**A model, never markup.** Everything here is geometry and text; the template emits the SVG
elements. That is a security boundary and not a style: a producer's `lanes[].agent` reaches
the page through Jinja's autoescape like every other string, and an SVG assembled here as
one string would have to be marked safe, which is the one door :mod:`basicly.board_render`'s
own docstring exists to keep shut.

**Inline SVG rather than Mermaid.** `tests/test_board_render.py` asserts the page carries no
``<script``, no ``<link`` and no ``src=``; Mermaid is a runtime renderer, so it is refused by
a gate that already exists. Drawn shapes also retire the tofu: a mark is a `path` and never a
codepoint, so it does not depend on a wall display's font coverage.

**`intake` is the hopper and not a station.** It is the derivation's `otherwise` rung, so its
count is the untouched backlog rather than a stage anything is moving through - the same
population error :mod:`basicly.board_loop` was written against.

**Two gaps are stated rather than guessed.** No artifact field exists anywhere on
``harness-board/v1``, so an edge *names* what it produces and may never colour it approved or
pending. And an ask is pinned to a station by its ``subject`` matching a phase name, which is
true of this producer and is a heuristic by contract; an ask that matches nothing appears in
the watch band alone and is counted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .board_loop import PHASES, beat, moved_within, phase_of
from .board_wall import DOT, clip, elapsed, joined, number, numeric, since

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_wall import Reading

# The drawing surface, in its own units. Every figure below is in these, so the page scales
# the whole diagram by setting one width and the aspect ratio holds.
VIEW_W = 1200.0
# Wide and short on purpose. The surface scales to the page's width, so the aspect ratio is
# what the region costs the wall: at 4.2:1 a 1920px page spends 455px on the diagram, and the
# 3:1 first draft spent 640 and pushed the ready list off the screen. `.flow`'s `max-height`
# is the second half of that bound, for a width this ratio was not chosen against.
VIEW_H = 286.0

# The serpentine: four columns, two rows, read left-to-right then right-to-left. A single row
# of eight would give each station 150 units and truncate every label; the fold buys 280. It
# also draws the loop as a loop, which a pipeline of eight boxes does not.
COLUMNS = (150.0, 430.0, 710.0, 990.0)
ROW_TOP = 66.0
ROW_BOTTOM = 196.0
BOX_W = 210.0
BOX_H = 60.0

# The stations, in loop order, at their fixed places. `intake` is absent on purpose - it is
# the hopper - and `done` is the sink, which is a terminal state rather than a phase.
STATIONS: tuple[str, ...] = PHASES[1:]
HOPPER = PHASES[0]
SINK = "done"

# Every node the drawing places, in order, so an edge is `CHAIN[i] -> CHAIN[i + 1]` and the
# seventh edge - `ship -> done`, which carries the `release-record` - cannot be lost by
# iterating the stations instead.
CHAIN: tuple[str, ...] = (HOPPER, *STATIONS, SINK)

# What each transition produces, and the list is the shipped schema directory's own: every
# name here is a file under `.basicly/core/schemas/`, and `tests/test_board_diagram.py` pins
# the pair so a new artifact cannot desynchronise the diagram from the engine.
ARTIFACTS: Mapping[str, str] = {
    "classify": "classification + change-shape",
    "decompose": "implementation-plan",
    "build": "change-summary",
    "verify": "verification-evidence",
    "validate": "validation-transcript",
    "ship": "release-record",
}

# The three transitions a person gates (`config.CHECKPOINTS`, architecture section 23.3). A
# checkpoint gates *leaving* the phase it names, so it is drawn on the outgoing edge.
CHECKPOINTS: frozenset[str] = frozenset({"classify", "decompose", "ship"})

# The one merge in the whole loop: children land into their parent's verify. It is also where
# the landing gate binds, which is why the gate verdict is drawn on this edge and no other.
MERGE_FROM = "build"

# How many lane marks a station draws before it says how many it dropped. Three dots at this
# scale are legible from six metres; a fourth is a smudge. The same three bound the names
# written under the box, because a fourth line reaches the row below it - the geometry, not a
# taste: `check_render_overflow.py` reported the collision at four.
LANE_MARKS = 3

LABEL_MAX = 30
AGENT_MAX = 28


@dataclass(frozen=True)
class Lane:
    """One agent inside a station: who it is, whether it moved, and whether it is wedged.

    `moved` and `stuck` are not each other's negation. A lane that has not moved this beat is
    ordinarily just mid-step; `stuck` is the producer saying so in `state`. Drawing one mark
    for both would report every working lane as wedged between beats.
    """

    label: str
    moved: bool
    stuck: bool


@dataclass(frozen=True)
class Station:
    """One phase as a drawn box: where it sits, what rests there, and who is inside it."""

    name: str
    x: float
    y: float
    count: int | None
    lanes: tuple[Lane, ...]
    crew: tuple[str, ...]
    dropped: int
    waiting: str


@dataclass(frozen=True)
class Flow:
    """One transition as a drawn edge: its path, what it produces, and its two marks."""

    frm: str
    to: str
    path: str
    artifact: str
    checkpoint: bool
    merge: bool
    verdict: str
    label_x: float
    label_y: float


@dataclass(frozen=True)
class Terminal:
    """The hopper that feeds the loop, or the sink it drains to."""

    name: str
    x: float
    y: float
    count: int | None
    detail: str


@dataclass(frozen=True)
class Diagram:
    """The whole drawing: two terminals, six stations, seven edges, and what it could not say."""

    hopper: Terminal
    sink: Terminal
    stations: tuple[Station, ...]
    flows: tuple[Flow, ...]
    width: float
    height: float
    note: str


def _crew(held: Sequence[Lane]) -> tuple[str, ...]:
    """The names written under a station, bounded, with the rest counted on the last line."""
    if len(held) <= LANE_MARKS:
        return tuple(lane.label for lane in held)
    named = [lane.label for lane in held[: LANE_MARKS - 1]]
    return (*named, f"+{len(held) - LANE_MARKS + 1} more")


def _whole(value: object) -> int | None:
    """*value* as a whole count, or None - the sink draws a tally, never `770.0`."""
    held = numeric(value)
    return None if held is None else int(held)


def _place(index: int) -> tuple[float, float]:
    """Where station *index* sits on the serpentine, top row left to right then back."""
    if index < len(COLUMNS):
        return COLUMNS[index], ROW_TOP
    return COLUMNS[len(COLUMNS) * 2 - 1 - index], ROW_BOTTOM


def _counts(units: Reading, dispatched: frozenset[str]) -> dict[str, int]:
    """Units **resting** at each phase: at it, with no agent inside.

    The two channels are disjoint on purpose - the record asks that "a lane an agent is
    inside SHALL be distinguishable from a unit merely resting at that phase", and a count
    that included the lanes would put the same unit in the digit and in a dot beside it. A
    lane's id need not appear in `units[]` at all, so this subtracts by id rather than by
    arithmetic on the two lengths.
    """
    counts: dict[str, int] = {}
    for row in units.dicts:
        name = phase_of(row)
        if name and str(row.get("id") or "") not in dispatched:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _lane_label(lane: Mapping[str, Any]) -> str:
    """Who is inside, in the producer's own words: the agent, then the model behind it."""
    return clip(joined(lane, ("agent", "model")) or str(lane.get("id") or "lane"), AGENT_MAX)


# What the producer calls a lane nobody is moving. Read from `lanes[].state` rather than
# inferred from a quiet beat, because a lane between steps is quiet and is not wedged.
STUCK_STATES = frozenset({"refused", "parked", "waits-to-land"})


def _lanes(lanes: Reading, now: datetime, window: float) -> dict[str, list[Lane]]:
    """The lanes an agent is inside, keyed by the phase each one reports."""
    held: dict[str, list[Lane]] = {}
    for row in lanes.dicts:
        name = phase_of(row)
        if name:
            held.setdefault(name, []).append(
                Lane(
                    _lane_label(row),
                    moved_within(row, now, window),
                    str(row.get("state") or "") in STUCK_STATES,
                )
            )
    return held


def _waits(asks: Reading, now: datetime) -> tuple[dict[str, str], int]:
    """How long a person has blocked each station, and how many asks pinned to none.

    The pin is `subject` matching a phase name. It is this producer's habit rather than a
    contract, so an ask that matches nothing is counted and reported instead of being
    attached to whichever station happened to be nearest.
    """
    held: dict[str, str] = {}
    unpinned = 0
    for ask in asks.dicts:
        name = str(ask.get("subject") or "")
        if name not in STATIONS:
            unpinned += 1
            continue
        waited = numeric(ask.get("waiting_s"))
        if waited is None:
            waited = since(ask.get("requested_at"), now)
        # `elapsed` and not `coarse`: the band shouts its headline in capitals because it is
        # the page's one alarm, and a second region spelling an age that way makes two.
        held[name] = elapsed(waited) if waited is not None else "waiting"
    return held, unpinned


def _verdict(gates: Reading) -> str:
    """What the landing gate will do next, in a word, or "" where nobody has said.

    Drawn on the merge edge alone, because that is where the gate binds - a red gate is the
    next thing that will fail, and painting it on seven edges would say it seven times.
    """
    if not gates.drawn:
        return ""
    passed = gates.fields.get("passed")
    if passed is True:
        return "green"
    if passed is not False:
        return ""
    checks = gates.fields.get("checks")
    named = [
        str(check.get("name"))
        for check in (checks if isinstance(checks, list) else [])
        if isinstance(check, dict) and str(check.get("status")) == "fail"
    ]
    return clip(", ".join(named), LABEL_MAX) if named else "red"


def _flow(index: int, verdict: str) -> Flow:
    """The edge out of node *index* into the next: its path, and the marks it carries.

    The node chain is the hopper, the six stations and the sink, so an edge is named by the
    pair it joins rather than by one end. The artifact is the *source's*: `classify` produces
    a `classification`, and that artifact travels the edge leaving `classify`.
    """
    frm, to = CHAIN[index], CHAIN[index + 1]
    frm_x, frm_y = _place(index)
    to_x, to_y = _place(index + 1)
    if to_y != frm_y:
        # The fold, and the loop's one merge: down the right-hand column into the row below.
        path = f"M {frm_x} {frm_y + BOX_H / 2} L {to_x} {to_y - BOX_H / 2}"
        label_x, label_y = frm_x + BOX_W / 2 + 8.0, (frm_y + to_y) / 2
    else:
        edge = BOX_W / 2 if to_x > frm_x else -BOX_W / 2
        path = f"M {frm_x + edge} {frm_y} L {to_x - edge} {to_y}"
        # Clear of the box band, not merely between two boxes: an artifact label is wider
        # than the 70-unit gap it sits in, so at the row's own height it paints over the
        # station either side of it. `check_render_overflow.py` reported exactly that.
        label_x, label_y = (frm_x + to_x) / 2, frm_y - BOX_H / 2 - 10.0
    return Flow(
        frm=frm,
        to=to,
        path=path,
        artifact=ARTIFACTS.get(frm, ""),
        checkpoint=frm in CHECKPOINTS,
        merge=frm == MERGE_FROM,
        verdict=verdict if frm == MERGE_FROM else "",
        label_x=label_x,
        label_y=label_y,
    )


def _note(reads: Sequence[tuple[str, Reading]], unpinned: int) -> str:
    """What the drawing could not say: a section it could not read, and an unpinned ask."""
    parts = [f"{name} {read.note}" for name, read in reads if not read.drawn]
    if unpinned:
        parts.append(f"{unpinned} ask(s) name no phase, in the watch band only")
    # Always said, because a reader cannot tell a named artifact from a tracked one by
    # looking, and the contract carries no artifact state to track.
    parts.append("edges name what they produce; the contract carries no artifact state")
    return DOT.join(parts)


def diagram(reads: Mapping[str, Reading], now: datetime) -> Diagram:
    """The loop drawn as stations, edges and marks, from the document and nothing else."""
    units, lanes, asks, gates = reads["units"], reads["lanes"], reads["asks"], reads["gates"]
    dispatched = frozenset(str(row.get("id")) for row in lanes.dicts if row.get("id") is not None)
    counts = _counts(units, dispatched)
    inside = _lanes(lanes, now, beat(reads))
    waits, unpinned = _waits(asks, now)
    verdict = _verdict(gates)
    stations = []
    for offset, name in enumerate(STATIONS):
        index = offset + 1
        x, y = _place(index)
        held = inside.get(name, [])
        stations.append(
            Station(
                name=name,
                x=x,
                y=y,
                count=counts.get(name, 0) if units.drawn else None,
                lanes=tuple(held[:LANE_MARKS]),
                crew=_crew(held),
                dropped=max(0, len(held) - LANE_MARKS),
                waiting=waits.get(name, ""),
            )
        )
    flows = tuple(_flow(index, verdict) for index in range(len(CHAIN) - 1))
    backlog = reads["backlog"]
    repo = reads["repo"]
    # Both ends indexed into `CHAIN` rather than counted off `STATIONS`: `len(STATIONS)` is
    # the *last station*, so the sink was drawn at `ship`'s centre and the station box painted
    # over it (basicly-tfelrt). Nothing reported it - the two text runs miss each other, and
    # an element drawn underneath an opaque one is neither clipped nor overlapping.
    hopper_x, hopper_y = _place(CHAIN.index(HOPPER))
    sink_x, sink_y = _place(CHAIN.index(SINK))
    return Diagram(
        hopper=Terminal(
            HOPPER,
            hopper_x,
            hopper_y,
            counts.get(HOPPER, 0) if units.drawn else None,
            f"{number(backlog.fields.get('ready'))} ready" if backlog.drawn else "",
        ),
        sink=Terminal(
            SINK,
            sink_x,
            sink_y,
            _whole(backlog.fields.get("closed")) if backlog.drawn else None,
            joined(repo.fields, ("branch", "head")) if repo.drawn else "",
        ),
        stations=tuple(stations),
        flows=flows,
        width=VIEW_W,
        height=VIEW_H,
        note=_note((("units", units), ("lanes", lanes), ("gates", gates)), unpinned),
    )
