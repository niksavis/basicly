from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_refine_sheet")


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def _no_developer_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "developer-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for variable in ("XDG_CONFIG_HOME", "BASICLY_HOLDER", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)


def _issue(number: int, **extra: object) -> dict[str, object]:
    return {
        "id": f"smp-{number}",
        "title": f"Sample issue {number}",
        "description": f"Sample description {number}",
        "status": "open",
        "priority": number % 5,
        "issue_type": "bug" if number % 4 == 0 else "task",
        "created_at": "2026-11-02T10:00:00Z",
        "updated_at": "2026-11-10T10:00:00Z",
        **extra,
    }


def _imported(tmp_path: Path, capsys: pytest.CaptureFixture[str], issues: list[dict]) -> Path:
    export = tmp_path / "issues.jsonl"
    export.write_text("".join(json.dumps(issue) + "\n" for issue in issues), encoding="utf-8")
    ledger = tmp_path / "ledger"
    code, report = _run(capsys, "import", str(ledger), str(export), "--from", "beads")
    assert code == cli.EXIT_OK, report
    return ledger


def test_import_puts_the_description_under_context_and_marks_one_criterion_tasks_small(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    export = tmp_path / "issues.jsonl"
    issues = [
        _issue(1, acceptance_criteria="It passes."),
        _issue(2),
        _issue(3, issue_type="feature"),
    ]
    export.write_text("".join(json.dumps(issue) + "\n" for issue in issues), encoding="utf-8")

    code, report = _run(capsys, "import", str(tmp_path / "ledger"), str(export), "--from", "beads")

    assert code == cli.EXIT_OK
    assert report["small"] == ["smp-1"]
    assert report["needs_criteria"] == ["smp-2", "smp-3"]
    assert report["next"] == f"refine-sheet {tmp_path / 'ledger'}"
    _, shown = _run(capsys, "show", str(tmp_path / "ledger"), "smp-1")
    assert shown["fields"]["description"] == "## Context\nSample description 1"
    _, ready = _run(capsys, "ready", str(tmp_path / "ledger"))
    assert [row["record"] for row in ready["records"]] == ["smp-1"]


def test_one_filled_sheet_and_one_apply_make_a_whole_backlog_ready(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _imported(tmp_path, capsys, [_issue(number) for number in range(1, 21)])
    assert _run(capsys, "ready", str(ledger))[1]["count"] == 0
    sheet = tmp_path / "sheet.txt"

    code, written = _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))

    assert code == cli.EXIT_OK
    assert len(written["records"]) == 20
    sheet.write_text(
        sheet.read_text(encoding="utf-8").replace(
            "acceptance:\n- \n", "acceptance:\n- It works.\n"
        ),
        encoding="utf-8",
    )
    code, applied = _run(capsys, "apply-sheet", str(ledger), str(sheet))
    assert code == cli.EXIT_OK, applied
    assert applied["still_owed"] == {}
    assert _run(capsys, "ready", str(ledger))[1]["count"] == 20


def test_apply_refuses_the_whole_sheet_and_names_each_empty_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _imported(tmp_path, capsys, [_issue(1), _issue(2)])
    sheet = tmp_path / "sheet.txt"
    _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))
    text = sheet.read_text(encoding="utf-8")
    sheet.write_text(
        text.replace("acceptance:\n- \n", "acceptance:\n- It works.\n", 1), encoding="utf-8"
    )

    code, refused = _run(capsys, "apply-sheet", str(ledger), str(sheet))

    assert code != cli.EXIT_OK
    assert "smp-2 has an empty acceptance line" in refused["refused"]
    assert _run(capsys, "ready", str(ledger))[1]["count"] == 0


def test_a_feature_keeps_its_review_owed_after_the_sheet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _imported(tmp_path, capsys, [_issue(1, issue_type="feature")])
    sheet = tmp_path / "sheet.txt"
    _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))
    sheet.write_text(
        sheet.read_text(encoding="utf-8").replace("- \n", "- It works.\n"), encoding="utf-8"
    )

    code, applied = _run(capsys, "apply-sheet", str(ledger), str(sheet))

    assert code == cli.EXIT_OK
    assert "smp-1" in applied["still_owed"]


def test_refine_sheet_refuses_an_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _imported(tmp_path, capsys, [_issue(1)])
    sheet = tmp_path / "sheet.txt"
    sheet.write_text("mine", encoding="utf-8")

    code, refused = _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))

    assert code != cli.EXIT_OK
    assert "exists" in refused["refused"]
    assert sheet.read_text(encoding="utf-8") == "mine"


def _filled(sheet: Path, criterion: str = "It works.") -> None:
    text = sheet.read_text(encoding="utf-8")
    sheet.write_text(
        text.replace("acceptance:\n- \n", f"acceptance:\n- {criterion}\n"), encoding="utf-8"
    )


def test_apply_reports_shaped_records_apart_from_the_ready_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _imported(
        tmp_path,
        capsys,
        [
            _issue(1),
            _issue(2, status="deferred"),
            _issue(
                3, dependencies=[{"issue_id": "smp-3", "depends_on_id": "smp-1", "type": "blocks"}]
            ),
        ],
    )
    sheet = tmp_path / "sheet.txt"
    _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))
    _filled(sheet)

    code, applied = _run(capsys, "apply-sheet", str(ledger), str(sheet))

    assert code == cli.EXIT_OK, applied
    assert applied["shaped"] == ["smp-1", "smp-2", "smp-3"]
    assert applied["ready"] == ["smp-1"]


def test_an_in_progress_record_with_no_holder_is_held_out_of_ready_until_claimed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    export = tmp_path / "issues.jsonl"
    issues = [_issue(4, status="in_progress", acceptance_criteria="It works.", issue_type="task")]
    export.write_text("".join(json.dumps(issue) + "\n" for issue in issues), encoding="utf-8")
    ledger = tmp_path / "ledger"

    _, report = _run(capsys, "import", str(ledger), str(export), "--from", "beads")

    assert report["in_progress_elsewhere"] == ["smp-4"]
    assert _run(capsys, "ready", str(ledger))[1]["count"] == 0
    _, blocked = _run(capsys, "blocked", str(ledger))
    assert blocked["records"][0]["in_progress_elsewhere"] == {
        "since": "2026-11-10T10:00:00Z",
        "adopt": "claim smp-4",
    }
    code, _ = _run(capsys, "claim", str(ledger), "smp-4", "--to", "ada")
    assert code == cli.EXIT_OK
    _, mine = _run(capsys, "ready", str(ledger))
    assert [(row["record"], row["holder"]["name"]) for row in mine["records"]] == [("smp-4", "ada")]


def test_a_native_in_progress_record_with_no_holder_is_held_out_of_ready_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _imported(tmp_path, capsys, [_issue(4, acceptance_criteria="It works.")])
    assert _run(capsys, "ready", str(ledger))[1]["count"] == 1
    monkeypatch.setenv("BASICLY_HOLDER", "fixture-holder")

    _run(capsys, "update", str(ledger), "smp-4", "--status", "in_progress")
    _run(capsys, "unassign", str(ledger), "smp-4")

    assert _run(capsys, "ready", str(ledger))[1]["count"] == 0
    _, blocked = _run(capsys, "blocked", str(ledger))
    assert blocked["records"][0]["in_progress_elsewhere"]["adopt"] == "claim smp-4"


def test_an_imported_assignee_holds_the_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _issue(4, status="in_progress", assignee="ada", acceptance_criteria="It works.")
    ledger = _imported(tmp_path, capsys, [issue])

    _, ready = _run(capsys, "ready", str(ledger))

    assert [(row["record"], row["holder"]["name"]) for row in ready["records"]] == [
        ("smp-4", "ada")
    ]


@pytest.mark.parametrize(
    ("description", "context", "kept"),
    [
        ("Intro line.\n### Steps\n1. Run it.", "", "### Steps\n1. Run it."),
        ("## Notes\nKeep me.", "Why it matters.", "## Notes\nKeep me."),
        ("", "Why it matters.", ""),
    ],
)
def test_apply_keeps_the_imported_description(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    description: str,
    context: str,
    kept: str,
) -> None:
    ledger = _imported(tmp_path, capsys, [_issue(1, description=description)])
    sheet = tmp_path / "sheet.txt"
    _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))
    text = sheet.read_text(encoding="utf-8")
    if context:
        text = text.replace("context: \n", f"context: {context}\n")
    sheet.write_text(
        text.replace("acceptance:\n- \n", "acceptance:\n- It works.\n"), encoding="utf-8"
    )

    code, applied = _run(capsys, "apply-sheet", str(ledger), str(sheet))

    assert code == cli.EXIT_OK, applied
    assert applied["ready"] == ["smp-1"]
    _, shown = _run(capsys, "show", str(ledger), "smp-1")
    assert kept in shown["fields"]["description"]
    assert shown["fields"]["description"].startswith("## Context\n")


def test_a_record_owing_only_context_keeps_its_criteria(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _imported(
        tmp_path, capsys, [_issue(1, description="", acceptance_criteria="- It has one.")]
    )
    sheet = tmp_path / "sheet.txt"
    _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))
    text = sheet.read_text(encoding="utf-8")
    assert "acceptance:\n- It has one.\n" in text
    sheet.write_text(text.replace("context: \n", "context: Why it matters.\n"), encoding="utf-8")

    code, applied = _run(capsys, "apply-sheet", str(ledger), str(sheet))

    assert code == cli.EXIT_OK, applied
    _, shown = _run(capsys, "show", str(ledger), "smp-1")
    assert shown["fields"]["acceptance_criteria"] == "- It has one."
    assert applied["ready"] == ["smp-1"]


def test_refine_sheet_writes_no_file_when_nothing_owes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _imported(tmp_path, capsys, [_issue(1, acceptance_criteria="It works.")])
    sheet = tmp_path / "sheet.txt"

    code, written = _run(capsys, "refine-sheet", str(ledger), "--out", str(sheet))

    assert code == cli.EXIT_OK
    assert written["records"] == []
    assert written["next"] == "ready"
    assert not sheet.exists()
