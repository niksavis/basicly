from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from . import board_fields

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

# module-size-waiver: cost(basicly-k6tpep.1): 6217 of 4000 tokens. It was 4416 when

_SUBJECT_SEP = "#wait-"

LANE_STATES = frozenset({
    "queued",
    "running",
    "waits-to-land",
    "landing",
    "landed",
    "refused",
    "parked",
})

CLOSED_STATUS = "closed"

ACTIVE_STATUS = "in_progress"


@dataclass(frozen=True)
class LaneFacts:
    id: str
    phase: str
    status: str = ""
    state: str = ""
    state_detail: str = ""
    state_since: str = ""
    agent: str = ""
    live: bool | None = None
    provisioned: bool | None = None
    started_at: str = ""
    tokens: int | None = None
    branch: str = ""
    model: str = ""
    cost_usd: float | None = None
    elapsed_s: float | None = None
    context_used: int | None = None
    context_window: int | None = None
    rework_attempt: int | None = None
    rework_allowance: int | None = None
    note: str = ""


@dataclass(frozen=True)
class DetailFacts:
    id: str
    worktree: str = ""
    branch: str = ""
    checkpoints_held: tuple[str, ...] = ()
    checkpoints_missing: tuple[str, ...] = ()
    rework: Mapping[str, int] = MappingProxyType({})
    next_command: str = ""


@dataclass(frozen=True)
class RepoFacts:
    branch: str = ""
    head: str = ""
    dirty: bool | None = None


def repo(name: str, facts: RepoFacts | None) -> dict[str, object]:

    section: dict[str, object] = {"name": board_fields.text(name, board_fields.ID_MAX)}
    if facts is None:
        return section
    if facts.branch:
        section["branch"] = board_fields.text(facts.branch, board_fields.TEXT_MAX)
    if facts.head:
        section["head"] = board_fields.text(facts.head, board_fields.HEAD_MAX)
    if facts.dirty is not None:
        section["dirty"] = facts.dirty
    return section


@dataclass(frozen=True)
class Readiness:
    ready: frozenset[str] = frozenset()
    blocked: frozenset[str] = frozenset()

    def flag(self, record: str) -> bool | None:
        if record in self.ready:
            return True
        return False if record in self.blocked else None


_OFFERS: Mapping[str, tuple[str, str]] = {
    "checkpoint": ("Approve it", "checkpoint-approve"),
    "decision": ("Answer it", "loop-answer"),
}


def asks(
    markers: Sequence[board_fields.Marker],
    *,
    now: datetime,
    questions: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:

    waits = [row for row in markers if row.family == board_fields.WAIT_FAMILY]
    answered = {
        row.fields["id"]
        for row in waits
        if "id" in row.fields and board_fields.ANSWERED in row.flags
    }
    pending = []
    for row in waits:
        wait_id = row.fields.get("id", "")
        kind = row.fields.get("kind", "")
        requested = board_fields.instant(row.at)
        if not wait_id or not kind or wait_id in answered or requested is None:
            continue
        ask: dict[str, object] = {
            "wait_id": board_fields.text(wait_id, board_fields.WAIT_ID_MAX),
            "kind": board_fields.text(kind, board_fields.KIND_MAX),
            "requested_at": board_fields.stamp(requested),
            "issue": board_fields.text(row.record, board_fields.ID_MAX),
        }
        ask["waiting_s"] = max(0.0, (now - requested).total_seconds())
        if subject := wait_id.partition(_SUBJECT_SEP)[2]:
            ask["subject"] = board_fields.text(subject, board_fields.TEXT_MAX)
        if question := (questions or {}).get(wait_id, ""):
            ask["question"] = board_fields.text(question, board_fields.QUESTION_MAX)
        if offered := _OFFERS.get(kind):
            ask["actions"] = [{"offer": offered[0], "basicly": offered[1]}]
        pending.append(ask)
    return pending


def _standing(lane: LaneFacts) -> dict[str, object]:

    if lane.state not in LANE_STATES:
        return {}
    held: dict[str, object] = {"state": lane.state}
    if lane.state_detail:
        held["state_detail"] = board_fields.text(lane.state_detail, board_fields.TEXT_MAX)
    if (entered := board_fields.instant(lane.state_since)) is not None:
        held["state_since"] = board_fields.stamp(entered)
    return held


def lanes(facts: Iterable[LaneFacts]) -> list[dict[str, object]]:

    rows = []
    for lane in facts:
        if not lane.id or not lane.phase:
            continue
        row: dict[str, object] = {
            "id": board_fields.text(lane.id, board_fields.ID_MAX),
            "phase": board_fields.text(lane.phase, board_fields.KIND_MAX),
        }
        if lane.status:
            row["status"] = board_fields.text(lane.status, board_fields.KIND_MAX)
        row.update(_standing(lane))
        if lane.agent:
            row["agent"] = board_fields.text(lane.agent, board_fields.AGENT_MAX)
        row.update(
            (name, flag)
            for name, flag in (("live", lane.live), ("provisioned", lane.provisioned))
            if flag is not None
        )
        if (started := board_fields.instant(lane.started_at)) is not None:
            row["started_at"] = board_fields.stamp(started)
        if lane.tokens is not None:
            row["tokens"] = max(0, lane.tokens)
        if lane.branch:
            row["branch"] = board_fields.text(lane.branch, board_fields.TEXT_MAX)
        if lane.model:
            row["model"] = board_fields.text(lane.model, board_fields.AGENT_MAX)
        if lane.note:
            row["note"] = board_fields.text(lane.note, board_fields.TEXT_MAX)
        for name, held in (
            ("cost_usd", lane.cost_usd),
            ("elapsed_s", lane.elapsed_s),
            ("context_used", lane.context_used),
            ("context_window", lane.context_window),
            ("rework_attempt", lane.rework_attempt),
            ("rework_allowance", lane.rework_allowance),
        ):
            if held is not None:
                row[name] = max(0, held)
        rows.append(row)
    return rows


def detail(facts: Iterable[DetailFacts]) -> list[dict[str, object]]:

    rows = []
    for held in facts:
        if not held.id:
            continue
        row: dict[str, object] = {
            "id": board_fields.text(held.id, board_fields.ID_MAX),
            "worktree": board_fields.text(held.worktree, board_fields.NAME_MAX),
            "branch": board_fields.text(held.branch, board_fields.TEXT_MAX),
            "checkpoints_held": [
                board_fields.text(name, board_fields.KIND_MAX) for name in held.checkpoints_held
            ],
            "checkpoints_missing": [
                board_fields.text(name, board_fields.KIND_MAX) for name in held.checkpoints_missing
            ],
            "rework": {
                board_fields.text(gate, board_fields.KIND_MAX): max(0, count)
                for gate, count in held.rework.items()
            },
        }
        if held.next_command:
            row["next_command"] = board_fields.text(held.next_command, board_fields.TEXT_MAX)
        rows.append(row)
    return rows


def units(
    states: Iterable[Any],
    *,
    phases: Mapping[str, str] | None = None,
    ready: Readiness | None = None,
    owes: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, object]]:

    rows = []
    for state in states:
        row: dict[str, object] = {"id": board_fields.text(state.record, board_fields.ID_MAX)}
        if title := state.fields.get("title"):
            row["title"] = board_fields.text(title, board_fields.TEXT_MAX)
        if state.status:
            row["status"] = board_fields.text(state.status, board_fields.KIND_MAX)
        priority = state.fields.get("priority")
        if isinstance(priority, int) and not isinstance(priority, bool):
            row["priority"] = board_fields.text(f"P{priority}", board_fields.PRIORITY_MAX)
        if kind := state.fields.get("issue_type"):
            row["type"] = board_fields.text(kind, board_fields.KIND_MAX)
        if phase := (phases or {}).get(state.record, ""):
            row["phase"] = board_fields.text(phase, board_fields.KIND_MAX)
        if ready is not None and (flag := ready.flag(state.record)) is not None:
            row["ready"] = flag
        if owes is not None and (sections := owes.get(state.record)) is not None:
            row["owes"] = [board_fields.text(name, board_fields.KIND_MAX) for name in sections]
        rows.append(row)
    return rows


def backlog(
    live: Sequence[Any],
    readiness: Readiness | None,
    closings: Mapping[str, str],
    moment: datetime,
) -> dict[str, object]:

    counts: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for state in live:
        counts[state.status or ""] = counts.get(state.status or "", 0) + 1
        priority = state.fields.get("priority")
        if isinstance(priority, int) and not isinstance(priority, bool):
            priorities[f"P{priority}"] = priorities.get(f"P{priority}", 0) + 1
    closed = counts.get(CLOSED_STATUS, 0)
    section: dict[str, object] = {
        "total": len(live),
        "active": len(live) - closed,
        "in_progress": counts.get(ACTIVE_STATUS, 0),
        "closed": closed,
        "closed_today": closed_on(live, closings, moment),
        "by_priority": priorities,
    }
    if readiness is not None:
        section["ready"] = len(readiness.ready)
        section["blocked"] = len(readiness.blocked)
    return section


def _day(moment: datetime) -> str:

    return moment.astimezone(UTC).date().isoformat()


def closing_days(kit: Any, collected: Iterable[Any]) -> dict[str, str]:

    days: dict[str, str] = {}
    for event in kit.events.canonical_order(collected):
        if event.kind != kit.events.KIND_STATUS:
            continue
        if event.payload.get("status") != CLOSED_STATUS:
            continue
        at = board_fields.instant(str(event.ts))
        if at is not None:
            days[event.record] = _day(at)
    return days


def closed_on(live: Iterable[Any], days: Mapping[str, str], moment: datetime) -> int:

    day = _day(moment)
    return sum(
        1 for state in live if state.status == CLOSED_STATUS and days.get(state.record) == day
    )


def edge_triples(kit: Any, collected: Iterable[Any]) -> list[tuple[str, str, str]]:

    held: dict[tuple[str, str, str], bool] = {}
    for event in kit.events.canonical_order(collected):
        if event.kind not in (kit.events.KIND_EDGE, kit.events.KIND_EDGE_RETRACTED):
            continue
        target = event.payload.get(kit.migrate.EDGE_TO)
        kind = event.payload.get(kit.migrate.EDGE_TYPE)
        if isinstance(target, str) and isinstance(kind, str):
            held[(event.record, kind, target)] = event.kind == kit.events.KIND_EDGE
    return [edge for edge, asserted in held.items() if asserted]


def graph(triples: Iterable[tuple[str, str, str]]) -> dict[str, object]:

    return {
        "edges": [
            {
                "from": board_fields.text(source, board_fields.ID_MAX),
                "to": board_fields.text(target, board_fields.ID_MAX),
                "kind": board_fields.text(kind, board_fields.KIND_MAX),
            }
            for source, kind, target in triples
        ]
    }


def events(markers: Sequence[board_fields.Marker], limit: int) -> list[dict[str, object]]:

    rows = []
    for row in markers[-limit:]:
        at = board_fields.instant(row.at)
        if at is None:
            continue
        declared = " ".join(f"{name}={value}" for name, value in sorted(row.fields.items()))
        rows.append({
            "at": board_fields.stamp(at),
            "issue": board_fields.text(row.record, board_fields.ID_MAX),
            "kind": board_fields.text(row.family.strip("[]"), board_fields.KIND_MAX),
            "text": board_fields.text(
                " ".join([*sorted(row.flags), declared]).strip(), board_fields.TEXT_MAX
            ),
        })
    return rows
