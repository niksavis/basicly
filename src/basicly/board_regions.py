# module-size-waiver: cost(basicly-bb98v4): 4056 of 4000. The layout rewrite grew the lane

from __future__ import annotations

import itertools
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import board_fields
from .board_loop import phase_of
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
    STUCK,
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
    bar,
    cell,
    clip,
    coarse,
    duration,
    elapsed,
    feature_of,
    joined,
    more,
    number,
    numeric,
    since,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .board_wall import Age, Reading, State

LANE_MARKS: Mapping[str, tuple[str, str]] = {
    "running": (LIVE, "running"),
    "landing": (LIVE, "landing"),
    "waits-to-land": (WAITING, "waits to land"),
    "landed": (CALM, "landed"),
    "queued": (WAITING, "queued"),
    "refused": (STUCK, "refused"),
    "parked": (WITHHELD, "parked"),
}

LANE_MOVING = frozenset({"running", "landing"})

LANE_RESUMABLE = frozenset({"queued", "waits-to-land", "refused"})

FLIGHT_SLOTS = 6
READY_SLOTS = 8

READY_SLOTS_WIDE = 14
READY_TITLE_WIDE = 110

READY_ROW_PITCH_PX = 24.09

READY_CHROME_CALIBRATION: tuple[tuple[float, float], ...] = (
    (1440.0, 726.0),
    (1600.0, 735.0),
    (1920.0, 690.0),
)

READY_CHROME_SAFETY_MARGIN_PX = READY_ROW_PITCH_PX

ACTS_CHROME_PX = 150.0
ACTS_ROW_PX = 90.0


def acts_reserve(rows: int) -> float:
    return ACTS_CHROME_PX + rows * ACTS_ROW_PX if rows else 0.0


CLAIMED_CHROME_PX = 24.0
CLAIMED_ROW_PX = 30.0


QUEUE_PX = 110.0


def claimed_reserve(rows: int) -> float:
    return CLAIMED_CHROME_PX + rows * CLAIMED_ROW_PX if rows else 0.0


PARKED_STRIP_PX = 46.0


def parked_reserve(rows: int) -> float:

    return PARKED_STRIP_PX if rows else 0.0


def _chrome_px(viewport_width: float | None) -> float:

    points = READY_CHROME_CALIBRATION
    if viewport_width is None or viewport_width <= points[0][0]:
        return points[0][1]
    if viewport_width >= points[-1][0]:
        return points[-1][1]
    for (w0, c0), (w1, c1) in itertools.pairwise(points):
        if w0 <= viewport_width <= w1:
            fraction = (viewport_width - w0) / (w1 - w0)
            return c0 + fraction * (c1 - c0)
    return points[-1][1]  # pragma: no cover - unreachable, the loop above covers the range


def ready_capacity(
    viewport_height: float | None, viewport_width: float | None = None, reserved: float = 0.0
) -> int:

    if viewport_height is None:
        return READY_SLOTS_WIDE
    chrome = _chrome_px(viewport_width) + READY_CHROME_SAFETY_MARGIN_PX + reserved
    return max(1, int((viewport_height - chrome) // READY_ROW_PITCH_PX))


BAND_ASKS = 1

BAND_ALARM_AFTER_S = 3600.0

QUESTION_MAX = 70

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def head(reads: Mapping[str, Reading]) -> tuple[Cell, ...]:

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
    given = numeric(ask.get("waiting_s"))
    return given if given is not None else since(ask.get("requested_at"), now)


def _since(ask: Mapping[str, Any]) -> str:
    stamp = ask.get("requested_at")
    written = board_fields.instant(stamp) if isinstance(stamp, str) else None
    if written is None:
        return "since an unreadable time"
    at = written.astimezone(UTC)
    return (
        f"since {_WEEKDAYS[at.weekday()]} {at.day:02d} {_MONTHS[at.month - 1]}"
        f" {at.hour:02d}:{at.minute:02d} UTC"
    )


def _offer(ask: Mapping[str, Any]) -> str:
    actions = ask.get("actions")
    first = actions[0] if isinstance(actions, list) and actions else None
    return str(first.get("offer", "")) if isinstance(first, dict) else ""


def _ask_line(ask: Mapping[str, Any]) -> str:
    question = ask.get("question")
    asked = f' "{clip(question, QUESTION_MAX)}"' if question else ""
    offer = _offer(ask)
    action = f"{DOT}do: {offer}" if offer else ""
    named = joined(ask, ("issue", "kind", "subject"))
    return f"{named}{DOT}{_since(ask)}{asked}{action}"


def _severity(waited: float | None) -> State:
    if waited is not None and waited >= BAND_ALARM_AFTER_S:
        return BY_KEY[STUCK]
    return BY_KEY[WAITING]


def band(reads: Mapping[str, Reading], drawn: Age, now: datetime) -> Band:

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
    lines = [_ask_line(ask) for ask in asks[:BAND_ASKS]]
    dropped = more(len(asks) - BAND_ASKS, "waiting")
    return Band(
        _severity(waited),
        coarse(waited) if waited is not None else "WAITING",
        f"{len(asks)} waiting on a person",
        tuple(lines + ([dropped] if dropped else [])),
        stale,
    )


def _lane_cells(lane: Mapping[str, Any]) -> tuple[Cell, ...]:

    used = lane.get("context_used")
    attempt = lane.get("rework_attempt")
    rework = (
        UNKNOWN
        if attempt is None
        else f"{number(attempt)} of {number(lane.get('rework_allowance'))}"
    )
    drawn = (
        Cell("id", str(lane.get("id") or UNKNOWN)),
        Cell("agent", joined(lane, ("agent", "model"))),
        Cell("branch", str(lane.get("branch") or UNKNOWN)),
        Cell("running", duration(lane.get("elapsed_s"))),
        Cell("tokens", number(lane.get("tokens"))),
        Cell("cost usd", number(lane.get("cost_usd"))),
        Cell("context", number(used), bar=bar(used, lane.get("context_window"))),
        Cell("rework", rework),
    )
    return tuple(cell for cell in drawn if cell.value != UNKNOWN)


def unit_titles(reads: Mapping[str, Reading]) -> dict[str, str]:

    units = reads["units"]
    return {
        str(row["id"]): str(row["title"])
        for row in units.dicts
        if row.get("id") and row.get("title")
    }


def _started_ago(lane: Mapping[str, Any], moment: datetime) -> str:

    waited = since(lane.get("started_at"), moment)
    return f"started {elapsed(waited)} ago" if waited is not None else ""


def _lane_mark(lane: Mapping[str, Any]) -> tuple[State, str]:

    key, word = LANE_MARKS.get(str(lane.get("state") or ""), ("", ""))
    if not key:
        return (BY_KEY[LIVE] if lane.get("live") else BY_KEY[ABSENT]), ""
    return BY_KEY[key], word


def _in_state_for(lane: Mapping[str, Any], moment: datetime) -> str:

    waited = since(lane.get("state_since"), moment)
    return elapsed(waited) if waited is not None else ""


def _primary_state(lane: Mapping[str, Any], moment: datetime) -> str:

    phase = phase_of(lane) or UNKNOWN
    word = _lane_mark(lane)[1]
    if not word:
        ago = _started_ago(lane, moment) if lane.get("live") else ""
        return f"{phase}{DOT}{ago}" if ago else phase
    held = _in_state_for(lane, moment) or (_started_ago(lane, moment) if lane.get("live") else "")
    return DOT.join(part for part in (word, held, phase) if part)


def _note_line(lane: Mapping[str, Any]) -> str:

    said = str(lane.get("note") or "")
    detail = str(lane.get("state_detail") or "")
    if detail or lane.get("state"):
        return DOT.join(part for part in (detail, said) if part)
    if lane.get("live"):
        return said
    return f"not confirmed live{DOT}{said}" if said else "not confirmed live"


def _card(lane: Mapping[str, Any], titles: Mapping[str, str], moment: datetime) -> Card:

    lane_id = str(lane.get("id") or UNKNOWN)
    live = bool(lane.get("live"))
    return Card(
        clip(titles.get(lane_id, "") or lane_id, TITLE_MAX),
        _primary_state(lane, moment),
        _lane_mark(lane)[0],
        _note_line(lane),
        _lane_cells(lane),
        working=live and bool(lane.get("note") or lane.get("tokens")),
        ident=str(lane.get("id") or ""),
    )


def flight(
    reads: Mapping[str, Reading], *, now: datetime | None = None
) -> tuple[tuple[Card, ...], str, str]:

    read = reads["lanes"]
    lanes = read.dicts if read.drawn else []
    titles = unit_titles(reads)
    moment = now or datetime.now(UTC)
    cards = tuple(_card(lane, titles, moment) for lane in lanes[:FLIGHT_SLOTS])
    note = read.note if not read.drawn else _waiting_on(reads, lanes)
    return cards, more(len(lanes) - FLIGHT_SLOTS, "lanes"), note


CLAIMED_STATUS = "in_progress"

CLAIMED_SLOTS = 4


def claimed(reads: Mapping[str, Reading]) -> tuple[tuple[dict[str, str], ...], str]:

    units, lanes = reads["units"], reads["lanes"]
    held = {str(row.get("id")) for row in lanes.dicts if row.get("id")}
    rows = [
        {
            "id": clip(str(row.get("id") or UNKNOWN), TITLE_MAX),
            "phase": phase_of(row) or UNKNOWN,
            "priority": str(row.get("priority") or UNKNOWN),
            "title": clip(str(row.get("title") or UNKNOWN), READY_TITLE_WIDE),
        }
        for row in units.dicts
        if str(row.get("status") or "") == CLAIMED_STATUS and str(row.get("id") or "") not in held
    ]
    return tuple(rows[:CLAIMED_SLOTS]), more(len(rows) - CLAIMED_SLOTS, "claimed")


PARKED_STATUS = "deferred"
PARKED_SLOTS = 4


def parked(reads: Mapping[str, Reading]) -> tuple[tuple[dict[str, str], ...], str]:

    units = reads["units"]
    rows = [
        {
            "id": clip(str(row.get("id") or UNKNOWN), TITLE_MAX),
            "phase": phase_of(row) or UNKNOWN,
            "priority": str(row.get("priority") or UNKNOWN),
            "title": clip(str(row.get("title") or UNKNOWN), READY_TITLE_WIDE),
        }
        for row in units.dicts
        if str(row.get("status") or "") == PARKED_STATUS
    ]
    return tuple(rows[:PARKED_SLOTS]), more(len(rows) - PARKED_SLOTS, "parked")


def _waiting_on(reads: Mapping[str, Reading], lanes: Sequence[Mapping[str, Any]]) -> str:

    if any(_moving(lane) for lane in lanes):
        return ""
    asks = reads["asks"]
    if asks.drawn and asks.dicts:
        return f"waits on a person - {len(asks.dicts)} checkpoint or decision pending"
    held = [str(lane.get("state") or "") for lane in lanes]
    stopped = [state for state in held if state in LANE_RESUMABLE]
    if stopped:
        words = ", ".join(LANE_MARKS[key][1] for key in LANE_MARKS if key in set(stopped))
        return f"waits for the next pass - {len(stopped)} lane(s) {words}"
    backlog = reads["backlog"].fields
    ready = numeric(backlog.get("ready")) or 0
    blocked = numeric(backlog.get("blocked"))
    if blocked and not ready:
        return f"waits on a blocker - {number(int(blocked))} record(s) have an unmet dependency"
    if [state for state in held if state in LANE_MOVING or state in LANE_RESUMABLE]:
        return "no lane of this pass is running or landing"
    return (
        f"no pass is running - {number(int(ready))} record(s) are ready to start"
        if ready
        else "no lane is dispatched"
    )


def _moving(lane: Mapping[str, Any]) -> bool:

    state = str(lane.get("state") or "")
    return state in LANE_MOVING or (not state and bool(lane.get("live")))


def _rank(unit: Mapping[str, Any]) -> tuple[str, str]:
    return str(unit.get("priority") or "\N{TILDE}"), str(unit.get("id") or "")


def _feature_names(
    reads: Mapping[str, Reading], units: Sequence[Mapping[str, Any]], ready: Sequence[Any]
) -> list[str]:

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


def next_up(
    reads: Mapping[str, Reading],
    *,
    wide: bool = False,
    viewport_height: float | None = None,
    viewport_width: float | None = None,
    reserved: float = 0.0,
) -> Listing:

    read = reads["units"]
    if not read.drawn:
        return Listing(read.state, note=read.note)
    units = read.dicts
    flagged = [unit for unit in units if isinstance(unit.get("ready"), bool)]
    if not flagged:
        return Listing(BY_KEY[ABSENT], note=f"ready {ABSENT_TEXT} on any of the {len(units)} units")
    fits = ready_capacity(viewport_height, viewport_width, reserved)
    slots = fits if wide else min(READY_SLOTS, fits)
    bound = READY_TITLE_WIDE if wide else TITLE_MAX
    ready = sorted((unit for unit in flagged if unit["ready"]), key=_rank)
    names = _feature_names(reads, units, ready)
    slots = max(slots // 2, slots - len(set(names[:slots])))
    rows = tuple(
        Item(
            str(unit.get("priority") or UNKNOWN),
            clip(unit.get("id", UNKNOWN), TITLE_MAX),
            clip(unit.get("title") or UNKNOWN, bound),
        )
        for unit in ready[:slots]
    )
    startable = sum(1 for unit in ready if not unit.get("owes"))
    note = "" if ready else f"nothing is ready of the {len(flagged)} units emitted"
    if ready and startable != len(ready):
        note = f"{startable} of {len(ready)} unblocked can be dispatched; the rest owe a section"
    groups = grouped(rows, names)
    return Listing(BY_KEY[RENDERABLE], rows, more(len(ready) - slots, "ready"), note, groups)
