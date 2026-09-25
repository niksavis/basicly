from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from basicly import mirror, owned_store, re_record, redact, tracker_argv
from basicly.owned_store import TrackerDivergenceError

OWNED_PROVENANCE = "engine"


def ledger_git_rules(repo_root: Path) -> tuple[str, ...]:
    try:
        events = owned_store.kit(repo_root, "events")
    except owned_store.TrackerDivergenceError:
        return ()
    return tuple(f"{glob} -text merge=union" for glob in (events.LOG_GLOB, events.PENDING_GLOB))


def _template(repo_root: Path) -> Any:
    return owned_store.kit(repo_root, "templates").load(owned_store.ledger_dir(repo_root))


def resolved_actor(repo_root: Path) -> str:
    return str(owned_store.kit(repo_root, "writers").writer_class())


def _stamped(kit_module: Any, drafts: Sequence[Any]) -> list[Any]:

    return [
        replace(
            draft,
            payload={**draft.payload, kit_module.migrate.PROVENANCE_KEY: OWNED_PROVENANCE},
        )
        for draft in drafts
    ]


def _current_labels(kit_module: Any, ledger: Path, record: str) -> list[str]:
    if not ledger.is_dir():
        return []
    state = kit_module.events.fold(kit_module.events.read_events(ledger)[0]).records.get(record)
    held = state.fields.get(tracker_argv.LABELS_FIELD) if state is not None else None
    return list(tracker_argv.labels_of(held))


def _resolve_labels(kit_module: Any, ledger: Path, args: Sequence[str]) -> list[str]:

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

    if drafts or (args and args[0] in mirror.UNMIRRORED_WRITES):
        return
    raise TrackerDivergenceError(
        f"{' '.join(args)} states nothing the ledger can record, so no event was appended; "
        f"name what should change, because the seam would otherwise report it as recorded"
    )


def refuse_a_write_to_an_absent_record(
    kit_module: Any, ledger: Path, subject: str, drafts: Sequence[Any]
) -> None:

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
    kit_module: Any, repo_root: Path, ledger: Path, drafts: Sequence[Any]
) -> None:

    retractions = [draft for draft in drafts if draft.kind == kit_module.events.KIND_EDGE_RETRACTED]
    if not retractions:
        return
    edges = owned_store.kit(repo_root, "edges")
    migrate = kit_module.migrate
    views = kit_module.views_from_events(kit_module.read_ledger(ledger)) if ledger.is_dir() else {}
    for draft in retractions:
        target, edge_type = draft.payload[migrate.EDGE_TO], draft.payload[migrate.EDGE_TYPE]
        edges.refuse_retraction(views, draft.record, target, edge_type)


def append(repo_root: Path, args: Sequence[str]) -> tuple[list[Any], list[Any]]:

    args, repeat = re_record.read_the_seams_own_flags(args)
    kit_module = owned_store.kit(repo_root)
    events = kit_module.events
    ledger = owned_store.ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            drafts = mirror.drafts(kit_module, _resolve_labels(kit_module, ledger, args), "")
            _refuse_a_write_that_records_nothing(args, drafts)
            refuse_a_write_to_an_absent_record(kit_module, ledger, " ".join(args), drafts)
            _refuse_a_retraction_of_an_absent_edge(kit_module, repo_root, ledger, drafts)
            held = events.fold(events.read_events(ledger)[0]).records if ledger.is_dir() else {}
            holders = owned_store.kit(repo_root, "holders")
            drafts = holders.claimed_by(held, drafts, holders.default_holder(repo_root))
            holders.refuse(held, drafts)
            review = owned_store.kit(repo_root, "review")
            review.refuse(held, drafts, resolved_actor(repo_root), _template(repo_root))
            stamped = _stamped(kit_module, drafts)
            owned_store.kit(repo_root, "values").refuse(events, stamped, _template(repo_root))
            stamped = owned_store.kit(repo_root, "recurrence").at_the_generation_this_write_needs(
                events, ledger, stamped, repeat=repeat, redact=redact.redact_committed
            )
            landed = events.append(
                ledger,
                stamped,
                actor=resolved_actor(repo_root),
                redact=redact.redact_committed,
                held_lock=lock,
            )
            return stamped, landed
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"{' '.join(args)} did not reach the owned ledger: {exc}"
        ) from exc


def create(repo_root: Path, args: Sequence[str]) -> str:

    kit_module = owned_store.kit(repo_root)
    events = kit_module.events
    parent = dict(tracker_argv.flag_pairs(args, tracker_argv.VALUE_FLAGS["create"])).get(
        "--parent", ""
    )
    prefix = owned_store.tracker_prefix(repo_root) if not parent else None
    if not parent and not prefix:
        raise TrackerDivergenceError(
            f"a create with no --parent needs an id prefix and this repository declares "
            f"none in {owned_store.PREFIX_HOME}: run "
            f"`{owned_store.set_prefix_command('NAME')}`, or name a parent. "
            f"{' '.join(args)} would otherwise have to guess a namespace no read would "
            f"find again"
        )
    ledger = owned_store.ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            minted = set(events.fold(events.read_events(ledger)[0]).records)
            record = (
                events.ids.next_child_id(parent, minted)
                if parent
                else events.ids.mint_root_id(events.ids.validate_prefix(prefix or ""), minted)
            )
            drafts = mirror.drafts(kit_module, args, json.dumps({"id": record}))
            owned_store.kit(repo_root, "values").refuse(events, drafts, _template(repo_root))
            events.append(
                ledger,
                _stamped(kit_module, drafts),
                actor=resolved_actor(repo_root),
                redact=redact.redact_committed,
                held_lock=lock,
            )
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"br {' '.join(args)} did not reach the owned ledger: {exc}"
        ) from exc
    return record
