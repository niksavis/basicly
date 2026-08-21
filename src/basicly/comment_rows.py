"""One comment history, as the rows every marker reader parses.

Either store's answer in ``br comments list --json``'s shape: br's own reply on one
side, a fold of the owned ledger's prose events on the other. One shape, so a
caller parses one thing and the flip is a change of source rather than of contract.

The boundary is *the rows* against *the store*: nothing here decides which store is
authoritative, loads a kit, or reads a path. The kit module and the events arrive as
parameters, the shape :mod:`basicly.mirror` takes and for the same reason — no import
back into the seam that calls it. Split out of ``br`` when the module-size ratchet
caught that module growing (`basicly-wpc8.1`).
"""

# comment-density-waiver: cohesion: 67.3% of a 1063-token module [re-measured 2026-08-18 after two
# lanes merged here], and what is left after a cutting pass is the four rules a reader would
# otherwise re-derive wrongly: canonical order (two readers depend on oldest-first),
# tombstone-as-absent (the stores spell a deletion differently), raise-rather-than-empty (an
# unreadable tracker must not read as "nothing is blocking"), and both-or-neither on the cap's
# markers. Each was carried out of `br` with the reason it exists, except the last, which
# arrived with `basicly-wug2o2`.

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# The two keys a row carries, in br's spelling. The ledger holds the body under the same
# ``text`` key and the stamp as the event's ``ts``, so rendering is a rename of one field
# rather than a second shape for a caller to learn.
TEXT_KEY = "text"
STAMP_KEY = "created_at"

# The two markers the event cap writes beside a body it cut, spelled as
# ``events._prepare_entry`` derives them from the key it capped.
TRUNCATED_KEY = f"{TEXT_KEY}_truncated"
ORIGINAL_LENGTH_KEY = f"{TEXT_KEY}_original_length_bytes"


def _cut_markers(payload: Any) -> dict[str, object]:
    """The cap's markers off *payload*, or nothing when the body was stored whole.

    Carried because the reader that refuses a cut body is the only one that can say *why*
    it is cut, and the original size is recorded here and nowhere else. Measured
    2026-08-18: 23 of this repo's 47 stored artifact record/kind pairs are cut, and
    without these two keys each was refused as a malformed JSON fragment instead.

    Both or neither. ``events._prepare_entry`` writes the flag and the length in one
    dict, so a flag standing alone describes no size a reader could act on — and an
    absent pair must leave the row at exactly the two keys three readers parse.
    """
    original = payload.get(ORIGINAL_LENGTH_KEY)
    if payload.get(TRUNCATED_KEY) is not True or not isinstance(original, int):
        return {}
    return {TRUNCATED_KEY: True, ORIGINAL_LENGTH_KEY: original}


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
        # Both spellings: keying on `note` alone would drop the markers on 2,667 `comment`
        # events this ledger already holds (basicly-vkh0.30).
        if event.kind not in kit_module.events.PROSE_KINDS:
            continue
        state = ledger_fold.records.get(event.record)
        if state is not None and state.tombstoned:
            continue
        text = event.payload.get(TEXT_KEY)
        if not isinstance(text, str):
            continue
        row: dict[str, object] = {TEXT_KEY: text, STAMP_KEY: event.ts}
        row.update(_cut_markers(event.payload))
        rows.setdefault(event.record, []).append(row)
    return rows
