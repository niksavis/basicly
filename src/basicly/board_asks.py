"""What a person is being asked for, with the exact command and the form to answer it.

Owner retro 2026-08-23: *"checkpoints that wait on people should be immediately visible and
actionable."* The actions worked; reaching them did not - the panel was appended after
``</main>`` into a ``100vh`` body with ``overflow: hidden``, so 274px was clipped on a page
that never scrolls, and its three forms were blank (basicly-ua9o5g).

One row per offer, **prefilled**: identifiers off the ask, what only a person holds left
empty. The command is built through the action's own ``build``, so text beside a button
cannot name a different command from the one it runs. **Data, never markup** - the template
draws it through the autoescape every producer string goes through.

``asks[].actions[].basicly`` is a closed enum of :data:`basicly.board_actions.ACTIONS`'
verbs, and the schema says an offer this consumer cannot execute is *"drawn without a button
rather than refused"* - so an unknown verb yields no row and the band still draws the ask.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .board_actions import ACTIONS, ROUTE, Action, Field, asked

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# How many rows before the region says how many it dropped. A pending decision outranks the
# ready list, but not without limit: an unbounded region pushes that list off the wall.
ASK_SLOTS = 3

# How wide an input draws, in characters. A prefilled one is sized to what it holds: a
# `wait_id` overran a fixed 14 by 27px, which only the geometry instrument saw. Bounded both
# ways - the floor keeps an empty field clickable, the ceiling keeps one id off the row.
FIELD_MIN = 14
FIELD_MAX = 30
FIELD_FREE = 32

# Where a reply lands, so a submission never navigates the wall away from the board.
RESULT_FRAME = "board-action-result"


def _offers(ask: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """*ask*'s offers: a list of objects, or nothing."""
    held = ask.get("actions")
    return [offer for offer in held if isinstance(offer, dict)] if isinstance(held, list) else []


def _prefill(source: Mapping[str, Any], field: Field) -> str:
    """What the board already knows for *field*, or "" where only a person holds it.

    *source* is an ask, or a lane rendered as one by :func:`killable`.
    """
    if not field.from_ask:
        return ""
    held = source.get(field.from_ask)
    return str(held) if isinstance(held, str) else ""


def _size(field: Field, value: str) -> int:
    """How wide *field* is drawn, in characters, for the value it will actually hold."""
    if field.free:
        return FIELD_FREE
    return max(FIELD_MIN, min(FIELD_MAX, len(value) + 1))


def _command(action: Action, fields: Sequence[Mapping[str, Any]]) -> str:
    """The exact `basicly` line, with `<label>` wherever a value is still owed.

    Through ``build`` rather than spelled again: a command printed beside a button that runs
    a different argv is worse than no command at all.
    """
    values = {str(field["name"]): str(field["value"]) or f"<{field['label']}>" for field in fields}
    return " ".join(("basicly", *action.build(values)))


def _form(action: Action, source: Mapping[str, Any], token: str) -> dict[str, Any]:
    """The parts of a row that are the same wherever the offer came from.

    Shared with :func:`killable`: the printed command must come from `build` at every site,
    or a second surface grows its own idea of the argv.
    """
    fields = _fields(action, source)
    return {
        "token": token,
        "route": ROUTE,
        "frame": RESULT_FRAME,
        "command": _command(action, fields),
        # Per row and not once for the region: two of the four actions need a code.
        "confirmed": action.confirmed,
        "fields": fields,
    }


def _row(ask: Mapping[str, Any], offer: Mapping[str, Any], token: str) -> dict[str, Any] | None:
    """One prefilled form for *offer* on *ask*, or None where this board cannot run it."""
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
    """*action*'s inputs, prefilled from *source*, each sized for what it will hold."""
    return [
        {
            "name": field.name,
            "label": field.label,
            "value": _prefill(source, field),
            # What the operator still owes; a form whose filled and empty inputs look
            # alike is one read field by field.
            "typed": not _prefill(source, field),
            # Prose, so the input draws wide. The flag the validator bounds by length.
            "free": field.free,
            "size": _size(field, _prefill(source, field)),
        }
        for field in asked(action)
    ]


# The verb a running lane offers, spelled at the one site that uses it.
KILL = "lane-kill"


def killable(
    lanes: Sequence[Mapping[str, Any]] | None, token: str | None
) -> dict[str, dict[str, Any]]:
    """A kill form per running lane, keyed by lane id, for the card that draws that lane.

    `lane-kill` was in `ACTIONS` from the start while `pending` builds rows from `asks[]`
    only, so it was reachable exactly when something *else* waited (basicly-x1h1dl5).

    Keyed by id and not by order: the cards are `lanes[:FLIGHT_SLOTS]` while this is every
    lane, so a positional join pairs a form with the wrong card once that bound bites.
    `board_wall.Card.ident` is the key's other half. Empty yields nothing.
    """
    action = ACTIONS.get(KILL)
    if not lanes or action is None:
        return {}
    forms: dict[str, dict[str, Any]] = {}
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        # `from_ask` names ask keys, so the lane is presented under them.
        ident = str(lane.get("id") or "")
        if ident:
            forms[ident] = {
                **_form(action, {"issue": ident}, token or ""),
                "action": KILL,
                "offer": action.label,
                "issue": ident,
            }
    return forms


def pending(
    asks: Sequence[Mapping[str, Any]] | None, token: str | None
) -> tuple[tuple[dict[str, Any], ...], int]:
    """The actionable rows and how many were dropped, in the order the producer asked them.

    *token* is None where there is no server to post to: ``--no-actions``, and the static
    ``--out`` artifact. The rows are still built and still carry the command - an operator
    who cannot press a button is owed the line to type - and the template draws no form.
    """
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
