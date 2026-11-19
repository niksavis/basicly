from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from . import gh_cli

WORKFLOW = Path(".github") / "workflows" / "compatibility.yml"
SOURCES = (("compatibility.yml", ("schedule", "workflow_dispatch")), ("release.yml", ("push",)))
LIMIT = "10"
REMEDY = "run gh workflow run compatibility.yml, wait for it to pass, then release again"

Query = Callable[[list[str]], str | None]


def declared(repo_root: Path) -> bool:
    path = repo_root / WORKFLOW
    return path.is_file() and "macos-latest" in path.read_text(encoding="utf-8")


def _runs(query: Query) -> list[dict] | None:
    found: list[dict] = []
    for workflow, events in SOURCES:
        fields = "databaseId,event,createdAt,url"
        listed = query(["run", "list", "--workflow", workflow, "--limit", LIMIT, "--json", fields])
        if listed is None:
            return None
        found.extend(run for run in json.loads(listed or "[]") if run.get("event") in events)
    return sorted(found, key=lambda run: str(run.get("createdAt", "")), reverse=True)


def _macos_job(query: Query, run: dict) -> dict | None:
    viewed = query(["run", "view", str(run["databaseId"]), "--json", "jobs"])
    if viewed is None:
        return None
    jobs = json.loads(viewed or "{}").get("jobs", [])
    done = [job for job in jobs if "macos" in str(job.get("name", "")).lower()]
    return next((job for job in done if job.get("status") == "completed"), None)


def refusal(repo_root: Path, query: Query = gh_cli.run) -> str | None:
    if not declared(repo_root):
        return None
    runs = _runs(query)
    if runs is None:
        return f"cannot read the macOS compatibility runs with gh; {REMEDY}"
    for run in runs:
        job = _macos_job(query, run)
        if job is None:
            continue
        if job.get("conclusion") == "success":
            return None
        return (
            f"the latest macOS compatibility job ended {job.get('conclusion')} in "
            f"{run.get('url')}; {REMEDY}"
        )
    return f"no completed macOS compatibility job in the last {LIMIT} runs; {REMEDY}"
