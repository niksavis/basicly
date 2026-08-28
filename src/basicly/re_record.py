"""Saying a re-record is deliberate, so a field can return to a value it once held.

An event id digests the record, kind and payload, so a genuine second statement of an
identical fact mints the first one's id and is swallowed; nothing separates the two, so the
intent comes from the caller (basicly-kn4rip).

**Not idempotent, and it cannot be** — the one rule that could collapse a second run is the
digest rule it defeats — so no engine path passes it, a state re-entered on every advance
still having to record one event.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from basicly import redact, tracker_argv, tracker_usage
from basicly.owned_store import TrackerDivergenceError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def read_the_seams_own_flags(args: Sequence[str]) -> tuple[list[str], bool]:
    """*args* without :data:`tracker_argv.REPEAT_FLAG`, and whether it asked for a repeat.

    Stripped rather than translated: it states nothing about the record.

    ``update`` rejects an unknown flag itself, but ``close``, both ``dep`` verbs and ``gate
    report`` **silently drop a miss**, so a misspelled ``--agian`` degrades back into the
    swallow while the caller reads that it landed.

    Raises:
        TrackerDivergenceError: a guarded verb carries a flag it cannot read.
    """
    surface, _ = tracker_usage.split_invocation(list(args))
    if unreadable := tracker_argv.unreadable_flags(surface, args):
        raise TrackerDivergenceError(
            f"{surface} reads nothing from {', '.join(unreadable)}, so it would record what "
            f"the rest of {' '.join(args)} says and drop that silently. The flags {surface} "
            f"reads: {', '.join(sorted(tracker_argv.GUARDED_FLAGS[surface]))}"
        )
    # Read off the strip, so the two cannot disagree about the `--again=x` form.
    kept = tracker_argv.without_flags(args, {tracker_argv.REPEAT_FLAG}, ())
    return kept, len(kept) != len(args)


def at_the_generation_a_repeat_needs(
    kit_module: Any, ledger: Path, drafts: Sequence[Any]
) -> list[Any]:
    """*drafts* each moved to the first generation their content is free at.

    Not any free one: `tracker.scrub_ledger` re-derives the generation as the **dense**
    occurrence count of an identical ``(record, kind, payload)`` and, on the commit path,
    rewrites nothing if one stored id fails to re-mint.

    Walked over ids rather than payload counts, so the canonical-JSON rule is not copied
    here. The payload is prepared as `events.append` prepares it, the id covering the
    *stored* form. Read under the lock, for `owned_write._resolve_labels`' reason.
    """
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
        # Across the batch, not only the file: two identical drafts in one argv are two
        # facts, and the second would otherwise land on the first's id and be skipped.
        taken.add(event_id)
        resolved.append(replace(draft, generation=generation))
    return resolved


def _stored_payload(events: Any, draft: Any) -> dict[str, object]:
    """*draft*'s payload in the form the ledger stores, which is what an id covers."""
    return events.prepare_payload(draft.payload, kind=draft.kind, redact=redact.redact_committed)


def at_the_generation_this_write_needs(
    kit_module: Any, ledger: Path, drafts: Sequence[Any], *, repeat: bool
) -> list[Any]:
    """*drafts* at the generation this write needs, asked for or derived.

    *repeat* is the caller's word that a second identical statement is deliberate, and it
    holds for every kind; the derived half finds a state the record has moved off.
    """
    if repeat:
        return at_the_generation_a_repeat_needs(kit_module, ledger, drafts)
    return _at_the_generation_a_recurring_state_needs(kit_module, ledger, drafts)


def _at_the_generation_a_recurring_state_needs(
    kit_module: Any, ledger: Path, drafts: Sequence[Any]
) -> list[Any]:
    """*drafts* moved off a swallowed id when they state a record's state again.

    The digest keys on content alone, so a value returning to one the record once held
    re-mints the first event's id and is skipped: ``--status open`` after ``deferred`` left
    the record at ``deferred`` while the command reported success. Measured the same on a
    priority driven 2 -> 1 -> 2 and on a label added, removed and added back (basicly-bj8kks).

    **A repeat is still a repeat**, so nothing moves while the record's newest event is one
    this write states: that is one command run twice, which the digest is right to collapse.

    Confined to the kinds that set the record's **own** state, and the cut is the fold's:
    ``status`` and ``field`` are last-write-wins over ``RecordState.status`` and ``.fields``,
    so one skipped leaves the record reading a value nobody asked for. A note accumulates,
    and a gate, an edge or a checkpoint is a row a sibling folds — those keep the digest rule
    and the explicit flag.
    """
    events = kit_module.events
    existing = events.read_events(ledger)[0] if ledger.is_dir() else []
    taken = {event.id for event in existing}
    state_kinds = {events.KIND_STATUS, events.KIND_FIELD}
    # One whole-ledger read, inside the caller's lock: 77 ms against the 93 ms `append`
    # already pays there, measured on this repo's 7,885-event log. It buys the common
    # answer "nothing recurs" — a draft whose id is free is new content either way.
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
    """The records whose newest event is one this write states: nothing has happened since.

    Against the whole write rather than its last draft, because a verb states more than one
    fact and the ledger holds them in the order they *first* landed: ``-p 2 --status open``
    on a record already reading both appended two events when the two were compared
    pairwise, having landed in the other order.
    """
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
