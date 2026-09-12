from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .board_loop import PHASES, beat, moved_within, phase_of, working_phase
from .board_wall import DOT, bar, clip, elapsed, joined, number, numeric, since

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .board_wall import Reading

VIEW_W = 1200.0
VIEW_H = 124.0

STATIONS: tuple[str, ...] = PHASES[1:]
HOPPER = PHASES[0]
SINK = "done"

CHAIN: tuple[str, ...] = (HOPPER, *STATIONS, SINK)

SLOT = VIEW_W / len(CHAIN)
ROW_Y = 62.0
BOX_W = 130.0
BOX_H = 56.0

ARTIFACT_Y = ROW_Y - BOX_H / 2 - 7.0
BAR_INSET = 4.0

ARTIFACTS: Mapping[str, str] = {
    "classify": "classification + change-shape",
    "decompose": "implementation-plan",
    "build": "change-summary",
    "verify": "verification-evidence",
    "validate": "validation-transcript",
    "ship": "release-record",
}

CHECKPOINTS: frozenset[str] = frozenset({"classify", "decompose", "ship"})

MERGE_FROM = "build"

LANE_MARKS = 3

LABEL_MAX = 30
AGENT_MAX = 20

DETAIL_MAX = int(SLOT / 5.5)


@dataclass(frozen=True)
class Lane:
    label: str
    moved: bool
    stuck: bool


@dataclass(frozen=True)
class Station:
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
    name: str
    x: float
    y: float
    count: int | None
    detail: str


@dataclass(frozen=True)
class Diagram:
    hopper: Terminal
    sink: Terminal
    stations: tuple[Station, ...]
    flows: tuple[Flow, ...]
    width: float
    height: float
    note: str


def _crew(held: Sequence[Lane]) -> str:

    if not held:
        return ""
    return held[0].label if len(held) == 1 else f"{len(held)} agents"


def _fill(count: int | None, whole: int) -> float | None:

    drawn = bar(count, whole)
    return None if drawn is None else (BOX_W - BAR_INSET * 2) * drawn.width / 100.0


def _whole(value: object) -> int | None:
    held = numeric(value)
    return None if held is None else int(held)


def _place(index: int) -> tuple[float, float]:
    return SLOT * (index + 0.5), ROW_Y


def _counts(units: Reading, dispatched: frozenset[str]) -> dict[str, int]:

    counts: dict[str, int] = {}
    for row in units.dicts:
        name = working_phase(row)
        if name and str(row.get("id") or "") not in dispatched:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _lane_label(lane: Mapping[str, Any]) -> str:
    return clip(joined(lane, ("agent", "model")) or str(lane.get("id") or "lane"), AGENT_MAX)


STUCK_STATES = frozenset({"refused", "parked", "waits-to-land"})


def _lanes(lanes: Reading, now: datetime, window: float) -> dict[str, list[Lane]]:
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
        held[name] = elapsed(waited) if waited is not None else "waiting"
    return held, unpinned


def _verdict(gates: Reading) -> str:

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
    parts = [f"{name} {read.note}" for name, read in reads if not read.drawn]
    if unpinned:
        parts.append(f"{unpinned} ask(s) name no phase, in the watch band only")
    parts.append("edges name what they produce; the contract carries no artifact state")
    return DOT.join(parts)


def diagram(reads: Mapping[str, Reading], now: datetime) -> Diagram:
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
