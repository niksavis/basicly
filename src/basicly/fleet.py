"""Cross-repo fleet rollup across the housed workspace repos (basicly-h0f0).

`basicly status --json` is single-repo; every dispatch also drops a metadata-only
run-record into the self-ignored `.basicly/usage/` (`run_record.py`). This module
aggregates both across the basicly-installed repos under a workspace root into one
read-only JSON payload (spike basicly-zv48, dimension 3) — the fleet view a
multi-repo loop needs without re-implementing either snapshot.

Read-only by construction: it discovers repos, reads their status snapshot and
run-records, and returns a payload. It never writes, and a single unreadable repo
is captured as an ``error`` entry rather than failing the whole rollup — the
command always exits 0. The per-repo status snapshot is produced in-process by the
caller's ``status_fn`` (the current engine's ``_status_report``); each repo's
payload still carries its own ``installed_version`` vs ``engine_version`` so
version skew across the fleet stays visible.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import run_record

# Bump only on a breaking change to the fleet payload shape — a fleet consumer
# keys on it to detect a schema it does not understand.
FLEET_SCHEMA_VERSION = 1

# A repo is "basicly-installed" (thus part of the fleet) when it carries this dir.
_MARKER_DIR = ".basicly"


def discover_repos(root: Path) -> list[Path]:
    """The basicly repos directly under *root*, sorted by name.

    A repo qualifies when it is an immediate, non-hidden subdirectory of *root*
    that contains a ``.basicly/`` directory. A non-existent or non-directory root
    yields an empty list (read-only, never raises).
    """
    if not root.is_dir():
        return []
    repos = [
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".") and (child / _MARKER_DIR).is_dir()
    ]
    return sorted(repos, key=lambda path: path.name)


def run_record_summary(repo_root: Path) -> dict[str, Any]:
    """Summarize *repo_root*'s run-records: totals, outcomes, agents, models.

    Tolerates a missing, corrupt, or externally-tampered record file (the loaders
    already degrade to an empty map), so a repo with no dispatches reports zeroes.
    """
    records = run_record.load_run_records(repo_root) or {}
    total_runs = 0
    by_outcome: dict[str, int] = {}
    agents: set[str] = set()
    models: set[str] = set()
    beads_with_runs = 0
    for history in records.values():
        if not isinstance(history, list):
            continue
        entries = [entry for entry in history if isinstance(entry, dict)]
        if not entries:
            continue
        beads_with_runs += 1
        for entry in entries:
            total_runs += 1
            outcome = entry.get("outcome")
            if isinstance(outcome, str):
                by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            agent = entry.get("agent")
            if isinstance(agent, str):
                agents.add(agent)
            model = entry.get("model")
            if isinstance(model, str):
                models.add(model)
    return {
        "total_runs": total_runs,
        "by_outcome": by_outcome,
        "agents": sorted(agents),
        "models": sorted(models),
        "beads_with_runs": beads_with_runs,
    }


def fleet_report(root: Path, status_fn: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    """Roll up every housed repo's status snapshot and run-records under *root*.

    *status_fn* produces a single repo's status payload (the caller injects the
    engine's ``_status_report`` so this module never imports the CLI). A repo whose
    snapshot raises is captured as ``{"error": ...}`` and still contributes its
    run-record summary — one bad repo never fails the rollup.
    """
    repos: list[dict[str, Any]] = []
    total_runs = 0
    total_by_outcome: dict[str, int] = {}
    for repo_root in discover_repos(root):
        try:
            status = status_fn(repo_root)
        except Exception as exc:  # a single repo's snapshot must not fail the fleet
            status = {"error": f"{type(exc).__name__}: {exc}"}
        runs = run_record_summary(repo_root)
        total_runs += runs["total_runs"]
        for outcome, count in runs["by_outcome"].items():
            total_by_outcome[outcome] = total_by_outcome.get(outcome, 0) + count
        repos.append({"name": repo_root.name, "status": status, "runs": runs})
    return {
        "schema_version": FLEET_SCHEMA_VERSION,
        "workspace_root": str(root),
        "repos": repos,
        "totals": {
            "repos": len(repos),
            "total_runs": total_runs,
            "by_outcome": total_by_outcome,
        },
    }
