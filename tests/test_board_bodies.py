"""The bodies a record page fetches, and the one property that makes them affordable.

Named after the module it tests, which `test-naming` requires. The seam is the *fold*: `--out`
writes a page per record, so a read apiece would fold one ledger 307 times (basicly-lc2bd3v.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import board_bodies, tracker

if TYPE_CHECKING:
    from pathlib import Path


def test_every_record_with_a_description_is_returned_keyed_by_its_id(work_repo: Path) -> None:
    """A positive control first: an empty map and an unreadable ledger must not look alike."""
    held = {str(r["id"]) for r in tracker.all_records(work_repo) if r.get("description")}
    assert held, "this repo fixture holds no described record, so the assertion below is empty"
    assert set(board_bodies.bodies(work_repo)) == held


def test_a_record_carrying_no_description_is_absent_rather_than_empty(work_repo: Path) -> None:
    """The page reads a missing key as an absence and prints that; "" would read as a body."""
    assert all(text.strip() for text in board_bodies.bodies(work_repo).values())


def test_the_whole_ledger_is_folded_once(work_repo: Path, monkeypatch: object) -> None:
    """307 pages against a per-page read is 307 folds of one log.

    Counted through the seam rather than timed: a timing assertion passes on a fast machine
    and on a wrong implementation alike.
    """
    calls = 0
    real = tracker.all_records

    def counted(repo_root: Path) -> list[dict]:
        nonlocal calls
        calls += 1
        return real(repo_root)

    monkeypatch.setattr(tracker, "all_records", counted)  # type: ignore[attr-defined]
    board_bodies.bodies(work_repo)
    assert calls == 1, f"one call builds the whole map; this made {calls}"
