from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import board_record
from .board_actions import ACTIONS, ROUTE, START_ACTION, Action, Field, asked, start_command

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ASK_SLOTS = 3

FIELD_MIN = 14
FIELD_MAX = 30
FIELD_FREE = 32

RESULT_FRAME = "board-action-result"


def _offers(ask: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    held = ask.get("actions")
    return [offer for offer in held if isinstance(offer, dict)] if isinstance(held, list) else []


def _prefill(source: Mapping[str, Any], field: Field) -> str:

    if not field.from_ask:
        return ""
    held = source.get(field.from_ask)
    return str(held) if isinstance(held, str) else ""


def _size(field: Field, value: str) -> int:
    if field.free:
        return FIELD_FREE
    return max(FIELD_MIN, min(FIELD_MAX, len(value) + 1))


def _command(action: Action, fields: Sequence[Mapping[str, Any]]) -> str:

    values = {str(field["name"]): str(field["value"]) or f"<{field['label']}>" for field in fields}
    return " ".join(("basicly", *action.build(values)))


def _form(action: Action, source: Mapping[str, Any], token: str) -> dict[str, Any]:

    fields = _fields(action, source)
    return {
        "token": token,
        "route": ROUTE,
        "frame": RESULT_FRAME,
        "command": _command(action, fields),
        "confirmed": action.confirmed,
        "fields": fields,
    }


def _row(ask: Mapping[str, Any], offer: Mapping[str, Any], token: str) -> dict[str, Any] | None:
    action = ACTIONS.get(str(offer.get("basicly") or ""))
    if action is None:
        return None
    return {
        **_form(action, ask, token),
        "action": str(offer.get("basicly")),
        "offer": str(offer.get("offer") or action.label),
        "issue": str(ask.get("issue") or ""),
        "kind": str(ask.get("kind") or ""),
        "subject": str(ask.get("subject") or ""),
        "question": str(ask.get("question") or ""),
        "waiting_s": ask.get("waiting_s"),
        "requested_at": ask.get("requested_at"),
    }


def _fields(action: Action, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": field.name,
            "label": field.label,
            "value": _prefill(source, field),
            "typed": not _prefill(source, field),
            "free": field.free,
            "size": _size(field, _prefill(source, field)),
        }
        for field in asked(action)
    ]


KILL = "lane-kill"


def killable(
    lanes: Sequence[Mapping[str, Any]] | None, token: str | None
) -> dict[str, dict[str, Any]]:

    action = ACTIONS.get(KILL)
    if not lanes or action is None:
        return {}
    forms: dict[str, dict[str, Any]] = {}
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        ident = str(lane.get("id") or "")
        if ident:
            forms[ident] = {
                **_form(action, {"issue": ident}, token or ""),
                "action": KILL,
                "offer": action.label,
                "issue": ident,
            }
    return forms


PARK = "record-park"
RESUME = "record-resume"
PARKABLE = frozenset({"open", "in_progress"})


def parking(
    units: Sequence[Mapping[str, Any]] | None, token: str | None
) -> dict[str, dict[str, Any]]:

    if not units:
        return {}
    forms: dict[str, dict[str, Any]] = {}
    for unit in units:
        if not isinstance(unit, dict):
            continue
        ident, state = str(unit.get("id") or ""), str(unit.get("status") or "")
        verb = PARK if state in PARKABLE else RESUME if state == "deferred" else ""
        action = ACTIONS.get(verb)
        if not ident or action is None:
            continue
        forms[ident] = {
            **_form(action, {"issue": ident}, token or ""),
            "action": verb,
            "offer": action.label,
            "issue": ident,
        }
    return forms


START = START_ACTION


def _as_ask(action: Action, built: Mapping[str, str]) -> dict[str, str]:

    return {field.from_ask: built[field.name] for field in action.fields if built.get(field.name)}


def starting(document: Mapping[str, Any] | None, token: str | None) -> dict[str, dict[str, Any]]:

    action = ACTIONS.get(START)
    if not document or action is None:
        return {}
    lanes = {
        str(lane.get("id") or ""): lane
        for lane in document.get("lanes") or []
        if isinstance(lane, dict)
    }
    forms: dict[str, dict[str, Any]] = {}
    for unit in document.get("units") or []:
        if not isinstance(unit, dict):
            continue
        ident = str(unit.get("id") or "")
        if not ident or not board_record.startable(unit, lanes.get(ident)):
            continue
        built = board_record.start_form(document, ident)
        forms[ident] = {
            **_form(action, _as_ask(action, built), token or ""),
            "command": start_command(built),
            "action": START,
            "offer": action.label,
            "issue": ident,
        }
    return forms


def pending(
    asks: Sequence[Mapping[str, Any]] | None, token: str | None
) -> tuple[tuple[dict[str, Any], ...], int]:

    if not asks:
        return (), 0
    rows = [
        row
        for ask in asks
        if isinstance(ask, dict)
        for offer in _offers(ask)
        if (row := _row(ask, offer, token or "")) is not None
    ]
    return tuple(rows[:ASK_SLOTS]), max(0, len(rows) - ASK_SLOTS)
