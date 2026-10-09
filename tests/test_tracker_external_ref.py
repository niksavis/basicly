from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

if TYPE_CHECKING:
    from pathlib import Path

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "tracker_external_ref_cli")
WHEN = "2026-10-01T00:00:00Z"


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = cli.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def _create(ledger: Path, capsys: pytest.CaptureFixture[str], *fields: str) -> str:
    args = ["create", str(ledger), "--prefix", "demo", "--title", "t"]
    for field in fields:
        args += ["--field", field]
    code, report = _run(capsys, *args)
    assert code == cli.EXIT_OK, report
    return report["record"]


@pytest.mark.parametrize("command", ["create", "update"])
def test_a_duplicate_external_ref_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    ledger = tmp_path / "ledger"
    first = _create(ledger, capsys, "external_ref=EXT-1")
    if command == "create":
        code, report = _run(
            capsys,
            "create",
            str(ledger),
            "--prefix",
            "demo",
            "--title",
            "t2",
            "--field",
            "external_ref=EXT-1",
        )
    else:
        second = _create(ledger, capsys)
        code, report = _run(capsys, "update", str(ledger), second, "--field", "external_ref=EXT-1")
    assert code == cli.EXIT_REFUSED
    assert f"which {first} already holds" in report["refused"]


def test_an_import_with_a_duplicate_external_ref_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = [
        {
            "id": f"demo-c{index}",
            "title": "t",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": WHEN,
            "updated_at": WHEN,
            "external_ref": "EXT-9",
        }
        for index in (1, 2)
    ]
    export = tmp_path / "issues.jsonl"
    export.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    code, report = _run(capsys, "import", str(tmp_path / "ledger"), str(export))
    assert code == cli.EXIT_REFUSED
    assert "demo-c2 sets external_ref 'EXT-9', which demo-c1 already holds" in report["refused"]
    assert not list((tmp_path / "ledger").glob("*.jsonl"))


def test_an_old_duplicate_blocks_only_an_external_ref_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    lines = [
        {
            "id": f"demo-c{index}",
            "title": "t",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": WHEN,
            "updated_at": WHEN,
        }
        for index in (1, 2)
    ]
    export = tmp_path / "issues.jsonl"
    export.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    assert _run(capsys, "import", str(ledger), str(export))[0] == cli.EXIT_OK
    found = cli.events.read_events(ledger)[0]
    duplicated = [
        cli.events.Draft(record, cli.events.KIND_FIELD, {"name": "external_ref", "value": "EXT-5"})
        for record in ("demo-c1", "demo-c2")
    ]
    cli.events.append(ledger, duplicated, clock=lambda: 0.0)
    assert len(cli.events.read_events(ledger)[0]) == len(found) + 2
    assert _run(capsys, "update", str(ledger), "demo-c1", "--field", "title=renamed")[0] == 0
    assert _run(capsys, "update", str(ledger), "demo-c1", "--field", "external_ref=EXT-5")[0] != 0
