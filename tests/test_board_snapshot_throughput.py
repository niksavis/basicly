from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from basicly import (
    board_footer,
    board_render,
    board_schema,
    board_sections,
    board_snapshot,
    owned_store,
)

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "board" / "ledger" / "events-0001.jsonl"

FIXTURE_CLOSED = 2
CLOSING_DAY = datetime(2026, 1, 1, 23, 59, tzinfo=UTC)
DAY_AFTER = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def board_repo(work_repo: Path) -> Path:
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    for stale in ledger.glob("events-*.jsonl"):
        stale.unlink()
    shutil.copy2(FIXTURE_LEDGER, ledger / "events-0001.jsonl")
    return work_repo


def _built(repo_root: Path, moment: datetime) -> dict[str, Any]:
    return cast("dict[str, Any]", board_snapshot.build_document(repo_root, now=moment))


def test_the_backlog_counts_the_records_closed_on_the_documents_own_day(
    board_repo: Path,
) -> None:

    closing = _built(board_repo, CLOSING_DAY)

    assert closing["backlog"]["closed_today"] == FIXTURE_CLOSED
    assert _built(board_repo, DAY_AFTER)["backlog"]["closed_today"] == 0
    assert closing["backlog"]["closed_today"] <= closing["backlog"]["closed"]
    assert len(closing["events"]) <= board_snapshot.EVENT_LIMIT
    assert not [row for row in closing["events"] if row["kind"] == "status"]


def test_the_footer_draws_the_figure_the_producer_folded(board_repo: Path) -> None:

    document = _built(board_repo, CLOSING_DAY)
    verdict = board_schema.verdict(board_repo, document)
    filled = board_render.context(document, verdict, CLOSING_DAY)

    assert verdict.exit_code == 0, verdict.summary
    assert filled["throughput"].label == "closed today"
    assert filled["throughput"].value == str(FIXTURE_CLOSED)
    assert filled["throughput"].value != board_footer.UNKNOWN


def test_the_figure_counts_records_and_never_close_events() -> None:

    live = [
        SimpleNamespace(record="a", status="closed"),
        SimpleNamespace(record="b", status="open"),
    ]
    days = {"a": "2026-01-01", "b": "2026-01-01"}

    assert board_sections.closed_on(live, days, CLOSING_DAY) == 1
    assert board_sections.closed_on(live, {}, CLOSING_DAY) == 0
