r"""Provenance on every edge event: how an edge got there decides what it may do.

Today a dependency edge is just an edge (`work-tracker.md` §9.6, basicly-vkh0.13).
An edge a human asserted during decomposition, an edge an agent proposed from a scope-glob
overlap, and an edge the merge queue deduced after a bounce are **indistinguishable in the
graph** — yet only the first should be trusted, unexamined, to hold up a landing. This
module is the label that tells them apart, and the disposition that label buys:

| Label | How the edge got there | Disposition |
| --- | --- | --- |
| :data:`EXTRACTED` | a human asserted it, or a repo fact implies it | **may gate** a landing |
| :data:`INFERRED` | an agent proposed it, or a bounce implies it | **shown as a proposal** |
| :data:`AMBIGUOUS` | the derivation is uncertain | **routes a decision** |

A repo fact is an import or a file two scope globs share — something a reader can check.
An ``INFERRED`` edge is *usable*, and only that: it is real, it is in the graph, and it is
labelled so nobody mistakes it for a declared dependency. An ``AMBIGUOUS`` one never
gates anything silently.

The three reasons this is schema rather than convention are §9.6's; the one worth
repeating is that it makes the coupling-edge feedback loop honest. When a merge conflict
adds a coupling edge because the decomposition missed one, that edge is an inference from
a single observation. Recording it as ``INFERRED`` keeps a later reader from mistaking it
for a declared dependency, and makes *how often are our inferred couplings right?* a
question the ledger can answer.

## The label is on the event, not on the edge

An edge has no record of its own and nothing here mutates one. An assertion is an event of
kind :data:`KIND_EDGE` on the **source** record, carrying the target, the edge type and the
label; an edge's state is a fold over every event that ever asserted it, in the event log's
canonical order. So the two things AC2 asks for fall out of §4's model rather than being
built: a human confirming an ``INFERRED`` edge **appends a promoting event**, the original
line is untouched, and :attr:`EdgeState.history` reads as the sequence it actually was.

## The strongest label wins, and the one thing that rule does not give you

:attr:`EdgeState.label` is the strongest label any event asserted (§9.6), so a promotion is
monotone: an edge that reached ``EXTRACTED`` stays there. The cost is stated rather than
hidden — **there is no demotion.** Asserting ``AMBIGUOUS`` over an ``EXTRACTED`` edge
records the doubt in the history and changes nothing about the disposition. Withdrawing a
label needs a retraction kind, which this module does not define; adding one later is
additive, and until then the honest reading of an edge's disposition is *the strongest
claim anyone has made*, not *the most recent one*.

## A label this version does not know is not a label it trusts

Forward compatibility is tolerant in the right direction (§4.5) and **fails closed** here,
because the tolerant direction for a *gate* is the restrictive one. A label from a newer
writer is preserved, counted in :attr:`EdgeFold.unknown_labels`, and given the disposition
of the thing we are least sure about: it routes a decision and it cannot gate. Only the
exact string :data:`EXTRACTED` gates, so a label we half-recognise — a truncated one, a
misspelled one — costs a decision item rather than a wrong landing.

That is also why the label, the target and the edge type are **structural** fields, absent
from the event log's size cap by construction (`events.TRUNCATABLE_KEYS`, §4.2): a cut
through ``EXTRACTED`` would change an edge's disposition as a function of how long its
neighbouring text was. The derivation's free text goes under :data:`KEY_DETAIL`, which
*is* capped, which is the whole reason it has that name.

## Strict on write, tolerant on read — and where that diverges from `events.fold`

The write path refuses what it cannot mean: an unknown label, an edge type outside
:data:`EDGE_TYPE_PATTERN`, a target that is not a record id, an edge from a record to
itself. That is validation at the trust boundary, before anything is authoritative.

The read path **reports and skips** a malformed edge event where `events.fold` would
raise, and the divergence is deliberate: a malformed *status* event skipped silently
produces a wrong status, while a malformed *edge* event skipped produces **no edge**, and
an absent edge can never gate anything. So the failure mode of skipping here is a missed
gate that :attr:`EdgeFold.malformed` names, rather than a wrong gate nobody can see — and
one bad line from a foreign writer does not wedge every edge read in the ledger.

## The seam with the record fold, stated because a reader will meet it

`events.fold` delegates :data:`KIND_EDGE` here, under ``delegated_kinds``. That
is correct rather than a defect: an edge is not a record field, there is no record state
for it to fold into, and its totals are counted like any other event's. `events.fold` is
simply an older reader with respect to this kind, which is exactly the case §4.5's
tolerance exists for. **This module is the reader that knows the kind.**

## What this module may not do

Kit rules (§4): **no basicly**, standard library only, no network, no subprocess, no
file access of its own — it reads what a caller read and returns drafts a caller writes.
It reads **no clock**: nothing here has a timestamp to be tempted by. It must also stay
parseable by an interpreter older than this repo's 3.14 floor, so: no syntax newer than
3.9, and one exception class per handler (`.basicly/core/kit/README.md`).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- the sibling event log ----------------------------------------------------

_HERE = Path(__file__).resolve().parent
EVENTS_MODULE_NAME = "basicly_tracker_kit_events"


def _load_events() -> Any:
    """Load ``events.py`` from beside this file, without touching ``sys.path``.

    The same loader `events.py` uses for `ids.py`, for the same reason: the kit is a set
    of sibling files rather than a package, and this is a library inside somebody else's
    process, so ``events`` is a name they may well own. The module name is **public**
    because it is a contract with the caller — a caller loading `events.py` under a
    second name would get a second copy of :class:`events.InvalidEventError`, and
    ``except`` clauses on it would stop matching. Load it from here, or load it under
    this name.

    Returns:
        The loaded module. Typed as :data:`~typing.Any` rather than ``object`` — unlike
        `events.py`'s loader — because this module subclasses one of its exceptions, and
        a base class cannot come from an ``object``-typed name.

    Raises:
        ImportError: ``events.py`` is not beside this file.
    """
    cached = sys.modules.get(EVENTS_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(EVENTS_MODULE_NAME, _HERE / "events.py")
    if spec is None or spec.loader is None:
        raise ImportError("the tracker kit's events.py is missing from beside provenance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[EVENTS_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


events = _load_events()


class InvalidEdgeError(events.InvalidEventError):
    """An edge assertion that cannot be recorded, or a recorded one that cannot be read.

    A subclass of the event log's own :class:`events.InvalidEventError`, so a caller
    wrapping a build-and-append in one ``except events.LedgerError`` catches both halves
    rather than the draft builder's refusal escaping the handler written for the write.
    """


# --- the vocabulary -----------------------------------------------------------

EXTRACTED = "EXTRACTED"
INFERRED = "INFERRED"
AMBIGUOUS = "AMBIGUOUS"

# Order matters only for a stable report; :data:`_STRENGTH` is what ranks them.
LABELS = (AMBIGUOUS, EXTRACTED, INFERRED)

# How much a label is worth when two events disagree. Not exposed as a number: a caller
# comparing strengths is asking about a disposition, and :func:`disposition` answers that
# without inviting a fourth label to be slotted between two existing ones by arithmetic.
_STRENGTH = {AMBIGUOUS: 1, INFERRED: 2, EXTRACTED: 3}

# A label from a newer writer ranks below every label we know, so it can never win a
# promotion contest and can never inherit a stronger label's disposition.
_UNKNOWN_STRENGTH = 0

# What an edge is allowed to do, one per label. Strings rather than an enum: the kit
# targets an interpreter older than this repo's and these values cross a JSON boundary
# into the engine's decision queue, where a string is what arrives anyway.
DISPOSITION_GATE = "gate"
DISPOSITION_PROPOSE = "propose"
DISPOSITION_DECIDE = "decide"

_DISPOSITIONS = {
    EXTRACTED: DISPOSITION_GATE,
    INFERRED: DISPOSITION_PROPOSE,
    AMBIGUOUS: DISPOSITION_DECIDE,
}

# The kind an edge assertion is recorded under, from the one definition (§4.5).
KIND_EDGE = events.KIND_EDGE

# The payload's structural fields. `detail` is the only free-text one, and the only one
# `events.TRUNCATABLE_KEYS` may cut — see the module docstring for why that split is not
# a style choice.
#
# One spelling per field, and these names are it (R2). `br` spells one dependency edge
# `id`/`dependency_type` from one command and `depends_on_id`/`type` from another, and a
# reader of the wrong spelling gets an empty graph rather than an error (basicly-kjc5.10).
KEY_TARGET = "target"
KEY_TYPE = "edge_type"
KEY_LABEL = "provenance"
KEY_DETAIL = "detail"

# The edge type is caller vocabulary — `blocks`, `parent-child`, `discovered-from` — but
# it is still a permanent token, so it is restricted to a shape every surface can round
# trip. A hyphen is fine here and only here: this is a payload value, never part of an id,
# so the commit gate's first-hyphen split (`ids.validate_prefix`) cannot reach it.
EDGE_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# The decision queue's kind for an edge that cannot stand on its own. `validate` is the
# engine's existing entry for *an uncertain machine judgment a human should check*
# (`decisions.KINDS`), which is precisely §9.6's disposition for `AMBIGUOUS` — the point
# of routing there is that the path already exists and is already governed by D2.
DECISION_KIND = "validate"


def strength_of(label: str) -> int:
    """How strong *label* is, for the promotion contest. Unknown ranks below all of them.

    Args:
        label: The label to rank.

    Returns:
        A positive rank for a known label, ``0`` for anything else.
    """
    return _STRENGTH.get(label, _UNKNOWN_STRENGTH)


def disposition(label: str) -> str:
    """What an edge carrying *label* may do.

    Fails closed: a label this version does not know gets :data:`DISPOSITION_DECIDE`,
    never :data:`DISPOSITION_GATE`. Only the exact known string gates.

    Args:
        label: The label to dispose of.

    Returns:
        One of :data:`DISPOSITION_GATE`, :data:`DISPOSITION_PROPOSE` or
        :data:`DISPOSITION_DECIDE`.
    """
    return _DISPOSITIONS.get(label, DISPOSITION_DECIDE)


def validate_label(label: str) -> str:
    """Return *label* unchanged, or refuse it — on the **write side only**.

    The read side deliberately does not call this: a newer writer's label is preserved
    and disposed of conservatively rather than rejected (see the module docstring).

    Args:
        label: The label a caller is about to assert.

    Returns:
        *label*, unchanged.

    Raises:
        InvalidEdgeError: *label* is not one this version can assert.
    """
    if label not in LABELS:
        raise InvalidEdgeError(f"provenance label {label!r} must be one of {LABELS}")
    return label


def validate_edge_type(edge_type: str) -> str:
    """Return *edge_type* unchanged, or refuse it.

    Args:
        edge_type: The relation the edge names, read source-to-target.

    Returns:
        *edge_type*, unchanged.

    Raises:
        InvalidEdgeError: *edge_type* does not match :data:`EDGE_TYPE_PATTERN`.
    """
    if not EDGE_TYPE_PATTERN.match(edge_type):
        raise InvalidEdgeError(f"edge type {edge_type!r} must match {EDGE_TYPE_PATTERN.pattern}")
    return edge_type


# --- the edge -----------------------------------------------------------------


@dataclass(frozen=True, order=True)
class EdgeKey:
    """The identity of one edge: which record, related how, to which record.

    Ordered as well as hashable so a report over a set of edges is stable without the
    caller inventing a sort key — a derived listing that changed order between two runs
    would show up as a diff in every artifact generated from it.

    Attributes:
        source: The record the assertion is recorded on, and the edge's tail.
        edge_type: The relation, **read source-to-target**: ``blocks`` on an edge from A
            to B says A blocks B. The direction is the caller's to keep consistent; this
            module only guarantees it never reverses one.
        target: The record at the head of the edge.
    """

    source: str
    edge_type: str
    target: str

    def as_text(self) -> str:
        """A one-line rendering for a report or a decision question."""
        return f"{self.source} -[{self.edge_type}]-> {self.target}"


@dataclass(frozen=True)
class EdgeAssertion:
    """One event that asserted a label on one edge.

    Attributes:
        key: The edge the event is about.
        label: What the event claimed about how the edge got there.
        event_id: The asserting event's id — the evidence a reader follows back.
        seq: The source record's sequence number for that event, so the history is
            ordered by something the ledger assigned rather than by a clock (§4.1).
        actor: The opaque lease holder that asserted it — a lane, a session, a human.
        detail: Free text naming the derivation. Capped by the event log, never read for
            a decision.
    """

    key: EdgeKey
    label: str
    event_id: str
    seq: int
    actor: str
    detail: str


@dataclass(frozen=True)
class MalformedEdge:
    """An edge event that could not be read, named rather than silently dropped.

    Attributes:
        event_id: The unreadable event's id.
        record: The record it was recorded on.
        reason: What was wrong with it.
    """

    event_id: str
    record: str
    reason: str


@dataclass
class EdgeState:
    """One edge, folded from every event that ever asserted it.

    Attributes:
        key: The edge's identity.
        history: Every assertion, in the event log's canonical order, **never empty**.
            Required rather than defaulted so an edge with no assertion behind it is
            unrepresentable — there is no such thing, and a state that carried one would
            have no label to answer with. Never rewritten: a promotion appends, so this
            reads as the sequence it actually was.
    """

    key: EdgeKey
    history: list[EdgeAssertion]

    @property
    def label(self) -> str:
        """The strongest label any event asserted (§9.6).

        A tie is broken by the later assertion, which matters only among labels this
        version does not know — two known labels of equal strength are the same label.
        """
        best = self.history[0]
        for assertion in self.history[1:]:
            if strength_of(assertion.label) >= strength_of(best.label):
                best = assertion
        return best.label

    @property
    def disposition(self) -> str:
        """What this edge may do, from its strongest label."""
        return disposition(self.label)

    @property
    def gates(self) -> bool:
        """Whether this edge may hold up a landing.

        True for :data:`EXTRACTED` alone. Whether an edge of this *type* blocks anything
        is a separate question the caller owns; this answers only whether the edge's
        provenance is trusted enough to be acted on unexamined.
        """
        return self.disposition == DISPOSITION_GATE

    @property
    def proposal(self) -> bool:
        """Whether this edge is usable but must be shown as a proposal."""
        return self.disposition == DISPOSITION_PROPOSE

    @property
    def needs_decision(self) -> bool:
        """Whether this edge routes a decision item instead of standing on its own."""
        return self.disposition == DISPOSITION_DECIDE


@dataclass
class EdgeFold:
    """The edge fold's output, including what it could not fold.

    Attributes:
        edges: Edge identity to state.
        unknown_labels: Label to count, for a label this version does not know. Reported
            rather than raised (§4.5) — and disposed of as :data:`DISPOSITION_DECIDE`, so
            the report is a warning about a decision that was routed, not about a gate
            that was skipped.
        malformed: Edge events that could not be read at all. Skipped, never guessed at,
            and never silently — see the module docstring for why this reports where
            `events.fold` raises.
    """

    edges: dict[EdgeKey, EdgeState] = field(default_factory=dict)
    unknown_labels: dict[str, int] = field(default_factory=dict)
    malformed: list[MalformedEdge] = field(default_factory=list)


# --- writing an assertion -----------------------------------------------------


def edge_draft(
    key: EdgeKey,
    label: str,
    *,
    detail: str = "",
    actor: str = "",
    generation: int = 1,
) -> Any:
    """A draft asserting *label* on *key*, for `events.append`.

    Pure: it reads nothing and writes nothing, so the caller keeps the lock scope and the
    batching. Everything it can refuse, it refuses here — before anything is
    authoritative — rather than leaving a line the fold would then have to skip.

    Args:
        key: The edge being asserted.
        label: One of :data:`LABELS`. A label this version does not know is refused on
            write even though it is tolerated on read; minting one is a bug, meeting one
            is a newer writer.
        detail: Free text naming the derivation — the bounce, the shared glob, the human.
            Capped by the event log, so it may be cut; nothing branches on it.
        actor: The opaque lease holder asserting it. Falls back to `events.append`'s.
        generation: ``>1`` names a genuine re-assertion of an identical fact. An event id
            is content-derived, so re-asserting the same label with the same detail on the
            same edge is otherwise swallowed as a replay — which is right when it is a
            replay and wrong when a second reviewer really did reach the same conclusion.

    Returns:
        An `events.Draft` on the **source** record.

    Raises:
        InvalidEdgeError: the label, the edge type, either record id, or the edge's
            direction cannot be recorded.
    """
    validate_label(label)
    validate_edge_type(key.edge_type)
    for record in (key.source, key.target):
        try:
            events.ids.validate_record_id(record)
        except events.ids.IdError as exc:
            raise InvalidEdgeError(f"edge {key.as_text()}: {exc}") from exc
    if key.source == key.target:
        # A record that gates its own landing can never be unblocked, and no derivation
        # this vocabulary describes produces one — so it is a caller bug, refused where
        # it is cheap rather than diagnosed later from a wedged lane.
        raise InvalidEdgeError(f"edge {key.as_text()} points a record at itself")
    return events.Draft(
        record=key.source,
        kind=KIND_EDGE,
        payload={
            KEY_TARGET: key.target,
            KEY_TYPE: key.edge_type,
            KEY_LABEL: label,
            KEY_DETAIL: detail,
        },
        actor=actor,
        generation=generation,
    )


def confirmation_draft(key: EdgeKey, *, detail: str = "", actor: str = "") -> Any:
    """A draft promoting *key* to :data:`EXTRACTED` — a human confirming a proposal.

    This is :func:`edge_draft` with the label fixed, and it is a named function because
    the alternative reading of "confirm" is the one this design refuses: it appends a new
    event and **nothing edits the original**, so the promotion is readable as a step in
    :attr:`EdgeState.history` rather than as a label that changed its mind.

    Args:
        key: The edge being confirmed. Taken as a whole rather than as three strings so
            it can be handed straight back from a fold.
        detail: Free text naming who confirmed it and on what evidence.
        actor: The opaque lease holder recording the confirmation.

    Returns:
        An `events.Draft` on the edge's source record.

    Raises:
        InvalidEdgeError: as :func:`edge_draft`.
    """
    return edge_draft(key, EXTRACTED, detail=detail, actor=actor)


# --- reading assertions back --------------------------------------------------


def is_edge_event(event: Any) -> bool:
    """Whether *event* is an edge assertion.

    Args:
        event: Any `events.Event`.

    Returns:
        True when its kind is :data:`KIND_EDGE`.
    """
    return event.kind == KIND_EDGE


def _required_text(payload: Any, key: str) -> str:
    """One required string field of an edge payload.

    Args:
        payload: The event's payload.
        key: The field to read.

    Returns:
        The field's value.

    Raises:
        InvalidEdgeError: it is missing, empty, or not a string.
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidEdgeError(
            f"an {KIND_EDGE} event needs a non-empty string {key}, got {value!r}"
        )
    return value


def read_assertion(event: Any) -> EdgeAssertion:
    """The assertion one edge event carries.

    The label is **not** validated here — that is the read side, where a newer writer's
    label is preserved and disposed of conservatively. What is required is only what an
    edge cannot exist without: a target, a type and a label string to carry forward.

    Args:
        event: An `events.Event` of kind :data:`KIND_EDGE`.

    Returns:
        The assertion, keyed on the event's own record as the edge's source.

    Raises:
        InvalidEdgeError: the event is not an edge event, or its payload is missing a
            structural field.
    """
    if not is_edge_event(event):
        raise InvalidEdgeError(f"event {event.id} is kind {event.kind!r}, not {KIND_EDGE!r}")
    payload = event.payload
    key = EdgeKey(
        source=event.record,
        edge_type=_required_text(payload, KEY_TYPE),
        target=_required_text(payload, KEY_TARGET),
    )
    detail = payload.get(KEY_DETAIL, "")
    if not isinstance(detail, str):
        raise InvalidEdgeError(f"an {KIND_EDGE} event needs string {KEY_DETAIL}, got {detail!r}")
    return EdgeAssertion(
        key=key,
        label=_required_text(payload, KEY_LABEL),
        event_id=event.id,
        seq=event.seq,
        actor=event.actor,
        detail=detail,
    )


def fold_edges(collected: Iterable[Any]) -> EdgeFold:
    """Fold *collected* events into per-edge state, in canonical order.

    Non-edge events are ignored, so a caller hands this whatever `events.read_events`
    returned rather than filtering first. The order is `events.canonical_order`'s and is
    not re-derived here: the result is a function of the event **set**, so a shuffled log,
    a reversed log and a union merge's arbitrary concatenation all fold to the same edges
    with the same histories.

    Args:
        collected: Any events. Duplicates by id are folded once.

    Returns:
        The fold, including the labels and the events it could not read.
    """
    result = EdgeFold()
    for event in events.canonical_order(collected):
        if not is_edge_event(event):
            continue
        try:
            assertion = read_assertion(event)
        except InvalidEdgeError as exc:
            result.malformed.append(MalformedEdge(event.id, event.record, str(exc)))
            continue
        if assertion.label not in LABELS:
            count = result.unknown_labels.get(assertion.label, 0)
            result.unknown_labels[assertion.label] = count + 1
        state = result.edges.get(assertion.key)
        if state is None:
            result.edges[assertion.key] = EdgeState(key=assertion.key, history=[assertion])
        else:
            state.history.append(assertion)
    return result


# --- what the fold is for -----------------------------------------------------


def edges_by_disposition(
    edge_fold: EdgeFold, wanted: str, *, source: str | None = None
) -> tuple[EdgeState, ...]:
    """Every edge whose disposition is *wanted*, in :class:`EdgeKey` order.

    Args:
        edge_fold: A :func:`fold_edges` result.
        wanted: One of the ``DISPOSITION_*`` values.
        source: Restrict to edges out of this record. ``None`` means every record.

    Returns:
        The matching states, ordered.
    """
    return tuple(
        edge_fold.edges[key]
        for key in sorted(edge_fold.edges)
        if edge_fold.edges[key].disposition == wanted and (source is None or key.source == source)
    )


def gating_edges(edge_fold: EdgeFold, source: str | None = None) -> tuple[EdgeState, ...]:
    """The edges whose provenance permits them to hold up a landing.

    :data:`EXTRACTED` only. An ``INFERRED`` edge is real and usable — it is in the fold,
    and :func:`edges_by_disposition` returns it as a proposal — but a landing that stopped
    for it would be stopped by a machine's guess with no human in the loop, which is the
    failure §9.6 exists to prevent.

    Args:
        edge_fold: A :func:`fold_edges` result.
        source: Restrict to edges out of this record — the usual call, when deciding
            whether one record may land.

    Returns:
        The gating states, ordered.
    """
    return edges_by_disposition(edge_fold, DISPOSITION_GATE, source=source)


@dataclass(frozen=True)
class EdgeDecision:
    """One decision-queue item an uncertain edge routes.

    Data, not an action: the kit may not reach the engine's queue, so it produces the
    item and the engine enqueues it (`decisions.enqueue`).

    Attributes:
        record: The record the item is queued on — the edge's source.
        kind: :data:`DECISION_KIND`.
        question: The judgment being asked for. Derived from the edge's identity **and
            nothing else**, so it is stable as the edge's history grows: the queue's ids
            are content-derived from ``(kind, question)``, and a question that drifted
            would re-enqueue the same uncertainty under a new id on every read.
        detail: The provenance history, so the answerer sees what was claimed and by whom
            without going back to the log.
        key: The edge, so an answer can be recorded against it.
    """

    record: str
    kind: str
    question: str
    detail: str
    key: EdgeKey


def _history_detail(state: EdgeState) -> str:
    """The edge's provenance history as one line per assertion, in order.

    The sequence number is spelled out rather than used as an ordinal: it is the source
    record's own numbering, so an item's first line can read ``seq 3`` and a reader who
    took it for "the third assertion" would be counting a different thing. Spelled out, it
    is what it is — the number that finds the asserting event back in the log.
    """
    return "\n".join(
        f"seq {assertion.seq}: {assertion.label}"
        f" by {assertion.actor or '<unattributed>'}"
        f"{' — ' + assertion.detail if assertion.detail else ''}"
        for assertion in state.history
    )


def decision_requests(edge_fold: EdgeFold) -> tuple[EdgeDecision, ...]:
    """One decision item per edge that may neither gate nor stand as a proposal.

    That is every :data:`AMBIGUOUS` edge and every edge whose label this version does not
    know — the fail-closed half of the same rule.

    Args:
        edge_fold: A :func:`fold_edges` result.

    Returns:
        The items to enqueue, in :class:`EdgeKey` order.
    """
    return tuple(
        EdgeDecision(
            record=state.key.source,
            kind=DECISION_KIND,
            question=f"Does the edge {state.key.as_text()} hold?",
            detail=_history_detail(state),
            key=state.key,
        )
        for state in edges_by_disposition(edge_fold, DISPOSITION_DECIDE)
    )
