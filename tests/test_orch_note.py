from __future__ import annotations

import json
from pathlib import Path

from basicly import orch_note
from tests.test_hook_prepush_skip import _orchestrator_store


def test_the_engine_note_writes_the_session_item_and_last_result(tmp_path: Path) -> None:
    _orchestrator_store(tmp_path)

    assert orch_note.note(
        tmp_path, item="acme-2", last="landing merged", environ={"ORCH_SESSION_ID": "s2"}
    )
    saved = json.loads((tmp_path / ".orchestrator/sessions/s2.json").read_text(encoding="utf-8"))
    assert (saved["item"], saved["last"], saved["last_ok"]) == ("acme-2", "landing merged", True)


def test_the_engine_note_does_nothing_without_a_session_id(tmp_path: Path) -> None:
    _orchestrator_store(tmp_path)

    assert orch_note.note(tmp_path, last="x", environ={}) is False
    assert not (tmp_path / ".orchestrator/sessions").exists()
