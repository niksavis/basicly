from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import test_tracker_process_evidence

cli = test_tracker_process_evidence.cli
_run = test_tracker_process_evidence._run

CRITERION = "When a task is saved, show shall return its title."


def _record(
    ledger: Path,
    capsys: pytest.CaptureFixture[str],
    *fields: str,
    acceptance: str = CRITERION,
) -> str:
    args = ["create", str(ledger), "--prefix", "demo", "--title", "save a task"]
    args += ["--description", "## Context\n\nSaved tasks lose their title.", "--acceptance"]
    args.append(acceptance)
    for field in fields:
        args += ["--field", field]
    code, saved = _run(capsys, *args)
    assert code == cli.EXIT_OK, saved
    return saved["record"]


def _confirmation() -> str:
    check = {"criterion": CRITERION, "command": ["uv", "run", "pytest", "tests/test_save.py"]}
    return json.dumps({"checks": [{**check, "result": "1 passed", "exit_code": 0}]})


@pytest.mark.parametrize("kind", ["task", "bug", "chore"])
def test_a_small_task_starts_without_a_review_and_closes_on_one_confirmed_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _record(ledger, capsys, f"issue_type={kind}", "size=small")

    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    code, refusal = _run(capsys, "close", str(ledger), record, "--reason", "Saved.")
    assert code == cli.EXIT_REFUSED
    assert "Completion Confirmation" in json.dumps(refusal)
    assert _run(capsys, "confirm", str(ledger), record, "--evidence", _confirmation())[0] == 0
    assert _run(capsys, "close", str(ledger), record, "--reason", "Saved.")[0] == cli.EXIT_OK


def test_without_the_size_field_a_task_still_needs_a_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _record(ledger, capsys, "issue_type=task")

    code, refusal = _run(capsys, "claim", str(ledger), record, "--to", "agent")

    assert code == cli.EXIT_REFUSED
    assert "Confirmation Plan" in json.dumps(refusal)


@pytest.mark.parametrize(
    ("fields", "acceptance", "rule"),
    [
        (("issue_type=feature", "size=small"), CRITERION, "issue_type task, bug or chore"),
        (
            ("issue_type=task", "size=small"),
            f"{CRITERION}\nWhen a task is renamed, show shall return the new title.",
            "exactly one acceptance criterion",
        ),
    ],
)
def test_a_small_record_outside_the_definition_is_refused_by_the_rule_it_breaks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fields: tuple[str, ...],
    acceptance: str,
    rule: str,
) -> None:
    ledger = tmp_path / "ledger"
    record = _record(ledger, capsys, *fields, acceptance=acceptance)

    code, refusal = _run(capsys, "claim", str(ledger), record, "--to", "agent")

    assert code == cli.EXIT_REFUSED
    assert rule in json.dumps(refusal)


def test_a_size_other_than_small_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _record(ledger, capsys, "issue_type=task")

    code, refusal = _run(capsys, "update", str(ledger), record, "--field", "size=medium")

    assert code == cli.EXIT_REFUSED
    assert "size 'medium' is not small" in json.dumps(refusal)
