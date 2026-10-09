from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_tracker_process_evidence import _card, _review, _run, cli

if TYPE_CHECKING:
    import pytest


def test_confirm_refusal_names_the_extra_and_missing_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    review = _review(ledger, record, capsys)
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    checks = [
        {
            "criterion": check["criterion"],
            "argv": check["command"],
            "result": "The saved title appeared.",
            "exit_code": 0,
        }
        for check in review["checks"]
    ]
    result, report = _run(
        capsys, "confirm", str(ledger), record, "--evidence", json.dumps({"checks": checks})
    )
    assert result == cli.EXIT_REFUSED
    assert "check 0 lacks the key command and holds the unknown key argv" in json.dumps(report)


def test_review_refusal_names_the_missing_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    evidence = _review(ledger, record, capsys)
    del evidence["checks"][0]["expected"]
    result, report = _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(evidence))
    assert result == cli.EXIT_REFUSED
    message = json.dumps(report)
    assert "check 0 lacks the key expected" in message
    assert "each check holds exactly the keys criterion, command, expected" in message


def test_remedies_name_the_command_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    unreviewed = json.dumps(_run(capsys, "show", str(ledger), record)[1])
    _review(ledger, record, capsys)
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    unconfirmed = json.dumps(
        _run(capsys, "close", str(ledger), record, "--reason", "Saved titles are readable.")[1]
    )
    assert "criterion, command (an argv list) and expected" in unreviewed
    assert "criterion, planned command, result, exit_code 0" in unconfirmed
    assert "argv," not in unreviewed + unconfirmed
