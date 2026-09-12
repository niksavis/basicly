from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .board_wall import (
    ABSENT_TEXT,
    DOT,
    Phase,
    bar,
    since,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from .board_wall import Reading

PHASES: tuple[str, ...] = (
    "intake",
    "classify",
    "decompose",
    "build",
    "verify",
    "validate",
    "ship",
)

BEAT_FALLBACK_S = 15.0

BEAT_CAP_S = 300.0


PARKED = frozenset({"deferred"})


def phase_of(row: Mapping[str, Any]) -> str:
    phase = row.get("phase")
    return phase if isinstance(phase, str) and phase else ""


def working_phase(row: Mapping[str, Any]) -> str:

    return "" if str(row.get("status") or "") in PARKED else phase_of(row)


def running(reads: Mapping[str, Reading]) -> bool:

    session, lanes = reads["session"], reads["lanes"]
    if session.drawn and str(session.fields.get("root") or ""):
        return True
    return lanes.drawn and bool(lanes.dicts)


def beat(reads: Mapping[str, Reading]) -> float:

    cadence = reads["freshness"].fields.get("cadence_s")
    if not isinstance(cadence, int | float) or cadence <= 0:
        return BEAT_FALLBACK_S
    return min(float(cadence), BEAT_CAP_S)


def moved_within(lane: Mapping[str, Any], now: datetime, window: float) -> bool:

    waited = since(lane.get("state_since"), now)
    return waited is not None and waited <= window


def _note(lanes: Reading, unphased: int, marks: int) -> str:
    parts = []
    if not lanes.drawn:
        parts.append(f"lanes {lanes.note}")
    elif not lanes.dicts:
        parts.append("the pass has selected no lane yet")
    elif unphased:
        parts.append(f"{unphased} of {len(lanes.dicts)} lanes carry no phase")
    if lanes.drawn and lanes.dicts and not marks:
        parts.append("no lane moved this beat")
    return DOT.join(parts)


def loop(reads: Mapping[str, Reading], now: datetime) -> tuple[tuple[Phase, ...], str, bool]:

    lanes = reads["lanes"]
    if not running(reads):
        return (), "no pass is running", False
    window = beat(reads)
    counts: dict[str, int] = {}
    marks: set[str] = set()
    unphased = 0
    for row in lanes.dicts:
        name = phase_of(row)
        if not name:
            unphased += 1
            continue
        counts[name] = counts.get(name, 0) + 1
        if moved_within(row, now, window):
            marks.add(name)
    measured = bool(counts)
    population = sum(counts.values())
    row = tuple(
        _phase(name, counts.get(name, 0) if measured else None, population, name in marks)
        for name in list(PHASES) + sorted(set(counts) - set(PHASES))
    )
    return row, _note(lanes, unphased, len(marks)), True


def _phase(name: str, count: int | None, population: int, moved: bool) -> Phase:
    held = count or 0
    return Phase(name, count, held > 0, bar(count, population), moved=moved)


def _backlog_note(units: Reading, counts: Mapping[str, int], missing: int, parked: int) -> str:

    parts = []
    if not units.drawn:
        parts.append(f"units {units.note}")
    elif not counts:
        parts.append(f"phase {ABSENT_TEXT} on any of the {len(units.dicts)} units")
    elif missing:
        parts.append(f"{missing} of {len(units.dicts)} units carry no phase")
    if parked:
        parts.append(f"{parked} parked, not counted at a phase")
    return DOT.join(parts)


def backlog_phases(reads: Mapping[str, Reading]) -> tuple[tuple[Phase, ...], str]:

    units = reads["units"]
    counts: dict[str, int] = {}
    missing = 0
    parked = 0
    for row in units.dicts:
        if str(row.get("status") or "") in PARKED:
            parked += 1
        elif name := phase_of(row):
            counts[name] = counts.get(name, 0) + 1
        else:
            missing += 1
    measured = units.drawn and bool(counts)
    population = sum(counts.values())
    row = tuple(
        Phase(
            name,
            counts.get(name, 0) if measured else None,
            here=False,
            share=bar(counts.get(name, 0) if measured else None, population),
        )
        for name in list(PHASES) + sorted(set(counts) - set(PHASES))
    )
    return row, _backlog_note(units, counts, missing, parked)
