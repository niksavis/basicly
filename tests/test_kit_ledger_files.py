from __future__ import annotations

from pathlib import Path

from tests.test_kit_tracker_locking import events


def test_authoritative_event_discovery_excludes_derived_files(tmp_path: Path) -> None:
    for name in (
        "events-0002.jsonl",
        "events-0001.jsonl",
        "pending-worker.jsonl",
        "snapshot.jsonl",
        "checkpoint-0001.jsonl",
        ".events.lock",
        "template.json",
    ):
        (tmp_path / name).write_text("fixture", encoding="utf-8")

    names = [path.name for path in events.ledger_files.ledger_paths(tmp_path)]

    assert names == ["events-0001.jsonl", "events-0002.jsonl", "pending-worker.jsonl"]
    assert events.ledger_paths(tmp_path) == events.ledger_files.ledger_paths(tmp_path)
