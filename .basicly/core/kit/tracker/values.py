from __future__ import annotations

import difflib
from collections.abc import Sequence
from typing import Any

WRITABLE_STATUSES = ("open", "in_progress", "blocked", "deferred", "closed")
CLOSED = "closed"
PRIORITY_FIELD = "priority"
PRIORITIES = range(5)
CLOSE_REASON_FIELD = "close_reason"


class RefusedValueError(ValueError):
    pass


def _status(value: object) -> None:

    if value in WRITABLE_STATUSES:
        return
    near = difflib.get_close_matches(str(value).replace("-", "_"), WRITABLE_STATUSES, n=1)
    hint = f"; did you mean {near[0]!r}?" if near else ""
    raise RefusedValueError(
        f"status {value!r} is not one of {', '.join(WRITABLE_STATUSES)}{hint} "
        f"A record at an unknown status is neither ready nor closed, so it would vanish"
    )


def _priority(value: object) -> None:

    if isinstance(value, int) and not isinstance(value, bool) and value in PRIORITIES:
        return
    raise RefusedValueError(
        f"priority {value!r} is not a whole number from 0 (critical) to 4 (backlog); "
        f"the ready ranking reads it as a number"
    )


def _named(events: Any, draft: Any, name: str) -> bool:
    return draft.kind == events.KIND_FIELD and draft.payload.get("name") == name


def refuse(events: Any, drafts: Sequence[Any]) -> None:

    reasoned = {
        draft.record
        for draft in drafts
        if (
            _named(events, draft, CLOSE_REASON_FIELD)
            and str(draft.payload.get("value") or "").strip()
        )
        or (
            draft.kind == events.KIND_CREATED
            and str(draft.payload.get(CLOSE_REASON_FIELD) or "").strip()
        )
    }
    for draft in drafts:
        if draft.kind == events.KIND_STATUS:
            _status(draft.payload.get("status"))
            if draft.payload.get("status") == CLOSED and draft.record not in reasoned:
                raise RefusedValueError(
                    f"closing {draft.record} needs a reason: name what shipped and the evidence, "
                    f"as close --reason; the reason is the permanent record, the diff is not"
                )
        elif _named(events, draft, PRIORITY_FIELD):
            _priority(draft.payload.get("value"))
        elif draft.kind == events.KIND_CREATED and PRIORITY_FIELD in draft.payload:
            _priority(draft.payload[PRIORITY_FIELD])
