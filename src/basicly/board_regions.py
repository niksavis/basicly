"""One region per question, because the layout is the acceptance criterion.

The board answers four questions from across a room and each has a region of its own: the
**watch band** says whether anybody waits on a person, **in flight** and **next up** say
what runs and what is next, and the **footer** says whether we are making progress. The
**loop row** is :mod:`basicly.board_loop`'s, one layer down, because both need
:func:`~basicly.board_loop.phase_of` and siblings here may not import each other.

Every region is a **fixed-height row that truncates with a visible marker**, which is the
defect this module was written against: the render it replaces gave each schema key a box
its content overflowed, so all four answers sat below a scrollbar nobody on a wall can
reach. A region caps what it draws at a slot count, says ``+N more`` naming what it
dropped, and clips a string itself rather than letting CSS hide the end.

**A layout count is not a section count.** One region may read several sections, or none.
:func:`inventory` draws the verdict's whole roster, so a section no region reads still
reports itself.

The vocabulary, the honesty rules and the shapes are :mod:`basicly.board_wall`'s;
:mod:`basicly.board_render` draws what this module returns.
"""

# comment-density-waiver: cost(basicly-0bj8q1): 50.3% of 6483 against the 50% cap, and the
# mechanism is inverted - this module did not gain prose, it LOST code. `loop`, `PHASES` and
# `phase_of` left for `board_loop`: prose fell 372 tokens and code fell 426, because the
# extracted region was denser than the module average. What is left is measurement rationale
# three records cite - the 24.09px row pitch, the three calibration widths, the 41-against-6
# count. Retired by `basicly-0bj8q1`, which takes `flight` and `_lane_cells`, the prose-heavy
# half; a cut of narration here would delete evidence instead.
# module-size-waiver: cost(basicly-bb98v4): 4056 of 4000. The layout rewrite grew the lane
# card - a row per figure the producer holds, a working mark, and a stuck note. Half the
# named cut is taken: `loop`, `PHASES` and `phase_of` left for `board_loop` with
# basicly-a68ggd. What remains nameable is `flight` and `_lane_cells`, held by
# `basicly-0bj8q1`.

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

# What each `lanes[].state` is drawn as: a palette key carrying the colour and the border
# style, and the word the card leads with. No glyph on a card. The **word** is the
# discriminator and the pair is the alarm, which is why two pairs are shared: `board_wall`
# ships nine states, and adding one is that module's own file rather than this one's.
LANE_MARKS: Mapping[str, tuple[str, str]] = {
    "running": (LIVE, "running"),
    "landing": (LIVE, "landing"),
    "waits-to-land": (WAITING, "waits to land"),
    "queued": (WAITING, "queued"),
    "refused": (STUCK, "refused"),
    "parked": (WITHHELD, "parked"),
}

# The states that mean the pass is moving without a person. When none of the lanes is in
# one of these, the region owes the sentence :func:`_waiting_on` writes.
LANE_MOVING = frozenset({"running", "landing"})

# How many items a capped region draws before it reports the rest. The running row no longer
# reserves empty frames: a dashed placeholder announcing nothing three times is 40% of a wall
# spent on the state that costs one token, so an empty row collapses to a line and the ready
# list takes the width. The cap survives because six live lanes still have to fit.
FLIGHT_SLOTS = 6
READY_SLOTS = 8

# What the ready list draws once the running row has collapsed and handed it the page, and how
# long a title may be there. The eight rows and 62-character titles of the narrow column leave
# two thirds of a 1080px screen blank and still truncate every line.
READY_SLOTS_WIDE = 14
READY_TITLE_WIDE = 110

# The measured height of a data row on the live board (basicly-ffm2yp): 17px at line-height
# 1.3 plus 1px padding each side, confirmed against a real render, not read off the CSS.
READY_ROW_PITCH_PX = 24.09

# The chrome above and below the reclaimed rows on the *live* board, in CSS pixels, at the
# three widths this layout claims, found by binary search with `.scripts/check_render_overflow.py`
# on the real document. A fixture-measured constant clipped 54px live, because live content
# wraps more at 1440; chrome falls as width grows, so three points interpolate. Sorted by width.
#
# Re-measured for the drawn loop (basicly-6c97zx), which is the whole of the rise from
# 514/429/381: a diagram is taller than the seven counted boxes it replaced, and this constant
# is the page's only account of what sits above the list. It is not derived from the region's
# own height - the instrument is run against the rendered document and the figure is whatever
# makes `body` stop overflowing, because a computed chrome is what the 54px clip already was.
READY_CHROME_CALIBRATION: tuple[tuple[float, float], ...] = (
    (1440.0, 726.0),
    (1600.0, 735.0),
    (1920.0, 690.0),
)

# One row of headroom over the measured chrome: tomorrow's longer branch name wraps a little
# further, and losing a row to the margin is cheaper than losing one to a clip.
READY_CHROME_SAFETY_MARGIN_PX = READY_ROW_PITCH_PX

# What the `acts` region costs the page when a person is being asked something: its heading
# and reply frame, and each row. Measured on the rendered document the way the chrome above
# was, at the three calibrated widths and at 900 as well as 1080 - a row's command and its
# form wrap onto separate lines at 1440, which is where the first pair of figures came out
# 108px short and the instrument said so (basicly-ua9o5g).
#
# **A conditional region has to be subtracted, not calibrated.** The chrome figures above are
# per width and constant per width, which is exactly what an always-drawn region is. This one
# draws only when something is pending, so folding it into those numbers would shorten the
# ready list all day to pay for a region that is absent all day.
ACTS_CHROME_PX = 150.0
ACTS_ROW_PX = 90.0


def acts_reserve(rows: int) -> float:
    """The height *rows* actionable asks take out of the page, or 0.0 where there are none."""
    return ACTS_CHROME_PX + rows * ACTS_ROW_PX if rows else 0.0


# What a claimed row costs, measured the same way and for the same reason: the region draws
# only when something is claimed, so its height is subtracted on the render that draws it and
# never folded into the width-keyed chrome. One row and its heading measured 47px at 1440x900.
CLAIMED_CHROME_PX = 24.0
CLAIMED_ROW_PX = 30.0


# What the queue block costs the footer. Always drawn - the shape of what is waiting is not a
# conditional fact - so unlike the two above it is a flat figure and not a per-row one.
#
# **A reserve buys whole rows, so the figure is not the block's height.** The block measured
# ~84px and the page still clipped 5px at 1440x900, because 78 and 90 both leave the same two
# rows standing - the capacity divides by the 24.09px pitch. 110 is the first figure that
# drops a row, and dropping one is what clears the 5px. Rounding down to the measured height
# is what leaves a page short by less than a row and clipping anyway.
QUEUE_PX = 110.0


def claimed_reserve(rows: int) -> float:
    """The height *rows* claimed records take out of the page, or 0.0 where there are none."""
    return CLAIMED_CHROME_PX + rows * CLAIMED_ROW_PX if rows else 0.0


# The parked strip: one wrapping line, measured at 18px with four entries at 1440x900, plus
# its 5px margin. Flat rather than per-row because :data:`PARKED_SLOTS` bounds it at four and
# four entries are ~520px of a 1400px region, so it cannot reach a second line.
#
# It was a row apiece first. That measured 104px and took five titles off the ready list,
# which is a bad trade: five ready titles are worth more to a reader than four parked ones.
PARKED_STRIP_PX = 28.0


def parked_reserve(rows: int) -> float:
    """The height the parked strip takes out of the page, or 0.0 where nothing is parked.

    Without this the strip drew under a list already sized to the whole viewport and the
    board clipped 65px at 1440x900 - on a page that had been clean at all three widths.
    """
    return PARKED_STRIP_PX if rows else 0.0


def _chrome_px(viewport_width: float | None) -> float:
    """The calibrated chrome height at *viewport_width*, interpolated between measured points.

    None or narrower than the narrowest calibration point takes that point's figure - the
    most conservative one measured - rather than extrapolating past what was ever checked.
    """
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
    """How many reclaimed-row slots fit at this viewport, or :data:`READY_SLOTS_WIDE`.

    A height of None means the caller does not know the viewport at all - basicly-ffm2yp's
    own finding is that guessing a number here for that case is the defect, so it is never
    guessed: the caller that knows a wall's own height is the one that must pass it, and
    until one does this returns the figure the layout has always been safe at instead of a
    computed one.

    *reserved* is height a region above this list is taking on *this* render and not on
    every one - :func:`acts_reserve`'s figure. It is subtracted rather than calibrated into
    the chrome because the region it pays for is conditional, and a list permanently short by
    a region that is usually absent is the cost this argument exists to avoid.
    """
    if viewport_height is None:
        return READY_SLOTS_WIDE
    chrome = _chrome_px(viewport_width) + READY_CHROME_SAFETY_MARGIN_PX + reserved
    return max(1, int((viewport_height - chrome) // READY_ROW_PITCH_PX))


# One ask on the band, not two. The age is the headline now, and an age belongs to exactly one
# ask - the one that has waited longest. A second detail line under a 44px headline is what
# pushes the alarm past the height the design gives it; the dropped count names the rest.
BAND_ASKS = 1

# The wait, in seconds, past which the band escalates from WAITING's amber to STUCK's orange.
# One hour: basicly-k6tpep's Fable-5 review found a checkpoint answered within it is normal
# attended turnaround, so the alarm has to mean the wait outlasted that, not that one exists.
BAND_ALARM_AFTER_S = 3600.0

QUESTION_MAX = 70

# Fixed rather than `strftime`, whose `%a`/`%b` read the host locale this repo cannot pin.
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


def _since(ask: Mapping[str, Any]) -> str:
    """When *ask* was made, as an absolute UTC stamp - never the headline's duration again."""
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
    """What the producer offered to do about *ask*, in its own wording, or ""."""
    actions = ask.get("actions")
    first = actions[0] if isinstance(actions, list) and actions else None
    return str(first.get("offer", "")) if isinstance(first, dict) else ""


def _ask_line(ask: Mapping[str, Any]) -> str:
    """One pending ask: who is waiting, since when, on what, and what to do about it."""
    question = ask.get("question")
    asked = f' "{clip(question, QUESTION_MAX)}"' if question else ""
    offer = _offer(ask)
    action = f"{DOT}do: {offer}" if offer else ""
    named = joined(ask, ("issue", "kind", "subject"))
    return f"{named}{DOT}{_since(ask)}{asked}{action}"


def _severity(waited: float | None) -> State:
    """STUCK for *waited* seconds at or past :data:`BAND_ALARM_AFTER_S`, else WAITING."""
    if waited is not None and waited >= BAND_ALARM_AFTER_S:
        return BY_KEY[STUCK]
    return BY_KEY[WAITING]


def band(reads: Mapping[str, Reading], drawn: Age, now: datetime) -> Band:
    """The alarm, which reads six ways and no more.

    Four of them are the ask verdict and exactly one holds: **withheld**, then **absent**,
    then **waiting** - split by :func:`_severity` into WAITING and STUCK - then **calm**, in
    that precedence, a section that could not be read must never report as a quiet room. The
    fifth is the **stale** marker, appended to whichever holds rather than replacing it, so a
    frozen screen says it is frozen and still shows the last values it knew.

    **While anybody is waiting the headline is the age**, in the coarsest unit that is still
    true, stated once, because that is what a wall ranks by; the id and the kind go beneath
    it with an absolute since-when instead of that duration again. An ask nobody could date
    keeps the alarm and says so rather than borrowing a number.
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
    """The figures a lane card carries, each drawn only where the producer held one.

    The context bar is the sharpest case: the two terms travel together or not at all, so a
    producer knowing only the occupancy draws the number and no bar. `id` and `branch` sit
    here rather than on the title line, demoted beside the agent that ran them (basicly-0xtzf1).
    """
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
    # A row saying "no value here" spends one on a non-exception; the absence is stated by
    # the row not being there.
    return tuple(cell for cell in drawn if cell.value != UNKNOWN)


def _unit_titles(reads: Mapping[str, Reading]) -> dict[str, str]:
    """Every unit's title, keyed by id - the join `lanes[].id` makes to `units[].title`.

    Both sections ride on every snapshot already, so the join costs no extra read
    (basicly-k6tpep's design of record), on the same rule :func:`_feature_names` reads `graph`.
    """
    units = reads["units"]
    return {
        str(row["id"]): str(row["title"])
        for row in units.dicts
        if row.get("id") and row.get("title")
    }


def _started_ago(lane: Mapping[str, Any], moment: datetime) -> str:
    """The phrase "started {elapsed} ago" from the lane's own stamp, or "" if it named none.

    Never "running for": `elapsed_s` is absent while a lane is live (`board_facts._lane_fact`),
    so this absolute stamp is the only duration left, and the label names what it measures.
    """
    waited = since(lane.get("started_at"), moment)
    return f"started {elapsed(waited)} ago" if waited is not None else ""


def _lane_mark(lane: Mapping[str, Any]) -> tuple[State, str]:
    """The card's colour-and-border pair and the word it leads with (basicly-ncday7).

    Falls back to the liveness bit for a producer that names no state, which is every
    snapshot written before the key existed: the card then reads exactly as it did.
    """
    key, word = LANE_MARKS.get(str(lane.get("state") or ""), ("", ""))
    if not key:
        return (BY_KEY[LIVE] if lane.get("live") else BY_KEY[ABSENT]), ""
    return BY_KEY[key], word


def _in_state_for(lane: Mapping[str, Any], moment: datetime) -> str:
    """How long the lane has held its current state, from `state_since`, or "".

    The duration a reader wants is of the *state*: a landing eight minutes in and one ten
    seconds in are the same word and a different thing to watch.
    """
    waited = since(lane.get("state_since"), moment)
    return elapsed(waited) if waited is not None else ""


def _primary_state(lane: Mapping[str, Any], moment: datetime) -> str:
    """The card's headline: what the lane is doing, since when, and its phase behind that.

    Never the tracker's own status, which does not move while a lane runs and read six
    working lanes as six idle ones (basicly-0xtzf1). Never the phase alone either: `build`
    was what a finished lane waiting for the merge queue, a lane being landed and a lane
    the WIP bound refused all read as (basicly-ncday7), so the pass state leads and the
    phase follows it. No duration for a lane nobody confirms is live and that names no
    state: its stamp would belong to whichever dispatch last ended.
    """
    phase = phase_of(lane) or UNKNOWN
    word = _lane_mark(lane)[1]
    if not word:
        ago = _started_ago(lane, moment) if lane.get("live") else ""
        return f"{phase}{DOT}{ago}" if ago else phase
    held = _in_state_for(lane, moment) or (_started_ago(lane, moment) if lane.get("live") else "")
    return DOT.join(part for part in (word, held, phase) if part)


def _note_line(lane: Mapping[str, Any]) -> str:
    """The card's activity line: why the lane is where it is, then what it last said.

    Unclipped (basicly-0xtzf1): the producer already bounds it, and a second, tighter clip
    here left a card with nothing more to expand to. `state_detail` leads because it is the
    answer - the bound that refused the lane, its place in the landing queue - and the two
    have different authors, so a reader has to be able to tell a refusal from a progress
    line. `not confirmed live` survives only for a producer that names no state at all,
    which is what it was always reporting: an absence, not an idle lane.
    """
    said = str(lane.get("note") or "")
    detail = str(lane.get("state_detail") or "")
    if detail or lane.get("state"):
        return DOT.join(part for part in (detail, said) if part)
    if lane.get("live"):
        return said
    return f"not confirmed live{DOT}{said}" if said else "not confirmed live"


def _card(lane: Mapping[str, Any], titles: Mapping[str, str], moment: datetime) -> Card:
    """One lane's card: its unit title dominant, its id demoted to :func:`_lane_cells`.

    `working` now requires `live` too (basicly-0xtzf1): a parked lane can still carry a
    previous dispatch's `tokens` and `note`, which drew the pulse of one nobody has confirmed.
    """
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
    """The running cards, what was dropped, and the one line that replaces them.

    **No empty slots.** The row used to reserve :data:`FLIGHT_SLOTS` dashed frames so its
    shape held at one lane and at six; on this repository's own wall that is 40% of the
    screen announcing nothing three times, while the ready list beside it truncated every
    title. A green state costs one token, so no lane means no card and the caller collapses
    the row to its note. The cap still holds because six live lanes still have to fit.

    *now* is `board_render.context`'s own injected instant, the same one :func:`band` takes;
    it defaults to the wall clock only for a caller that has none to inject.
    """
    read = reads["lanes"]
    lanes = read.dicts if read.drawn else []
    titles = _unit_titles(reads)
    moment = now or datetime.now(UTC)
    cards = tuple(_card(lane, titles, moment) for lane in lanes[:FLIGHT_SLOTS])
    note = read.note if not read.drawn else _waiting_on(reads, lanes)
    return cards, more(len(lanes) - FLIGHT_SLOTS, "lanes"), note


# What a status means when somebody has taken a record but no lane holds it. The page counted
# `IN PROGRESS 1` three times over and named nothing, so the owner could not tell what was
# being worked on - and the record it counted was not even the one at `build` (basicly-5jkxqk).
CLAIMED_STATUS = "in_progress"

# How many claimed rows are drawn. A claim with no lane is a person working by hand or a pass
# that ended without teardown; more than a few is a filing problem, not a busy factory.
CLAIMED_SLOTS = 4


def claimed(reads: Mapping[str, Reading]) -> tuple[tuple[dict[str, str], ...], str]:
    """Records somebody has claimed that no lane holds, and how many were not drawn.

    **A count with no member named is a count a reader cannot act on.** `IN PROGRESS 1`,
    `BUILD 1` and the diagram's `build 1` were three different populations of size one, and
    the page named none of them: the first was a record resting at `intake`, the second a
    *deferred* record holding a stale worktree, and no lane existed at all (basicly-5jkxqk).

    Rows and not cards. A claimed record has no agent, no branch and no spend, so giving it a
    lane's frame would promise figures that do not exist - which is the distinction
    `basicly-9guj21` asks for in the other direction.
    """
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


# A parked record and how many are drawn. Four, as the claimed row draws: both are a strip
# under the ready list and a fifth of either pushes a ready row off the wall.
PARKED_STATUS = "deferred"
PARKED_SLOTS = 4


def parked(reads: Mapping[str, Reading]) -> tuple[tuple[dict[str, str], ...], str]:
    """Records somebody parked, and how many were not drawn.

    **Nothing on the board drew one.** A deferred record is excluded from the phase counts,
    from the claimed rows and from the ready set, and the only trace it left was the loop
    note's `4 parked, not counted at a phase` - a number with no member named, which is the
    same defect `basicly-5jkxqk` fixed one region over. So a person could park work from a
    terminal and the wall would never show it again (basicly-arxhshr).

    That also gives `record-resume` somewhere to be pressed. A verb whose surface does not
    exist is the state `lane-kill` was in for a month.
    """
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
    """What the pass waits for, in one sentence, or "" while it is moving on its own.

    **The reading the operator reported was "nothing is happening"** (basicly-ncday7): with
    no lane running the region said "no lane is dispatched" and stopped, which is true and
    is not what a person in the room has to know. The answers are ordered by what a reader
    can do about them, and each is read off a section the document already carries so the
    sentence costs no producer field of its own. A lane running or landing needs no sentence:
    the cards are the answer, and a line above them would be a second one.

    **A rung must not answer a question it cannot.** The blocked count fired whenever it was
    non-zero, so an idle factory with a ready set of 231 read `waits on a blocker`, and the
    true answer sat under the diagram in the smallest type on the page (basicly-9guj21).
    Blockers are a cause only when there is nothing else to start.
    """
    if any(_moving(lane) for lane in lanes):
        return ""
    asks = reads["asks"]
    if asks.drawn and asks.dicts:
        return f"waits on a person - {len(asks.dicts)} checkpoint or decision pending"
    held = [str(lane.get("state") or "") for lane in lanes]
    stopped = [state for state in held if state in LANE_MARKS]
    if stopped:
        words = ", ".join(LANE_MARKS[key][1] for key in LANE_MARKS if key in set(stopped))
        return f"waits for the next pass - {len(stopped)} lane(s) {words}"
    backlog = reads["backlog"].fields
    ready = numeric(backlog.get("ready")) or 0
    blocked = numeric(backlog.get("blocked"))
    # Only a cause when there is nothing else to start. With 231 ready the blocked count has
    # no bearing on why nothing runs, and naming it read as a dependency-starved factory that
    # was merely idle - the owner's report, and the note was the reason (basicly-9guj21).
    if blocked and not ready:
        return f"waits on a blocker - {number(int(blocked))} record(s) have an unmet dependency"
    if lanes:
        return "no lane of this pass is running or landing"
    # One expression rather than a seventh return: the arity of this ladder is itself gated.
    return (
        f"no pass is running - {number(int(ready))} record(s) are ready to start"
        if ready
        else "no lane is dispatched"
    )


def _moving(lane: Mapping[str, Any]) -> bool:
    """True when the pass is working this lane without waiting on anybody.

    A lane that names no state falls back to its liveness bit, so every snapshot written
    before the key existed draws exactly as it did: the producer said nothing about the
    pass, and reading that silence as "stopped" would be the same overclaim in reverse.
    """
    state = str(lane.get("state") or "")
    return state in LANE_MOVING or (not state and bool(lane.get("live")))


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


def next_up(
    reads: Mapping[str, Reading],
    *,
    wide: bool = False,
    viewport_height: float | None = None,
    viewport_width: float | None = None,
    reserved: float = 0.0,
) -> Listing:
    """The ready set, ranked, with priority, id and title on each row.

    Three absences are distinguished and not one of them is a zero: the section not emitted,
    the section emitted with no ``ready`` flag on any row, and a flagged set with nothing in
    it. The middle one is the case a count would have reported as "0 ready".

    *wide* is the shape the list takes when no lane is dispatched and the running row gave it
    the width: more rows, and a title bound that fits them. Two shapes rather than one because
    a cap is a promise about a rendered width, and the list has two. *viewport_height*,
    *viewport_width* and *reserved* are :func:`ready_capacity`'s own arguments, threaded
    through rather than read here, because a caller that knows the wall's actual size - and
    what else is being drawn on it this time - is the one with the fact to give.
    """
    read = reads["units"]
    if not read.drawn:
        return Listing(read.state, note=read.note)
    units = read.dicts
    flagged = [unit for unit in units if isinstance(unit.get("ready"), bool)]
    if not flagged:
        return Listing(BY_KEY[ABSENT], note=f"ready {ABSENT_TEXT} on any of the {len(units)} units")
    fits = ready_capacity(viewport_height, viewport_width, reserved)
    # The narrow column is bounded by the viewport too, and not only by its own cap. It was
    # the half of basicly-ffm2yp that never landed: the wide list learned to read the wall
    # and this one kept a fixed eight, so a short wall with lanes running clipped in silence
    # - 116px at 1440x900 on `main` before any of this (basicly-c9crxu). `min`, because the
    # cap is a promise about a rendered *width* and the capacity is one about height; the
    # column owes both.
    slots = fits if wide else min(READY_SLOTS, fits)
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
