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
