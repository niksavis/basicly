from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import board_bodies, tracker

if TYPE_CHECKING:
    from pathlib import Path


def test_every_record_with_a_description_is_returned_keyed_by_its_id(work_repo: Path) -> None:
    held = {str(r["id"]) for r in tracker.all_records(work_repo) if r.get("description")}
    assert held, "this repo fixture holds no described record, so the assertion below is empty"
    assert set(board_bodies.bodies(work_repo)) == held


def test_a_record_carrying_no_description_is_absent_rather_than_empty(work_repo: Path) -> None:
    assert all(text.strip() for text in board_bodies.bodies(work_repo).values())


def test_the_whole_ledger_is_folded_once(work_repo: Path, monkeypatch: object) -> None:

    calls = 0
    real = tracker.all_records

    def counted(repo_root: Path) -> list[dict]:
        nonlocal calls
        calls += 1
        return real(repo_root)

    monkeypatch.setattr(tracker, "all_records", counted)  # type: ignore[attr-defined]
    board_bodies.bodies(work_repo)
    assert calls == 1, f"one call builds the whole map; this made {calls}"
