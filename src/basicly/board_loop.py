"""The loop region, which answers *what is this pass doing* and nothing else.

The region headed `the loop` used to bin **every active record in the backlog**, making its
total equal `backlog.active` by construction (basicly-a68ggd): 262 of 291 records sat at
`intake` because nobody had classified them, so the largest bar on a factory wall measured
untouched backlog. Two populations, therefore two rows - :func:`loop` over `lanes[]`, which
is what the pass selected, and :func:`backlog_phases` over `units[]` under its own label.

**Working against wedged.** A still picture of positions cannot tell a lane that reached
`build` this beat from one wedged there since morning, so a phase carries a `moved` mark off
`lanes[].state_since`. It is the *state* that is stamped and not the phase, so the mark is a
proxy; a producer-side `phase_since` is the exact answer and is filed separately.

Below :mod:`basicly.board_regions` in the layer order, because both need :func:`phase_of`
and siblings there may not import each other.
"""

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

# The harness's own lifecycle, and the order is load-bearing: the palette ships four hues
# against seven phases, so a phase is carried by its position and label, never by a colour.
PHASES: tuple[str, ...] = (
    "intake",
    "classify",
    "decompose",
    "build",
    "verify",
    "validate",
    "ship",
)

# The `moved` window where the document states no cadence: a one-shot build has no beat and
# a state-change producer has no cadence, so this is the interval a supervisor ticks at.
BEAT_FALLBACK_S = 15.0

# And its ceiling. A producer free to declare a one-hour cadence would mark every lane as
# moving all day, and an alarm that is always on carries no information.
BEAT_CAP_S = 300.0


# A status that means nobody is coming. A deferred record keeps whatever phase its worktree
# binding derives, so `basicly-3iaw0x` - parked, holding a live worktree - was drawn as work
# at `build` and read as activity (basicly-5jkxqk).
PARKED = frozenset({"deferred"})


def phase_of(row: Mapping[str, Any]) -> str:
    """The phase *row* declares, or an empty string where it declares none."""
    phase = row.get("phase")
    return phase if isinstance(phase, str) and phase else ""


def working_phase(row: Mapping[str, Any]) -> str:
    """*row*'s phase where its status means work could move, else "".

    Here rather than twice: two populations disagreeing about whether `deferred` counts is
    how one number on a page contradicts another.
    """
    return "" if str(row.get("status") or "") in PARKED else phase_of(row)


def running(reads: Mapping[str, Reading]) -> bool:
    """Whether a pass is being observed at all.

    The schema says an absent ``session`` "means no run is being observed, which is a
    different statement from a run with nothing in it", so the two are read separately and
    either is enough: a session with no lane yet is a pass that has selected nothing, and
    lanes with no session is a producer holding the lanes and not the run.
    """
    session, lanes = reads["session"], reads["lanes"]
    if session.drawn and str(session.fields.get("root") or ""):
        return True
    return lanes.drawn and bool(lanes.dicts)


def beat(reads: Mapping[str, Reading]) -> float:
    """The window a `moved` mark is taken over: the producer's cadence, bounded.

    Public because :mod:`basicly.board_diagram` marks the same movement on its lane dots,
    and two regions disagreeing about how long a beat is would be two answers to one
    question a reader compares across the page.
    """
    cadence = reads["freshness"].fields.get("cadence_s")
    if not isinstance(cadence, int | float) or cadence <= 0:
        return BEAT_FALLBACK_S
    return min(float(cadence), BEAT_CAP_S)


def moved_within(lane: Mapping[str, Any], now: datetime, window: float) -> bool:
    """Whether *lane* entered its current state within the last *window* seconds.

    The stamp is the lane's *state* and not its phase, so this is a proxy for movement
    rather than a phase transition; `basicly-jxemn3` holds the producer-side stamp.
    """
    waited = since(lane.get("state_since"), now)
    return waited is not None and waited <= window


def _note(lanes: Reading, unphased: int, marks: int) -> str:
    """What the pass row could not say, in the producer's terms rather than as a zero."""
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
    """The pass row: where each unit **this pass selected** is, and which of them moved.

    The third term is not derivable from the first two - a pass whose lanes failed to parse
    draws an empty row a reader must not read as an idle factory. The population is `lanes[]`
    and never `units[]`, and nothing is joined: a lane id need not appear in `units[]` at
    all, so the lane's own `phase` is the one read.
    """
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
    # An empty phase of a pass that *has* lanes is a measured nought; a pass that selected
    # nothing has measured nothing, and seven noughts would claim seven empty stages.
    measured = bool(counts)
    population = sum(counts.values())
    row = tuple(
        _phase(name, counts.get(name, 0) if measured else None, population, name in marks)
        for name in list(PHASES) + sorted(set(counts) - set(PHASES))
    )
    return row, _note(lanes, unphased, len(marks)), True


def _phase(name: str, count: int | None, population: int, moved: bool) -> Phase:
    """One phase box: its count, its share of the population, and its two marks."""
    held = count or 0
    return Phase(name, count, held > 0, bar(count, population), moved=moved)


def _backlog_note(units: Reading, counts: Mapping[str, int], missing: int, parked: int) -> str:
    """Why the backlog row is not fully populated, in the producer's own terms.

    *missing* and *parked* are different facts. Folded together, the note said `4 units carry
    no phase` about four records that each carry one.
    """
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
    """The backlog census by phase, under its own denominator and its own label.

    Kept rather than dropped: the defect was the heading it sat under, not its existence.
    Nothing is marked - a backlog record is at a phase because that is where it stopped,
    which is a position and not a movement.
    """
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
    # The share is of the *phased* population and never the section's length: an unphased
    # unit is in no phase, and counting it shortens every bar by the same wrong amount.
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
