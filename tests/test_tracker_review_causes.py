from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load
from tests.tracker_process_fixture import INVEST

if TYPE_CHECKING:
    from pathlib import Path

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "tracker_review_causes_cli")
CRITERION = "When a task is saved, show shall return its title."
TRIGGER = "When a task arrives, I want it saved, so I can find the work later."


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = cli.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def _card(ledger: Path, capsys: pytest.CaptureFixture[str]) -> tuple[str, int]:
    _, created = _run(
        capsys,
        "create",
        str(ledger),
        "--prefix",
        "demo",
        "--title",
        "save a task",
        "--description",
        TRIGGER,
        "--acceptance",
        CRITERION,
        "--requirements",
        "Use the existing ledger.",
    )
    record = created["record"]
    _run(capsys, "comment", str(ledger), record, "Check the saved title with show.")
    return record, _run(capsys, "show", str(ledger), record)[1]["comment_log"][0]["seq"]


def _review(ledger: Path, record: str, seq: int, text: str, capsys) -> tuple[int, dict]:
    check = {"criterion": text, "command": ["python3", "check.py"], "expected": "ok"}
    evidence = {"invest": dict(INVEST), "conversation": [seq], "checks": [check]}
    return _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(evidence))


def test_claim_says_no_review_is_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record, _ = _card(ledger, capsys)
    code, report = _run(capsys, "claim", str(ledger), record, "--to", "agent")
    assert code == cli.EXIT_REFUSED
    assert "(no review is recorded; run review --evidence)" in report["refused"]


def test_claim_says_the_review_is_void_after_a_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record, seq = _card(ledger, capsys)
    assert _review(ledger, record, seq, CRITERION, capsys)[0] == cli.EXIT_OK
    _run(capsys, "update", str(ledger), record, "--description", TRIGGER.replace("saved", "kept"))
    code, report = _run(capsys, "claim", str(ledger), record, "--to", "agent")
    assert code == cli.EXIT_REFUSED
    assert "the recorded review is void because the record changed after it" in report["refused"]


def test_a_mismatch_lists_the_parsed_criteria(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record, seq = _card(ledger, capsys)
    code, report = _review(ledger, record, seq, "Saving a task shows its title.", capsys)
    assert code == cli.EXIT_REFUSED
    assert f"the record holds the criteria {CRITERION!r}" in report["refused"]
    assert "each check holds exactly the keys" not in report["refused"]


def test_acceptance_help_says_one_criterion_per_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command in ("create", "update"):
        with pytest.raises(SystemExit):
            cli.main([command, "--help"])
        assert "one criterion per line" in " ".join(capsys.readouterr().out.split())
