from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".basicly" / "core" / "kit" / "orch"))

brief = importlib.import_module("orch_relay.brief")


def _notes(*texts: str) -> list:
    return [brief.Note(f"2026-11-21T10:{n:02d}:00Z", text) for n, text in enumerate(texts)]


def test_the_brief_starts_at_the_newest_handover_and_lists_owner_items_first() -> None:
    found = _notes(
        "[handover old] goal A",
        "[state] before the new handover",
        "[handover new] goal B",
        "[decision] go on lane A",
        "[owner 2026-11-21] finish the kit",
    )

    shown = brief.render("dev-x", found, ["worker  working  5m lane A"])

    assert "goal B" in shown and "goal A" not in shown
    assert "before the new handover" not in shown
    owner, later = shown.index("owner items (1):"), shown.index("later notes (1 of 1):")
    assert owner < shown.index("finish the kit") < shown.index("sessions:") < later
    assert shown.index("go on lane A") > later
    assert "worker  working  5m lane A" in shown


def test_a_brief_over_the_limit_drops_the_oldest_later_notes_and_says_so() -> None:
    found = _notes("[handover] goal", *(f"[state] note {n} " + "x" * 900 for n in range(40)))

    shown = brief.render("dev-x", found, [], limit=20_000)

    assert len(shown.encode("utf-8")) <= 20_000
    assert "note 39" in shown and "note 0 " not in shown
    assert "older note(s) left out; read them with: tracker show dev-x" in shown
    assert brief.NO_SESSIONS in shown


def test_a_record_with_no_handover_shows_every_note_and_says_none_found() -> None:
    shown = brief.render("dev-x", _notes("[state] only note"), [])

    assert "handover:\nnone found" in shown
    assert "later notes (1 of 1):" in shown and "left out" not in shown
