"""The board producer's field-selection boundary: bounded values, and markers as fields.

**One rule, and this module is it.** *Select fields, never records.* Measured: the whole log
is 5,890,340 B against 44,454 B for the active records at six selected fields, so field
selection alone is **132.5x** while minifying buys 0.1%. So no description, no acceptance
criteria and no raw comment body leaves here - a comment becomes a :class:`Marker`, which is
its family and the ``key=value`` fields its header declares, and every string crossing the
wire comes through :func:`text`. The bounds belong beside that reduction because they are one
rule at two sizes: a value is admitted at the declared width, and prose is not admitted.

The marker roster below is the other half, and :data:`FAMILY_NAMES` carries its reasoning.
:class:`LaneFacts` is the exception that proves the rule: a lane's phase is not selected from
anything, because no file this producer opens holds it.
"""

# comment-density-waiver: 1665 tokens of code - a 12-member roster, three one-line value
# helpers, six small reducers and one facts record - so the share is set by the member count
# and not by narration, the same shape as `tracker_paths` and `.scripts/ratchet.py`. Every
# block states a measurement or a rule a reader cannot recover from the code: the 132.5x
# field-selection figure, the two roster shortcuts that are refuted (11 derived, 15 grepped),
# why the roster is composed rather than spelled, the 140/203/1 pairing criterion, and why a
# lane phase is an argument rather than a derivation.

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import redact

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

# The roster of `[harness-*]` families, as **suffixes**, and three facts decide both halves
# of that.
#
# *Which families*: all 12 ever written, which is the 11 the engine declares plus the retired
# `[harness-overrun]`. Derived from the live constants the set is 11 and that family's 12 rows
# render as nothing; grepped out of the tree it is 15, because `[harness-side]`,
# `[harness-estimate]` and `[harness-conflict]` occur only inside record prose. So it is
# frozen, `.scripts/check_marker_families.FROZEN` is the authority, and
# `tests/test_board_fields.py` binds the two by loading that gate **by file path** - an
# import is impossible, since `.scripts/` is no package and its gates import *into*
# `basicly`, so importing one would put a gate script on the engine's import path.
#
# *Why suffixes*: that same gate counts a family as **declared** wherever a `[harness-...]`
# string constant appears under `src/`, because that is where a writer spells one. This module
# only reads, so a literal roster tells the gate `[harness-overrun]` has a producer again -
# false, and it reports it. Composing keeps the declared population honest.
#
# Nothing here branches on live-versus-retired: that distinction governs writing.
_FAMILY = "[harness-{}]"

FAMILY_NAMES = (
    "artifact",
    "classification",
    "cost",
    "decision",
    "info",
    "overrun",
    "policy",
    "retro",
    "review",
    "run",
    "sizing",
    "wait",
)

MARKER_FAMILIES: frozenset[str] = frozenset(_FAMILY.format(name) for name in FAMILY_NAMES)

# The family carrying an ask. Composed rather than imported from `policy`, which sits nine
# tiers higher; the string is gate-bound either way.
WAIT_FAMILY = _FAMILY.format("wait")

ANSWERED = "answered"

# The schema's own bounds, at the properties this producer fills. Honoured here rather than
# left to the validator: an over-long string withholds its whole section, and a withheld panel
# is a worse answer than a truncated string.
ID_MAX = 120
WAIT_ID_MAX = 200
TEXT_MAX = 200
KIND_MAX = 40
NAME_MAX = 80
AGENT_MAX = 60
PRIORITY_MAX = 16

# The family, then the rest of its first line. The character class is the roster gate's own, so
# a malformed marker fails to match and is skipped rather than raising - the best-effort
# contract `policy._parse_wait_event` already keeps.
_MARKER = re.compile(r"^(\[harness-[a-z][a-z-]*\])(.*)")

# A bare token in a marker header: `answered`, `requested`. Bounded so a sentence fragment
# cannot enter the flag set.
_FLAG = re.compile(r"^[a-z][a-z0-9_]*$")

_COMMENT_KIND = "comment"
_SUBJECT_SEP = "#wait-"


@dataclass(frozen=True)
class Marker:
    """One `[harness-*]` comment reduced to its family and the fields its header declares."""

    record: str
    at: str
    family: str
    fields: Mapping[str, str]
    flags: frozenset[str]


@dataclass(frozen=True)
class LaneFacts:
    """One in-flight lane, supplied by a caller that drives the loop rather than derived.

    **`phase` is why this is an argument.** The schema requires it, and its authority is
    `loop_state.read_node_state`, which calls `validate_gate.required_config` to learn what
    the unit owes before it reads a gate. That required-gate set is a fourth source, outside
    the three files this producer opens, and a phase folded out of ledger evidence alone
    diverges from the engine's for any unit owing validation. The schema has no field marking
    a value as derived, so the two would render identically. Caller-supplied or omitted, and
    nothing between - the same rule the supervisor lock takes in `board_snapshot.SessionFacts`.

    The eight fields are exactly what a caller above the loop already holds:
    `supervise.LaneView` carries `issue_id`, `status`, `last_agent`, `live`, `last_tokens`,
    `branch` and `last_run_at`, and the phase comes from the loop read beside it. The schema's
    other lane properties - `model`, `cost_usd`, `elapsed_s`, the `context_used` pair, the
    rework counters, `note` - are unemitted until a caller holds them, because an omitted
    property renders as absent while a guessed one renders as fact.

    Lives here beside :func:`lanes`, the reducer that consumes it, for the reason
    `SessionFacts` lives beside `_session`: a facts record and its reducer are one thing.
    """

    id: str
    phase: str
    status: str = ""
    agent: str = ""
    live: bool | None = None
    started_at: str = ""
    tokens: int | None = None
    branch: str = ""


def text(value: object, limit: int) -> str:
    """*value* as a redacted string, truncated to *limit*. Every string passes through here."""
    return redact.redact_committed(str(value))[:limit]


def stamp(moment: datetime) -> str:
    """*moment* as the RFC3339 UTC instant the schema's ``instant`` pattern admits."""
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def instant(value: str) -> datetime | None:
    """A ledger timestamp as an aware instant, or None when it will not parse.

    A naive stamp is read as UTC, ``policy._parse_ts``'s rule: guessing the local zone would
    turn a missing suffix into an hours-wrong interval.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def marker(record: str, at: str, body: str) -> Marker | None:
    """*body* as a :class:`Marker`, or None when it is not a marker this roster knows."""
    found = _MARKER.match(body.strip())
    if found is None or found[1] not in MARKER_FAMILIES:
        return None
    fields: dict[str, str] = {}
    flags: set[str] = set()
    for token in found[2].split():
        if "=" in token:
            name, _, value = token.partition("=")
            fields[name] = value
        elif _FLAG.match(token):
            flags.add(token)
    return Marker(record, at, found[1], fields, frozenset(flags))


def read_markers(events: Iterable[Any]) -> list[Marker]:
    """Every marker comment in *events*, in the order they were read.

    Takes the caller's already-read event list, never the log: one read is the whole point.
    """
    found = (
        marker(event.record, event.ts, str(event.payload.get("text") or ""))
        for event in events
        if getattr(event, "kind", "") == _COMMENT_KIND
    )
    return [row for row in found if row is not None]


def asks(markers: Sequence[Marker]) -> list[dict[str, object]]:
    """The pending asks: a wait whose id no marker anywhere reports as answered.

    **Pairing, not counting, and that is the whole criterion.** Reading every request as
    pending reports **140** against **1** genuinely open, with **203** distinct answered ids
    behind it - so a parser matching nothing still looks plausible on the pending count, and
    the test pins all three. Order-independent, ``policy._open_wait_stamp``'s rule: an answer
    anywhere closes the wait, and comment order is not chronological.

    No ``waiting_s``: the schema leaves it optional, a consumer holding ``requested_at`` and
    the document's ``generated_at`` has it exactly, and subtracting two wall-clock readings in
    the engine is what ``test_no_engine_interval_is_measured_on_a_wall_clock`` refuses.
    """
    waits = [row for row in markers if row.family == WAIT_FAMILY]
    answered = {row.fields["id"] for row in waits if "id" in row.fields and ANSWERED in row.flags}
    pending = []
    for row in waits:
        wait_id = row.fields.get("id", "")
        kind = row.fields.get("kind", "")
        requested = instant(row.at)
        if not wait_id or not kind or wait_id in answered or requested is None:
            continue
        ask: dict[str, object] = {
            "wait_id": text(wait_id, WAIT_ID_MAX),
            "kind": text(kind, KIND_MAX),
            "requested_at": stamp(requested),
            "issue": text(row.record, ID_MAX),
        }
        if subject := wait_id.partition(_SUBJECT_SEP)[2]:
            ask["subject"] = text(subject, TEXT_MAX)
        pending.append(ask)
    return pending


def lanes(facts: Iterable[LaneFacts]) -> list[dict[str, object]]:
    """*facts* as bounded lane rows, one per lane, in the order the caller supplied.

    A lane missing either required value is skipped rather than completed: `id` and `phase`
    are the two the schema refuses a row without, and a row invented for a lane whose phase
    the caller could not read is the estimate :class:`LaneFacts` exists to refuse. Every
    other value is emitted only when the caller held one, so no lane panel draws a zero it
    was not given. An empty result is still a section: `[]` is a pass with nothing running,
    and *absent* is a producer that cannot see lanes - the schema separates the two.
    """
    rows = []
    for lane in facts:
        if not lane.id or not lane.phase:
            continue
        row: dict[str, object] = {"id": text(lane.id, ID_MAX), "phase": text(lane.phase, KIND_MAX)}
        if lane.status:
            row["status"] = text(lane.status, KIND_MAX)
        if lane.agent:
            row["agent"] = text(lane.agent, AGENT_MAX)
        if lane.live is not None:
            row["live"] = lane.live
        if (started := instant(lane.started_at)) is not None:
            row["started_at"] = stamp(started)
        if lane.tokens is not None:
            row["tokens"] = max(0, lane.tokens)
        if lane.branch:
            row["branch"] = text(lane.branch, TEXT_MAX)
        rows.append(row)
    return rows


def units(states: Iterable[Any]) -> list[dict[str, object]]:
    """One bounded row per folded record in *states*, at the five fields a board draws.

    **This is the rule at its sharpest: fields, never records.** A folded record carries its
    description, its acceptance criteria and every comment body, and a row shaped like one
    would put 1,472,207 tokens on the wire against 11,113 for the selection - the 132.5x this
    module exists for. `title` is the only prose admitted and it is bounded, so a description
    cannot arrive by being called a title.

    Two properties the schema offers are deliberately not filled. `phase` has the same
    authority problem :class:`LaneFacts` documents, and `ready` is the tracker's own
    derivation over a status vocabulary and the whole edge population - the same reason
    `backlog` carries no `ready` or `blocked`. A second spelling of either here is how two
    derivations come to disagree, so both stay absent until a caller supplies them.
    """
    rows = []
    for state in states:
        row: dict[str, object] = {"id": text(state.record, ID_MAX)}
        if title := state.fields.get("title"):
            row["title"] = text(title, TEXT_MAX)
        if state.status:
            row["status"] = text(state.status, KIND_MAX)
        priority = state.fields.get("priority")
        if isinstance(priority, int) and not isinstance(priority, bool):
            row["priority"] = text(f"P{priority}", PRIORITY_MAX)
        if kind := state.fields.get("issue_type"):
            row["type"] = text(kind, KIND_MAX)
        rows.append(row)
    return rows


def edge_triples(kit: Any, collected: Iterable[Any]) -> list[tuple[str, str, str]]:
    """Every edge the log still asserts, as ``(source, kind, target)``, last statement wins.

    **Read off the events rather than through the kit's own `views_from_events`, and the
    reason is the one guarantee this producer sells.** That function folds the log a second
    time to answer this, and a second fold is exactly what makes `observe()` cost 6.1 s over
    93 of them. So the caller's already-read event list is walked once more here, which is a
    pass and not a fold.

    Nothing about the dialect is respelled: the kinds, the payload keys and the ordering are
    all *kit* values reached through the sanctioned attribute chain, and
    `tests/test_board_snapshot.py` binds the result to `views_from_events`'s own edge set on
    a corpus holding a retraction - the shape `test_tracker_query` already holds two
    producers of one answer to. A retracted edge is therefore absent here while both of its
    events stay in the log, which is what makes a retraction a retraction and not a deletion.
    """
    held: dict[tuple[str, str, str], bool] = {}
    for event in kit.events.canonical_order(collected):
        if event.kind not in (kit.events.KIND_EDGE, kit.events.KIND_EDGE_RETRACTED):
            continue
        target = event.payload.get(kit.migrate.EDGE_TO)
        kind = event.payload.get(kit.migrate.EDGE_TYPE)
        if isinstance(target, str) and isinstance(kind, str):
            held[(event.record, kind, target)] = event.kind == kit.events.KIND_EDGE
    return [edge for edge, asserted in held.items() if asserted]


def graph(triples: Iterable[tuple[str, str, str]]) -> dict[str, object]:
    """*triples* as the `graph` section: `from`, `to` and the edge kind, each bounded.

    Separate from :func:`units` because edges answer the one question a count of blocked
    items raises and cannot settle. The kind is passed through rather than mapped: the schema
    leaves it an open string, so a foreign harness's own vocabulary crosses unaltered.
    """
    return {
        "edges": [
            {"from": text(source, ID_MAX), "to": text(target, ID_MAX), "kind": text(kind, KIND_MAX)}
            for source, kind, target in triples
        ]
    }


def events(markers: Sequence[Marker], limit: int) -> list[dict[str, object]]:
    """The last *limit* marker rows, each as its family and its declared fields.

    The family is the event kind, which is where the whole roster is used and why all 12 are
    parsed. ``text`` is the header's fields re-rendered, never the body.
    """
    rows = []
    for row in markers[-limit:]:
        at = instant(row.at)
        if at is None:
            continue
        declared = " ".join(f"{name}={value}" for name, value in sorted(row.fields.items()))
        rows.append({
            "at": stamp(at),
            "issue": text(row.record, ID_MAX),
            "kind": text(row.family.strip("[]"), KIND_MAX),
            "text": text(" ".join([*sorted(row.flags), declared]).strip(), TEXT_MAX),
        })
    return rows
