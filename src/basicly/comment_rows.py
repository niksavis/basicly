from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TEXT_KEY = "text"
STAMP_KEY = "created_at"

TRUNCATED_KEY = f"{TEXT_KEY}_truncated"
ORIGINAL_LENGTH_KEY = f"{TEXT_KEY}_original_length_bytes"


def _cut_markers(payload: Any) -> dict[str, object]:

    original = payload.get(ORIGINAL_LENGTH_KEY)
    if payload.get(TRUNCATED_KEY) is not True or not isinstance(original, int):
        return {}
    return {TRUNCATED_KEY: True, ORIGINAL_LENGTH_KEY: original}


def from_ledger(kit_module: Any, found: Iterable[Any]) -> dict[str, list[dict]]:

    events = list(found)
    ledger_fold = kit_module.events.fold(events)
    rows: dict[str, list[dict]] = {}
    for event in kit_module.events.canonical_order(events):
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
