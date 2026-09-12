from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from basicly import redact, tracker_argv, tracker_usage
from basicly.owned_store import TrackerDivergenceError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def read_the_seams_own_flags(args: Sequence[str]) -> tuple[list[str], bool]:

    surface, _ = tracker_usage.split_invocation(list(args))
    if unreadable := tracker_argv.unreadable_flags(surface, args):
        raise TrackerDivergenceError(
            f"{surface} reads nothing from {', '.join(unreadable)}, so it would record what "
            f"the rest of {' '.join(args)} says and drop that silently. The flags {surface} "
            f"reads: {', '.join(sorted(tracker_argv.GUARDED_FLAGS[surface]))}"
        )
    kept = tracker_argv.without_flags(args, {tracker_argv.REPEAT_FLAG}, ())
    return kept, len(kept) != len(args)


def at_the_generation_a_repeat_needs(
    kit_module: Any, ledger: Path, drafts: Sequence[Any]
) -> list[Any]:

    events = kit_module.events
    taken = {event.id for event in events.read_events(ledger)[0]} if ledger.is_dir() else set()
    resolved: list[Any] = []
    for draft in drafts:
        stored = events.prepare_payload(
            draft.payload, kind=draft.kind, redact=redact.redact_committed
        )
        generation = 1
        event_id = events.event_id_for(draft.record, draft.kind, stored, generation=generation)
        while event_id in taken:
            generation += 1
            event_id = events.event_id_for(draft.record, draft.kind, stored, generation=generation)
        taken.add(event_id)
        resolved.append(replace(draft, generation=generation))
    return resolved


def _stored_payload(events: Any, draft: Any) -> dict[str, object]:
    return events.prepare_payload(draft.payload, kind=draft.kind, redact=redact.redact_committed)


def at_the_generation_this_write_needs(
    kit_module: Any, ledger: Path, drafts: Sequence[Any], *, repeat: bool
) -> list[Any]:

    if repeat:
        return at_the_generation_a_repeat_needs(kit_module, ledger, drafts)
    return _at_the_generation_a_recurring_state_needs(kit_module, ledger, drafts)


def _at_the_generation_a_recurring_state_needs(
    kit_module: Any, ledger: Path, drafts: Sequence[Any]
) -> list[Any]:

    events = kit_module.events
    existing = events.read_events(ledger)[0] if ledger.is_dir() else []
    taken = {event.id for event in existing}
    state_kinds = {events.KIND_STATUS, events.KIND_FIELD}
    if not any(
        draft.kind in state_kinds
        and events.event_id_for(
            draft.record, draft.kind, _stored_payload(events, draft), generation=1
        )
        in taken
        for draft in drafts
    ):
        return list(drafts)
    repeated = _the_records_this_write_only_repeats(events, existing, drafts)
    moving = [
        index
        for index, draft in enumerate(drafts)
        if draft.kind in state_kinds and draft.record not in repeated
    ]
    resolved = list(drafts)
    moved = at_the_generation_a_repeat_needs(kit_module, ledger, [drafts[i] for i in moving])
    for index, draft in zip(moving, moved, strict=True):
        resolved[index] = draft
    return resolved


def _the_records_this_write_only_repeats(
    events: Any, existing: Sequence[Any], drafts: Sequence[Any]
) -> set[str]:

    stated: dict[str, set[str]] = {}
    for draft in drafts:
        stated.setdefault(draft.record, set()).add(
            events.event_id_for(
                draft.record, draft.kind, _stored_payload(events, draft), generation=1
            )
        )
    newest: dict[str, str] = {}
    for event in events.canonical_order(list(existing)):
        newest[event.record] = event.id
    return {record for record, ids in stated.items() if newest.get(record) in ids}
