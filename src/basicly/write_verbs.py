from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from . import tracker_argv
from .owned_store import TrackerDivergenceError
from .tracker_argv import CREATE_FIELD_FLAGS, UPDATE_FIELD_FLAGS, UPDATE_STATUS_FLAGS, VALUE_FLAGS

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


MIRROR_PROVENANCE = "dual-write"

CLOSE_REASON_FIELD = "close_reason"


def _priority(value: str) -> int:

    try:
        return int(value.removeprefix("P").removeprefix("p"))
    except ValueError as exc:
        raise TrackerDivergenceError(
            f"priority {value!r} is neither a number nor a P-form, so the int the "
            f"ledger holds cannot be derived"
        ) from exc


_FIELD_TYPES: dict[str, Callable[[str], object]] = {"priority": _priority}

_GATE_PASS_STATUS = "pass"  # noqa: S105 — a gate verdict, not a credential

CREATED_STATUS = "open"


def _payload(kit_module: Any, **fields: object) -> dict[str, object]:
    payload: dict[str, object] = {kit_module.migrate.PROVENANCE_KEY: MIRROR_PROVENANCE}
    payload.update(fields)
    return payload


def _update_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:

    events = kit_module.events
    records = tracker_argv.positionals(args, VALUE_FLAGS["update"])[1:]
    if not records:
        raise TrackerDivergenceError(f"update names no record: {' '.join(args)}")
    drafts: list[object] = []
    for flag, value in tracker_argv.flag_pairs(args, VALUE_FLAGS["update"]):
        if flag in UPDATE_STATUS_FLAGS:
            drafts += [
                events.Draft(record, events.KIND_STATUS, _payload(kit_module, status=value))
                for record in records
            ]
        elif (name := UPDATE_FIELD_FLAGS.get(flag)) is not None:
            stored = _FIELD_TYPES.get(name, str)(value)
            drafts += [
                events.Draft(
                    record,
                    events.KIND_FIELD,
                    _payload(kit_module, name=name, value=stored),
                )
                for record in records
            ]
        elif flag in tracker_argv.UPDATE_LABEL_FLAGS:
            raise TrackerDivergenceError(
                f"update {flag} accumulates against the labels the record already holds, "
                f"and this translator cannot read the ledger; the write seam resolves it "
                f"into --labels before translating (owned_write._resolve_labels)"
            )
        else:
            raise TrackerDivergenceError(
                f"update {flag} has no owned-ledger equivalent, so translating it would "
                f"drop the field the caller asked to write; add it to "
                f"tracker_argv.UPDATE_FIELD_FLAGS if the argv's own value is what the ledger "
                f"stores — that table's note lists the flags measured not to, and why"
            )
    return drafts


def _create_drafts(kit_module: Any, args: Sequence[str], stdout: str) -> list[object]:

    events = kit_module.events
    try:
        reply = json.loads(stdout)
    except ValueError as exc:
        raise TrackerDivergenceError(
            f"br create replied with no JSON record, so the id it minted cannot be mirrored: {exc}"
        ) from exc
    record = reply.get("id") if isinstance(reply, dict) else None
    if not isinstance(record, str) or not record:
        raise TrackerDivergenceError("br create replied with no issue id to mirror")
    positional = tracker_argv.positionals(args, VALUE_FLAGS["create"])
    if len(positional) < 2 or not positional[1].strip():
        raise TrackerDivergenceError(
            f"br create names no title: {' '.join(args)}. A titleless record is a `created` "
            f"event stating nothing, and `ledger_bodies` reads the event's presence rather "
            f"than its content, so nothing downstream would report it"
        )
    if strays := positional[2:]:
        raise TrackerDivergenceError(
            f"br create places one positional, the title, so "
            f"{', '.join(repr(word) for word in strays)} cannot be placed: a field is set by "
            f"a flag ({', '.join(tracker_argv.CREATE_LONG_FLAGS)}). Dropping it mints a "
            f"record nothing reads as typed"
        )
    fields: dict[str, object] = {"title": positional[1]}
    parent = ""
    for flag, value in tracker_argv.flag_pairs(args, VALUE_FLAGS["create"]):
        name = CREATE_FIELD_FLAGS.get(flag)
        if name == "parent":
            parent = value
        elif name is not None:
            fields[name] = _FIELD_TYPES.get(name, str)(value)
    status = reply.get("status")
    drafts: list[object] = [
        events.Draft(record, events.KIND_CREATED, _payload(kit_module, **fields)),
        events.Draft(
            record,
            events.KIND_STATUS,
            _payload(
                kit_module,
                status=status if isinstance(status, str) and status else CREATED_STATUS,
            ),
        ),
    ]
    if parent:
        drafts.append(
            _edge_draft(kit_module, record, parent, kit_module.DEFAULT_VOCABULARY.parent_child_type)
        )
    return drafts


def _edge_draft(
    kit_module: Any, record: str, target: str, edge_type: str, *, retracted: bool = False
) -> object:

    migrate = kit_module.migrate
    events = kit_module.events
    payload = _payload(kit_module)
    payload[migrate.EDGE_FROM] = record
    payload[migrate.EDGE_TO] = target
    payload[migrate.EDGE_TYPE] = edge_type
    kind = events.KIND_EDGE_RETRACTED if retracted else migrate.KIND_EDGE
    return events.Draft(record, kind, payload)


def _gate_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:

    kind = kit_module.KIND_GATE
    positional = tracker_argv.positionals(args, VALUE_FLAGS["gate report"])
    if len(positional) != 3:
        raise TrackerDivergenceError(f"br gate report names no single issue: {' '.join(args)}")
    values = dict(tracker_argv.flag_pairs(args, VALUE_FLAGS["gate report"]))
    gate = values.get("--gate", "")
    provider = values.get("--provider", "")
    if not gate or not provider:
        raise TrackerDivergenceError(f"br gate report names no gate and provider: {' '.join(args)}")
    payload = _payload(kit_module)
    payload[kit_module.GATE_NAME_KEY] = gate
    payload[kit_module.GATE_PROVIDER_KEY] = provider
    payload[kit_module.GATE_PASSED_KEY] = values.get("--status", "") == _GATE_PASS_STATUS
    return [kit_module.events.Draft(positional[2], kind, payload)]


def _close_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:

    events = kit_module.events
    records = tracker_argv.positionals(args, VALUE_FLAGS["close"])[1:]
    if not records:
        raise TrackerDivergenceError(f"br close names no issue: {' '.join(args)}")
    values = dict(tracker_argv.flag_pairs(args, VALUE_FLAGS["close"]))
    reason = values.get("--reason", "").strip()
    drafts: list[object] = []
    for record in records:
        if reason:
            drafts.append(
                events.Draft(
                    record,
                    events.KIND_FIELD,
                    _payload(kit_module, name=CLOSE_REASON_FIELD, value=reason),
                )
            )
        drafts.append(
            events.Draft(record, events.KIND_STATUS, _payload(kit_module, status="closed"))
        )
    return drafts


def _comment_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:

    events = kit_module.events
    if len(args) != 4:
        raise TrackerDivergenceError(
            f"br comments add takes one issue and one body; got {len(args)} arguments"
        )
    payload = _payload(kit_module, text=args[3])
    return [events.Draft(args[2], events.KIND_COMMENT, payload)]


def _named_edge(surface: str, args: Sequence[str]) -> tuple[str, str, str]:

    positional = tracker_argv.positionals(args, VALUE_FLAGS[surface])
    if len(positional) != 4:
        raise TrackerDivergenceError(f"br {surface} names no single edge: {' '.join(args)}")
    values = dict(tracker_argv.flag_pairs(args, VALUE_FLAGS[surface]))
    edge_type = values.get("-t") or values.get("--type") or ""
    if not edge_type:
        raise TrackerDivergenceError(f"br {surface} names no edge type: {' '.join(args)}")
    return positional[2], positional[3], edge_type


def _dep_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    return [_edge_draft(kit_module, *_named_edge("dep add", args))]


def _dep_remove_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:

    record, target, edge_type = _named_edge("dep remove", args)
    parent_child = kit_module.DEFAULT_VOCABULARY.parent_child_type
    if edge_type == parent_child:
        raise TrackerDivergenceError(
            f"a {parent_child!r} edge is not retractable: removing it re-parents {record}, "
            f"changing what `basicly loop supervise` fans out over while the record's own "
            f"id still spells its parent. Re-parenting needs its own verb"
        )
    return [_edge_draft(kit_module, record, target, edge_type, retracted=True)]
