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
    invest,
    owned_store,
    projection,
    run_record,
    tracker_paths,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

SCHEMA = board_schema.VERSION

TOOL = "basicly"

DEFAULT_STALE_AFTER_S = 60.0

ONE_SHOT = "one-shot"
SUPERVISOR_TICK = "supervisor-tick"
SELF_REFRESH = "self-refresh"

SNAPSHOT_FILE = Path(".basicly/usage/board/snapshot.json")


@dataclass(frozen=True)
class Freshness:
    source: str = ONE_SHOT
    cadence_s: float | None = None
    stale_after_s: float = DEFAULT_STALE_AFTER_S


EVENT_LIMIT = 6

board_usage.MACHINE_LOCAL = "machine-local"

board_usage.CHECK_STATUS = {"pass": "pass", "fail": "fail", "skip": "not_run", "not_run": "not_run"}


@dataclass(frozen=True)
class SessionFacts:
    root_issue: str
    supervised: bool = False
    session_id: str = ""
    age_s: float | None = None
    stale: bool | None = None
    grant_level: str = ""
    token_budget: int | None = None
    spent_tokens: int | None = None


@dataclass(frozen=True)
class Facts:
    session: SessionFacts | None = None
    lanes: Sequence[board_sections.LaneFacts] | None = None
    details: Sequence[board_sections.DetailFacts] | None = None
    repo: board_sections.RepoFacts | None = None
    phases: Mapping[str, str] | None = None
    readiness: board_sections.Readiness | None = None
    questions: Mapping[str, str] | None = None
    advances: Callable[[Sequence[Any]], Sequence[Mapping[str, object]]] | None = None


def _read_and_fold(
    repo_root: Path,
) -> tuple[Mapping[str, Any], list[Any], list[tuple], Mapping[str, str]] | None:

    try:
        kit = owned_store.kit(repo_root)
        events = kit.read_ledger(owned_store.ledger_dir(repo_root))
    except owned_store.TrackerDivergenceError, OSError, ValueError:
        return None
    return (
        kit.events.fold(events).records,
        board_fields.read_markers(events),
        board_sections.edge_triples(kit, events),
        board_sections.closing_days(kit, events),
    )


def _live(records: Mapping[str, Any]) -> list[Any]:

    return [records[name] for name in sorted(records) if not records[name].tombstoned]


def _session(facts: SessionFacts, records: Mapping[str, Any]) -> dict[str, object]:
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
        "repo": board_sections.repo(
            tracker_paths.tracker_root(repo_root).resolve().name, known.repo
        ),
    }
    read = _read_and_fold(repo_root)
    if read is not None:
        records, markers, edges, closings = read
        live = _live(records)
        active = [state for state in live if state.status != board_sections.CLOSED_STATUS]
        drawn = {state.record for state in active}
        document["backlog"] = board_sections.backlog(live, known.readiness, closings, moment)
        document["units"] = board_sections.units(
            active,
            phases=known.phases,
            ready=known.readiness,
            owes=invest.owed(active, repo_root),
        )
        document["graph"] = board_sections.graph(
            edge for edge in edges if edge[0] in drawn or edge[2] in drawn
        )
        document["asks"] = board_sections.asks(markers, now=moment, questions=known.questions)
        if known.advances is not None:
            document["asks"] = [*document["asks"], *known.advances(markers)]
        document["events"] = board_sections.events(markers, event_limit)
        if known.details is not None:
            document["detail"] = board_sections.detail(
                held for held in known.details if held.id in drawn
            )
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

    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def write_document(repo_root: Path, document: Mapping[str, object]) -> Path:

    path = repo_root / SNAPSHOT_FILE
    projection.atomic_write_text(path, serialize(document))
    return path
