"""One write the engine makes itself, appended to the owned ledger with no process spawned.

The flipped half of the write path (basicly-wpc8): :mod:`basicly.mirror` translates a write
the external tracker has *already* accepted, and everything here records one no external
tracker saw. Both go through ``mirror.drafts``, so what a write means cannot depend on which
store took it.

The boundary is *the append* against :mod:`basicly.tracker`, which decides which store a write
reaches and refuses one inside a read-only section.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from basicly import mirror, owned_store, redact, tracker_argv
from basicly.owned_store import TrackerDivergenceError

# How a write the engine made itself says it got here — as against one the dual write
# mirrored (:data:`mirror.MIRROR_PROVENANCE`) and one `migrate.py` extracted out of the
# export. One of `migrate.RESERVED_KEYS`, so it is dropped when a record is rendered back.
OWNED_PROVENANCE = "engine"


def _stamped(kit_module: Any, drafts: Sequence[Any]) -> list[Any]:
    """*drafts* restamped as the engine's own write rather than a mirrored one.

    ``replace``, not a fresh draft: a field the translator sets and this function does not
    know about has to survive the way through.
    """
    return [
        replace(
            draft,
            payload={**draft.payload, kit_module.migrate.PROVENANCE_KEY: OWNED_PROVENANCE},
        )
        for draft in drafts
    ]


def _current_labels(kit_module: Any, ledger: Path, record: str) -> list[str]:
    """The labels *record* holds right now, in the order the ledger stores them."""
    if not ledger.is_dir():
        return []
    state = kit_module.events.fold(kit_module.events.read_events(ledger)[0]).records.get(record)
    held = state.fields.get(tracker_argv.LABELS_FIELD) if state is not None else None
    return list(tracker_argv.labels_of(held))


def _resolve_labels(kit_module: Any, ledger: Path, args: Sequence[str]) -> list[str]:
    """*args* with every accumulating label flag replaced by one ``--labels`` set.

    A ``field`` event carries the whole value, so ``--add-label`` has to be applied to the
    labels the record already holds. Read and rewrite happen under the caller's lock, which
    is why this takes the ledger rather than reading it a second time: two lanes labelling
    one record concurrently would otherwise each write the set the other did not see.

    Order is preserved rather than sorted — a label set is what an operator typed, and a
    reordering shows up as a change in every differential that compares the field.

    Raises:
        TrackerDivergenceError: the update names no record to accumulate against.
    """
    pairs = tracker_argv.flag_pairs(args, tracker_argv.VALUE_FLAGS["update"])
    if not any(flag in tracker_argv.UPDATE_LABEL_FLAGS for flag, _ in pairs):
        return list(args)
    records = tracker_argv.positionals(args, tracker_argv.VALUE_FLAGS["update"])[1:]
    if not records:
        raise TrackerDivergenceError(f"update names no issue to label: {' '.join(args)}")
    if len(records) != 1:
        raise TrackerDivergenceError(
            f"a label write accumulates against one record's own set, and "
            f"{' '.join(args)} names {len(records)}; issue one write per record"
        )
    labels = _current_labels(kit_module, ledger, records[0])
    for flag, value in pairs:
        adding = tracker_argv.UPDATE_LABEL_FLAGS.get(flag)
        if adding is None:
            continue
        for name in (part.strip() for part in value.split(tracker_argv.LABEL_SEPARATOR)):
            if not name:
                continue
            if adding and name not in labels:
                labels.append(name)
            elif not adding and name in labels:
                labels.remove(name)
    return [*_without_label_flags(args), "--labels", tracker_argv.LABEL_SEPARATOR.join(labels)]


def _without_label_flags(args: Sequence[str]) -> list[str]:
    """*args* with each label flag and the value it consumes dropped."""
    kept: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        name, sep, _ = arg.partition("=")
        if name in tracker_argv.UPDATE_LABEL_FLAGS:
            skip = not sep
            continue
        kept.append(arg)
    return kept


def _refuse_a_write_to_an_absent_record(
    kit_module: Any, ledger: Path, args: Sequence[str], drafts: Sequence[Any]
) -> None:
    """Refuse a write naming a record the ledger does not hold.

    :func:`_refuse_a_retraction_of_an_absent_edge`'s reason, one level out: a typo in an id
    otherwise reads as a successful write. The cost is worse here than a no-op, and that is
    what makes it an error — a tolerant write **folds the mistyped id into existence** as a
    record no ``create`` ever minted, carrying whichever half-fact the argv stated. Measured
    2026-08-20 on a seeded ledger: all five verbs that come through :func:`append` accepted
    an absent id and appended one event for it.

    Idempotence survives because a record's existence only ever moves one way: a delete
    leaves a tombstone and the record stays in the fold (``events.RecordState.tombstoned``),
    so no engine path that re-enters a state on every advance can meet this refusal on a
    later pass having got past it on the first. Unlike a retraction, this refuses nothing
    that once succeeded.

    Read under the caller's lock, for `_resolve_labels`' reason — the record set a write is
    checked against has to be the set the append lands on. ``create`` is the one write with
    no record to find, and it is exempt by construction: it mints its id in :func:`create`
    and never comes through here.

    An edge's *target* is not checked. A dangling target is a different claim from a write
    to nothing, and `merge` and `supervise` both add edges best-effort.

    Raises:
        TrackerDivergenceError: a write names a record the ledger does not hold.
    """
    if not drafts:
        return
    events = kit_module.events
    held = events.fold(events.read_events(ledger)[0]).records if ledger.is_dir() else {}
    missing = [record for record in dict.fromkeys(d.record for d in drafts) if record not in held]
    if missing:
        raise TrackerDivergenceError(
            f"{' '.join(args)} names a record the ledger does not hold: {', '.join(missing)}. "
            f"Accepting it would fold that id into existence rather than write to anything, "
            f"so check it against `basicly tracker show {missing[0]}`"
        )


def _refuse_a_retraction_of_an_absent_edge(
    kit_module: Any, ledger: Path, drafts: Sequence[Any]
) -> None:
    """Refuse a ``dep remove`` whose edge the ledger does not hold.

    **An error rather than a no-op, and a typo is why.** A retraction names two record ids
    and an edge type; get one wrong and a tolerant write records a withdrawal of nothing,
    while the operator reads "recorded" and believes the edge is gone. The edge that has to
    be gone is usually the one about to be re-added in the other direction, so the silent
    version leaves a cycle behind. The cost is stated rather than hidden: a retraction is
    **not idempotent**, so a caller that replays one meets this refusal on the second pass.

    Read under the caller's lock, for `_resolve_labels`' reason — the edge set a retraction
    is checked against has to be the set the append lands on.

    Raises:
        TrackerDivergenceError: a retraction names an edge no record holds.
    """
    retractions = [draft for draft in drafts if draft.kind == kit_module.events.KIND_EDGE_RETRACTED]
    if not retractions:
        return
    migrate = kit_module.migrate
    views = kit_module.views_from_events(kit_module.read_ledger(ledger)) if ledger.is_dir() else {}
    for draft in retractions:
        target = draft.payload[migrate.EDGE_TO]
        edge_type = draft.payload[migrate.EDGE_TYPE]
        held = views.get(draft.record)
        if held is not None and any(
            edge.target == target and edge.type == edge_type for edge in held.dependencies
        ):
            continue
        raise TrackerDivergenceError(
            f"{draft.record} holds no {edge_type!r} edge to {target}, so there is nothing "
            f"to retract; the edge is recorded on the dependent, so check both ids against "
            f"`basicly tracker show {draft.record}`"
        )


def append(repo_root: Path, args: Sequence[str]) -> None:
    """Record on the owned ledger the fact *args* states.

    The echo is empty because no process ran, which is why ``create`` cannot come through
    here — its translation reads the reply for the minted id, and :func:`create` is that
    surface.

    The lock is held across the whole call rather than left to ``events.append``, because
    a label write is a read-modify-write: :func:`_resolve_labels` reads the record's
    current set and the append writes the successor, and a second writer in between would
    drop one of the two labels.

    Raises:
        TrackerDivergenceError: the kit is not installed, the write has no owned-ledger
            translation, the write names a record the ledger does not hold, a retraction
            names an edge nothing holds, or the append failed.
    """
    kit_module = owned_store.kit(repo_root)
    events = kit_module.events
    ledger = owned_store.ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            drafts = mirror.drafts(kit_module, _resolve_labels(kit_module, ledger, args), "")
            _refuse_a_write_to_an_absent_record(kit_module, ledger, args, drafts)
            _refuse_a_retraction_of_an_absent_edge(kit_module, ledger, drafts)
            events.append(
                ledger,
                _stamped(kit_module, drafts),
                redact=redact.redact_committed,
                held_lock=lock,
            )
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"{' '.join(args)} did not reach the owned ledger: {exc}"
        ) from exc


def create(repo_root: Path, args: Sequence[str]) -> str:
    """Mint the id one ``br create`` *args* asks for and record the create; return the id.

    **The mint and the append are one critical section**, over the ledger's own lock, the
    rule the kit's ``cli.create_record`` states: minting reads every id the ledger ever held
    and `supervise` runs its lanes concurrently, so a writer appending in between could be
    handed the same id. A child inherits its prefix from its parent; a root takes it from
    ``[tracker] prefix``, because the prefix used to live in the external tracker's own
    config and the flip deletes that (basicly-vkh0.42.7).

    Raises:
        TrackerDivergenceError: the create names no parent, the kit is not installed, or
            the ledger refused the write.
    """
    kit_module = owned_store.kit(repo_root)
    events = kit_module.events
    parent = dict(tracker_argv.flag_pairs(args, tracker_argv.VALUE_FLAGS["create"])).get(
        "--parent", ""
    )
    prefix = owned_store.tracker_prefix(repo_root) if not parent else None
    if not parent and not prefix:
        raise TrackerDivergenceError(
            f"a create with no --parent needs an id prefix and this repository declares "
            f"none: set [tracker] prefix in basicly.toml, or name a parent. "
            f"{' '.join(args)} would otherwise have to guess a namespace no read would "
            f"find again"
        )
    ledger = owned_store.ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            # Every id the ledger ever held, tombstones included: `ids.minted_ever`'s rule
            # is that a deleted record's id is never handed out again.
            minted = set(events.fold(events.read_events(ledger)[0]).records)
            record = (
                events.ids.next_child_id(parent, minted)
                if parent
                else events.ids.mint_root_id(events.ids.validate_prefix(prefix or ""), minted)
            )
            drafts = mirror.drafts(kit_module, args, json.dumps({"id": record}))
            events.append(
                ledger,
                _stamped(kit_module, drafts),
                redact=redact.redact_committed,
                held_lock=lock,
            )
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"br {' '.join(args)} did not reach the owned ledger: {exc}"
        ) from exc
    return record
