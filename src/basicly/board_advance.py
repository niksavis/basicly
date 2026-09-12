from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import board_fields

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

KIND = "advance"

_PARKED = frozenset({"closed", "deferred"})

_DONE = "done"


def newest(markers: Sequence[Any]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for row in markers:
        if row.record and (row.record not in latest or row.at > latest[row.record]):
            latest[row.record] = row.at
    return latest


def remedy(record: str) -> str:
    return f"basicly loop advance {record}"


def _stalled(states: Mapping[str, tuple[str, bool, str]], held: frozenset[str]) -> list[str]:
    return sorted(
        record
        for record, (phase, allowed, status) in states.items()
        if allowed and phase != _DONE and status not in _PARKED and record not in held
    )


def asks(
    states: Mapping[str, tuple[str, bool, str]],
    *,
    lanes: Sequence[Mapping[str, Any]] | None,
    supervised: bool,
    last_event: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:

    if supervised:
        return []
    held = frozenset(
        str(lane.get("id")) for lane in (lanes or []) if isinstance(lane, dict) and lane.get("id")
    )
    built = []
    for record in _stalled(states, held):
        phase = states[record][0]
        ask: dict[str, object] = {
            "wait_id": board_fields.text(f"{record}#advance", board_fields.WAIT_ID_MAX),
            "kind": KIND,
            "issue": board_fields.text(record, board_fields.ID_MAX),
            "subject": board_fields.text(phase, board_fields.TEXT_MAX),
            "question": board_fields.text(
                f"{phase} is cleared and nothing is scheduled to advance it",
                board_fields.QUESTION_MAX,
            ),
        }
        stamp = board_fields.instant((last_event or {}).get(record, ""))
        if stamp is not None:
            ask["requested_at"] = board_fields.stamp(stamp)
        ask["actions"] = [{"offer": remedy(record)}]
        built.append(ask)
    return built
