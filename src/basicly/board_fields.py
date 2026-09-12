from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import redact

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

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

WAIT_FAMILY = _FAMILY.format("wait")

ANSWERED = "answered"

ID_MAX = 120
WAIT_ID_MAX = 200
TEXT_MAX = 200
KIND_MAX = 40
NAME_MAX = 80
AGENT_MAX = 60
PRIORITY_MAX = 16
HEAD_MAX = 40
QUESTION_MAX = 500

_MARKER = re.compile(r"^(\[harness-[a-z][a-z-]*\])(.*)")

_FLAG = re.compile(r"^[a-z][a-z0-9_]*$")

_COMMENT_KIND = "comment"
_SUBJECT_SEP = "#wait-"


@dataclass(frozen=True)
class Marker:
    record: str
    at: str
    family: str
    fields: Mapping[str, str]
    flags: frozenset[str]


def text(value: object, limit: int) -> str:
    return redact.redact_committed(str(value))[:limit]


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def instant(value: str) -> datetime | None:

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def marker(record: str, at: str, body: str) -> Marker | None:
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

    found = (
        marker(event.record, event.ts, str(event.payload.get("text") or ""))
        for event in events
        if getattr(event, "kind", "") == _COMMENT_KIND
    )
    return [row for row in found if row is not None]
