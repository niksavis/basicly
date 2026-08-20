"""Fold a ``harness-board/v1`` snapshot out of files, and out of nothing else.

**This is basicly's reference producer, not *the* producer.** The contract is
`.basicly/core/schemas/board-snapshot.schema.json`, and a foreign harness that writes three
keys into a file is as conformant as this module. `docs/requirements/harness-board.md` is the
design; what follows is only what a reader of *this* module has to know.

**Why this exists rather than ``supervise.observe()``, measured.** One ``observe()`` folds the
whole event log **93 times** and parses 554,280 events to answer one question, at 6.1 s. That
is an uncached repeated read, not a subprocess problem, so the whole advantage here is that
:func:`_read_and_fold` folds **once** and every section takes that result rather than
reaching back for a tracker read of its own. ``tests/test_board_snapshot.py`` spies on the kit's own
``fold`` and on ``subprocess.Popen`` and pins both counts, because "reads only files" is a
claim one convenience import can quietly break.

**The live-lock facts are an argument, and a cycle is why.** Reading the supervisor lock here
would mean calling ``supervise.read_holder``, and the supervisor emits a snapshot itself, so
the import would close ``supervise -> board_snapshot -> supervise``. Every caller either sits
above ``supervise`` or *is* it, so every caller already holds the facts: they arrive as
:class:`SessionFacts`, and with none supplied the ``session`` section is **omitted**.

**Omit, never estimate.** The schema has no field marking a value as estimated, so an
estimate would render indistinguishably from a billed number. A section whose source is absent
is therefore absent from the document - never zero - and the page renders "not emitted by this
producer". `.basicly/usage/` is git-ignored and worktree-local, so in a lane worktree all
three usage sources really are missing and the tracker half still draws.

**Lanes are the second thing a caller supplies, and for the same reason.**
``lanes[].phase`` is required by the schema and its authority is
``loop_state.read_node_state``, which calls ``validate_gate.required_config`` for the set of
gates the unit owes - a fourth source, outside the three files opened here. A phase folded out
of ledger evidence alone diverges from the engine's for any unit owing validation, so the
facts arrive as :class:`basicly.board_fields.LaneFacts` and with none supplied the ``lanes``
section is **omitted**.

``backlog`` carries no ``ready`` or ``blocked``, and ``units`` carries no ``ready``: each is
the tracker's own derivation over a vocabulary and a full edge population, and a second
spelling of one in a display producer is how the two come to disagree.
"""

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
    health,
    owned_store,
    run_record,
    tracker_paths,
    verify_artifact,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

SCHEMA = board_schema.VERSION

# `generator.tool`: which producer wrote the document, since it is one of several.
TOOL = "basicly"

# `freshness.source`, from the schema's closed set. One call to `build_document` is a one-shot by
# definition; a caller on a tick says so itself.
ONE_SHOT = "one-shot"

# How stale a document may get before a viewer says so. Carried so the consumer never guesses.
DEFAULT_STALE_AFTER_S = 60.0

# The event strip is a strip, not a log: six rows is what the wall has height for.
EVENT_LIMIT = 6

# `spend.scope`. An honesty flag rather than a detail: `.basicly/usage/.gitignore` is a bare
# `*`, so these numbers are one operator's machine and never a team's.
MACHINE_LOCAL = "machine-local"

# The verify runner's check vocabulary is pass/fail/skip and the schema's is
# pass/fail/not_run. Mapped rather than passed through, because a value outside a closed set
# is refused outright by an already-shipped consumer of this major.
CHECK_STATUS = {"pass": "pass", "fail": "fail", "skip": "not_run", "not_run": "not_run"}

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


@dataclass(frozen=True)
class Freshness:
    """How the document comes to be rewritten, so a viewer can tell live from frozen."""

    source: str = ONE_SHOT
    cadence_s: float | None = None
    stale_after_s: float = DEFAULT_STALE_AFTER_S


@dataclass(frozen=True)
class Facts:
    """Everything the caller knows that this producer may not derive.

    One record rather than one argument each, because the two are one rule - OQ-D's *the
    layer above supplies the fact the layer below cannot honestly derive*. :attr:`session`
    needs the supervisor lock, which reading here would close the cycle
    ``supervise -> board_snapshot -> supervise`` (C11); :attr:`lanes` needs the loop's
    required-gate set. Whichever is None has its section **omitted**, and unit F's supervisor
    caller adds its facts here rather than to a signature.
    """

    session: SessionFacts | None = None
    lanes: Sequence[board_fields.LaneFacts] | None = None


def _read_and_fold(repo_root: Path) -> tuple[list[Any], Mapping[str, Any]] | None:
    """*repo_root*'s ledger events and **the** fold over them, or None if unreadable.

    The ledger is resolved through the redirect, so a worktree reads the base checkout's one
    store rather than a copy of it. Best-effort in the direction the whole document is: an
    unreadable ledger costs the tracker sections, never the document.
    """
    try:
        kit = owned_store.kit(repo_root)
        events = kit.read_ledger(owned_store.ledger_dir(repo_root))
    except owned_store.TrackerDivergenceError, OSError, ValueError:
        return None
    return events, kit.events.fold(events).records


def _live(records: Mapping[str, Any]) -> list[Any]:
    """Every folded record a board may draw, tombstones dropped.

    A tombstone is a deletion the log keeps and every reader treats as an absence, which is
    the seam :func:`basicly.tracker.owned_record` states.
    """
    return [records[name] for name in sorted(records) if not records[name].tombstoned]


def _backlog(live: Sequence[Any]) -> dict[str, object]:
    """The status tally, and a count per priority label as the producer spells it."""
    counts: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for state in live:
        counts[state.status or ""] = counts.get(state.status or "", 0) + 1
        priority = state.fields.get("priority")
        if isinstance(priority, int) and not isinstance(priority, bool):
            priorities[f"P{priority}"] = priorities.get(f"P{priority}", 0) + 1
    closed = counts.get(_CLOSED_STATUS, 0)
    return {
        "total": len(live),
        "active": len(live) - closed,
        "in_progress": counts.get(_ACTIVE_STATUS, 0),
        "closed": closed,
        "by_priority": priorities,
    }


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
    return section


def _gates(repo_root: Path) -> dict[str, object] | None:
    """The last recorded verify run, or None when no artifact is on disk or it is unusable."""
    artifact = repo_root / verify_artifact.RUN_ARTIFACT
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    recorded = board_fields.instant(str(payload.get("recorded_at", "")))
    if recorded is None:
        return None
    rows = payload.get("checks")
    section: dict[str, object] = {
        "recorded_at": board_fields.stamp(recorded),
        "checks": [
            {
                "name": board_fields.text(check.get("name", ""), board_fields.NAME_MAX),
                "status": CHECK_STATUS[str(check.get("status"))],
            }
            for check in (rows if isinstance(rows, list) else ())
            if isinstance(check, dict) and str(check.get("status")) in CHECK_STATUS
        ],
    }
    if isinstance(payload.get("mode"), str):
        section["mode"] = board_fields.text(payload["mode"], board_fields.KIND_MAX)
    if isinstance(payload.get("passed"), bool):
        section["passed"] = payload["passed"]
    return section


def _billed(records: Mapping[str, list]) -> list[Mapping[str, object]]:
    """The dispatch entries carrying billed usage, an estimate dropped.

    A transcript estimate would render identically to an adapter-reported figure and the
    schema has no field to tell them apart. 0 of 398 dispatch records are copilot today, so
    this drops nothing here and is the declared limit for the cell where it would drop
    everything - at which point ``spend`` is omitted rather than guessed.
    """
    return [
        entry
        for history in records.values()
        if isinstance(history, list)
        for entry in history
        if isinstance(entry, dict) and not entry.get("estimated")
    ]


def _sum(entries: Iterable[Mapping[str, object]], key: str) -> float:
    """The numeric values recorded under *key*, summed; anything else contributes nothing."""
    return sum(
        float(value)
        for entry in entries
        if isinstance(value := entry.get(key), int | float) and not isinstance(value, bool)
    )


def _spend(records: Mapping[str, list]) -> dict[str, object] | None:
    """What this machine has been billed, or None when nothing billed is recorded.

    The cache pair sits beside the billed pair and is never summed into it: one is tokens
    paid for and the other is tokens moved.
    """
    billed = _billed(records)
    if not billed:
        return None
    costs = [_sum([entry], "cost") for entry in billed]
    return {
        "scope": MACHINE_LOCAL,
        "lifetime_usd": sum(costs),
        "largest_dispatch_usd": max(costs, default=0.0),
        "input_tokens": int(_sum(billed, "input_tokens")),
        "output_tokens": int(_sum(billed, "output_tokens")),
        "cache_read_tokens": int(_sum(billed, "cache_read_tokens")),
        "cache_write_tokens": int(_sum(billed, "cache_write_tokens")),
    }


def _health(records: dict[str, list]) -> list[dict[str, object]]:
    """Per-agent health, scored by :mod:`basicly.health` and not by a second scorer.

    Both scorers are pure functions of the record map, so they take the map this snapshot
    already read: one file read, one scorer, and no way for the board to disagree with
    ``basicly health`` about the same number.
    """
    drift = {entry.agent: entry.delta for entry in health.agent_drift(records)}
    rows = []
    for scored in health.agent_health(records):
        row: dict[str, object] = {
            "agent": board_fields.text(scored.agent, board_fields.AGENT_MAX),
            "runs": scored.runs,
            "score": scored.health_score,
            "failure_rate": min(1.0, max(0.0, scored.failure_rate)),
        }
        if scored.agent in drift:
            row["drift"] = drift[scored.agent]
        rows.append(row)
    return rows


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
        facts: What the caller knows and this module may not derive - the live-lock facts
            and the in-flight lanes. Each one absent omits its section rather than guessing
            a root or a phase. An empty ``lanes`` sequence still emits ``[]``, which is the
            different claim that the caller can see lanes and there are none.
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
        "repo": {
            "name": board_fields.text(
                tracker_paths.tracker_root(repo_root).resolve().name, board_fields.ID_MAX
            )
        },
    }
    read = _read_and_fold(repo_root)
    if read is not None:
        events, records = read
        markers = board_fields.read_markers(events)
        document["backlog"] = _backlog(_live(records))
        document["asks"] = board_fields.asks(markers)
        document["events"] = board_fields.events(markers, event_limit)
        if known.session is not None:
            document["session"] = _session(known.session, records)
    if known.lanes is not None:
        document["lanes"] = board_fields.lanes(known.lanes)
    gates = _gates(repo_root)
    if gates is not None:
        document["gates"] = gates
    runs = run_record.load_run_records(repo_root)
    if runs:
        spend = _spend(runs)
        if spend is not None:
            document["spend"] = spend
        document["health"] = _health(runs)
    return document
