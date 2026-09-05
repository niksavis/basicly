"""The factory loop as a drawn diagram, because seven counted boxes are not a workflow.

The owner's report (basicly-6c97zx) was that the page carries *"no actual diagramatic
visualization of the loop"*. A histogram says how many units are at a phase. It cannot say
what the phases are, which transition a human gates, what each one produces, or which edge
will fail next, and those are the questions a person watching a factory asks.

**A model, never markup.** Everything here is geometry and text; the template emits the SVG
elements. That is a security boundary and not a style: a producer's `lanes[].agent` reaches
the page through Jinja's autoescape like every other string, and an SVG assembled here as
one string would have to be marked safe, which is the one door :mod:`basicly.board_render`'s
own docstring exists to keep shut.

**Inline SVG rather than Mermaid.** `tests/test_board_render.py` asserts the page carries no
``<script``, no ``<link`` and no ``src=``, so a runtime renderer is already refused. Drawn
shapes also retire the tofu: a mark is a `path` and never a codepoint.

**`intake` is the hopper and not a station.** It is the derivation's `otherwise` rung, so its
count is the untouched backlog rather than a stage anything moves through.

**Two gaps are stated rather than guessed.** No artifact field exists anywhere on
``harness-board/v1``, so an edge *names* what it produces and may never colour it approved
or pending. And an ask is pinned to a station by its ``subject`` matching a phase name,
which is a heuristic by contract; an ask that matches nothing is counted here instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .board_loop import PHASES, beat, moved_within, phase_of, working_phase
from .board_wall import DOT, bar, clip, elapsed, joined, number, numeric, since

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_wall import Reading

# The drawing surface, in its own units. Every figure below is in these, so the page scales
# the whole diagram by setting one width and the aspect ratio holds.
VIEW_W = 1200.0
# 9.7:1, and the ratio is the whole of basicly-ubwp49. `preserveAspectRatio` fits the drawing
# to whichever axis binds, so a surface taller than its region letterboxes: at the first
# draft's 4.2:1 a 1920px page clamped the height, scaled every figure by 0.909 and left 43% of
# the width empty, drawing the station labels at 13.6px under a 17px ready row. Here the
# *width* binds from 1440 up, so a label renders at 17.8px or better. The type size is the
# scale, and a font-size inside the viewBox cannot reach it.
VIEW_H = 124.0

# The stations, in loop order. `intake` is absent on purpose - it is the hopper - and `done`
# is the sink, which is a terminal state rather than a phase.
STATIONS: tuple[str, ...] = PHASES[1:]
HOPPER = PHASES[0]
SINK = "done"

# Every node the drawing places, in order, so an edge is `CHAIN[i] -> CHAIN[i + 1]` and the
# seventh edge - `ship -> done`, which carries the `release-record` - cannot be lost by
# iterating the stations instead.
CHAIN: tuple[str, ...] = (HOPPER, *STATIONS, SINK)

# One row of eight, because a fold costs height and height is what the ratio above spends.
# The first draft folded into two rows to buy each station 280 units of width, which doubled
# the content bands and pinned the surface at 4.2:1. The fold also claimed to draw the loop
# as a loop, and `CHAIN` refutes that: no edge returns from `done` to `intake`.
SLOT = VIEW_W / len(CHAIN)
ROW_Y = 62.0
BOX_W = 130.0
BOX_H = 56.0

# The artifact band, derived rather than set: a label wider than the 20-unit gap it spans
# paints over the station either side of it unless it clears the box, which
# `check_render_overflow.py` reported. A verdict sits 15 units above this baseline and a
# checkpoint 28 below it on the edge line, both the template's arithmetic off this one line.
ARTIFACT_Y = ROW_Y - BOX_H / 2 - 7.0
BAR_INSET = 4.0

# What each transition produces. Every name is a file under `.basicly/core/schemas/`, and
# `tests/test_board_diagram.py` pins the pair so the diagram cannot drift from the engine.
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

# How many lane marks a station draws. Three dots at this scale are legible from six metres,
# and a fourth reaches past the box's own bottom edge.
LANE_MARKS = 3

LABEL_MAX = 30
AGENT_MAX = 20

# A terminal's detail is centred under a node, and the last node has half a slot to its
# right. Unbounded it reached 1216 of 1200 units and was cut mid-commit, which nothing
# reported: text outside a `viewBox` is not an element holding more than it shows.
DETAIL_MAX = int(SLOT / 5.5)


@dataclass(frozen=True)
class Lane:
    """One agent inside a station: who it is, whether it moved, and whether it is wedged.

    `moved` and `stuck` are not each other's negation. A lane that has not moved this beat is
    ordinarily mid-step; `stuck` is the producer saying so in `state`.
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
    crew: str
    crew_row: int
    fill: float | None
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


def _crew(held: Sequence[Lane]) -> str:
    """The one line under a box: who is inside, or how many where a name would not fit.

    One line and not three. A 130-unit box carries one name at 11 units, and the lane cards
    below already name every agent and its model in full.
    """
    if not held:
        return ""
    return held[0].label if len(held) == 1 else f"{len(held)} agents"


def _fill(count: int | None, whole: int) -> float | None:
    """The magnitude bar's width in view units, over the units *inside* the loop.

    The hopper is out of the denominator on purpose: `intake` held 263 of 290 records, so a
    share counting it drew every station under four percent, which is what the census strip
    this replaces drew and why its bars said nothing (basicly-ubwp49).
    """
    drawn = bar(count, whole)
    return None if drawn is None else (BOX_W - BAR_INSET * 2) * drawn.width / 100.0


def _whole(value: object) -> int | None:
    """*value* as a whole count, or None - the sink draws a tally, never `770.0`."""
    held = numeric(value)
    return None if held is None else int(held)


def _place(index: int) -> tuple[float, float]:
    """Where node *index* sits: one row, each node centred in its own slot."""
    return SLOT * (index + 0.5), ROW_Y


def _counts(units: Reading, dispatched: frozenset[str]) -> dict[str, int]:
    """Units **resting** at each phase: at it, with no agent inside.

    The two channels are disjoint on purpose: a count including the lanes would put the same
    unit in the digit and in a dot beside it. A lane's id need not appear in `units[]` at
    all, so this subtracts by id and never by arithmetic on the two lengths.
    """
    counts: dict[str, int] = {}
    for row in units.dicts:
        # `working_phase`, so a parked record is not drawn as work at a phase: a deferred
        # one keeps the phase its worktree binding derives (basicly-5jkxqk).
        name = working_phase(row)
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

    The pin is `subject` matching a phase name, which is this producer's habit and not a
    contract, so an ask matching nothing is counted rather than attached to the nearest.
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

    An edge is named by the pair it joins rather than by one end, and the artifact is the
    *source's*: a `classification` travels the edge leaving `classify`.
    """
    frm, to = CHAIN[index], CHAIN[index + 1]
    frm_x, _ = _place(index)
    to_x, _ = _place(index + 1)
    return Flow(
        frm=frm,
        to=to,
        path=f"M {frm_x + BOX_W / 2} {ROW_Y} L {to_x - BOX_W / 2} {ROW_Y}",
        artifact=ARTIFACTS.get(frm, ""),
        checkpoint=frm in CHECKPOINTS,
        merge=frm == MERGE_FROM,
        verdict=verdict if frm == MERGE_FROM else "",
        label_x=(frm_x + to_x) / 2,
        label_y=ARTIFACT_Y,
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
    agents = _lanes(lanes, now, beat(reads))
    waits, unpinned = _waits(asks, now)
    verdict = _verdict(gates)
    inside = sum(counts.get(name, 0) for name in STATIONS)
    stations = []
    for offset, name in enumerate(STATIONS):
        index = offset + 1
        x, y = _place(index)
        held = agents.get(name, [])
        waiting = waits.get(name, "")
        stations.append(
            Station(
                name=name,
                x=x,
                y=y,
                count=counts.get(name, 0) if units.drawn else None,
                lanes=tuple(held[:LANE_MARKS]),
                crew=_crew(held),
                crew_row=1 if waiting else 0,
                fill=_fill(counts.get(name, 0) if units.drawn else None, inside),
                waiting=waiting,
            )
        )
    flows = tuple(_flow(index, verdict) for index in range(len(CHAIN) - 1))
    backlog = reads["backlog"]
    repo = reads["repo"]
    # Indexed into `CHAIN` and never counted off `STATIONS`: `len(STATIONS)` is the *last*
    # station, so the sink was once drawn at `ship`'s centre under its opaque box, which is
    # neither clipped nor overlapping and which no instrument reported (basicly-tfelrt).
    hopper_x, hopper_y = _place(CHAIN.index(HOPPER))
    sink_x, sink_y = _place(CHAIN.index(SINK))
    return Diagram(
        hopper=Terminal(
            HOPPER,
            hopper_x,
            hopper_y,
            counts.get(HOPPER, 0) if units.drawn else None,
            clip(f"{number(backlog.fields.get('ready'))} ready", DETAIL_MAX)
            if backlog.drawn
            else "",
        ),
        sink=Terminal(
            SINK,
            sink_x,
            sink_y,
            _whole(backlog.fields.get("closed")) if backlog.drawn else None,
            clip(joined(repo.fields, ("branch", "head")), DETAIL_MAX) if repo.drawn else "",
        ),
        stations=tuple(stations),
        flows=flows,
        width=VIEW_W,
        height=VIEW_H,
        note=_note((("units", units), ("lanes", lanes), ("gates", gates)), unpinned),
    )
