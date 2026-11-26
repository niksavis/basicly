from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import merge, orch_note
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


def test_the_engine_pace_passes_the_record_and_moment_to_the_kit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[object, ...]] = []

    class Kit:
        @staticmethod
        def pace(_cwd: Path, _environ: object, record: str, moment: str) -> bool:
            seen.append((record, moment))
            return True

    monkeypatch.setattr(orch_note, "_kit_note", lambda: Kit)
    assert orch_note.pace(tmp_path, "acme-3", "done", environ={})
    assert seen == [("acme-3", "done")]


@pytest.mark.parametrize(
    ("status", "paced"), [("merged", [("acme-4", "done")]), ("verify-failed", [])]
)
def test_only_a_merged_landing_asks_for_the_done_pace_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, paced: list[tuple[str, str]]
) -> None:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(orch_note, "note", lambda *_a, **_k: True)
    monkeypatch.setattr(
        orch_note, "pace", lambda _cwd, record, moment: seen.append((record, moment))
    )
    merge._Landing(tmp_path, "acme-4").close(merge.MergeResult("feat", status, "d"))
    assert seen == paced
