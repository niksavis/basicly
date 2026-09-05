"""The throughput figure, from the ledger's close events to the footer's cell.

Split out of `test_board_snapshot` under the `test_<module>_<aspect>` form that
`.scripts/check_test_naming.py` permits, as `test_board_snapshot_lock` already is: that module
sits 243 tokens under the size cap and these do not fit under it.

**End to end on purpose, and basicly-w6vbw61 is why.** The defect was a consumer reading a
field no producer ever wrote, with both sides green because each was asserted alone - the
third of that class in one day. A reducer-level assertion here would repeat it, so the
producer test drives the real corpus through `build_document` and the consumer test reads the
cell the wall draws.
"""

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

# The two records the frozen corpus closes, both on 2026-01-01. It writes six status events
# that day and only two are closes, so a figure counting status events would read six.
FIXTURE_CLOSED = 2
CLOSING_DAY = datetime(2026, 1, 1, 23, 59, tzinfo=UTC)
DAY_AFTER = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def board_repo(work_repo: Path) -> Path:
    """A work repo whose ledger is the frozen board corpus."""
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    for stale in ledger.glob("events-*.jsonl"):
        stale.unlink()
    shutil.copy2(FIXTURE_LEDGER, ledger / "events-0001.jsonl")
    return work_repo


def _built(repo_root: Path, moment: datetime) -> dict[str, Any]:
    """The document, typed for indexing, as `test_board_snapshot._built` types it."""
    return cast("dict[str, Any]", board_snapshot.build_document(repo_root, now=moment))


def test_the_backlog_counts_the_records_closed_on_the_documents_own_day(
    board_repo: Path,
) -> None:
    """AC 1, 2, 4 and 5, which one corpus built twice answers.

    Only the document's day moves between the builds, so a figure taken off the reader's clock
    could not produce both; `DAY_AFTER` reads a **measured zero** rather than an absence. AC 4
    rides the same build - the tail is capped by contract, so spelling the closes into it
    stays refused, and a status row cannot appear there at all.
    """
    closing = _built(board_repo, CLOSING_DAY)

    assert closing["backlog"]["closed_today"] == FIXTURE_CLOSED
    assert _built(board_repo, DAY_AFTER)["backlog"]["closed_today"] == 0
    assert closing["backlog"]["closed_today"] <= closing["backlog"]["closed"]
    assert len(closing["events"]) <= board_snapshot.EVENT_LIMIT
    assert not [row for row in closing["events"] if row["kind"] == "status"]


def test_the_footer_draws_the_figure_the_producer_folded(board_repo: Path) -> None:
    """The end of the wire, and the assertion the two green sides did not make.

    The cell read `not measured` on a day the ledger closed twenty records, so the claim under
    test is that a close reaches the page and not merely the section.
    """
    document = _built(board_repo, CLOSING_DAY)
    verdict = board_schema.verdict(board_repo, document)
    filled = board_render.context(document, verdict, CLOSING_DAY)

    assert verdict.exit_code == 0, verdict.summary
    assert filled["throughput"].label == "closed today"
    assert filled["throughput"].value == str(FIXTURE_CLOSED)
    assert filled["throughput"].value != board_footer.UNKNOWN


def test_the_figure_counts_records_and_never_close_events() -> None:
    """A close the log later undid is not a unit closed, which is what bounds it by `closed`.

    Pinned on the reducer because the frozen corpus holds no reopened record, and a ledger
    written to hold one would assert this module's idea of an event rather than the rule.
    """
    live = [
        SimpleNamespace(record="a", status="closed"),
        SimpleNamespace(record="b", status="open"),
    ]
    days = {"a": "2026-01-01", "b": "2026-01-01"}

    assert board_sections.closed_on(live, days, CLOSING_DAY) == 1
    assert board_sections.closed_on(live, {}, CLOSING_DAY) == 0
