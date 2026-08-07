r"""The append-only event log: the tracker kit's authoritative store.

Every change to a record is a new line; a record's state is a **fold** over its events
(`design/work-tracker.md` §4, basicly-vkh0.11). Nothing here rewrites a line, and no
other file is authoritative — the snapshot, the index and the edge list are derived and
disposable, which is what makes a corrupt derivative something you delete rather than
repair.

The two properties this module exists to guarantee, both of which are easy to *claim*
and are asserted in `tests/test_kit_tracker_events.py` instead:

## 1. The fold is a function of the event set, not of the file's append order

Idempotency by event id handles union-merge **duplication** and does nothing about
**ordering**, and status is inherently ordered — ``open → in_progress → done`` and a
``done → open`` reopen fold to different states. So every event carries a **per-item
integer sequence number**: the writer reads the item's current max and writes max+1,
ties break by event id, and :func:`fold` **sorts into that canonical order before
folding** (§4.1). A commutative fold over raw events would be a CRDT, which §15 rejects;
one integer field and one sort rule is the whole cost.

Sequence is *per item*, not per ledger: a ledger-wide counter would put every item behind
one number and fork on every branch. Two branches incrementing the same item's sequence
produce a **visible fork** — :attr:`FoldResult.forked` names it, and §4.6's rule is that a
forked item's carried totals are void until a fold restates them. A conflict you can see
beats a state you cannot explain.

## 2. Nothing branches on a wall clock

A timestamp is **evidence, never a constraint** (§9.5). `br` validates
``updated_at >= created_at`` and hard-errors when the host clock steps backwards, which an
unconverged NTP resync does routinely — that turns a host's clock into a source of tracker
failures mid-landing (R1). Here the clock is an injected argument, ``ts`` is recorded and
read by nobody, and it is **excluded from the event id digest** — so appending the same
logical events under two different clocks produces logs with identical ids, sequences,
totals and folded state, differing only in the field nothing reads. That is the test.

Where this module needs a duration it takes it from a **monotonic** clock, also injected:
the lock's staleness bound (§4.4). A wall-clock age can come out negative after a
backwards step and would then never expire.

## The carried totals, and the one accumulator

Every event carries the item's running aggregates as they hold immediately after it
(§4.6), so the common query — *what is this item's spend, how many attempts, how many
events* — is answered by the item's last event instead of a fold. The rules that keep that
from becoming a second source of truth:

- **The fold is the authority; a carried total is a cache that lives in the log.** `fsck`
  (basicly-vkh0.15) recomputes the fold and reports every disagreement as a **finding,
  never a repair in place**.
- **One accumulator, called from both sides.** :func:`accumulate` is called by the writer
  and by the fold; there is no hand-written increment anywhere. Two copies that disagree is
  the defect this repo keeps paying for — `session_issue_ids` disagreed by 14 beads
  (`basicly-tcmy.30`), the context ceiling disagreed about a bead's whole fate
  (`basicly-7kxq`).
- **Only pure functions of the events qualify.** Counts, integer sums, and the last status.
  Spend is summed in **integer micro-units** rather than floats: a float sum is exact only
  for the order it was taken in, and a cached total that depends on summation order is not a
  pure function of the event set.
- **An unknown kind still counts.** The fold skips a kind it does not know *for state* but
  counts it in the totals, because a newer writer's carried totals counted it — an old
  reader that skipped it entirely would report every later event as a false disagreement.

## The one trap a caller has to know about

An event id is derived from its kind and payload, which is what makes a replayed write
idempotent. The corollary is the trap: **re-recording a fact you have already recorded is
swallowed, even when you mean it.** A record going ``done → open`` records ``open`` for the
second time, so that draft's id is the id of the first ``open`` event and :func:`append`
skips it as a replay — the reopen never lands. Pass ``generation=2`` on the draft, which is
what §9.4 has that parameter for: it names a genuine re-recording of an identical fact and
gives it its own id rather than collapsing it into the first.

## Forward compatibility, in the tolerant direction

The fold **skips unknown kinds and unknown fields and preserves them verbatim** on any
rewrite (§4.5), so a round-trip through an old reader loses nothing. The discipline no rule
can enforce: **never change a kind's meaning, never reuse a kind name, only add kinds and
optional fields.** A new kind that semantically supersedes an old one makes old readers
silently wrong, and nothing here can detect that.

Two things §4.5 asks for are deliberately **not** here. The ledger-scoped
``format_version`` event has no item to carry a per-item sequence, so it needs a
ledger-record shape this module does not define, and the reader-refuses-to-write behaviour
it gates belongs with `fsck`. Edge events and their provenance labels are
basicly-vkh0.13's; adding a kind is additive by construction.

## What this module may not do

Kit rules (§4): **no basicly**, standard library only, no network, no subprocess. It reads
and writes its own ledger directory and takes everything else — the clock, the actor, the
lock's platform answers, the redactor — as an argument. It must also stay parseable by an
interpreter older than this repo's 3.14 floor, so: no syntax newer than 3.9, and one
exception class per handler (`.basicly/core/kit/README.md`).

**It needs one thing from its deployment that it cannot do itself**: `events-*.jsonl` must
be declared ``-text`` in `.gitattributes`, or a Windows ``autocrlf`` checkout rewrites the
ledger in place. Every ``open()`` here passes ``encoding="utf-8"`` and ``newline="\n"``,
which controls what *we* write and not what git does on checkout.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --- errors -------------------------------------------------------------------


class LedgerError(Exception):
    """Anything this module refuses. Base class so a caller can catch the family."""


class InvalidEventError(LedgerError):
    """A draft or a parsed line that cannot become an event."""


class LockUnavailableError(LedgerError):
    """The ledger's writer lock could not be taken before the timeout.

    ``retryable`` is a class attribute rather than prose because R7/R8 make it the
    contract: contention waits, and a wait that gives up **says so**, so the caller backs
    off instead of the gate failing. `br` marked this class of failure
    ``retryable: false`` and cost three lanes their rework budget (basicly-vkh0.10).
    """

    retryable = True


# --- the sibling id module ----------------------------------------------------

_HERE = Path(__file__).resolve().parent
_IDS_MODULE_NAME = "basicly_tracker_kit_ids"


def _load_ids() -> object:
    """Load ``ids.py`` from beside this file, without touching ``sys.path``.

    The kit is a set of sibling files rather than a package, so a relative import is not
    available. `claude_tier_hook.py` inserts its own directory at the front of
    ``sys.path`` and imports by bare name; that is right for a hook script and wrong here,
    because this is a library loaded into somebody else's process and ``ids`` is a name
    they may well own. Loading by path under a qualified name mutates nothing of theirs.
    """
    cached = sys.modules.get(_IDS_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_IDS_MODULE_NAME, _HERE / "ids.py")
    if spec is None or spec.loader is None:
        raise LedgerError("the tracker kit's ids.py is missing from beside events.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_IDS_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


ids = _load_ids()

# --- the ledger's shape -------------------------------------------------------

# Read by contract, not by convention: `rebuild` and `fsck` glob the same pattern, and a
# test fails if it narrows (basicly-vkh0.14). Files are ordered lexicographically by
# name, which is why a rotation period must be written so it sorts in period order —
# `events-2026.jsonl` does, and the zero-padded initial name sorts before every year.
LOG_GLOB = "events-*.jsonl"
INITIAL_LOG_NAME = "events-0001.jsonl"

LOCK_NAME = ".events.lock"

# Free-text payload keys the size cap may cut. Structural fields are absent on purpose
# (§4.2): truncating an id, a sequence, a kind, a status, a provenance label or a total
# would make a derived value depend on the cap. A caller with another free-text key adds
# it here rather than inventing a second cap.
TRUNCATABLE_KEYS = frozenset({"text", "value", "output", "detail"})

# Per free-text field, in bytes. Sized from §4.4's interleave exposure: Python's buffered
# writer flushes in ~8 KiB chunks, so a logical line larger than that becomes several
# syscalls a concurrent appender could interleave between. The cap **bounds** that
# exposure; the lock is what eliminates it — an event carrying several text fields can
# still exceed one chunk, which is exactly why `O_APPEND` is not the guarantee.
BUFFER_CHUNK_BYTES = 8192
MAX_TEXT_BYTES = 4096

# Lock timing. The expected hold is milliseconds — read the log, append, release — so the
# stale bound is thousands of times the hold rather than a guess at a slow machine.
DEFAULT_LOCK_TIMEOUT_S = 5.0
LOCK_STALE_AFTER_S = 30.0
LOCK_POLL_S = 0.01

# A steal removes another writer's lock file, so the loop that does it is bounded: a
# healthy contest resolves in one or two, and a run of them means the staleness answers
# are wrong rather than that the lock keeps going stale.
MAX_LOCK_STEALS = 8

KIND_CREATED = "created"
KIND_FIELD = "field"
KIND_STATUS = "status"
KIND_COMMENT = "comment"
KIND_DISPATCH = "dispatch"
KIND_TOMBSTONE = "tombstone"

# A kind is a permanent vocabulary entry, so it is restricted to a shape that can be read
# back from any surface. Free text would let a writer mint `status ` or `Status` beside
# `status` and split one meaning across three kinds.
KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# The visible label on an event id. Uniform rather than the event's kind, because a kind
# is vocabulary this module does not control and `ids.FAMILY_PATTERN` would reject one
# carrying an underscore — an id that sometimes has a family and sometimes does not is
# worse than one that always looks the same.
EVENT_FAMILY = "ev"

# The fields this version understands. Anything else on a parsed line is preserved
# verbatim in `Event.extra` and written back out unchanged.
KNOWN_FIELDS = frozenset({"id", "record", "seq", "kind", "actor", "ts", "payload", "totals"})


# --- totals -------------------------------------------------------------------


@dataclass(frozen=True)
class Totals:
    """One item's running aggregates as they hold immediately after one event.

    Attributes:
        events: Events on this item up to and including the one carrying these totals.
        attempts: ``dispatch`` events among them.
        spend_micros: Sum of the ``spend_micros`` payload field, in millionths of a
            currency unit. Integer so the sum is exact and independent of summation order.
        status: The item's status as of this event, or ``None`` before its first.
    """

    events: int = 0
    attempts: int = 0
    spend_micros: int = 0
    status: str | None = None

    def as_dict(self) -> dict[str, object]:
        """The JSON form written on an event line."""
        return {
            "events": self.events,
            "attempts": self.attempts,
            "spend_micros": self.spend_micros,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Totals:
        """Read the JSON form back, refusing a shape that would corrupt a later sum.

        Raises:
            InvalidEventError: a count or a sum is not an integer.
        """
        values = {}
        for name in ("events", "attempts", "spend_micros"):
            value = raw.get(name, 0)
            if not _is_int(value):
                raise InvalidEventError(f"totals.{name} must be an integer, got {value!r}")
            values[name] = int(value)  # type: ignore[arg-type]
        status = raw.get("status")
        if status is not None and not isinstance(status, str):
            raise InvalidEventError(f"totals.status must be a string or null, got {status!r}")
        return cls(status=status, **values)


def accumulate(previous: Totals, kind: str, payload: Mapping[str, object]) -> Totals:
    """The item's totals after an event of *kind* carrying *payload*.

    **The one accumulator** (§4.6). :func:`append` calls it to stamp the totals onto the
    event it is minting, and :func:`fold` calls it to recompute them; there is no second
    implementation to disagree with this one. It takes the event's parts rather than an
    :class:`Event` because the writer has not minted the event yet — that asymmetry is why
    a hand-written increment on the write side is the tempting mistake.

    Every kind counts toward ``events``, including one this version does not know: a
    newer writer's carried totals counted it, so skipping it here would make every later
    event look like a disagreement to `fsck`.

    Raises:
        InvalidEventError: ``spend_micros`` is present and not an integer. A float would
            make the running sum depend on the order it was taken in.
    """
    spend = payload.get("spend_micros", 0)
    if not _is_int(spend):
        raise InvalidEventError(
            f"spend_micros must be an integer number of micro-units, got {spend!r}: "
            f"a float sum is exact only for the order it was taken in"
        )
    status = previous.status
    if kind == KIND_STATUS:
        recorded = payload.get("status")
        if not isinstance(recorded, str):
            raise InvalidEventError(
                f"a {KIND_STATUS} event needs a string status, got {recorded!r}"
            )
        status = recorded
    return Totals(
        events=previous.events + 1,
        attempts=previous.attempts + (1 if kind == KIND_DISPATCH else 0),
        spend_micros=previous.spend_micros + int(spend),  # type: ignore[arg-type]
        status=status,
    )


def _is_int(value: object) -> bool:
    """True for a genuine integer. ``bool`` is excluded: ``True + 1`` is 2, silently."""
    return isinstance(value, int) and not isinstance(value, bool)


# --- events -------------------------------------------------------------------


@dataclass(frozen=True)
class Draft:
    """What a caller hands :func:`append`: everything except what the ledger assigns.

    Attributes:
        record: The record this event is about.
        kind: The vocabulary entry, matching :data:`KIND_PATTERN`.
        payload: The fact. String values are redacted; those under
            :data:`TRUNCATABLE_KEYS` are also capped.
        actor: An opaque lease holder — a lane, a session, a human. Not
            assignee-as-person modelling (§4.5). Falls back to :func:`append`'s *actor*.
        generation: ``>1`` names a genuine re-recording of an identical fact, which needs
            its own id rather than collapsing into the first by content.
    """

    record: str
    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)
    actor: str = ""
    generation: int = 1


@dataclass(frozen=True)
class Event:
    """One appended line.

    Attributes:
        id: Content-derived, so re-recording the same fact is idempotent rather than
            duplicated. ``ts`` is **not** part of the digest.
        record: The item the event is about.
        seq: The item's sequence number, ``max + 1`` at write time (§4.1).
        kind: The vocabulary entry. One this version does not know is still an event.
        actor: The opaque lease holder.
        ts: Wall-clock evidence, ISO-8601 UTC. Nothing reads it.
        payload: The fact.
        totals: The item's aggregates immediately after this event (§4.6).
        extra: Fields a newer writer added, preserved verbatim through a round-trip.
    """

    id: str
    record: str
    seq: int
    kind: str
    actor: str
    ts: str
    payload: Mapping[str, object] = field(default_factory=dict)
    totals: Totals = field(default_factory=Totals)
    extra: Mapping[str, object] = field(default_factory=dict)


def canonical_key(event: Event) -> tuple[str, int, str]:
    """The sort key that makes the fold a function of the event set.

    ``(record, seq, id)``. The id is the tie-break §4.1 requires: two branches that both
    wrote sequence *n* on one item fold in a stable order rather than in whichever order
    a union merge happened to concatenate their hunks. A timestamp may not appear here —
    one skewed clock would resurrect a ``done`` item.
    """
    return (event.record, event.seq, event.id)


def canonical_order(events: Iterable[Event]) -> list[Event]:
    """*events* in canonical order, with a repeated id kept once.

    Deduplication is by id and happens **before** the order is fixed, so a duplicate
    arriving from a union merge cannot change the result. The first occurrence in
    canonical order wins, which is well defined because equal ids sort together.
    """
    seen: set[str] = set()
    ordered = []
    for event in sorted(events, key=canonical_key):
        if event.id in seen:
            continue
        seen.add(event.id)
        ordered.append(event)
    return ordered


def event_id_for(
    record: str, kind: str, payload: Mapping[str, object], *, generation: int = 1
) -> str:
    """The content-derived id of one event.

    The digest covers the kind and a canonical JSON rendering of the payload, and
    **nothing else** — in particular not the timestamp, not the sequence number and not
    the actor. That exclusion is what makes the same logical event minted under two
    clocks, or on two branches, carry one id (§9.4, §9.5).

    Raises:
        IdError: *record* is not a record id, from `ids.evidence_id`.
    """
    content = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return ids.evidence_id(  # type: ignore[attr-defined]
        record, kind, content, family=EVENT_FAMILY, generation=generation
    )


def to_json(event: Event) -> str:
    """One ledger line, without its newline.

    ``sort_keys`` rather than a declared field order, so a round-trip is byte-identical
    however a writer built the object — the upgradability property §14 asserts. Unknown
    fields are written first and then overwritten by the known ones, so a newer writer's
    field is preserved and can never shadow ``id`` or ``seq``.
    """
    obj: dict[str, object] = dict(event.extra)
    obj.update({
        "id": event.id,
        "record": event.record,
        "seq": event.seq,
        "kind": event.kind,
        "actor": event.actor,
        "ts": event.ts,
        "payload": dict(event.payload),
        "totals": event.totals.as_dict(),
    })
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def from_json(line: str) -> Event:
    """Parse one ledger line.

    Raises:
        InvalidEventError: the line is not a JSON object, or a known field is missing or
            of the wrong type. Callers that must survive a corrupt line — the fold, `fsck`
            — catch this and quarantine rather than raise.
    """
    try:
        raw = json.loads(line)
    except ValueError as exc:
        raise InvalidEventError(f"not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise InvalidEventError(f"not a JSON object: {type(raw).__name__}")
    for name in ("id", "record", "kind", "actor", "ts"):
        if not isinstance(raw.get(name), str):
            raise InvalidEventError(f"{name} must be a string, got {raw.get(name)!r}")
    if not _is_int(raw.get("seq")):
        raise InvalidEventError(f"seq must be an integer, got {raw.get('seq')!r}")
    payload = raw.get("payload", {})
    if not isinstance(payload, dict):
        raise InvalidEventError(f"payload must be an object, got {type(payload).__name__}")
    totals = raw.get("totals", {})
    if not isinstance(totals, dict):
        raise InvalidEventError(f"totals must be an object, got {type(totals).__name__}")
    return Event(
        id=raw["id"],
        record=raw["record"],
        seq=int(raw["seq"]),
        kind=raw["kind"],
        actor=raw["actor"],
        ts=raw["ts"],
        payload=payload,
        totals=Totals.from_dict(totals),
        extra={key: value for key, value in raw.items() if key not in KNOWN_FIELDS},
    )


# --- the fold -----------------------------------------------------------------


@dataclass
class RecordState:
    """One item's state, folded from its events.

    Attributes:
        record: The item's id.
        status: Its status, or ``None`` if no ``status`` event has landed.
        fields: Values set by ``created`` and ``field`` events.
        comments: ``comment`` texts in canonical order.
        tombstoned: A ``tombstone`` event has landed. The record stays in the fold: a
            delete leaves a tombstone rather than removing anything, or a later mint hands
            its id to a new record and the history reads as one record changing its mind.
        totals: Recomputed by :func:`accumulate` — the authority the carried totals are
            checked against.
        max_seq: The highest sequence number seen, which is what the next append reads.
    """

    record: str
    status: str | None = None
    fields: dict[str, object] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)
    tombstoned: bool = False
    totals: Totals = field(default_factory=Totals)
    max_seq: int = 0


@dataclass
class FoldResult:
    """The fold's output, including what it could not fold.

    Attributes:
        records: Item id to state.
        unknown_kinds: Kind to count, for a kind this version does not know. A warning,
            never an error — an old reader hitting a newer ledger must not report false
            corruption (§4.5).
        duplicate_ids: Ids that appeared more than once, folded once.
        forked: Items where two distinct events share a sequence number. §4.6's rule is
            that a forked item's **carried** totals are void until a fold restates them;
            :attr:`RecordState.totals` is that restatement.
        mismatched_totals: Ids whose carried totals disagree with the fold. A finding for
            `fsck` to report, **never** a repair in place (§4.4).
    """

    records: dict[str, RecordState] = field(default_factory=dict)
    unknown_kinds: dict[str, int] = field(default_factory=dict)
    duplicate_ids: list[str] = field(default_factory=list)
    forked: list[str] = field(default_factory=list)
    mismatched_totals: list[str] = field(default_factory=list)


def _apply_created(state: RecordState, payload: Mapping[str, object]) -> None:
    """A record comes into existence carrying its initial fields."""
    state.fields.update(payload)


def _apply_field(state: RecordState, payload: Mapping[str, object]) -> None:
    """One field takes a new value."""
    name = payload.get("name")
    if not isinstance(name, str):
        raise InvalidEventError(f"a {KIND_FIELD} event needs a string name, got {name!r}")
    state.fields[name] = payload.get("value")


def _apply_status(state: RecordState, payload: Mapping[str, object]) -> None:
    """The status moves. Ordered, which is the whole reason sequence numbers exist."""
    state.status = payload["status"]  # type: ignore[assignment]


def _apply_comment(state: RecordState, payload: Mapping[str, object]) -> None:
    """A comment is appended. Comments are 45% of this repo's tracker traffic."""
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise InvalidEventError(f"a {KIND_COMMENT} event needs string text, got {text!r}")
    state.comments.append(text)


def _apply_tombstone(state: RecordState, payload: Mapping[str, object]) -> None:  # noqa: ARG001
    """The record is deleted, and stays in the log saying so."""
    state.tombstoned = True


# `dispatch` is known and changes no state — it is counted by the accumulator, which is
# the point of `attempts`. Mapping it to a no-op function instead would read as an
# oversight the next time somebody looked for its handler.
_HANDLERS: dict[str, Callable[[RecordState, Mapping[str, object]], None]] = {
    KIND_CREATED: _apply_created,
    KIND_FIELD: _apply_field,
    KIND_STATUS: _apply_status,
    KIND_COMMENT: _apply_comment,
    KIND_TOMBSTONE: _apply_tombstone,
}
KNOWN_KINDS = frozenset(_HANDLERS) | {KIND_DISPATCH}


def _resumed(state: RecordState) -> RecordState:
    """A copy of *state*, so folding onto a checkpoint never mutates the caller's records."""
    return RecordState(
        record=state.record,
        status=state.status,
        fields=dict(state.fields),
        comments=list(state.comments),
        tombstoned=state.tombstoned,
        totals=state.totals,
        max_seq=state.max_seq,
    )


def fold(events: Iterable[Event], *, seed: Mapping[str, RecordState] | None = None) -> FoldResult:
    """Fold *events* into per-item state, in canonical order.

    The sort is the contract: the result is a function of the event **set**, so a shuffled
    log, a reversed log and a log a union merge concatenated in an arbitrary side-order all
    fold to the same state. An unknown kind is counted and reported, never folded and never
    an error.

    *seed* resumes the fold from state somebody already folded — the rotation checkpoint
    (`snapshot.py`, basicly-vkh0.14), so steady state folds a checkpoint plus the current
    file instead of the whole archive. It exists so there is **one** fold rather than a
    second implementation that applies an event to an existing state; the seed is copied,
    never mutated. It carries one limitation the from-scratch fold does not: a seeded item's
    already-claimed sequence numbers are not in the checkpoint, so a fork straddling the
    boundary is invisible here and only a full-history fold reports it.

    Raises:
        InvalidEventError: a **known** kind carries a payload it cannot mean — a ``field``
            with no name, a ``status`` with no status. Refused rather than skipped: a
            reader that guesses at a malformed event of a kind it *does* know produces a
            wrong answer with no evidence of having done so.
    """
    collected = list(events)
    result = FoldResult()
    if seed is not None:
        result.records = {name: _resumed(state) for name, state in seed.items()}
    counts: dict[str, int] = {}
    for event in collected:
        counts[event.id] = counts.get(event.id, 0) + 1
    result.duplicate_ids = sorted(key for key, count in counts.items() if count > 1)
    # The ordering and the dedup are :func:`canonical_order`'s and are not repeated here:
    # a second copy of the rule that decides which of two same-id events wins is exactly
    # the shape of defect this design keeps paying for.
    ordered = canonical_order(collected)
    claimed: dict[str, set[int]] = {}
    for event in ordered:
        state = result.records.setdefault(event.record, RecordState(record=event.record))
        sequences = claimed.setdefault(event.record, set())
        if event.seq in sequences and event.record not in result.forked:
            result.forked.append(event.record)
        sequences.add(event.seq)
        state.max_seq = max(state.max_seq, event.seq)
        state.totals = accumulate(state.totals, event.kind, event.payload)
        if event.totals != state.totals:
            result.mismatched_totals.append(event.id)
        handler = _HANDLERS.get(event.kind)
        if handler is not None:
            handler(state, event.payload)
        elif event.kind != KIND_DISPATCH:
            result.unknown_kinds[event.kind] = result.unknown_kinds.get(event.kind, 0) + 1
    return result


# --- reading ------------------------------------------------------------------


@dataclass(frozen=True)
class Quarantine:
    """A line the fold could not parse, named by position and never edited.

    `fsck` repairs only by appending corrective events (§4.4); an editor that "fixed" a
    line would stop the log being the truth.
    """

    path: Path
    line_number: int
    line: str
    reason: str


def log_paths(directory: Path | str) -> list[Path]:
    """Every log file in *directory*, in lexicographic name order.

    :data:`LOG_GLOB` is the contract `rebuild` and `fsck` share. Rotated files are
    archived and never pruned, because a full-history fold is requirement 6.
    """
    return sorted(Path(directory).glob(LOG_GLOB))


def read_log(path: Path | str) -> tuple[list[Event], list[Quarantine]]:
    """Parse one log file into events and quarantined lines.

    **One unparseable trailing line is tolerated silently, and only when the file does not
    end in a newline.** That combination is the torn-write signature — a crash between the
    payload and the newline — and it is the one case where a lost line is expected rather
    than a finding. An unparseable line that *is* newline-terminated was written whole and
    is interior garbage wherever it sits, so it is quarantined like any other.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], []
    complete = text.endswith("\n")
    lines = text.splitlines()
    events: list[Event] = []
    quarantined: list[Quarantine] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            events.append(from_json(line))
        except InvalidEventError as exc:
            torn = number == len(lines) and not complete
            if not torn:
                quarantined.append(Quarantine(file_path, number, line, str(exc)))
    return events, quarantined


def read_events(directory: Path | str) -> tuple[list[Event], list[Quarantine]]:
    """Every event in the ledger, plus every quarantined line, across all log files."""
    events: list[Event] = []
    quarantined: list[Quarantine] = []
    for path in log_paths(directory):
        found, bad = read_log(path)
        events.extend(found)
        quarantined.extend(bad)
    return events, quarantined


def append_target(directory: Path | str) -> Path:
    """The file the next append goes to: the last log, or the first one if none exists.

    Rotation is basicly-vkh0.14's. It needs no cooperation from this function and gets
    none: a rotation policy creates ``events-2027.jsonl``, that name sorts last, and this
    returns it. Choosing a period here would put a wall-clock branch (*which year is it*)
    on the write path, which §9.5 forbids.
    """
    paths = log_paths(directory)
    return paths[-1] if paths else Path(directory) / INITIAL_LOG_NAME


# --- the writer's lock --------------------------------------------------------


def default_pid_liveness(pid: int) -> bool | None:
    """Whether *pid* is running: ``True``, ``False``, or ``None`` for *cannot tell*.

    ``None`` is not laziness. On Windows ``os.kill(pid, 0)`` does not send a signal — it
    calls ``TerminateProcess``, so the idiomatic POSIX liveness probe would **kill the
    process it was asking about**. There is no stdlib answer there, so the platform
    reports "unknown" and the staleness bound decides alone. A caller with a better answer
    injects one, which is also how a test makes all three answers test data instead of a
    property of whichever machine ran it.
    """
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive and owned by somebody else. Not ours to steal on liveness grounds.
        return True
    return True


class LedgerLock:
    """The ledger's single-writer lock: ``O_CREAT|O_EXCL`` plus a steal rule.

    ``fcntl.flock`` does not exist on Windows and §12 commits to three platforms, so the
    lock is a file whose existence is the lock. That needs an escape hatch, because a
    crashed writer's lock file would otherwise wedge every lane forever: the file carries
    the holder's pid and a **monotonic** reading, and it is stolen when the pid is known
    dead, when the reading is from another monotonic epoch, or when the hold outlives
    :data:`LOCK_STALE_AFTER_S`.

    Monotonic rather than wall clock, and this is the one place a cross-process monotonic
    comparison is sound: on all three platforms the monotonic clock counts from system
    start, so two processes on one host read the same scale. Across a reboot they do not,
    which shows up as a **negative** age — and a negative age is treated as stale, because
    a holder from before the reboot cannot be running.

    Use it as a context manager. :func:`append` takes it for the duration of one append;
    a caller needing a wider critical section — §4.5's locked read-check-write for a claim,
    where two CLI calls would let two lanes take the same item — holds it and passes it in.
    """

    def __init__(  # noqa: PLR0913 — one keyword per injected seam; see the class docstring
        self,
        directory: Path | str,
        *,
        timeout_s: float = DEFAULT_LOCK_TIMEOUT_S,
        stale_after_s: float = LOCK_STALE_AFTER_S,
        poll_s: float = LOCK_POLL_S,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        pid: int | None = None,
        is_alive: Callable[[int], bool | None] = default_pid_liveness,
    ) -> None:
        """Build a lock over *directory*. Nothing is acquired until :meth:`acquire`."""
        self.path = Path(directory) / LOCK_NAME
        self._timeout_s = timeout_s
        self._stale_after_s = stale_after_s
        self._poll_s = poll_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._pid = os.getpid() if pid is None else pid
        self._is_alive = is_alive
        self._held = False
        self.steals = 0

    @property
    def held(self) -> bool:
        """Whether this instance currently holds the lock."""
        return self._held

    def acquire(self) -> LedgerLock:
        """Take the lock, waiting and stealing as the rules allow.

        Raises:
            LockUnavailableError: a live holder kept it past *timeout_s*, or the steal
                bound was reached. Marked ``retryable`` so the caller backs off (R8).
        """
        deadline = self._monotonic() + self._timeout_s
        while True:
            if self._try_create():
                self._held = True
                return self
            if self._steal_if_stale():
                if self.steals > MAX_LOCK_STEALS:
                    raise LockUnavailableError(
                        f"{self.path} went stale {self.steals} times in one acquire: "
                        f"the staleness answers are wrong, not the lock"
                    )
                continue
            if self._monotonic() >= deadline:
                raise LockUnavailableError(
                    f"another writer holds {self.path} after {self._timeout_s}s"
                )
            self._sleep(self._poll_s)

    def release(self) -> None:
        """Drop the lock, and only ours.

        A lock we were holding may have been stolen — our hold outran the stale bound and
        another writer took it. Removing the file then would delete *their* lock and let a
        third writer in, so the pid is checked first.
        """
        self._held = False
        record = self._read_holder()
        if record is not None and record.get("pid") != self._pid:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            return

    def __enter__(self) -> LedgerLock:
        """Acquire on entry."""
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        """Release on exit, however the block ended."""
        self.release()

    def _try_create(self) -> bool:
        """Create the lock file exclusively, writing who holds it and since when."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump({"pid": self._pid, "monotonic": self._monotonic()}, stream, sort_keys=True)
        return True

    def _read_holder(self) -> dict[str, object] | None:
        """The lock file's record, or ``None`` if it is absent or unreadable."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            record = json.loads(raw)
        except ValueError:
            return None
        return record if isinstance(record, dict) else None

    def _steal_if_stale(self) -> bool:
        """Remove the lock if its holder cannot be alive. True when the file is now gone."""
        record = self._read_holder()
        if record is None:
            # Absent, or a lock nobody can read. Neither is a holder to respect: an
            # unparseable lock would otherwise wedge the ledger until a human deleted it.
            return self._steal()
        pid = record.get("pid")
        if _is_int(pid) and self._is_alive(int(pid)) is False:  # type: ignore[arg-type]
            return self._steal()
        stamp = record.get("monotonic")
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
            return self._steal()
        age = self._monotonic() - float(stamp)
        if age < 0.0 or age > self._stale_after_s:
            return self._steal()
        return False

    def _steal(self) -> bool:
        """Unlink the lock file. Counted, because a run of steals is itself a finding."""
        self.steals += 1
        try:
            self.path.unlink()
        except FileNotFoundError:
            return True
        return True


# --- writing ------------------------------------------------------------------


def _stamp(seconds: float) -> str:
    """Render a POSIX timestamp as ISO-8601 UTC. Evidence only — nothing reads it back.

    ``timezone.utc`` rather than ruff's preferred ``datetime.UTC``: that alias landed in
    3.11, and the kit has to import on an interpreter older than this repo's 3.14 floor.
    An alias missing at import time is an ``AttributeError`` on a consumer's Python, which
    is worse than a lint suppression carrying its reason.
    """
    return (
        datetime
        .fromtimestamp(seconds, tz=timezone.utc)  # noqa: UP017 — 3.11+ alias
        .isoformat()
        .replace("+00:00", "Z")
    )


def _truncate(text: str, max_bytes: int) -> tuple[str, int]:
    """*text* cut to *max_bytes*, with the length of the whole thing in bytes.

    The cut lands on a **character** boundary: a byte-sliced UTF-8 payload stops being
    decodable and takes the whole line down with it. ``errors="ignore"`` drops a partial
    trailing character, which is exactly that boundary.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, len(encoded)
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), len(encoded)


def _redacted(value: object, redact: Callable[[str], str] | None) -> object:
    """*value* with every string inside it redacted, container shape unchanged."""
    if isinstance(value, str):
        return value if redact is None else redact(value)
    if isinstance(value, dict):
        return {key: _redacted(item, redact) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item, redact) for item in value]
    return value


def _prepare_entry(
    key: str, value: object, redact: Callable[[str], str] | None, max_text_bytes: int
) -> dict[str, object]:
    """One payload entry, redacted and capped, as the field(s) it becomes.

    **Redact, then truncate, then measure** (§4.2). Redaction can *lengthen* text — a
    matched pattern becomes a placeholder — and a cut through the middle of a secret can
    defeat the pattern that would have caught it, so the order is not interchangeable.
    ``original_length_bytes`` is therefore the length of the **redacted** text, which is
    the honest number anyway: the raw bytes were never ours to keep.

    The cap **truncates; it never refuses, and it never conceals that it truncated.**
    Refusing loses the event — the fact that a gate ran, along with its output — and
    quietly clipping makes a cut comment indistinguishable from a short one, so a reader
    cannot tell evidence from a fragment.

    A key the cap names therefore has to hold a string. A list or an object under one is
    refused, and that is the *schema* refusing a shape rather than the cap refusing a size:
    the markers say how much was cut from **one** field, so there is nowhere honest to put
    them for a list of ten. A caller with structured evidence nests it under another key,
    which is outside the cap — which is why §4.4's guarantee is the lock and not the cap.

    Raises:
        InvalidEventError: a truncatable key holds a container.
    """
    if key in TRUNCATABLE_KEYS and isinstance(value, (dict, list)):
        raise InvalidEventError(
            f"payload key {key!r} is capped free text and must be a string, "
            f"got {type(value).__name__}"
        )
    prepared = _redacted(value, redact)
    if key not in TRUNCATABLE_KEYS or not isinstance(prepared, str):
        return {key: prepared}
    cut, original = _truncate(prepared, max_text_bytes)
    if cut == prepared:
        return {key: prepared}
    return {
        key: cut,
        f"{key}_truncated": True,
        f"{key}_original_length_bytes": original,
    }


def prepare_payload(
    payload: Mapping[str, object],
    *,
    redact: Callable[[str], str] | None = None,
    max_text_bytes: int = MAX_TEXT_BYTES,
) -> dict[str, object]:
    """*payload* as it will be stored: every string redacted, free text capped.

    Truncation is a **write-time property of the event, not a later rewrite**: it happens
    once, before the event is authoritative, and nothing revisits it. That is the whole
    difference between this and the compaction §9.1 declines.

    *redact* is injected because the pattern set is not the kit's to own — secret shapes
    and this repo's rule against committed machine paths live in the engine. What is owned
    here is the **ordering**, which is the part that goes wrong silently.
    """
    prepared: dict[str, object] = {}
    for key, value in payload.items():
        prepared.update(_prepare_entry(key, value, redact, max_text_bytes))
    return prepared


def _append_lines(path: Path, lines: Sequence[str]) -> None:
    """Append *lines*, repairing a missing final newline first.

    **The torn-line guard.** If the last byte is not a newline the previous append was
    torn, and appending onto it would concatenate a good event onto a partial line and
    corrupt the good one. One newline first turns a lost event into a lost event, instead
    of into two.

    No ``fsync``: the push is the durability boundary (§4.4). Stated so nobody adds one
    and destroys the millisecond-scale lock hold that makes single-writer viable.
    """
    needs_newline = False
    if path.exists():
        with path.open("rb") as stream:
            if stream.seek(0, os.SEEK_END):
                stream.seek(-1, os.SEEK_END)
                needs_newline = stream.read(1) != b"\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        if needs_newline:
            stream.write("\n")
        for line in lines:
            stream.write(line + "\n")


def append(  # noqa: PLR0913 — every keyword is an injected dependency the kit may not read
    directory: Path | str,
    drafts: Iterable[Draft],
    *,
    actor: str = "",
    clock: Callable[[], float] | None = None,
    redact: Callable[[str], str] | None = None,
    max_text_bytes: int = MAX_TEXT_BYTES,
    held_lock: LedgerLock | None = None,
    lock_timeout_s: float = DEFAULT_LOCK_TIMEOUT_S,
) -> list[Event]:
    """Append *drafts* to the ledger in *directory* and return the events written.

    Each draft is assigned the item's sequence ``max + 1`` and the totals
    :func:`accumulate` gives for its predecessor. Both come from a **fold** of the existing
    events rather than from a raw tail read, which is the stronger source: after a fork the
    tail carries totals that omit the other side, and folding restates them — §4.6's "void
    until a fold restates them", discharged on the next write. §4.6's reverse-scan
    optimisation and the rotation checkpoint that bounds it are basicly-vkh0.14's.

    A draft whose content-derived id is already in the ledger is **skipped**, so replaying
    a write changes nothing and the return value says what actually landed. Pass
    ``generation=2`` on the draft for a genuine re-recording of an identical fact.

    Args:
        directory: The ledger directory. Created if it does not exist.
        drafts: What to append, in the order they were decided.
        actor: The default lease holder for a draft that names none.
        clock: Wall clock, epoch seconds, recorded as ``ts`` and read by nothing.
            Defaults to :func:`time.time`. **The only wall-clock read in this module.**
        redact: Applied to every string value before the cap (§4.2).
        max_text_bytes: The per-field cap.
        held_lock: An already-held :class:`LedgerLock`, for a caller whose critical
            section is wider than one append. When ``None`` a lock is taken and released.
        lock_timeout_s: How long to wait for the lock when taking one here.

    Raises:
        InvalidEventError: a draft's record id, kind or payload cannot become an event.
        LockUnavailableError: the lock could not be taken. Retryable.
    """
    pending = list(drafts)
    if not pending:
        return []
    for draft in pending:
        ids.validate_record_id(draft.record)  # type: ignore[attr-defined]
        if not KIND_PATTERN.match(draft.kind):
            raise InvalidEventError(f"kind {draft.kind!r} must match {KIND_PATTERN.pattern}")
    ledger = Path(directory)
    ledger.mkdir(parents=True, exist_ok=True)
    now = time.time if clock is None else clock
    lock = held_lock if held_lock is not None else LedgerLock(ledger, timeout_s=lock_timeout_s)
    acquired = held_lock is None
    if acquired:
        lock.acquire()
    try:
        existing, _ = read_events(ledger)
        state = fold(existing)
        seen = {event.id for event in existing}
        minted: list[Event] = []
        for draft in pending:
            payload = prepare_payload(draft.payload, redact=redact, max_text_bytes=max_text_bytes)
            event_id = event_id_for(draft.record, draft.kind, payload, generation=draft.generation)
            if event_id in seen:
                continue
            item = state.records.setdefault(draft.record, RecordState(record=draft.record))
            item.totals = accumulate(item.totals, draft.kind, payload)
            # Fold the draft into the item as it is written, using the same handlers the
            # read side uses. Two reasons, and neither is redundancy: it refuses a
            # malformed event of a **known** kind here rather than writing a line the fold
            # will then refuse to read, and it keeps the item's state current *within* one
            # batch, so a later draft in the same call reads what an earlier one set.
            handler = _HANDLERS.get(draft.kind)
            if handler is not None:
                handler(item, payload)
            item.max_seq += 1
            minted.append(
                Event(
                    id=event_id,
                    record=draft.record,
                    seq=item.max_seq,
                    kind=draft.kind,
                    actor=draft.actor or actor,
                    ts=_stamp(now()),
                    payload=payload,
                    totals=item.totals,
                )
            )
            seen.add(event_id)
        if minted:
            _append_lines(append_target(ledger), [to_json(event) for event in minted])
        return minted
    finally:
        if acquired:
            lock.release()
