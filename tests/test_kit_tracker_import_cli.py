from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "kit_import_test_cli")

SHAPED = {
    "description": "When a backlog moves, I want each record ready, so I can keep working.",
    "acceptance_criteria": "- the ready query offers it",
    "requirements": "- standard library only",
}

EXPORT = [
    {
        "id": "demo-aa11",
        "title": "move the backlog across",
        "status": "open",
        **SHAPED,
        "comments": [{"text": "carried over from the old tracker"}],
        "dependencies": [{"issue_id": "demo-aa11", "depends_on_id": "demo-bb22", "type": "blocks"}],
    },
    {"id": "demo-bb22", "title": "a record the first one waits on", "status": "open", **SHAPED},
    {"id": "not-an-id", "title": "this one cannot be a record id"},
]


@pytest.fixture
def export(tmp_path: Path) -> Path:
    path = tmp_path / "issues.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in EXPORT), encoding="utf-8")
    return path


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "ledger"
    directory.mkdir()
    return directory


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict:
    assert cli.main(list(argv)) == cli.EXIT_OK
    return json.loads(capsys.readouterr().out)


def test_a_dry_run_reports_the_plan_and_writes_nothing(
    ledger: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _run(capsys, "import", str(ledger), str(export), "--dry-run")

    assert report["dry_run"] is True
    assert report["imported"] == ["demo-aa11", "demo-bb22"]
    assert list(ledger.iterdir()) == [], (
        "a preview that writes is not a preview; the ledger must be untouched"
    )


def test_the_real_run_imports_what_the_dry_run_named(
    ledger: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    planned = _run(capsys, "import", str(ledger), str(export), "--dry-run")

    done = _run(capsys, "import", str(ledger), str(export))

    assert done["imported"] == planned["imported"]
    assert done["rejected"] == planned["rejected"]
    assert done["dry_run"] is False


def test_a_record_the_export_cannot_name_is_refused_rather_than_dropped(
    ledger: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _run(capsys, "import", str(ledger), str(export))

    refused = {one["subject"]: one["reason"] for one in report["rejected"]}
    assert refused == {"'not-an-id'": "not a record id"}


def test_the_imported_graph_answers_the_ready_query(
    ledger: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(capsys, "import", str(ledger), str(export))

    ready = _run(capsys, "ready", str(ledger))

    assert [row["record"] for row in ready["records"]] == ["demo-bb22"], (
        "demo-aa11 blocks on demo-bb22 in the export, so only the target is ready; "
        "an import that dropped the edge would offer both"
    )


def test_importing_the_same_export_twice_appends_nothing(
    ledger: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _run(capsys, "import", str(ledger), str(export))
    assert first["imported"], "the control: the first run imports something"
    lines = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in sorted(ledger.iterdir())
    )

    again = _run(capsys, "import", str(ledger), str(export))

    assert again["imported"] == []
    assert (
        sum(len(path.read_text(encoding="utf-8").splitlines()) for path in sorted(ledger.iterdir()))
        == lines
    )


def test_the_source_name_defaults_to_the_export_file_name(
    ledger: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _run(capsys, "import", str(ledger), str(export), "--dry-run")

    assert report["source"] == "issues.jsonl"


def test_a_named_source_is_recorded_instead(
    ledger: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _run(capsys, "import", str(ledger), str(export), "--source", "old-tracker")

    assert report["source"] == "old-tracker"
    text = "".join(path.read_text(encoding="utf-8") for path in sorted(ledger.iterdir()))
    assert "old-tracker" in text
