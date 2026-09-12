from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import fleet, run_record
from .run_record import EXECUTED, FAILED

HEALTH_SCHEMA_VERSION = 2

DEFAULT_WINDOW = 5

MIN_WINDOW_SAMPLE = 3

REGRESSION_DELTA = 0.2

REWORK_PENALTY = 0.3


@dataclass(frozen=True)
class AgentHealth:
    agent: str
    runs: int
    executed: int
    failed: int
    handoff: int
    stopped: int
    stopped_bounds: dict[str, int]
    failure_rate: float
    rework_beads: int
    rework_rate: float
    health_score: float


@dataclass(frozen=True)
class AgentDrift:
    agent: str
    baseline_runs: int
    recent_runs: int
    baseline_failure_rate: float
    recent_failure_rate: float
    delta: float
    regressed: bool


def _agent_entries(records_by_bead: dict[str, list]) -> dict[str, dict[str, Any]]:

    agents: dict[str, dict[str, Any]] = {}
    for bead_id, history in records_by_bead.items():
        if not isinstance(history, list):
            continue
        for entry in history:
            if not isinstance(entry, dict):
                continue
            agent = entry.get("agent")
            outcome = entry.get("outcome")
            if not isinstance(agent, str) or not isinstance(outcome, str):
                continue
            timestamp = entry.get("timestamp")
            timestamp = timestamp if isinstance(timestamp, str) else ""
            bound = entry.get("stopped_bound")
            bound = bound if isinstance(bound, str) and bound else None
            bucket = agents.setdefault(agent, {"entries": [], "bead_counts": {}})
            bucket["entries"].append((timestamp, outcome, bound))
            bucket["bead_counts"][bead_id] = bucket["bead_counts"].get(bead_id, 0) + 1
    return agents


def _scored(entries: list[tuple[str, str, str | None]]) -> list[str]:

    return [outcome for _timestamp, outcome, bound in entries if bound is None]


def _stopped_bounds(entries: list[tuple[str, str, str | None]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _timestamp, _outcome, bound in entries:
        if bound is not None:
            counts[bound] = counts.get(bound, 0) + 1
    return dict(sorted(counts.items()))


def _failure_rate(outcomes: list[str]) -> tuple[int, float]:
    dispatched = [o for o in outcomes if o in (EXECUTED, FAILED)]
    if not dispatched:
        return 0, 0.0
    failed = sum(1 for o in dispatched if o == FAILED)
    return len(dispatched), failed / len(dispatched)


def agent_health(records_by_bead: dict[str, list]) -> list[AgentHealth]:

    result: list[AgentHealth] = []
    for agent, bucket in _agent_entries(records_by_bead).items():
        outcomes = _scored(bucket["entries"])
        stopped_bounds = _stopped_bounds(bucket["entries"])
        executed = sum(1 for o in outcomes if o == EXECUTED)
        failed = sum(1 for o in outcomes if o == FAILED)
        handoff = len(outcomes) - executed - failed
        _dispatched, failure_rate = _failure_rate(outcomes)

        bead_counts: dict[str, int] = bucket["bead_counts"]
        rework_beads = sum(1 for count in bead_counts.values() if count > 1)
        rework_rate = rework_beads / len(bead_counts) if bead_counts else 0.0

        score = (1.0 - failure_rate) * (1.0 - REWORK_PENALTY * min(rework_rate, 1.0))
        result.append(
            AgentHealth(
                agent=agent,
                runs=len(bucket["entries"]),
                executed=executed,
                failed=failed,
                handoff=handoff,
                stopped=sum(stopped_bounds.values()),
                stopped_bounds=stopped_bounds,
                failure_rate=round(failure_rate, 3),
                rework_beads=rework_beads,
                rework_rate=round(rework_rate, 3),
                health_score=round(max(0.0, min(1.0, score)), 3),
            )
        )
    return sorted(result, key=lambda h: h.agent)


def agent_drift(
    records_by_bead: dict[str, list], *, window: int = DEFAULT_WINDOW
) -> list[AgentDrift]:

    result: list[AgentDrift] = []
    for agent, bucket in _agent_entries(records_by_bead).items():
        dispatched = sorted(
            (entry for entry in bucket["entries"] if entry[1] in (EXECUTED, FAILED)),
            key=lambda entry: entry[0],
        )
        recent = dispatched[-window:] if window > 0 else []
        baseline = dispatched[: len(dispatched) - len(recent)]

        base_n, base_fr = _failure_rate(_scored(baseline))
        recent_n, recent_fr = _failure_rate(_scored(recent))
        regressed = (
            base_n >= MIN_WINDOW_SAMPLE
            and recent_n >= MIN_WINDOW_SAMPLE
            and (recent_fr - base_fr) >= REGRESSION_DELTA
        )
        result.append(
            AgentDrift(
                agent=agent,
                baseline_runs=base_n,
                recent_runs=recent_n,
                baseline_failure_rate=round(base_fr, 3),
                recent_failure_rate=round(recent_fr, 3),
                delta=round(recent_fr - base_fr, 3),
                regressed=regressed,
            )
        )
    return sorted(result, key=lambda d: d.agent)


def health_report(repo_root: Path, *, window: int = DEFAULT_WINDOW) -> dict[str, Any]:

    records = run_record.load_run_records(repo_root) or {}
    drift = agent_drift(records, window=window)
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "repo": repo_root.name,
        "window": window,
        "agents": [asdict(h) for h in agent_health(records)],
        "drift": [asdict(d) for d in drift],
        "regressions": [d.agent for d in drift if d.regressed],
    }


def fleet_health(root: Path, *, window: int = DEFAULT_WINDOW) -> dict[str, Any]:

    repos: list[dict[str, Any]] = []
    total_regressions = 0
    for repo_root in fleet.discover_repos(root):
        report = health_report(repo_root, window=window)
        total_regressions += len(report["regressions"])
        repos.append({"name": repo_root.name, "health": report})
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "workspace_root": str(root),
        "repos": repos,
        "totals": {"repos": len(repos), "regressions": total_regressions},
    }
