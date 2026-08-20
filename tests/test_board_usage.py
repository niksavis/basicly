"""Tests for the board sections whose source is ``.basicly/usage/`` (basicly-y754k2).

The sibling of `test_board_snapshot`, split from it when `board_usage` was split out of
`board_snapshot`: leaving these here would be the drift `check_test_naming` exists to stop.

Each case is a claim about **omission**. The schema has no field marking a value as
estimated, so a section whose source is absent must be absent from the document rather than
zero, and an estimate must be dropped rather than rendered beside a billed figure. Both are
asserted through `build_document` rather than against the reducer, because the omission is a
property of the document a consumer reads.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from basicly import board_snapshot, board_usage, owned_store, run_record, verify_artifact

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "board" / "ledger" / "events-0001.jsonl"

NOW = datetime(2026, 1, 2, tzinfo=UTC)

# What the frozen corpus holds; only the closed count is asserted from here.
FIXTURE_CLOSED = 2


def _built(repo_root: Path, **kwargs: Any) -> dict[str, Any]:
    """The document, typed for indexing. `build_document` returns `dict[str, object]`."""
    return cast("dict[str, Any]", board_snapshot.build_document(repo_root, **kwargs))


def _run_records(repo_root: Path, records: dict) -> None:
    """Write a run-record file, the source `spend` and `health` are omitted without."""
    path = repo_root / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def _dispatch(**overrides: object) -> dict:
    """One dispatch entry in the shape `run_record` persists."""
    entry = {
        "agent": "claude",
        "outcome": "executed",
        "returncode": 0,
        "duration_s": 1.0,
        "command": ["claude"],
        "timestamp": "2026-01-01T00:00:00+00:00",
        "cost": 2.0,
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 30,
        "cache_write_tokens": 40,
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def board_repo(work_repo: Path) -> Path:
    """A work repo whose ledger is the frozen board corpus and whose usage dir is absent."""
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    for stale in ledger.glob("events-*.jsonl"):
        stale.unlink()
    shutil.copy2(FIXTURE_LEDGER, ledger / "events-0001.jsonl")
    return work_repo


def test_spend_and_health_are_omitted_while_no_run_records_exist(board_repo: Path) -> None:
    """AC 7: in a lane worktree `.basicly/usage/` does not exist, and the rest still builds."""
    document = _built(board_repo, now=NOW)
    assert "spend" not in document
    assert "health" not in document
    assert document["backlog"]["closed"] == FIXTURE_CLOSED


def test_spend_and_health_arrive_with_the_run_records(board_repo: Path) -> None:
    """The other half of AC 7, so the omission above is a state and not a dead branch."""
    _run_records(board_repo, {"fx-root.1": [_dispatch(), _dispatch(cost=5.0)]})
    document = _built(board_repo, now=NOW)
    assert document["spend"] == {
        "scope": board_usage.MACHINE_LOCAL,
        "lifetime_usd": 7.0,
        "largest_dispatch_usd": 5.0,
        "input_tokens": 20,
        "output_tokens": 40,
        "cache_read_tokens": 60,
        "cache_write_tokens": 80,
    }
    assert [row["agent"] for row in document["health"]] == ["claude"]
    assert document["health"][0]["runs"] == 2


def test_an_estimated_dispatch_is_left_out_of_spend(board_repo: Path) -> None:
    """AC 10: the schema cannot mark a value as estimated, so an estimate is not emitted."""
    _run_records(board_repo, {"fx-root.1": [_dispatch(), _dispatch(cost=99.0, estimated=True)]})
    assert _built(board_repo, now=NOW)["spend"]["lifetime_usd"] == 2.0


def test_spend_is_omitted_where_every_dispatch_is_an_estimate(board_repo: Path) -> None:
    """The declared limit for the copilot cells, where dropping estimates drops everything."""
    _run_records(board_repo, {"fx-root.1": [_dispatch(estimated=True)]})
    document = _built(board_repo, now=NOW)
    assert "spend" not in document
    assert "health" in document


def test_the_gates_section_comes_from_the_verify_artifact(board_repo: Path) -> None:
    """And a `skip` becomes `not_run`, because a value outside a closed set is refused."""
    artifact = board_repo / verify_artifact.RUN_ARTIFACT
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps({
            "mode": "fast",
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "passed": True,
            "checks": [
                {"name": "ruff", "status": "pass"},
                {"name": "docs", "status": "skip"},
                {"name": "invented", "status": "wat"},
            ],
        }),
        encoding="utf-8",
    )
    section = _built(board_repo, now=NOW)["gates"]
    assert section["mode"] == "fast"
    assert section["passed"] is True
    assert section["checks"] == [
        {"name": "ruff", "status": "pass"},
        {"name": "docs", "status": "not_run"},
    ]
