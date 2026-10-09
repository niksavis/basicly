from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.test_kit_tracker_beans import BEANS, _import, _repo, _shown, cli

if TYPE_CHECKING:
    from pathlib import Path

WHEN = "2026-10-01T00:00:00Z"
BUG = "demo-bu03--fix-the-quote.md"


def _beads(tmp_path: Path, **overrides: object) -> tuple[int, dict]:
    line = {
        "id": "demo-c1",
        "title": "probe",
        "status": "open",
        "priority": 2,
        "issue_type": "task",
        "created_at": WHEN,
        "updated_at": WHEN,
        **overrides,
    }
    export = tmp_path / "issues.jsonl"
    export.write_text(json.dumps(line) + "\n", encoding="utf-8")
    return cli.invoke(
        cli.arguments.parser().parse_args(["import", str(tmp_path / "ledger"), str(export)])
    )


@pytest.mark.parametrize("source", ["beads", "beans"])
def test_an_unmapped_type_is_kept(tmp_path: Path, source: str) -> None:
    if source == "beads":
        code, report = _beads(tmp_path, issue_type="handoff")
        record = "demo-c1"
    else:
        text = BEANS[BUG].replace("type: bug\n", "type: handoff\n")
        code, report = _import(tmp_path, _repo(tmp_path, **{BUG: text}))
        record = "demo-bu03"
    assert code == cli.EXIT_OK, report
    assert _shown(tmp_path, record)["fields"]["issue_type"] == "handoff"


def test_an_unknown_status_is_still_refused(tmp_path: Path) -> None:
    code, report = _beads(tmp_path, status="someday")
    assert code == cli.EXIT_OK
    assert "has status 'someday', which the br table does not map" in json.dumps(report)
