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
