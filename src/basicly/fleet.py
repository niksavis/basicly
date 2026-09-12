from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import run_record

FLEET_SCHEMA_VERSION = 1

_MARKER_DIR = ".basicly"


def discover_repos(root: Path) -> list[Path]:

    if not root.is_dir():
        return []
    repos = [
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".") and (child / _MARKER_DIR).is_dir()
    ]
    return sorted(repos, key=lambda path: path.name)


def run_record_summary(repo_root: Path) -> dict[str, Any]:

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

    repos: list[dict[str, Any]] = []
    total_runs = 0
    total_by_outcome: dict[str, int] = {}
    for repo_root in discover_repos(root):
        try:
            status = status_fn(repo_root)
        except Exception as exc:  # noqa: BLE001 — injected callable, recorded in the rollup
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
