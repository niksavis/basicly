"""The shipped package's forecast-vs-actual cost, written where a clone can read it.

Split out of ``loop`` when the module-size ratchet left that module no room for the
curator dispatch. The boundary is *what a shipped package cost* against *what shipping
does*: nothing here tears a worktree down, closes a bead or commits tracker state.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from . import decompose, policy, run_record

# The forecast was computed by this module, after the fact, rather than at dispatch.
ROLLUP_FORECAST = "rollup"


def _forecast(
    repo_root: Path, issue_id: str
) -> tuple[str | None, decompose.CostEstimate | None, str | None]:
    """*issue_id*'s task class, forecast and that forecast's provenance.

    The dispatch resolution answers first: an estimate is keyed by the working
    set, this asked with the ownership scope, and the two differ on any bead
    declaring one — 185 of 202 nulls (basicly-agzx.4). The absence reason takes
    the source's place when neither lookup answers.
    """
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
    """*source*, renamed when computed here rather than at dispatch.

    An unfrozen resolution prices with today's factors and this runs after the
    merge, so the dispatch label would pair a past actual with a present
    estimator.
    """
    return ROLLUP_FORECAST if source == decompose.DISPATCH_FORECAST else source


def record(repo_root: Path, issue_id: str) -> bool:
    """Write *issue_id*'s forecast-vs-actual cost onto its bead (kjc5.50).

    Run-records live in the self-ignored ``.basicly/usage/``, so a fresh clone would
    forecast this package's class from the seed factors and never learn what it cost.
    The bead is the only carrier that survives a clone — the forecast beside the actual
    it produced, summed over *every* dispatch including the failed ones.

    A node that was never dispatched — a decomposed feature, whose cost is its
    children's — gets no rollup: counting it would both double-count the work and dilute
    cost-per-landed-package with a null.

    Best-effort in full: it runs after the merge, on a package that has shipped, and
    evidence is never worth failing a landing for.
    """
    try:
        history = run_record.dispatch_history(repo_root).get(issue_id, [])
        if not history:
            return False
        rework: int | None = None
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            rework = policy.rework_recorded(repo_root, issue_id)
        task_class, estimate, source = _forecast(repo_root, issue_id)
        # Money is never recomputed — the forecast carries tokens only.
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
