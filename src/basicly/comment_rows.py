"""One comment history, as the rows every marker reader parses.

Either store's answer in ``br comments list --json``'s shape: br's own reply on one
side, a fold of the owned ledger's ``comment`` events on the other. One shape, so a
caller parses one thing and the flip is a change of source rather than of contract.

The boundary is *the rows* against *the store*: nothing here decides which store is
authoritative, loads a kit, or reads a path. The kit module and the events arrive as
parameters, the shape :mod:`basicly.mirror` takes and for the same reason — no import
back into the seam that calls it. Split out of ``br`` when the module-size ratchet
caught that module growing (`basicly-wpc8.1`).
"""

# comment-density-waiver: 64.3% of a 962-token module, and what is left after a cutting
# pass is the three rules a reader would otherwise re-derive wrongly: canonical order
# (two readers depend on oldest-first), tombstone-as-absent (the stores spell a deletion
# differently), and raise-rather-than-empty (an unreadable tracker must not read as
# "nothing is blocking"). Each was carried out of `br` with the reason it exists.

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# The two keys a row carries, in br's spelling. The ledger holds the body under the same
# ``text`` key (`events.KIND_COMMENT`) and the stamp as the event's ``ts``, so rendering
# is a rename of one field rather than a second shape for a caller to learn.
TEXT_KEY = "text"
STAMP_KEY = "created_at"


def from_ledger(kit_module: Any, found: Iterable[Any]) -> dict[str, list[dict]]:
    """Every record's comments in *found*, keyed by record, oldest-first.

    Canonical order — ``(record, seq, id)`` — rather than file order, so the rows come
    back oldest-first however the log was concatenated: `decisions` documents its
    per-bead read as oldest-first, and `policy`'s wait clock takes the *first* stamp it
    sees for a request.

    **A tombstoned record answers empty**, the rule ``tracker.owned_record`` states for the
    same reason: the two stores spell a deletion differently, and a reader served a
    deleted bead's markers would count rework on work somebody removed.
    """
    events = list(found)
    ledger_fold = kit_module.events.fold(events)
    rows: dict[str, list[dict]] = {}
    for event in kit_module.events.canonical_order(events):
        if event.kind != kit_module.events.KIND_COMMENT:
            continue
        state = ledger_fold.records.get(event.record)
        if state is not None and state.tombstoned:
            continue
        text = event.payload.get(TEXT_KEY)
        if not isinstance(text, str):
            continue
        rows.setdefault(event.record, []).append({TEXT_KEY: text, STAMP_KEY: event.ts})
    return rows
