"""A record one allowed advance from moving, that nothing is going to advance.

Owner, 2026-09-02: *"there is 1 in ship - we do not know why it is not drained."* The band
read `NOTHING IS WAITING` and was right about checkpoints: `basicly-b2n2`'s ship checkpoint
was approved on 2026-08-22, and it then sat one `basicly loop advance` from closed for eleven
days. `asks` lists checkpoints and decisions, so the one case a person must act on read as
nothing - and a false calm is the expensive direction.

Three conditions, all needed: the advance is **allowed**, off
:func:`basicly.loop_state.state_map` so the board and `loop status` share one derivation; **no
lane** holds it; and **no supervisor** holds the checkout. The ask offers no runnable verb -
`loop advance` at `build` dispatches an agent and spends a budget - so it names the command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import board_fields

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Deliberately not `checkpoint`: nothing was requested and nobody is being asked anything -
# the engine waits to be told to take a step it has already cleared.
KIND = "advance"

# A closed or deferred record owes nothing; `in_progress` under no lane is the case itself.
_PARKED = frozenset({"closed", "deferred"})

# `derive_phase` has no transition out of `done`; an ask there names a dead command.
_DONE = "done"


def newest(markers: Sequence[Any]) -> dict[str, str]:
    """The newest stamp each record has a marker at; it dates an ask nobody requested."""
    latest: dict[str, str] = {}
    for row in markers:
        if row.record and (row.record not in latest or row.at > latest[row.record]):
            latest[row.record] = row.at
    return latest


def _remedy(record: str) -> str:
    """The exact command that moves *record*, which is the row's whole point."""
    return f"basicly loop advance {record}"


def _stalled(states: Mapping[str, tuple[str, bool, str]], held: frozenset[str]) -> list[str]:
    """Every record whose advance is allowed, that no lane in *held* is going to take."""
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
    """The advance asks for *states*, or nothing where a supervisor is driving the checkout.

    *states* is :func:`basicly.loop_state.state_map`'s, passed in rather than read here: the
    caller already folds it for the unit phases, and a function that reaches for its own
    engine state cannot be handed a population to test against.

    *supervised* refuses the whole population rather than filtering it: this producer cannot
    know a supervisor's selector, so a row per record would name work a pass is about to do.
    *last_event* dates each ask; absent leaves the stamp off rather than aging it from now.
    """
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
        # The stamp only. The consumer ages an ask from its stamp where it has no figure
        # (`board_regions._waited`), through the one function the wall-clock gate counts.
        stamp = board_fields.instant((last_event or {}).get(record, ""))
        if stamp is not None:
            ask["requested_at"] = board_fields.stamp(stamp)
        ask["actions"] = [{"offer": _remedy(record)}]
        built.append(ask)
    return built
