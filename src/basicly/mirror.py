"""One tracker write, as the events the owned ledger records the same fact with.

The write vocabulary's translator, and that is its whole responsibility: an argv in,
event drafts out. Nothing here knows where the ledger is or whether the append
succeeded; :mod:`basicly.owned_store` answers the first and :mod:`basicly.tracker`
performs the second.

Nothing is dropped: an untranslated write raises (:func:`drafts`) and so does an
untranslated *flag* of a write that has one, because a ledger quietly short of the
field a caller asked for is the divergence this layer exists to prevent.

The kit module arrives as a parameter rather than being loaded here, which keeps the
translation testable against a kit without a repo and keeps this module free of any
import back into the seam that calls it. Every name it writes in — the event kinds,
the edge and gate payload keys, the parent-child edge type — is read off that module
rather than respelled, so a fact can never be recorded under a key the kit does not
read.

The boundary is *translation* against *the store*: nothing here touches a file.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from basicly import tracker_argv, tracker_usage
from basicly.owned_store import TrackerDivergenceError
from basicly.tracker_argv import (
    CREATE_FIELD_FLAGS,
    UPDATE_FIELD_FLAGS,
    UPDATE_STATUS_FLAGS,
    VALUE_FLAGS,
)

# How a translated fact says it got here (§9.6). Distinguishes it from one `migrate.py`
# extracted out of an import, and it is one of `migrate.RESERVED_KEYS`, so it is dropped
# again when a record is rendered back. `owned_write` restamps it as the engine's own.
MIRROR_PROVENANCE = "dual-write"

# Writes that carry no record fact, so there is nothing to translate. Named rather than
# defaulted to "skip", because the default for an unrecognised write is a refusal — see
# :func:`drafts`. The ledger needs no equivalent of either: it *is* the committed
# artifact (git is its transport, §4) and `events.append` creates its directory.
UNMIRRORED_WRITES = frozenset({"init", "sync"})


def _priority(value: str) -> int:
    """``-p P3`` and ``-p 3`` name one priority; the ledger holds the int.

    Raises:
        TrackerDivergenceError: *value* is neither spelling, so no int can be recorded.
            Raised rather than let through as a ``ValueError``, so every refusal that
            reaches a caller from here is one type.
    """
    try:
        return int(value.removeprefix("P").removeprefix("p"))
    except ValueError as exc:
        raise TrackerDivergenceError(
            f"priority {value!r} is neither a number nor a P-form, so the int the "
            f"ledger holds cannot be derived"
        ) from exc


# The shape each field has to be stored in, because a flag's value arrives as one argv
# string while a folded record returns it typed. Anything absent here is text on both
# sides. `labels` is deliberately not here: a `field` event's `value` is one of
# `events.TRUNCATABLE_KEYS`, so the schema refuses the list, and the joined form is what
# `tracker_argv.labels_of` splits back at every reader.
_FIELD_TYPES: dict[str, Callable[[str], object]] = {"priority": _priority}

# `br gate report --status` spells a pass this way; anything else is a failure, which
# is `policy.GateStatus`'s own reading of the same field.
_GATE_PASS_STATUS = "pass"  # noqa: S105 — a gate verdict, not a credential

# The status br gives a record it just created, when the `--json` echo does not say.
_CREATED_STATUS = "open"


def _payload(kit_module: Any, **fields: object) -> dict[str, object]:
    """A mirrored event's payload, carrying how the fact got here."""
    payload: dict[str, object] = {kit_module.migrate.PROVENANCE_KEY: MIRROR_PROVENANCE}
    payload.update(fields)
    return payload


def _update_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br update``.

    Every flag has to be translatable. An unrecognised one raises rather than being
    dropped, because the alternative is a ledger that is missing precisely the field
    somebody just added a flag for.
    """
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
    """Drafts for one ``br create``, whose record id only the reply carries.

    ``--parent`` becomes a ``parent-child`` edge on the new record rather than a field:
    that is where both stores hold it (`differential.Edge`), and it is what makes the
    parent read as decomposed.
    """
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
    fields: dict[str, object] = {"title": positional[1]} if len(positional) > 1 else {}
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
                status=status if isinstance(status, str) and status else _CREATED_STATUS,
            ),
        ),
    ]
    if parent:
        # The kit's own name for the edge, not a fourth spelling of the string: this is
        # exactly the value `differential.children_of` inverts the population on, so a
        # literal here would make a mirrored parent invisible to the ready query.
        drafts.append(
            _edge_draft(kit_module, record, parent, kit_module.DEFAULT_VOCABULARY.parent_child_type)
        )
    return drafts


def _edge_draft(
    kit_module: Any, record: str, target: str, edge_type: str, *, retracted: bool = False
) -> object:
    """One dependency edge, recorded on the dependent — where both stores hold it.

    *retracted* withdraws it instead. The kind is the only difference, so a retraction can
    never name a different edge from the one a ``dep add`` wrote.
    """
    migrate = kit_module.migrate
    events = kit_module.events
    payload = _payload(kit_module)
    payload[migrate.EDGE_FROM] = record
    payload[migrate.EDGE_TO] = target
    payload[migrate.EDGE_TYPE] = edge_type
    kind = events.KIND_EDGE_RETRACTED if retracted else migrate.KIND_EDGE
    return events.Draft(record, kind, payload)


def _gate_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br gate report``.

    **The writer `differential.KIND_GATE` was defined for.** The export carries no gate
    field at all, so `migrate.py` had nothing to import and the third of the three
    queries the shadow differential compares had no owned-side rows to compare — it
    reported ``inconclusive`` on every population. This is what fills it.
    """
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
    """Drafts for one ``br close``: the status move on every id it names.

    **Every id, because br closes every id** — its own ``--help`` takes ``[IDS]...``.
    Refusing the plural form was the divergence, not a guard against one: br had
    already closed all of them by the time the mirror looked (`basicly-e2mz.24`).

    The ``--reason`` is not mirrored as a comment. br records it as a field of the
    close rather than as a comment row, so writing one would put a comment on the
    owned side that the reference side does not hold — a difference invented by the
    mirror rather than found by it.
    """
    events = kit_module.events
    records = tracker_argv.positionals(args, VALUE_FLAGS["close"])[1:]
    if not records:
        raise TrackerDivergenceError(f"br close names no issue: {' '.join(args)}")
    return [
        events.Draft(record, events.KIND_STATUS, _payload(kit_module, status="closed"))
        for record in records
    ]


def _comment_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br comments add`` — 45% of this repo's tracker traffic.

    Read by position rather than through :func:`tracker_argv.positionals`, and that is the whole
    point: the body is arbitrary free text, so a body beginning with ``-`` would be
    taken for a flag and silently dropped — losing exactly the checkpoint or rework
    marker the engine's whole policy layer is carried in.
    """
    events = kit_module.events
    if len(args) != 4:
        raise TrackerDivergenceError(
            f"br comments add takes one issue and one body; got {len(args)} arguments"
        )
    payload = _payload(kit_module, text=args[3])
    return [events.Draft(args[2], events.KIND_COMMENT, payload)]


def _named_edge(surface: str, args: Sequence[str]) -> tuple[str, str, str]:
    """The ``(record, target, type)`` one ``dep`` argv names.

    Read the same way for both dep verbs: an edge is identified by all three, so an
    assertion and a withdrawal that read the argv differently could not be paired.

    Raises:
        TrackerDivergenceError: *args* names no single edge, or no edge type.
    """
    positional = tracker_argv.positionals(args, VALUE_FLAGS[surface])
    if len(positional) != 4:
        raise TrackerDivergenceError(f"br {surface} names no single edge: {' '.join(args)}")
    values = dict(tracker_argv.flag_pairs(args, VALUE_FLAGS[surface]))
    edge_type = values.get("-t") or values.get("--type") or ""
    if not edge_type:
        raise TrackerDivergenceError(f"br {surface} names no edge type: {' '.join(args)}")
    return positional[2], positional[3], edge_type


def _dep_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br dep add``, recorded on the dependent."""
    return [_edge_draft(kit_module, *_named_edge("dep add", args))]


def _dep_remove_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br dep remove``: the retraction of the edge it names.

    **A retraction, not a deletion**, because the log is append-only — the fold drops the
    edge and the history keeps both statements. That is what makes an *inverted* edge
    enactable: the reverse edge alone closes a cycle.

    Raises:
        TrackerDivergenceError: as :func:`_named_edge`, or the edge is ``parent-child`` —
            refused rather than translated, for the reason the refusal states.
    """
    record, target, edge_type = _named_edge("dep remove", args)
    parent_child = kit_module.DEFAULT_VOCABULARY.parent_child_type
    if edge_type == parent_child:
        raise TrackerDivergenceError(
            f"a {parent_child!r} edge is not retractable: removing it re-parents {record}, "
            f"changing what `basicly loop supervise` fans out over while the record's own "
            f"id still spells its parent. Re-parenting needs its own verb"
        )
    return [_edge_draft(kit_module, record, target, edge_type, retracted=True)]


# The record-write surface, as the translation each one takes. A dispatch table rather
# than a chain of comparisons so the mirrored set is *readable as a set* — it is the
# thing a reviewer has to check against the measured surface, and a branch buried in a
# function body is not.
_MIRRORED_WRITES: dict[str, Callable[[Any, Sequence[str], str], list[object]]] = {
    "close": _close_drafts,
    "comments add": _comment_drafts,
    "create": _create_drafts,
    "dep add": _dep_drafts,
    "dep remove": _dep_remove_drafts,
    "gate report": _gate_drafts,
    "update": _update_drafts,
}


def drafts(kit_module: Any, args: Sequence[str], stdout: str) -> list[object]:
    """The owned-ledger drafts recording the same fact *args* just wrote to br.

    Empty for a read and for the two writes that state nothing about a record. Every
    other write is translated, and one this function does not know **raises**: the
    surface was frozen by measurement (`basicly.tracker_usage`), so a write outside it
    is a new dependency on br that nobody decided to take, and the mirror is the only
    place that can still see it before the two stores drift.

    Raises:
        TrackerDivergenceError: *args* is a write with no owned-ledger translation.
    """
    surface, _ = tracker_usage.split_invocation(list(args))
    if tracker_usage.classify_access(surface) == "read" or surface in UNMIRRORED_WRITES:
        return []
    translate = _MIRRORED_WRITES.get(surface)
    if translate is None:
        raise TrackerDivergenceError(
            f"{surface!r} is not a write this tracker knows how to record; the verbs that "
            f"are: {', '.join(sorted(_MIRRORED_WRITES))}. Add a translation to "
            f"mirror._MIRRORED_WRITES, or list it in mirror.UNMIRRORED_WRITES if it "
            f"states nothing about a record"
        )
    return translate(kit_module, args, stdout)


# What a `br create` echoes back, faked so :func:`refuse_untranslatable` can run the
# real translator before the real echo exists. Only `_create_drafts` reads the echo,
# and only for the minted id, which no argv can carry.
_ECHO_PLACEHOLDER = json.dumps({"id": "unminted", "status": _CREATED_STATUS})
