from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from basicly import config, tracker

KIT_SOURCE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "tracker"


def flipped_repo(tmp_path: Path) -> Path:

    kit_dir = tmp_path / tracker.KIT_TRACKER_DIR
    kit_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, kit_dir / source.name)
    ignore = tmp_path / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    if "__pycache__" not in existing:
        ignore.write_text(f"{existing}__pycache__/\n", encoding="utf-8")
    (tmp_path / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    config_file = tmp_path / "basicly.toml"
    if not config_file.is_file():
        config_file.write_text(f'[tracker]\nmode = "{tracker.MODE_OWNED}"\n', encoding="utf-8")
    assert config.load_tracker_mode(tmp_path) == tracker.MODE_OWNED
    return tmp_path


def seed(repo: Path, record: str, **fields: str) -> None:
    kit = tracker.kit(repo)
    drafts = [kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})]
    drafts += [
        kit.events.Draft(record, kit.events.KIND_FIELD, {"name": name, "value": value})
        for name, value in fields.items()
    ]
    kit.events.append(tracker.ledger_dir(repo), drafts)


def refuse_spawn(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


def ledger_events(repo: Path) -> list[Any]:
    kit = tracker.kit(repo)
    return kit.read_ledger(tracker.ledger_dir(repo))


def seed_records(repo: Path, records: Iterable[Mapping[str, Any]]) -> None:

    kit = tracker.kit(flipped_repo(repo))
    drafts: list[Any] = []
    for record in records:
        issue = str(record["id"])
        drafts.append(
            kit.events.Draft(
                issue, kit.events.KIND_STATUS, {"status": str(record.get("status", "open"))}
            )
        )
        drafts += [
            kit.events.Draft(issue, kit.events.KIND_COMMENT, {"text": str(row["text"])})
            for row in record.get("comments", ())
            if isinstance(row, dict) and isinstance(row.get("text"), str)
        ]
        drafts += [
            kit.events.Draft(issue, kit.events.KIND_FIELD, {"name": name, "value": str(value)})
            for name, value in record.items()
            if name not in {"id", "status", "comments", "dependencies"}
        ]
        drafts += [
            kit.events.Draft(
                issue,
                kit.migrate.KIND_EDGE,
                {
                    kit.migrate.EDGE_FROM: issue,
                    kit.migrate.EDGE_TO: str(edge.get("depends_on_id") or edge["id"]),
                    kit.migrate.EDGE_TYPE: str(edge.get("type") or edge["dependency_type"]),
                },
            )
            for edge in record.get("dependencies", ())
        ]
    kit.events.append(tracker.ledger_dir(repo), drafts)
