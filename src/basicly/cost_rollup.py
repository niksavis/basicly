from __future__ import annotations

import contextlib
from pathlib import Path

from . import decompose, policy, run_record

ROLLUP_FORECAST = "rollup"


def _forecast(
    repo_root: Path, issue_id: str
) -> tuple[str | None, decompose.CostEstimate | None, str | None]:

    lookup = decompose.resolve_dispatch_sizing(repo_root, issue_id)
    if lookup.sizing is not None:
        return (
            lookup.sizing.task_class,
            lookup.sizing.estimate,
            _rollup_source(lookup.sizing.source),
        )

    info = decompose.bead_class_and_scope(repo_root, issue_id)
    task_class, scope = info if info is not None else (None, ())
    estimate = (
        decompose.forecast_for(repo_root, task_class, scope) if task_class is not None else None
    )
    if estimate is not None:
        return task_class, estimate, decompose.FROZEN_FORECAST
    return task_class, None, lookup.absence or None


def _rollup_source(source: str) -> str:

    return ROLLUP_FORECAST if source == decompose.DISPATCH_FORECAST else source


def record(repo_root: Path, issue_id: str) -> bool:

    try:
        history = run_record.dispatch_history(repo_root).get(issue_id, [])
        if not history:
            return False
        rework: int | None = None
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            rework = policy.rework_recorded(repo_root, issue_id)
        task_class, estimate, source = _forecast(repo_root, issue_id)
        forecast = run_record.CostForecast(
            tokens=estimate.total if estimate else None,
            source=source,
        )
        ident = run_record.record_cost_marker(
            repo_root,
            issue_id,
            actual=run_record.cost_rollup(history, rework=rework),
            forecast=forecast,
            task_class=task_class,
            scope_tokens=estimate.scope_tokens if estimate else None,
        )
    except RuntimeError, ValueError, OSError:
        return False
    return ident is not None
