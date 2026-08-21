"""Fold a ``harness-board/v1`` snapshot out of files, and out of nothing else.

**This is basicly's reference producer, not *the* producer.** The contract is
`.basicly/core/schemas/board-snapshot.schema.json`, and a foreign harness that writes three
keys into a file is as conformant as this module. `docs/requirements/harness-board.md` is the
design; what follows is only what a reader of *this* module has to know.

**Why this exists rather than ``supervise.observe()``, measured.** One ``observe()`` folds the
whole event log **93 times** and parses 554,280 events to answer one question, at 6.1 s. That
is an uncached repeated read, not a subprocess problem, so the whole advantage here is that
:func:`_read_and_fold` folds **once** and every section takes that result. Both counts are
pinned by spies in ``tests/test_board_snapshot.py``.

**The live-lock facts are an argument, and a cycle is why.** Reading the supervisor lock here
would mean calling ``supervise.read_holder``, and the supervisor emits a snapshot itself, so
the import would close ``supervise -> board_snapshot -> supervise``. Every caller either sits
above ``supervise`` or *is* it, so every caller already holds the facts: they arrive as
:class:`SessionFacts`, and with none supplied the ``session`` section is **omitted**.

``backlog``'s ``ready`` and ``blocked``, ``units[].phase`` and ``units[].ready``,
``repo``'s git state and the grant triple on ``session`` all arrive the same way and for the
same reason: each is a derivation whose authority is a layer above this one, and a second
spelling of one in a display producer is how the two come to disagree. Whichever the caller
withholds is **omitted**. The sections whose source is ``.basicly/usage/`` are
:mod:`basicly.board_usage`; the row reducers are :mod:`basicly.board_sections`.
"""

# comment-density-waiver: cohesion: 51.5% after basicly-y754k2 moved the usage sections and the row
# reducers out. The module lost 630 tokens of code and 505 of prose, so the share rose while
# the file got smaller - the two ratchets pulling opposite ways, measured. Every paragraph
# left is a measurement or a refuted alternative: the 93-fold 6.1 s cost of `observe()`, the
# `supervise -> board_snapshot -> supervise` cycle that makes the lock facts an argument, and
# why `backlog` refuses to respell the tracker's own derivations. The `Args:` block on
# `build_document` is mandated by ruff `D` and is a third of the remaining prose.

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import (
    __version__,
    board_fields,
    board_schema,
    board_sections,
    board_usage,
    owned_store,
    projection,
    run_record,
    tracker_paths,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SCHEMA = board_schema.VERSION

# `generator.tool`: which producer wrote the document, since it is one of several.
TOOL = "basicly"

# How stale a document may get before a viewer says so. Carried so the consumer never guesses.
DEFAULT_STALE_AFTER_S = 60.0

# `freshness.source`, from the schema's closed set. One call to `build_document` is a
# one-shot by definition; a caller on a tick says so itself.
ONE_SHOT = "one-shot"
SUPERVISOR_TICK = "supervisor-tick"
SELF_REFRESH = "self-refresh"

# The whole transport: a file at a path the consumer is told, and no other. Under
# `.basicly/usage/`, whose `.gitignore` is a bare `*`, so one operator's board never commits.
SNAPSHOT_FILE = Path(".basicly/usage/board/snapshot.json")


@dataclass(frozen=True)
class Freshness:
    """How the document comes to be rewritten, so a viewer can tell live from frozen."""

    source: str = ONE_SHOT
    cadence_s: float | None = None
    stale_after_s: float = DEFAULT_STALE_AFTER_S


# The event strip is a strip, not a log: six rows is what the wall has height for.
EVENT_LIMIT = 6

# `spend.scope`. An honesty flag rather than a detail: `.basicly/usage/.gitignore` is a bare
# `*`, so these numbers are one operator's machine and never a team's.
board_usage.MACHINE_LOCAL = "machine-local"

# The verify runner's check vocabulary is pass/fail/skip and the schema's is
# pass/fail/not_run. Mapped rather than passed through, because a value outside a closed set
# is refused outright by an already-shipped consumer of this major.
board_usage.CHECK_STATUS = {"pass": "pass", "fail": "fail", "skip": "not_run", "not_run": "not_run"}

_ACTIVE_STATUS = "in_progress"
_CLOSED_STATUS = "closed"


@dataclass(frozen=True)
class SessionFacts:
    """The live factory's facts, supplied by a caller that sits above ``supervise``.

    Nothing here is derived, and the names are ``supervise.LockInfo``'s so a caller passes
    that reader's fields straight through. ``root_issue`` and ``supervised`` are the caller's
    knowledge of which pass is running; the rest is whatever the lock held, including
    nothing. Absent entirely, the ``session`` section is omitted rather than emitted with
    nulls, because a guessed root on a wall display is the false claim this contract is
    written against.
    """

    root_issue: str
    supervised: bool = False
    session_id: str = ""
    age_s: float | None = None
    stale: bool | None = None
    # The autonomy grant the run is under, from ``policy.active_grant``. Empty level and None
    # budget omit their keys: an L1 grant genuinely has no budget, and a wall board that draws
    # `0` for it has published a ceiling nobody set.
    grant_level: str = ""
    token_budget: int | None = None
    # Spend under *this grant*, never the session's lifetime figure. The two cover different
    # windows, so publishing the lifetime one beside the ceiling is how a display comes to draw
    # 177970761/4000000 with nothing spent under that grant (basicly-e2mz.13);
    # ``policy.tokens_under_grant`` is the subtraction that makes the pair comparable. None
    # where the caller could not measure it, which omits the key rather than reporting zero
    # spend on a session whose run records this checkout cannot see.
    spent_tokens: int | None = None


@dataclass(frozen=True)
class Facts:
    """Everything the caller knows that this producer may not derive.

    One record rather than one argument each, because they are one rule - OQ-D's *the layer
    above supplies the fact the layer below cannot honestly derive*. :attr:`session` needs the
    supervisor lock, which reading here would close the cycle
    ``supervise -> board_snapshot -> supervise`` (C11); :attr:`lanes` and :attr:`phases` need
    the loop's required-gate set, whose authority is ``loop_state.derive_phase``;
    :attr:`readiness` is the tracker's own ready walk; :attr:`repo` needs a subprocess; and
    :attr:`questions` is the decision queue's wording, which no wait marker carries. Whichever
    is None has its section or its key **omitted**, and a caller adds its facts here rather
    than to a signature.
    """

    session: SessionFacts | None = None
    lanes: Sequence[board_sections.LaneFacts] | None = None
    repo: board_sections.RepoFacts | None = None
    phases: Mapping[str, str] | None = None
    readiness: board_sections.Readiness | None = None
    questions: Mapping[str, str] | None = None


def _read_and_fold(repo_root: Path) -> tuple[Mapping[str, Any], list[Any], list[tuple]] | None:
    """*repo_root*'s folded records, its marker rows and its asserted edges, or None.

    **One read of the log and one fold over it, and everything the document says about the
    tracker comes out of these three.** The markers and the edges are further passes over the
    same in-memory list rather than reads of their own, which is the whole distance between
    this producer and `observe()`'s 93 folds.

    The ledger is resolved through the redirect, so a worktree reads the base checkout's one
    store rather than a copy of it. Best-effort in the direction the whole document is: an
    unreadable ledger costs the tracker sections, never the document.
    """
    try:
        kit = owned_store.kit(repo_root)
        events = kit.read_ledger(owned_store.ledger_dir(repo_root))
    except owned_store.TrackerDivergenceError, OSError, ValueError:
        return None
    return (
        kit.events.fold(events).records,
        board_fields.read_markers(events),
        board_sections.edge_triples(kit, events),
    )


def _live(records: Mapping[str, Any]) -> list[Any]:
    """Every folded record a board may draw, tombstones dropped.

    A tombstone is a deletion the log keeps and every reader treats as an absence, which is
    the seam :func:`basicly.tracker.owned_record` states.
    """
    return [records[name] for name in sorted(records) if not records[name].tombstoned]


def _backlog(live: Sequence[Any], readiness: board_sections.Readiness | None) -> dict[str, object]:
    """The status tally, a count per priority label, and the caller's two set sizes.

    ``ready`` and ``blocked`` are ``len`` over the sets *readiness* carries, which is counting
    a supplied answer rather than deriving one - the tracker's ready walk stays the only walk.
    """
    counts: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for state in live:
        counts[state.status or ""] = counts.get(state.status or "", 0) + 1
        priority = state.fields.get("priority")
        if isinstance(priority, int) and not isinstance(priority, bool):
            priorities[f"P{priority}"] = priorities.get(f"P{priority}", 0) + 1
    closed = counts.get(_CLOSED_STATUS, 0)
    section: dict[str, object] = {
        "total": len(live),
        "active": len(live) - closed,
        "in_progress": counts.get(_ACTIVE_STATUS, 0),
        "closed": closed,
        "by_priority": priorities,
    }
    if readiness is not None:
        section["ready"] = len(readiness.ready)
        section["blocked"] = len(readiness.blocked)
    return section


def _session(facts: SessionFacts, records: Mapping[str, Any]) -> dict[str, object]:
    """The live factory, from the caller's facts plus the root's own folded status."""
    section: dict[str, object] = {
        "root": board_fields.text(facts.root_issue, board_fields.ID_MAX),
        "supervised": facts.supervised,
    }
    state = records.get(facts.root_issue)
    if state is not None and state.status:
        section["root_status"] = board_fields.text(state.status, board_fields.KIND_MAX)
    holder: dict[str, object] = {}
    if facts.session_id:
        holder["id"] = board_fields.text(facts.session_id, board_fields.ID_MAX)
    if facts.age_s is not None:
        holder["heartbeat_age_s"] = max(0.0, facts.age_s)
    if facts.stale is not None:
        holder["stale"] = facts.stale
    if holder:
        section["holder"] = holder
    if facts.grant_level:
        section["grant_level"] = board_fields.text(facts.grant_level, board_fields.PRIORITY_MAX)
    if facts.token_budget is not None:
        section["token_budget"] = max(0, facts.token_budget)
    if facts.spent_tokens is not None:
        section["spent_tokens"] = max(0, facts.spent_tokens)
    return section


def build_document(
    repo_root: Path,
    *,
    facts: Facts | None = None,
    freshness: Freshness | None = None,
    now: datetime | None = None,
    event_limit: int = EVENT_LIMIT,
) -> dict[str, object]:
    """A ``harness-board/v1`` document for *repo_root*, from its files alone.

    One fold of the event log, one parse of each usage file, zero subprocesses. Every section
    but the three required ones is optional, and an unreadable source costs that section and
    nothing else.

    Args:
        repo_root: The checkout to read.
        facts: What the caller knows and this module may not derive - the live-lock facts,
            the in-flight lanes, a phase per record, the ready walk, the checkout's git state
            and the wording of each pending ask. Each one absent omits its section or its key
            rather than guessing a root, a phase or a clean tree. An empty ``lanes`` sequence
            still emits ``[]``, which is the different claim that the caller can see lanes and
            there are none.
        freshness: What will rewrite this document. Defaults to a one-shot build.
        now: The instant to stamp. Injected so a test is a function of its fixture rather
            than of the clock.
        event_limit: How many marker rows the event strip carries.
    """
    moment = now or datetime.now(UTC)
    chosen = freshness or Freshness()
    known = facts or Facts()
    document: dict[str, object] = {
        "schema": SCHEMA,
        "generated_at": board_fields.stamp(moment),
        "freshness": {
            "source": chosen.source,
            "cadence_s": chosen.cadence_s,
            "stale_after_s": chosen.stale_after_s,
        },
        "generator": {"tool": TOOL, "version": board_fields.text(__version__, 60)},
        # Resolved before its last component is taken, or a caller spelling the root as
        # `Path(".")` publishes an empty name and the schema withholds the whole section.
        "repo": board_sections.repo(
            tracker_paths.tracker_root(repo_root).resolve().name, known.repo
        ),
    }
    read = _read_and_fold(repo_root)
    if read is not None:
        records, markers, edges = read
        live = _live(records)
        # The active population, not every record: `units` and `graph` are what a board draws
        # rather than what the log holds, and C6 priced the payload on exactly this cut.
        active = [state for state in live if state.status != _CLOSED_STATUS]
        drawn = {state.record for state in active}
        document["backlog"] = _backlog(live, known.readiness)
        document["units"] = board_sections.units(active, phases=known.phases, ready=known.readiness)
        document["graph"] = board_sections.graph(
            edge for edge in edges if edge[0] in drawn or edge[2] in drawn
        )
        document["asks"] = board_sections.asks(markers, now=moment, questions=known.questions)
        document["events"] = board_sections.events(markers, event_limit)
        if known.session is not None:
            document["session"] = _session(known.session, records)
    if known.lanes is not None:
        document["lanes"] = board_sections.lanes(known.lanes)
    gates = board_usage.gates(repo_root)
    if gates is not None:
        document["gates"] = gates
    runs = run_record.load_run_records(repo_root)
    if runs:
        spend = board_usage.spend(runs)
        if spend is not None:
            document["spend"] = spend
        document["health"] = board_usage.health_rows(runs)
    return document


def serialize(document: Mapping[str, object]) -> str:
    """*document* as the bytes the transport carries: sorted, indented, newline-terminated.

    One function because a server that holds its own fold in memory must answer
    ``GET /snapshot.json`` with the same bytes :func:`write_document` would have landed - the
    contract says the served bytes *are* the file's - and two spellings of the encoding would
    make that identity a coincidence.
    """
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def write_document(repo_root: Path, document: Mapping[str, object]) -> Path:
    """Land *document* at :data:`SNAPSHOT_FILE` under *repo_root*; the path written.

    Temp-then-rename, so a consumer polling the path reads the previous document or this one
    and never a partial. Here rather than in each producer because the path is the contract's
    transport, not any one producer's choice of where to put its output.
    """
    path = repo_root / SNAPSHOT_FILE
    projection.atomic_write_text(path, serialize(document))
    return path
