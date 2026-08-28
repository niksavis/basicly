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
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from basicly import mirror, owned_store, re_record, redact, tracker_argv
from basicly.owned_store import TrackerDivergenceError

# How a write the engine made itself says it got here — as against one the dual write
# mirrored (:data:`mirror.MIRROR_PROVENANCE`) and one `migrate.py` extracted out of the
# export. One of `migrate.RESERVED_KEYS`, so it is dropped when a record is rendered back.
OWNED_PROVENANCE = "engine"

# The dispatched agent's own name, from the overlay `runner.br_attribution_env` puts on every
# dispatch. Nothing has read it since the flip retired br, which was its only reader.
AGENT_ENV_VAR = "BR_AGENT_NAME"

# Prefixed, so an agent named `claude` cannot read as an operator of that name.
AGENT_ACTOR = "agent:"
OPERATOR_ACTOR = "operator:"

# Not `events.UNATTRIBUTED_ACTOR`, which says no caller supplied one: this says the seam looked.
UNRESOLVED_ACTOR = "unresolved:no-redactable-identity"

# `actor` sits outside the payload, so `events.prepare_payload`'s cap never reaches it.
MAX_ACTOR_CHARS = 64


def resolved_actor(environ: Mapping[str, str] | None = None) -> str:
    """Who an append is made under: the dispatched agent, else this machine's masked operator.

    The agent wins, so a landing names the runner that produced it rather than the account the
    supervisor runs as (`work-tracker.md` §4.3 item 5).

    An operator is the redactor's placeholder and never the username: `redact.machine_identity`
    answers ``""`` for a name too short to word-bound, so handing that name to
    `redact_committed` would return it verbatim into the committed store R6 cleared. Redact then
    cap, never the reverse — capping first can split a value past the point its rule matches.
    """
    values = os.environ if environ is None else environ
    agent = " ".join(values.get(AGENT_ENV_VAR, "").split())
    if agent:
        return AGENT_ACTOR + redact.redact_committed(agent)[:MAX_ACTOR_CHARS]
    name = redact.machine_identity()
    if not name:
        return UNRESOLVED_ACTOR
    return OPERATOR_ACTOR + redact.redact_machine_identity(name)


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
    stripped = tracker_argv.without_flags(
        args, tracker_argv.UPDATE_LABEL_FLAGS, tracker_argv.VALUE_FLAGS["update"]
    )
    return [*stripped, "--labels", tracker_argv.LABEL_SEPARATOR.join(labels)]


def _refuse_a_write_that_records_nothing(args: Sequence[str], drafts: Sequence[Any]) -> None:
    """Refuse a write whose translation produced no event at all.

    ``cmd_write`` reports from what landed, and a translation yielding **nothing** lands
    vacuously — every one of no events — so the empty case still reads as success and is
    refused here. Measured on a flagless ``update``, which `mirror._update_drafts`
    translates to an empty list (basicly-holhk4). Here and not in that translator because
    the defect is the shape rather than the verb: any translation yielding nothing is a
    confirmation about nothing, and this covers all seven.

    ``init`` and ``sync`` are exempt by construction: `mirror.UNMIRRORED_WRITES` is the set
    of writes that legitimately state nothing about a record.

    Raises:
        TrackerDivergenceError: *args* records nothing and is not an unmirrored write.
    """
    if drafts or (args and args[0] in mirror.UNMIRRORED_WRITES):
        return
    raise TrackerDivergenceError(
        f"{' '.join(args)} states nothing the ledger can record, so no event was appended; "
        f"name what should change, because the seam would otherwise report it as recorded"
    )


def refuse_a_write_to_an_absent_record(
    kit_module: Any, ledger: Path, subject: str, drafts: Sequence[Any]
) -> None:
    """Refuse a write naming a record the ledger does not hold.

    Public because :func:`append` is not the only write path: ``tracker.add_artifact``
    hands the ledger an object rather than an argv, so it cannot come through here, and it
    was the seventh write path and the one this guard did not cover (basicly-kmqno2). It
    takes *subject* rather than an argv so a non-argv caller can name itself.

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
            f"{subject} names a record the ledger does not hold: {', '.join(missing)}. "
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


def append(repo_root: Path, args: Sequence[str]) -> bool:
    """Record on the owned ledger the fact *args* states; False if it was already there.

    The echo is empty because no process ran, which is why ``create`` cannot come through
    here — its translation reads the reply for the minted id, and :func:`create` is that
    surface.

    The lock is held across the whole call rather than left to ``events.append``, because
    a label write is a read-modify-write: :func:`_resolve_labels` reads the record's
    current set and the append writes the successor, and a second writer in between would
    drop one of the two labels.

    Raises:
        TrackerDivergenceError: the kit is not installed, the write has no translation or
            carries a flag its verb cannot read, it names an absent record, a
            retraction names an edge nothing holds, or the append failed.
    """
    args, repeat = re_record.read_the_seams_own_flags(args)
    kit_module = owned_store.kit(repo_root)
    events = kit_module.events
    ledger = owned_store.ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            drafts = mirror.drafts(kit_module, _resolve_labels(kit_module, ledger, args), "")
            _refuse_a_write_that_records_nothing(args, drafts)
            refuse_a_write_to_an_absent_record(kit_module, ledger, " ".join(args), drafts)
            _refuse_a_retraction_of_an_absent_edge(kit_module, ledger, drafts)
            stamped = _stamped(kit_module, drafts)
            # After the stamp, never before: stamping rewrites the payload the id
            # derives from.
            stamped = re_record.at_the_generation_this_write_needs(
                kit_module, ledger, stamped, repeat=repeat
            )
            landed = events.append(
                ledger,
                stamped,
                actor=resolved_actor(),
                redact=redact.redact_committed,
                held_lock=lock,
            )
            return len(landed) == len(stamped)
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
                actor=resolved_actor(),
                redact=redact.redact_committed,
                held_lock=lock,
            )
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"br {' '.join(args)} did not reach the owned ledger: {exc}"
        ) from exc
    return record
