"""The single seam to the owned work tracker.

Every harness module used to carry its own copy of the tracker call — eight call sites to
audit whenever the store's shape changed. This module is the one place: one write funnel,
one record read, one absence, one read-only guard.

Two modules sit below it: :mod:`basicly.owned_store` answers where the ledger is and which
kit reads it, and :mod:`basicly.mirror` says what one write becomes as ledger events. The
boundary is *the decision* against *the store* — what stays here is what a caller needs
decided before it can act on either.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from basicly import (
    comment_rows,
    owned_store,
    owned_write,
    redact,
    tracker_argv,
    tracker_usage,
)

# --- Read-only sections (factory-loop.md §5.1) -------------------------
#
# A pre-flight gate reads the world, returns a verdict, and writes nothing. The
# rule earns enforcement here, at the funnel, rather than at each gate's call
# site: both of the incidents behind it were *tracker* writes — a hand-recorded
# verify gate that shipped a bead with its code stranded unmerged, and an approved
# ship checkpoint that wedged phase derivation with no un-approve path — and tracker
# comments and gate results cannot be deleted, so a write that should have been a
# refusal is unrecoverable. A gate that cannot reach this function cannot leave
# the tracker in a state no command can undo.
#
# The guard covers the tracker only, which is the boundary the rule needs and no
# more. The engine's other writes during a check — the verify run artifact and the
# usage ledger under `.basicly/usage/` — are self-ignored, rewritten by every run,
# and undone by deleting the file; neither can strand a bead.
#
# :func:`basicly.policy.preflight_gate` is the typed entry point. This is the
# mechanism it installs, and it lives here because :mod:`basicly.policy` sits
# above this module in the import contract while every other write path
# (:mod:`basicly.verify`, :mod:`basicly.rubrics`, :mod:`basicly.decisions`) is a
# sibling of it or higher — the funnel is the one place all of them pass through.


class TrackerWriteRefusedError(Exception):
    """A tracker write was attempted inside a read-only section.

    Deliberately **not** a :class:`RuntimeError`, unlike every other failure these
    funnels raise. Two dozen call sites across the engine wrap a br call in
    ``except RuntimeError, OSError, ValueError`` and answer None or a typed
    absence — :func:`read_record` is one — so a refusal in that family would be
    swallowed into "the tracker had nothing to say", which is the fail-open
    direction for the one guard whose whole purpose is that a write cannot slip
    through. A violation here is a gate breaking its own declared type, not a
    tracker that misbehaved, and it must reach the top.
    """


# Scoped to the calling thread by construction: a supervised pass runs its lanes
# in a ThreadPoolExecutor, and a process-global flag would let one lane's
# read-only section refuse another lane's legitimate write. The honest bound is
# the other direction — a section that hands its work to a *new* thread does not
# guard that thread, because a fresh context starts empty.
_read_only: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "br_read_only", default=None
)


@contextlib.contextmanager
def read_only(reason: str) -> Iterator[None]:
    """Refuse every tracker write attempted in this thread until the block exits.

    *reason* is quoted in the refusal, so the traceback names the gate that must
    not have written rather than only the write it attempted. Restores the previous
    state on the way out, exception or not, so a refused write does not leave the
    tracker read-only for the rest of the process.
    """
    token = _read_only.set(reason)
    try:
        yield
    finally:
        _read_only.reset(token)


def _refuse_write_in_read_only(args: Sequence[str]) -> None:
    """Raise when *args* is not a read and a read-only section is active.

    Fail-closed on an unclassified surface. ``tracker_usage.READ_SUBCOMMANDS`` is
    deliberately not exhaustive, so "not known to be a read" is the only safe test
    a guard against unrecoverable writes can make: a refusal is loud and fixed by
    classifying the surface, while a leaked write is silent and permanent.

    The two cases get different messages on purpose. A known write is the caller's
    bug and there is nothing to reclassify; only an *unclassified* surface should
    ever send a reader to :mod:`basicly.tracker_usage`, because telling them to
    classify ``comments add`` as a read is how a guard gets disabled by its own
    error text.
    """
    reason = _read_only.get()
    if reason is None:
        return
    surface, _ = tracker_usage.split_invocation(list(args))
    access = tracker_usage.classify_access(surface)
    if access == "read":
        return
    named = surface or " ".join(args)
    fault = (
        f"{named} is not classified, and unknown is not read: classify it in "
        "tracker_usage if it only reads"
        if access == "unclassified"
        else f"{named} writes"
    )
    raise TrackerWriteRefusedError(f"{reason} must write nothing, but {fault}")


# --- The owned tracker: dual write, then the flip (basicly-vkh0.19) ----------
#
# Steps 3 and 4 of the cutover in `docs/requirements/work-tracker.md` §5. *Where* the
# owned store is and *what* a write becomes in it are :mod:`basicly.owned_store`
# and :mod:`basicly.mirror`; what stays here is *when* either applies, because
# this module is the one place the engine spawns br and therefore the only place
# that sees a write land on the external tracker.
#
# The store's vocabulary is re-bound below so ``br.<name>`` keeps naming it. That
# is not tidiness: :mod:`basicly.config` installs its mode reader through this
# module (the inversion :func:`owned_store.set_mode_reader` documents), and the
# engine's callers read the mode constants off the same seam they spawn through.
# Each is an alias rather than a wrapper — one object, so a test that patches
# ``tracker.kit`` patches the loader this module calls.
MODE_OWNED = owned_store.MODE_OWNED
TRACKER_MODES = owned_store.TRACKER_MODES
DEFAULT_TRACKER_MODE = owned_store.DEFAULT_TRACKER_MODE
KIT_TRACKER_DIR = owned_store.KIT_TRACKER_DIR
LEDGER_DIR = owned_store.LEDGER_DIR
SCHEDULER_KIT_MODULE = owned_store.SCHEDULER_KIT_MODULE
TrackerDivergenceError = owned_store.TrackerDivergenceError
TrackerModeUnknownError = owned_store.TrackerModeUnknownError
set_mode_reader = owned_store.set_mode_reader
tracker_mode = owned_store.tracker_mode
ledger_dir = owned_store.ledger_dir
kit = owned_store.kit


def owned_record(repo_root: Path, issue_id: str) -> dict | None:
    """*issue_id* as the owned ledger holds it, in ``br show --json``'s shape.

    The flipped half of :func:`read_record`, and it keeps that function's contract
    rather than inventing one: None for every way the read comes back without a
    record, never an exception.

    **A tombstoned record reads as absent**, which is not a detail. The owned store
    expresses a deletion by keeping the record and flagging it (`events.py`), and the
    live tracker expresses the same deletion by not returning the record at all. A
    reader that saw the tombstoned record would hand out work on a bead somebody
    deleted — the defect `differential.is_ready` names — so the two stores are made to
    spell absence the same way here, at the seam, once.

    Two passes over one event list, and the second is not redundancy: the fold is the
    authority for status, fields and comments, while `events.py` has no handler for the
    ``edge`` and ``gate`` kinds at all, so `differential.views_from_events` is the one
    reader of those. Duplicating either here is the drift the kit's own loaders exist
    to prevent.
    """
    try:
        kit_module = kit(repo_root)
        found = kit_module.read_ledger(ledger_dir(repo_root))
        # Not named `folded`: `.scripts/wired_or_deleted.py` counts an identifier
        # anywhere outside `tests/` as a read of a same-named record field, so a local
        # by that name silently retires the suppression on
        # `basicly.supervise.DispatchBundle.folded` and turns that gate red here.
        ledger_fold = kit_module.events.fold(found)
        state = ledger_fold.records.get(issue_id)
        if state is None or state.tombstoned:
            return None
        views = kit_module.views_from_events(found)
    except TrackerDivergenceError, OSError, ValueError:
        return None
    return _rendered(kit_module, issue_id, state, views, ledger_fold.records)


def _rendered(
    kit_module: Any,
    issue_id: str,
    state: Any,
    views: Mapping[str, Any],
    states: Mapping[str, Any],
) -> dict:
    """One folded record in the shape every consumer parses.

    Both edge directions are carried, and the inverse one is not decoration: a record's
    *dependents* are how `supervise.derive_session` finds a pass's lanes, and rendering
    only the outgoing half left the supervisor deriving an empty session from a root whose
    children were all in the ledger (basicly-vkh0.29).
    """
    reserved = kit_module.migrate.RESERVED_KEYS
    record: dict = {key: value for key, value in state.fields.items() if key not in reserved}
    record["id"] = issue_id
    record["status"] = state.status or ""
    # Normalised here, at the one read seam, because two shapes reach the fold: the import
    # stored a list on `created` and a later label write stores the joined form a capped
    # `value` key admits. A consumer iterating the second would get its characters.
    if tracker_argv.LABELS_FIELD in record:
        record[tracker_argv.LABELS_FIELD] = list(
            tracker_argv.labels_of(record[tracker_argv.LABELS_FIELD])
        )
    record["comments"] = [{"text": text} for text in state.comments]
    record.update(_edges(issue_id, views, states))
    return record


def _edges(issue_id: str, views: Mapping[str, Any], states: Mapping[str, Any]) -> dict[str, list]:
    """*issue_id*'s outgoing and incoming edges, each naming the other record's status.

    A target the ledger does not hold reads ``unknown`` rather than ``""``, the spelling
    `queries._open_blockers` uses: an empty status is a record that is held and never
    opened, and a dangling edge must never read as one.

    The kit renders the same two lists in its own ``cli.read_record`` and may not import
    this module; `test_tracker_query` holds the two producers to one shape.
    """
    view = views.get(issue_id)
    return {
        "dependencies": [
            {
                "id": edge.target,
                "dependency_type": edge.type,
                "status": _edge_status(views, edge.target),
            }
            for edge in (view.dependencies if view is not None else ())
        ],
        "dependents": [
            {
                "id": other,
                "dependency_type": edge.type,
                "status": held.status or "",
                # The title travels with the edge because a caller listing a parent's
                # children has no second read to reach for: `loop`'s sub-task report names
                # them, and falling back to the id there prints an id twice.
                "title": str(states[other].fields.get("title", "")) if other in states else "",
            }
            for other, held in sorted(views.items())
            for edge in held.dependencies
            if edge.target == issue_id and not held.tombstoned
        ],
    }


def _edge_status(views: Mapping[str, Any], issue_id: str) -> str:
    """*issue_id*'s status as the population reports it, or ``unknown`` when it holds none."""
    view = views.get(issue_id)
    return "unknown" if view is None else view.status or ""


def record_edges(repo_root: Path, issue_id: str) -> dict[str, list]:
    """Both directions of *issue_id*'s dependency graph, in :func:`owned_record`'s shape.

    Split from :func:`owned_record` for the read that needs the graph over a record it
    already has — `tracker_query.cmd_show` prints the kit's fold shape, which nests its
    fields and carries no edge at all. Empty lists for a record the ledger does not hold,
    so a caller merges both keys unconditionally: a *missing* key is what reads as a
    surface that renders no edges (basicly-ztik9a).
    """
    try:
        kit_module = kit(repo_root)
        found = kit_module.read_ledger(ledger_dir(repo_root))
        states = kit_module.events.fold(found).records
        views = kit_module.views_from_events(found)
    except TrackerDivergenceError, OSError, ValueError:
        return {"dependencies": [], "dependents": []}
    return _edges(issue_id, views, states)


# --- Harness markers, carried natively (basicly-s5li) ------------------------
#
# The step that removed the external tracker from the engine. Comments were the largest
# dependency — 26 of 55 spawn sites and 45% of all recorded traffic — and measured over
# the live tracker on 2026-08-07, **89% of it (1646 of 1834) is `[harness-*]` markers**:
# checkpoint approvals, grants, gate records, rework counters, needs-input, the wait
# clock, dispatch records and spend rollups, all using a comment purely as transport.
# That is what the plan's standing constraint anticipated in saying to land evidence as
# markers "a format we own, which migrates with us".


# The key a comment row carries its body under, re-bound so ``tracker.COMMENT_TEXT_KEY``
# keeps naming it for the readers outside this module. :mod:`basicly.comment_rows` owns
# the row shape, both stores' side of it, and why they can share one.
COMMENT_TEXT_KEY = comment_rows.TEXT_KEY


# How the ledger's own contention reads in a failure's text. The lock is taken per
# append and a supervised pass runs its lanes concurrently, so a lane can genuinely
# collide with a sibling's write — and that is a back-off, not a defect in the lane.
_LOCK_CONTENTION = ("another writer holds", "went stale")


def is_transient_storage_error(text: str) -> bool:
    """True when *text* carries the ledger's contention failure, which is retryable.

    Takes text rather than an exception because the caller has usually already turned
    the failure into a ``RuntimeError`` by the time it needs to decide whether to back
    off; ``str(exc)`` is the same evidence.

    **A lane that could not take the lock ran nothing**, which is the whole distinction
    this makes: it is charged no rework and re-dispatched, while a lane that started and
    failed is charged. The predecessor of this function overrode a store that classified
    its own contention as terminal (`basicly-vkh0.10`); here the store agrees, so what
    remains is telling contention apart from every other write failure.
    """
    return any(marker in text for marker in _LOCK_CONTENTION)


def _comments_add_argv(issue_id: str, body: str) -> list[str]:
    """The br invocation one marker write is, whichever store ends up taking it.

    Built even on the owned path, because it is what :func:`_refuse_write_in_read_only`
    classifies: a gate that promised to write nothing must be refused for the *fact* it
    is about to record, not for which store happens to be authoritative this week.
    """
    return ["comments", "add", issue_id, body]


def write(repo_root: Path, args: list[str]) -> None:
    """Record one engine write on the owned ledger.

    The caller states the write as an argv, which is what
    :func:`_refuse_write_in_read_only` classifies and what the translator turns into
    events, so every call site says the same thing.

    Raises:
        TrackerWriteRefusedError: a :func:`read_only` section is active. Refused **here**,
            at the seam: a recorded fact cannot be deleted from the append-only log, and
            a gate that cannot reach this function cannot leave the tracker in a state no
            command can undo.
        RuntimeError: the write did not land, which includes a surface with **no owned
            equivalent** — that stops the work rather than letting a half-stated fact
            land, and it is the type the callers already catch.
    """
    _refuse_write_in_read_only(args)
    # A create has an id to hand back and this returns nothing, so it belongs to
    # :func:`create_record`. Refused by name rather than attempted: the translator has no
    # minted id and fails with "replied with no JSON record", which sends the reader to
    # the reply instead of to the call (basicly-vkh0.29).
    if args and args[0] == "create":
        raise RuntimeError(f"a create names an id the store mints; call create_record: {args}")
    owned_write.append(repo_root, args)


def create_record(repo_root: Path, args: list[str]) -> str:
    """Record one ``br create`` and return the id the store minted for it.

    The one write whose *result* the caller needs, so it cannot go through :func:`write`:
    the id is minted by the store rather than stated by the caller.

    Raises:
        TrackerWriteRefusedError: a :func:`read_only` section is active.
        RuntimeError: the write did not land, or no id could be minted.
    """
    _refuse_write_in_read_only(args)
    return owned_write.create(repo_root, args)


def try_write(repo_root: Path, args: list[str]) -> bool:
    """:func:`write` for a caller that tolerates the write not landing; False when it did not.

    The soft half of the seam: "tolerates a store that cannot answer", never "tolerates
    writing when a gate promised not to" — the read-only refusal is raised before the
    store is reached and is deliberately outside the caught set. A caller that needs the
    *reason* the write did not land uses :func:`write` and catches it.
    """
    _refuse_write_in_read_only(args)
    try:
        owned_write.append(repo_root, args)
    except TrackerDivergenceError:
        return False
    return True


def _owned_comment_rows(repo_root: Path) -> dict[str, list[dict]]:
    """Every record's comments out of the ledger, keyed by record, oldest-first.

    The store access; :func:`basicly.comment_rows.from_ledger` is the rendering and
    states the two rules that answer depends on.
    """
    kit_module = kit(repo_root)
    return comment_rows.from_ledger(kit_module, kit_module.read_ledger(ledger_dir(repo_root)))


def add_comment(repo_root: Path, issue_id: str, body: str) -> None:
    """Record one harness marker on *issue_id*, through :func:`write`.

    Named rather than left to the callers: 26 of them record a marker, and the argv is
    the one thing they must not each spell (:func:`_comments_add_argv`).

    Raises:
        TrackerWriteRefusedError: a :func:`read_only` section is active.
        RuntimeError: the write did not land.
    """
    write(repo_root, _comments_add_argv(issue_id, body))


def try_add_comment(repo_root: Path, issue_id: str, body: str) -> bool:
    """:func:`add_comment` for a caller that tolerates the write not landing."""
    return try_write(repo_root, _comments_add_argv(issue_id, body))


def read_comments(repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s comments, oldest-first, each row carrying ``text`` and ``created_at``.

    The marker read seam, and the hard half of its contract: a store that cannot answer
    raises rather than reporting an empty history.

    The stamp is the ledger's own event ``ts``, which is what keeps `policy`'s wait clock
    measuring an interval that outlives the process that opened it.

    Raises:
        RuntimeError: the kit will not load, or the ledger could not be read.
    """
    return _owned_comment_rows(repo_root).get(issue_id, [])


def try_read_comments(repo_root: Path, issue_id: str) -> list[dict]:
    """:func:`read_comments` for an evidence reader; ``[]`` when the store cannot answer.

    The soft contract, and it is soft on purpose only where an empty answer is honest:
    its callers deduplicate a dispatch or a spend rollup, so "no markers recorded" and
    "the tracker did not answer" both correctly mean *write one now*. A counter or a
    refusal must not read this — it must use :func:`read_comments` and fail loudly.
    """
    try:
        return _owned_comment_rows(repo_root).get(issue_id, [])
    except TrackerDivergenceError, OSError, ValueError:
        return []


def all_comment_texts(repo_root: Path) -> dict[str, list[str]]:
    """Every record's comment bodies, keyed by record id — the whole-tracker marker read.

    The travelling read (D11): what a fresh clone can answer with no local state, because
    the ledger commits its own event logs. That is what makes a teammate's dispatch
    history and the cost-per-landed-package rollup readable at all.

    Best-effort, matching :func:`all_records`: every consumer here is evidence or
    telemetry, never a gate.
    """
    try:
        rows = _owned_comment_rows(repo_root)
    except TrackerDivergenceError, OSError, ValueError:
        return {}
    return {record: [str(row[COMMENT_TEXT_KEY]) for row in found] for record, found in rows.items()}


# --- Export scrubbing (basicly-vkh0.5) --------------------------------------


# A path can also reach the export as ordinary text — pasted into a description
# or a comment. That half cannot be fixed by asking br to drop a field, and it
# cannot be edited away either: `br comments` offers only `add` and `list`, so a
# path already recorded in a comment has no removal path through the documented
# CLI (another requirement carried to basicly-vkh0.6 — the replacement owes a
# redaction path for recorded text).
#
# The export is therefore the layer where this is fixed. The local database is
# git-ignored and keeps full fidelity; only the published artifact is redacted,
# so nobody working in the repo loses the original.


# How long a publish waits for a reader to let go of the export before giving up.
# Only Windows ever spends this: `os.replace` needs delete access to the
# destination, and CPython opens a file for reading with `FILE_SHARE_READ |
# FILE_SHARE_WRITE` and *not* `FILE_SHARE_DELETE` — so renaming over a file another
# process is mid-read raises ERROR_SHARING_VIOLATION there while succeeding silently
# on POSIX. Under the fan-out this requirement is about, some reader is nearly always
# mid-read, which would have made the atomic write a Windows-only failure. Bounded on
# a monotonic clock for the reason R1 gives: the wall clock is not trustworthy here.
_PUBLISH_DEADLINE_S = 5.0
_PUBLISH_FIRST_WAIT_S = 0.005
_PUBLISH_MAX_WAIT_S = 0.1
# The reader's half of the same Windows sharing window, and shorter than the writer's
# on purpose: a denied read has a correct answer waiting microseconds away, so a long
# budget here would only delay a genuinely unreadable file on the telemetry read path.
_READ_DEADLINE_S = 1.0


def _publish(tmp: Path, export: Path) -> bool:
    """Rename *tmp* over *export*; False when a reader never let go in time.

    False rather than an exception because :func:`scrub_export` runs on the commit
    path and must never be the reason tracker state fails to land. An unpublished
    scrub leaves the export exactly as it was — still carrying whatever the scrub
    would have removed — and the companion ``tracker-path-scan`` hook is the gate
    that then refuses the commit. Failing to repair is safe; a half-written export
    is not.
    """
    deadline = time.monotonic() + _PUBLISH_DEADLINE_S
    delay = _PUBLISH_FIRST_WAIT_S
    while True:
        try:
            tmp.replace(export)
        except OSError:
            if time.monotonic() >= deadline:
                tmp.unlink(missing_ok=True)
                return False
            time.sleep(delay)
            delay = min(delay * 2, _PUBLISH_MAX_WAIT_S)
        else:
            return True


def _dump_record(record: dict[str, object]) -> str:
    """Serialize *record* the way br writes the export.

    Compact separators with UTF-8 left unescaped: every untouched record
    round-trips byte-identically under these, so a scrub's diff is exactly the
    fields it changed and nothing else.
    """
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def dependency_edge(dep: object) -> tuple[str, str] | None:
    """One dependency row as ``(dep_id, dep_type)``, or None when it is not a row.

    **br spells a dependency two ways for the same edge**: ``br show --json``
    emits ``id``/``dependency_type`` while the ``create``/``dep add`` echo emits
    ``depends_on_id``/``type``. Reading only one spelling silently yields *no
    dependencies at all* rather than an error, which is how it degraded every
    landing order to the caller's (basicly-kjc5.10).

    One reader for both spellings, so a new call site cannot re-acquire the bug by
    picking a spelling. Carried as a requirement on the replacement, which must
    emit exactly one spelling (`docs/requirements/work-tracker.md` R2, basicly-vkh0.6).
    """
    if not isinstance(dep, dict):
        return None
    dep_id = dep.get("depends_on_id") or dep.get("id")
    dep_type = dep.get("dependency_type") or dep.get("type")
    if not isinstance(dep_id, str) or not dep_id:
        return None
    return dep_id, dep_type if isinstance(dep_type, str) else ""


def read_record(repo_root: Path, issue_id: str) -> dict | None:
    """*issue_id*'s ``br show`` record, or None when the tracker has no usable one.

    The one read seam. `tracker.py` was already the only place that *spawns* tracker, but not the
    only place that *reads* it: the unwrap below was written out at eleven call sites
    across eight modules, and they disagreed about failure four ways — two raised, two
    returned None, four returned a local empty, one carried a typed absence — plus one
    (`loop._child_states`) that guarded the shape not at all and would raise
    ``AttributeError`` on a payload that was not an object (basicly-tcmy.14).

    **One contract, and it is deliberately the soft one:** None for every way the read
    can come back without a record — br absent from PATH, a spawn that fails, a
    non-zero exit, output that is not JSON, an empty array, or a payload that is not an
    object. It never raises for absence. A caller that must have the record calls
    :func:`require_record`, so the hard contract is one wrapper over this rather than a
    second reader.

    Swallowing the spawn's own errors here costs nothing that the split does not give
    back: every caller that needs an exception gets one from
    :func:`require_record`, with a message that names the id rather than the layer that
    failed. `decompose._read_bead` already caught exactly this set to produce a typed
    absence, which is the behaviour being generalised rather than invented.

    Why one seam matters more than the duplication: `basicly-vkh0.19` replaces br with
    an in-process log, and the replacement **chooses** what "not found" looks like. An
    empty list is the natural in-process answer, and against the old eleven that choice
    split six sites (``IndexError``) from five (their documented absence) — a behaviour
    change introduced by the change that is supposed to be transparent. With one reader
    the choice is made once, here.

    **The flip landed here and nowhere else** (`basicly-vkh0.19`). The record comes from
    :func:`owned_record`, and the contract above is unchanged — the same absences answer
    None, and :func:`require_record` still raises one message naming the bead. That the
    choice of what "not found" means was already made once, here, is the whole reason the
    flip was not eleven decisions.

    The engine's other reads each took the same shape rather than staying at their call
    site: the ranking at basicly-vkh0.20 (:func:`read_ranking`), comments at
    basicly-s5li (:func:`read_comments`), gate rows at basicly-vkh0.27
    (:mod:`basicly.gate_source`), and the blocking graph and label members at
    basicly-wpc8 (:mod:`basicly.dependency_graph`, :mod:`basicly.label_source`). The
    Definition-of-Ready rule was never ported — basicly-wpc8.1 owned it in
    `policy.required_sections` instead.
    """
    return owned_record(repo_root, issue_id)


def require_record(repo_root: Path, issue_id: str) -> dict:
    """*issue_id*'s ``br show`` record, raising when the tracker has no usable one.

    The hard half of :func:`read_record`'s contract, for a caller whose work cannot
    proceed without the record. One message for every absence, so a caller no longer has
    to know whether it is looking at a missing bead, a missing binary or a malformed
    payload to explain what went wrong.

    Raises:
        RuntimeError: :func:`read_record` found no usable record.
    """
    record = read_record(repo_root, issue_id)
    if record is None:
        raise RuntimeError(f"the tracker holds no usable record for {issue_id}")
    return record


def owned_ranking(repo_root: Path, limit: int | None = None) -> dict:
    """The owned scheduler's answer for *repo_root*, in ``br scheduler --json``'s shape.

    The flipped half of :func:`read_ranking` (basicly-vkh0.20). Rendered into br's payload
    shape for the same reason :func:`owned_record` is rendered into ``br show``'s: the
    caller then has one parser rather than one per store, so the flip is a change of source
    and not of contract.

    Two fields of that shape mean something different on this side, and both are stated
    rather than papered over. ``fallback_rank`` equals the rank, because the owned ordering
    has no evidence-weighted pass above it that a fallback could differ from — br's two
    diverge exactly when its scoring evidence moved a node, and here the score *is* the
    ordering. And ``schema`` reads ``basicly.scheduler.v1`` rather than ``tracker.scheduler.v1``,
    which is what lets a marker recorded before the flip be told from one recorded after it.

    Unlike :func:`owned_record` this **raises** rather than degrading to an empty answer. An
    absent record is an ordinary fact a caller handles; an empty ranking is
    indistinguishable from "no work is ready", so a kit that would not load would stall the
    loop silently instead of failing.

    Raises:
        TrackerDivergenceError: the kit is not installed or will not load.
    """
    scheduler = kit(repo_root, SCHEDULER_KIT_MODULE)
    answer = scheduler.ranking(ledger_dir(repo_root), limit=limit)
    return {
        "schema": answer.schema,
        "fallback_policy": {"sort": answer.sort},
        "recommendations": [
            {
                "rank": entry.rank,
                "fallback_rank": entry.rank,
                "score": entry.score,
                "issue": {"id": entry.record, "title": entry.title},
            }
            for entry in answer.records
        ],
    }


def read_ranking(repo_root: Path, limit: int | None = None) -> dict:
    """The ranked ready set for *repo_root*, as the scheduler payload its caller parses.

    The ranking read's one seam, and the second thing the cutover flipped
    (basicly-vkh0.20). It exists for the reason ``tests/test_br_seam.py`` guards: a caller
    that reached into the ledger itself would scatter the store across the modules
    `basicly-tcmy.14` spent its whole budget collapsing.

    The payload carries a ``schema``, a ``fallback_policy`` and a list of
    ``recommendations``, rendered by :func:`owned_ranking`, so `basicly.loop_state` parses
    one shape.

    Raises:
        RuntimeError: the ledger could not be ranked. Unchanged in direction from when
            this read lived at the call site: an unrankable ready set is a stop, never an
            empty list, because an empty list reads as "nothing to do" and the loop would
            idle instead of reporting.
    """
    return owned_ranking(repo_root, limit)


def _redact_paths(value: object) -> object:
    """Recursively redact machine-specific paths and identity inside *value*.

    Comments and dependency rows are nested under the record, so a leak is not
    confined to a top-level field. Identity joined paths here for `basicly-r166`:
    br writes ``created_by`` on every record it mints, so the committed export
    carried the OS username on 813 of 876 records against R6's requirement that no
    committed artifact carry one.
    """
    if isinstance(value, str):
        return redact.redact_committed(value)
    if isinstance(value, dict):
        return {key: _redact_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_paths(item) for item in value]
    return value


def _event_generation(counts: dict[tuple[str, str, str], int], event: Mapping[str, object]) -> int:
    """The occurrence number of this exact (record, kind, payload), counting from 1."""
    key = (
        str(event.get("record", "")),
        str(event.get("kind", "")),
        json.dumps(event.get("payload") or {}, sort_keys=True, separators=(",", ":")),
    )
    counts[key] = counts.get(key, 0) + 1
    return counts[key]


def scrub_ledger(repo_root: Path) -> int:
    """Strip machine identity and paths from the owned ledger; return events changed.

    The one place this design edits a line rather than appending one (work-tracker
    §4.4). An append cannot un-publish a string, and the ledger is committed, so
    removing a leaked username is the history rewrite §4.2 reserves for an explicit
    owner decision — taken as `basicly-r166`.

    An event id covers its kind and payload and nothing else, so a redacted payload
    has to be re-minted or `fsck` reads every scrubbed event as inconsistent. The
    generation folded into that id is not written on the line; it is the occurrence
    count of an identical (record, kind, payload), and this refuses to write anything
    unless that derivation reproduces **every** stored id first. Minting counts the
    redacted payloads separately, so two events that redact to the same content get
    distinct ids rather than colliding.
    """
    # Files before the kit, and that ordering is the point: this runs on the commit
    # path beside :func:`scrub_export`, so a repo with no ledger — `external`, or a
    # fixture tree — must be a no-op rather than the reason a landing fails.
    files = sorted(ledger_dir(repo_root).glob("events-*.jsonl"))
    if not files:
        return 0
    kit_module = kit(repo_root)
    changed = 0
    for path in files:
        raw = path.read_text(encoding="utf-8")
        stored: dict[tuple[str, str, str], int] = {}
        minted: dict[tuple[str, str, str], int] = {}
        lines: list[str] = []
        for line in raw.splitlines():
            if not line.strip():
                lines.append(line)
                continue
            event = json.loads(line)
            generation = _event_generation(stored, event)
            expected = kit_module.events.event_id_for(
                event["record"], event["kind"], event["payload"], generation=generation
            )
            if expected != event["id"]:
                raise TrackerDivergenceError(
                    f"{path.name}: {event['id']} does not re-mint from its own content, so the"
                    " generation cannot be derived and nothing is rewritten"
                )
            scrubbed = dict(event)
            scrubbed["actor"] = redact.redact_committed(str(event.get("actor") or ""))
            scrubbed["payload"] = _redact_paths(event["payload"])
            # Counted for every event, not only a rewritten one: an untouched event still
            # occupies its generation, so a later event that redacts onto the same content
            # takes the next one instead of colliding with it.
            scrubbed["id"] = kit_module.events.event_id_for(
                scrubbed["record"],
                scrubbed["kind"],
                scrubbed["payload"],
                generation=_event_generation(minted, scrubbed),
            )
            if scrubbed == event:
                lines.append(line)
                continue
            lines.append(_dump_record(scrubbed))
            changed += 1
        if not changed:
            continue
        trailer = "\n" if raw.endswith("\n") else ""
        tmp = path.with_suffix(f".{os.getpid()}.jsonl.tmp")
        tmp.write_text("\n".join(lines) + trailer, encoding="utf-8")
        _publish(tmp, path)
    return changed


# --- The whole-tracker read (basicly-kjc5.50) --------------------------------


def all_records(repo_root: Path) -> list[dict]:
    """Every record the ledger holds, in id order, in :func:`owned_record`'s shape.

    The travelling read (D11): what a fresh clone can answer with no local state, because
    the ledger commits its own event logs. It carries each record's comments, which makes
    the harness marker families readable — the bulk read a paged query cannot serve.

    **One fold for the whole tracker**, never one per record: the ledger is a single log,
    so a per-record loop would re-read it once per record. That is why this exists rather
    than a loop over :func:`owned_record` (basicly-vkh0.42.5).

    Tombstoned records are left out, for the reason :func:`owned_record` gives: a deletion
    is an event here and an absence to every reader, and the seam is where they meet.

    Best-effort: every consumer is evidence or telemetry, never a gate, so an unreadable
    ledger is an empty answer.
    """
    try:
        kit_module = kit(repo_root)
        found = kit_module.read_ledger(ledger_dir(repo_root))
        ledger_fold = kit_module.events.fold(found)
        views = kit_module.views_from_events(found)
    except TrackerDivergenceError, OSError, ValueError:
        return []
    return [
        _rendered(kit_module, issue_id, ledger_fold.records[issue_id], views, ledger_fold.records)
        for issue_id in sorted(ledger_fold.records)
        if not ledger_fold.records[issue_id].tombstoned
    ]


def export_comment_texts(record: Mapping[str, object]) -> list[str]:
    """The comment bodies on one exported record, in export order."""
    comments = record.get("comments")
    if not isinstance(comments, list):
        return []
    return [
        str(comment["text"])
        for comment in comments
        if isinstance(comment, Mapping) and isinstance(comment.get("text"), str)
    ]
