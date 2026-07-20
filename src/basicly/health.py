"""Per-agent health scoring and behavioral drift over time (basicly-y886).

Health is derived from the durable run-record log
(``.basicly/usage/run-records.json``, :mod:`run_record`) — the only append-only
historical signal the harness keeps. ``br`` gate results are **not** a source:
they overwrite in ``br`` (no history, see :mod:`policy`), so gate pass/fail over
time is not queryable. A failed dispatch is a ``failed`` run and a rework
re-dispatch appends another record for the same bead, so run-record outcomes are
a durable proxy for the gate-fail + rework signal without coupling to ``br``'s
non-historical gate state.

Two signals, both computed as pure functions of the record map:

- **Health** — per agent: dispatch failure rate, a rework signal (beads the agent
  re-dispatched), and a bounded ``health_score`` in ``[0, 1]``.
- **Drift** — a rolling baseline read straight off the log's own timestamps: an
  agent's most-recent ``window`` dispatched runs (the *recent* window) are
  compared against everything older (the *baseline* window). A behavioral
  regression is flagged when the recent failure rate exceeds the baseline by
  :data:`REGRESSION_DELTA`, with :data:`MIN_WINDOW_SAMPLE` dispatched runs in each
  window so a couple of runs cannot trip it.

Everything here is read-only, advisory (nothing gates on a score), and
deterministic — no wall-clock enters a payload, so a given log always scores the
same. Handoffs carry no execution outcome, so they count toward an agent's run
total but never toward a failure or drift signal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import fleet, run_record
from .run_record import EXECUTED, FAILED

# Bump only on a breaking change to the health payload shape — a consumer keys on
# it to detect a schema it does not understand.
HEALTH_SCHEMA_VERSION = 1

# Default number of most-recent dispatched runs that form an agent's recent
# window for drift; the rest are its baseline.
DEFAULT_WINDOW = 5

# Minimum dispatched runs required in *each* window before a regression can be
# flagged — below this the sample is too small to be a signal.
MIN_WINDOW_SAMPLE = 3

# How much the recent failure rate must exceed the baseline (absolute, 0..1) to
# count as a behavioral regression.
REGRESSION_DELTA = 0.2

# How much a fully-reworked agent's score is discounted (multiplicative, on top
# of its success rate). Failure dominates the score; rework is a secondary drag.
REWORK_PENALTY = 0.3


@dataclass(frozen=True)
class AgentHealth:
    """One agent's aggregate health across every bead it ran in a repo."""

    agent: str
    runs: int
    executed: int
    failed: int
    handoff: int
    failure_rate: float
    rework_beads: int
    rework_rate: float
    health_score: float


@dataclass(frozen=True)
class AgentDrift:
    """One agent's recent-vs-baseline failure-rate drift (rolling baseline)."""

    agent: str
    baseline_runs: int
    recent_runs: int
    baseline_failure_rate: float
    recent_failure_rate: float
    delta: float
    regressed: bool


def _agent_entries(records_by_bead: dict[str, list]) -> dict[str, dict[str, Any]]:
    """Group the record map by agent: time-ordered outcomes and per-bead counts.

    Returns ``{agent: {"entries": [(timestamp, outcome), ...],
    "bead_counts": {bead_id: n}}}``. Tolerant of a corrupt/tampered map: any entry
    that is not a dict, or lacks a string ``agent``/``outcome``, is skipped rather
    than raising — the log is telemetry and must never fail a read.
    """
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
            bucket = agents.setdefault(agent, {"entries": [], "bead_counts": {}})
            bucket["entries"].append((timestamp, outcome))
            bucket["bead_counts"][bead_id] = bucket["bead_counts"].get(bead_id, 0) + 1
    return agents


def _failure_rate(outcomes: list[str]) -> tuple[int, float]:
    """Dispatched-run count and failure rate for *outcomes* (handoffs excluded)."""
    dispatched = [o for o in outcomes if o in (EXECUTED, FAILED)]
    if not dispatched:
        return 0, 0.0
    failed = sum(1 for o in dispatched if o == FAILED)
    return len(dispatched), failed / len(dispatched)


def agent_health(records_by_bead: dict[str, list]) -> list[AgentHealth]:
    """Per-agent health aggregated across every bead, sorted by agent name."""
    result: list[AgentHealth] = []
    for agent, bucket in _agent_entries(records_by_bead).items():
        outcomes = [outcome for _timestamp, outcome in bucket["entries"]]
        executed = sum(1 for o in outcomes if o == EXECUTED)
        failed = sum(1 for o in outcomes if o == FAILED)
        handoff = len(outcomes) - executed - failed
        _dispatched, failure_rate = _failure_rate(outcomes)

        bead_counts: dict[str, int] = bucket["bead_counts"]
        rework_beads = sum(1 for count in bead_counts.values() if count > 1)
        rework_rate = rework_beads / len(bead_counts) if bead_counts else 0.0

        # Failure dominates (all-fail -> 0); rework is a multiplicative drag.
        score = (1.0 - failure_rate) * (1.0 - REWORK_PENALTY * min(rework_rate, 1.0))
        result.append(
            AgentHealth(
                agent=agent,
                runs=len(outcomes),
                executed=executed,
                failed=failed,
                handoff=handoff,
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
    """Per-agent recent-vs-baseline failure-rate drift, sorted by agent name.

    An agent's dispatched runs (handoffs dropped — they carry no outcome) are
    ordered by timestamp; the last *window* are the recent window and the rest the
    baseline. A regression is flagged only when both windows hold at least
    :data:`MIN_WINDOW_SAMPLE` runs and the recent failure rate exceeds the
    baseline by :data:`REGRESSION_DELTA`.
    """
    result: list[AgentDrift] = []
    for agent, bucket in _agent_entries(records_by_bead).items():
        dispatched = sorted(
            (entry for entry in bucket["entries"] if entry[1] in (EXECUTED, FAILED)),
            key=lambda entry: entry[0],
        )
        recent = dispatched[-window:] if window > 0 else []
        baseline = dispatched[: len(dispatched) - len(recent)]

        base_n, base_fr = _failure_rate([o for _t, o in baseline])
        recent_n, recent_fr = _failure_rate([o for _t, o in recent])
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
    """A repo's per-agent health + drift payload (schema-versioned, deterministic).

    A repo with no run-records (missing or corrupt log) reports empty ``agents``
    and ``drift`` with no regressions rather than raising.
    """
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
    """Roll up per-repo health across every housed repo under *root*.

    Reuses :func:`fleet.discover_repos`; read-only, and a repo with no records
    simply contributes an empty health report. Never raises on an empty or
    non-existent root — it yields an empty rollup.
    """
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
