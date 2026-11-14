from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import tracker
from tests import flipped_tracker, test_tracker_process_evidence
from tests.tracker_process_fixture import recorded_review

cli = test_tracker_process_evidence.cli
_card = test_tracker_process_evidence._card
_run = test_tracker_process_evidence._run


def _compact() -> dict:
    return {
        "checks": [
            {
                "criterion": "When a task is saved, show shall return its title.",
                "command": ["python", "-c", "print('confirmed fixture')"],
                "expected": "The fixture confirms the saved task.",
            }
        ]
    }


@pytest.mark.parametrize("kind", ["task", "bug", "chore"])
def test_compact_review_claim_confirmation_and_close_need_no_conversation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    assert _run(capsys, "update", str(ledger), record, "--field", f"issue_type={kind}")[0] == 0
    review = _compact()
    code, report = _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(review))
    assert code == cli.EXIT_OK, report
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    assert (
        _run(capsys, "close", str(ledger), record, "--reason", "Fixture completed.")[0]
        == cli.EXIT_REFUSED
    )
    actual = subprocess.run(
        review["checks"][0]["command"], capture_output=True, text=True, check=True
    )
    check = review["checks"][0]
    confirmation = {
        "checks": [
            {
                "criterion": check["criterion"],
                "command": check["command"],
                "result": actual.stdout,
                "exit_code": actual.returncode,
            }
        ]
    }
    assert (
        _run(capsys, "confirm", str(ledger), record, "--evidence", json.dumps(confirmation))[0]
        == cli.EXIT_OK
    )
    assert (
        _run(capsys, "close", str(ledger), record, "--reason", "Fixture completed.")[0]
        == cli.EXIT_OK
    )
    found = cli.events.read_events(ledger)[0]
    assert not any(event.kind in cli.events.PROSE_KINDS for event in found)
    assert cli.events.fold(found).records[record].status == "closed"


@pytest.mark.parametrize("kind", ["feature", "epic", "research"])
def test_compact_review_does_not_replace_a_complex_work_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    assert _run(capsys, "update", str(ledger), record, "--field", f"issue_type={kind}")[0] == 0
    before = cli.events.read_events(ledger)[0]
    assert (
        _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(_compact()))[0]
        == cli.EXIT_REFUSED
    )
    assert cli.events.read_events(ledger)[0] == before


def test_committed_redaction_preserves_a_meaningful_confirmed_observation(tmp_path: Path) -> None:
    flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(
        tmp_path,
        "fx-1",
        title="confirm the actual result",
        description="When a check completes, I want its outcome saved, so I can confirm the work.",
        acceptance_criteria="When the fixture runs, its result shall be confirmed.",
        requirements="Preserve privacy and observed outcomes.",
        issue_type="task",
    )
    recorded_review(tracker.kit(tmp_path, "commands"), tracker.ledger_dir(tmp_path), "fx-1")
    tracker.write(tmp_path, ["update", "fx-1", "--status", "in_progress"])
    review = json.loads(tracker.require_record(tmp_path, "fx-1")["process_review"])
    path = "/".join(["", "home", "fixture-person", "cache"])
    check = review["checks"][0]
    tracker.write(
        tmp_path,
        [
            "confirm",
            "fx-1",
            "--evidence",
            json.dumps({
                "checks": [
                    {
                        "criterion": check["criterion"],
                        "command": check["command"],
                        "result": "The check passed using fixture cache " + path,
                        "exit_code": 0,
                    }
                ]
            }),
        ],
    )
    saved = tracker.require_record(tmp_path, "fx-1")["process_confirmation"]
    assert path not in saved
    assert "<redacted:posix-home-path>" in saved
    tracker.write(tmp_path, ["close", "fx-1", "--reason", "The actual check was confirmed."])
    assert tracker.require_record(tmp_path, "fx-1")["status"] == "closed"


@pytest.mark.parametrize("change", ["card", "template", "dependency", "type"])
def test_compact_review_and_confirmation_are_invalidated_by_contract_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    assert _run(capsys, "update", str(ledger), record, "--field", "issue_type=task")[0] == 0
    target = _card(ledger, capsys)
    if change == "dependency":
        assert _run(capsys, "dep", str(ledger), record, target)[0] == cli.EXIT_OK
    review = _compact()
    assert _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(review))[0] == 0
    planned = review["checks"][0]
    completed = {
        "checks": [
            {
                "criterion": planned["criterion"],
                "command": planned["command"],
                "result": "The fixture confirmed the task.",
                "exit_code": 0,
            }
        ]
    }
    assert _run(capsys, "confirm", str(ledger), record, "--evidence", json.dumps(completed))[0] == 0
    if change == "template":
        (ledger / "template.json").write_text(json.dumps({"sections": ["## Risks"]}))
    elif change == "dependency":
        assert (
            _run(
                capsys,
                "update",
                str(ledger),
                target,
                "--acceptance",
                "When saved, the task shall show its identifier.",
            )[0]
            == 0
        )
    else:
        field = (
            "issue_type=feature" if change == "type" else "requirements=Show the saved identifier."
        )
        assert _run(capsys, "update", str(ledger), record, "--field", field)[0] == 0
    result, report = _run(capsys, "dor", str(ledger), record)
    assert result == cli.EXIT_REFUSED
    assert "## Confirmation Plan" in report["owed"]
    before = cli.events.read_events(ledger)[0]
    assert (
        _run(capsys, "close", str(ledger), record, "--reason", "Fixture completed.")[0]
        == cli.EXIT_REFUSED
    )
    assert cli.events.read_events(ledger)[0] == before


@pytest.mark.parametrize(
    "result",
    [
        "<redacted:posix-home-path>",
        "TODO",
        "The result is <pending>",
        "<redacted:github-token> TODO",
    ],
)
def test_redaction_does_not_certify_meaningless_or_unfinished_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], result: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    review = test_tracker_process_evidence._review(ledger, record, capsys)
    check = review["checks"][0]
    evidence = {
        "checks": [
            {
                "criterion": check["criterion"],
                "command": check["command"],
                "result": result,
                "exit_code": 0,
            }
        ]
    }
    before = cli.events.read_events(ledger)[0]
    assert (
        _run(capsys, "confirm", str(ledger), record, "--evidence", json.dumps(evidence))[0]
        == cli.EXIT_REFUSED
    )
    assert cli.events.read_events(ledger)[0] == before


def test_engine_compact_review_uses_the_same_no_comment_completion_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flipped_tracker.flipped_repo(tmp_path)
    ledger = tracker.ledger_dir(tmp_path)
    record = _card(ledger, capsys)
    tracker.write(tmp_path, ["update", record, "-t", "task"])
    review = _compact()
    tracker.write(tmp_path, ["review", record, "--evidence", json.dumps(review)])
    tracker.write(tmp_path, ["update", record, "--status", "in_progress"])
    planned = review["checks"][0]
    completed = {
        "checks": [
            {
                "criterion": planned["criterion"],
                "command": planned["command"],
                "result": "The fixture confirms the task.",
                "exit_code": 0,
            }
        ]
    }
    tracker.write(tmp_path, ["confirm", record, "--evidence", json.dumps(completed)])
    tracker.write(tmp_path, ["close", record, "--reason", "The fixture task was confirmed."])
    assert tracker.require_record(tmp_path, record)["status"] == "closed"
    assert not any(
        event.record == record and event.kind in cli.events.PROSE_KINDS
        for event in cli.events.read_events(ledger)[0]
    )


@pytest.mark.parametrize(
    "command",
    [
        ["/".join(["", "home", "fixture", "python"]), "check.py"],
        ["python3", "/".join(["", "tmp", "fixture", "check.py"])],
        ["python3", "C:" + chr(92) + "fixture" + chr(92) + "check.py"],
    ],
)
@pytest.mark.parametrize("operation", ["review", "confirm"])
def test_process_evidence_refuses_commands_that_privacy_would_change_before_appending(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: list[str], operation: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    code, report = _run(
        capsys, "update", str(ledger), record, "--field", "issue_type=task", "--field", "size=small"
    )
    assert code == cli.EXIT_OK, report
    before = cli.events.read_events(ledger)[0]
    evidence = {
        "checks": [
            {
                "criterion": "When a task is saved, show shall return its title.",
                "command": command,
                "result": "The fixture observed the saved title.",
                "exit_code": 0,
            }
        ]
    }
    if operation == "review":
        evidence["checks"][0].pop("exit_code")
        evidence["checks"][0]["expected"] = evidence["checks"][0].pop("result")
    code, report = _run(capsys, operation, str(ledger), record, "--evidence", json.dumps(evidence))
    assert code == cli.EXIT_REFUSED, report
    assert "redact" in report["refused"] and "python3" in report["refused"]
    assert cli.events.read_events(ledger)[0] == before
