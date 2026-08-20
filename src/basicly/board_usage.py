"""The board sections whose source is ``.basicly/usage/``: gates, spend and agent health.

The boundary is which sources a section reads, against the ledger half and the assembly
:mod:`basicly.board_snapshot` holds.

**Omit, never estimate.** The schema has no field marking a value as estimated, so an estimate
would render indistinguishably from a billed number. A section whose source is absent is
absent from the document - never zero - and the page renders "not emitted by this producer".
``.basicly/usage/`` is git-ignored and worktree-local, so in a lane worktree all three of
these sources really are missing and the tracker half still draws (basicly-y754k2).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from . import board_fields, health, verify_artifact

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

# The vocabulary the schema admits for a check result.
CHECK_STATUS = {"pass": "pass", "fail": "fail", "skip": "not_run", "not_run": "not_run"}

# The spend scope this producer can honestly claim: `.basicly/usage/` never leaves the
# machine, so a figure read from it is not the repository's total.
MACHINE_LOCAL = "machine-local"


def gates(repo_root: Path) -> dict[str, object] | None:
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


def spend(records: Mapping[str, list]) -> dict[str, object] | None:
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


def health_rows(records: dict[str, list]) -> list[dict[str, object]]:
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
