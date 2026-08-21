"""The board producer's field-selection boundary: bounded values, and markers as fields.

**One rule, and this module is it.** *Select fields, never records.* Measured: the whole log
is 5,890,340 B against 44,454 B for the active records at six selected fields, so field
selection alone is **132.5x** while minifying buys 0.1%. So no description, no acceptance
criteria and no raw comment body leaves here - a comment becomes a :class:`Marker`, which is
its family and the ``key=value`` fields its header declares, and every string crossing the
wire comes through :func:`text`. The bounds belong beside that reduction because they are one
rule at two sizes: a value is admitted at the declared width, and prose is not admitted.

The marker roster below is the other half, and :data:`FAMILY_NAMES` carries its reasoning.
The six section reducers that consume all of this are :mod:`basicly.board_sections`; the
boundary is what may cross the wire against which rows a section is.
"""

# comment-density-waiver: cohesion: a 12-member roster, seven bounds and four one-line
# value helpers, so the share is set by the member count and not by narration - the same shape as
# `tracker_paths` and `.scripts/ratchet.py`. Every block states a measurement or a rule a
# reader cannot recover from the code: the 132.5x field-selection figure, the two roster
# shortcuts that are refuted (11 derived, 15 grepped), and why the roster is composed rather
# than spelled. The reducer inventory this text used to carry moved with the reducers
# (basicly-y754k2), which is the second time its count went stale in place.

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import redact

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

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
# `repo.head` is a commit, abbreviated or full, so 40 is the full sha and not a prose bound.
HEAD_MAX = 40
# `asks[].question` is the one value bounded wider than :data:`TEXT_MAX`, and the schema says
# why: it is a prompt somebody in the room has to read, not the marker body it was parsed from.
QUESTION_MAX = 500

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
