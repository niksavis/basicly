"""One accepted ``br`` write, as the events the owned ledger records the same fact with.

The dual write's translator, and that is its whole responsibility: an argv the
external tracker has already accepted, plus whatever it echoed, in — the owned
store's event drafts out. Nothing here knows which rung the repo is on, where the
ledger is, or whether the append succeeded; :mod:`basicly.owned_store` answers the
first three questions and :mod:`basicly.br` performs the write.

Nothing is dropped: an untranslated write raises (:func:`drafts`) and so does an
untranslated *flag* of a write that has one, because a ledger quietly short of the
one field br just recorded is the divergence this mode exists to prevent.

The kit module arrives as a parameter rather than being loaded here, which is what
keeps the translation testable against a kit without a repo, and what keeps this
module free of any import back into the seam that calls it. Every name it writes in
— the event kinds, the edge and gate payload keys, the parent-child edge type — is
read off that module rather than respelled, so a fact can never be recorded under a
key the kit does not read.

Split out of ``br`` when the module-size ratchet caught that module growing. The
boundary is *translation* against *the spawn and the store*: nothing here runs a
process or touches a file.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Sequence
from typing import Any

from basicly import tracker_usage
from basicly.owned_store import TrackerDivergenceError

# How a mirrored fact says it got here (§9.6). Distinguishes an event the dual write
# recorded from one `migrate.py` extracted out of the export, and it is one of
# `migrate.RESERVED_KEYS`, so it is dropped again when a record is rendered back.
MIRROR_PROVENANCE = "dual-write"

# br's writes that carry no record fact, so there is nothing to mirror. Named rather
# than defaulted to "skip", because the default for an unrecognised write is a
# refusal — see :func:`drafts`.
#
# `sync` moves the whole store between its database and its export and `init` creates
# the store; neither states anything about a record. The owned ledger needs no
# equivalent of either: it *is* the export (git is its transport, §4) and
# `events.append` creates its directory on first write.
_UNMIRRORED_WRITES = frozenset({"init", "sync"})

# `br update`'s flags, as the ledger fact each one records. Two mappings rather than
# one because `status` has its own event kind while everything else is a `field`.
#
# **Deliberately only what the engine spawns** (`br update -t` in `classify`,
# `br update --external-ref` in `loop`), plus the status flag. A flag absent here is
# not dropped — it raises :class:`TrackerDivergenceError`, because a mirrored write that
# silently omitted half of what br recorded is exactly the divergence this mode
# exists to prevent, and it would be invisible until the differential ran.
_UPDATE_FIELD_FLAGS = {
    "-t": "issue_type",
    "--type": "issue_type",
    "--external-ref": "external_ref",
}
_UPDATE_STATUS_FLAGS = frozenset({"-s", "--status"})

# `br create`'s flags, as the fields the created record carries.
_CREATE_FIELD_FLAGS = {
    "-t": "issue_type",
    "--type": "issue_type",
    "-p": "priority",
    "--priority": "priority",
    "-l": "labels",
    "--label": "labels",
    "-d": "description",
    "--description": "description",
    "--parent": "parent",
}

# ...and the shape each one has to be stored in, because a flag's value arrives as one
# argv string while `br show --json` returns it typed. Not cosmetic: `supervise` reads
# ``record["labels"]`` as a list and a stored ``"phase-6,ready"`` iterates as characters,
# so a lane's follow-up would inherit twelve one-letter labels after the flip. Anything
# absent here is text on both sides.
_CREATE_FIELD_TYPES: dict[str, Callable[[str], object]] = {
    "priority": int,
    "labels": lambda value: [part for part in value.split(",") if part],
}

# Flags whose value is the following token, per subcommand. Needed to find the
# positional a write is about: `br gate report` puts the issue id *last*, after four
# or five flag/value pairs, so "the last argument" is only right by accident and
# "every token that is not a flag" would collect `--note`'s free text as one.
_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "create": frozenset(_CREATE_FIELD_FLAGS) | {"-a", "--assignee"},
    "update": frozenset(_UPDATE_FIELD_FLAGS) | _UPDATE_STATUS_FLAGS,
    "close": frozenset({"--reason"}),
    "dep add": frozenset({"-t", "--type"}),
    "gate report": frozenset({"--gate", "--provider", "--status", "--note", "--actor"}),
}

# `br gate report --status` spells a pass this way; anything else is a failure, which
# is `policy.GateStatus`'s own reading of the same field.
_GATE_PASS_STATUS = "pass"  # noqa: S105 — a gate verdict, not a credential

# The status br gives a record it just created, when the `--json` echo does not say.
_CREATED_STATUS = "open"


def _positionals(args: Sequence[str], value_flags: Collection[str]) -> list[str]:
    """The positional words in *args*, with each value-taking flag's value consumed.

    ``--flag=value`` carries its own value, so only the space-separated form skips the
    next token. Anything after a flag this subcommand does not take a value for stays
    a positional, which is what makes an unexpected argument visible to the caller
    below rather than silently absorbed.
    """
    found: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            skip = "=" not in arg and arg in value_flags
            continue
        found.append(arg)
    return found


def _flag_pairs(args: Sequence[str], value_flags: Collection[str]) -> list[tuple[str, str]]:
    """Each ``(flag, value)`` in *args*, in the order given, both spellings accepted."""
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("-"):
            name, sep, inline = arg.partition("=")
            if sep:
                pairs.append((name, inline))
            elif name in value_flags and index + 1 < len(args):
                pairs.append((name, args[index + 1]))
                index += 1
            else:
                pairs.append((name, ""))
        index += 1
    return pairs


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
    positional = _positionals(args, _VALUE_FLAGS["update"])
    if len(positional) != 2:
        raise TrackerDivergenceError(f"br update names no single issue: {' '.join(args)}")
    record = positional[1]
    drafts: list[object] = []
    for flag, value in _flag_pairs(args, _VALUE_FLAGS["update"]):
        if flag in _UPDATE_STATUS_FLAGS:
            drafts.append(
                events.Draft(record, events.KIND_STATUS, _payload(kit_module, status=value))
            )
        elif (name := _UPDATE_FIELD_FLAGS.get(flag)) is not None:
            drafts.append(
                events.Draft(
                    record,
                    events.KIND_FIELD,
                    _payload(kit_module, name=name, value=value),
                )
            )
        else:
            raise TrackerDivergenceError(
                f"br update {flag} has no owned-ledger equivalent, so mirroring it would "
                f"drop the field br just wrote; add it to mirror._UPDATE_FIELD_FLAGS"
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
    positional = _positionals(args, _VALUE_FLAGS["create"])
    fields: dict[str, object] = {"title": positional[1]} if len(positional) > 1 else {}
    parent = ""
    for flag, value in _flag_pairs(args, _VALUE_FLAGS["create"]):
        name = _CREATE_FIELD_FLAGS.get(flag)
        if name == "parent":
            parent = value
        elif name is not None:
            fields[name] = _CREATE_FIELD_TYPES.get(name, str)(value)
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


def _edge_draft(kit_module: Any, record: str, target: str, edge_type: str) -> object:
    """One dependency edge, recorded on the dependent — where both stores hold it."""
    migrate = kit_module.migrate
    payload = _payload(kit_module)
    payload[migrate.EDGE_FROM] = record
    payload[migrate.EDGE_TO] = target
    payload[migrate.EDGE_TYPE] = edge_type
    return kit_module.events.Draft(record, migrate.KIND_EDGE, payload)


def _gate_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br gate report``.

    **The writer `differential.KIND_GATE` was defined for.** The export carries no gate
    field at all, so `migrate.py` had nothing to import and the third of the three
    queries the shadow differential compares had no owned-side rows to compare — it
    reported ``inconclusive`` on every population. This is what fills it.
    """
    kind = kit_module.KIND_GATE
    positional = _positionals(args, _VALUE_FLAGS["gate report"])
    if len(positional) != 3:
        raise TrackerDivergenceError(f"br gate report names no single issue: {' '.join(args)}")
    values = dict(_flag_pairs(args, _VALUE_FLAGS["gate report"]))
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
    """Drafts for one ``br close``: the status move, and only that.

    The ``--reason`` is not mirrored as a comment. br records it as a field of the
    close rather than as a comment row, so writing one would put a comment on the
    owned side that the reference side does not hold — a difference invented by the
    mirror rather than found by it.
    """
    events = kit_module.events
    positional = _positionals(args, _VALUE_FLAGS["close"])
    if len(positional) != 2:
        raise TrackerDivergenceError(f"br close names no single issue: {' '.join(args)}")
    return [events.Draft(positional[1], events.KIND_STATUS, _payload(kit_module, status="closed"))]


def _comment_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br comments add`` — 45% of this repo's tracker traffic.

    Read by position rather than through :func:`_positionals`, and that is the whole
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


def _dep_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br dep add``, recorded on the dependent."""
    positional = _positionals(args, _VALUE_FLAGS["dep add"])
    if len(positional) != 4:
        raise TrackerDivergenceError(f"br dep add names no single edge: {' '.join(args)}")
    values = dict(_flag_pairs(args, _VALUE_FLAGS["dep add"]))
    edge_type = values.get("-t") or values.get("--type") or ""
    if not edge_type:
        raise TrackerDivergenceError(f"br dep add names no edge type: {' '.join(args)}")
    return [_edge_draft(kit_module, positional[2], positional[3], edge_type)]


# The record-write surface, as the translation each one takes. A dispatch table rather
# than a chain of comparisons so the mirrored set is *readable as a set* — it is the
# thing a reviewer has to check against the measured surface, and a branch buried in a
# function body is not.
_MIRRORED_WRITES: dict[str, Callable[[Any, Sequence[str], str], list[object]]] = {
    "close": _close_drafts,
    "comments add": _comment_drafts,
    "create": _create_drafts,
    "dep add": _dep_drafts,
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
    if tracker_usage.classify_access(surface) == "read" or surface in _UNMIRRORED_WRITES:
        return []
    translate = _MIRRORED_WRITES.get(surface)
    if translate is None:
        raise TrackerDivergenceError(
            f"br {surface} has no owned-ledger translation, so the dual write cannot keep "
            f"the two stores in step; add one to mirror._MIRRORED_WRITES, or list it in "
            f"mirror._UNMIRRORED_WRITES if it states nothing about a record"
        )
    return translate(kit_module, args, stdout)
