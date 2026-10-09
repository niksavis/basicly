from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load
from tests.tracker_process_fixture import INVEST

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "tracker_numbered_criteria_cli")
CRITERIA = (
    "When a task is saved, show shall return its title.",
    "When a task is closed, list shall omit it.",
)


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = cli.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def _numbered_card(ledger: Path, capsys: pytest.CaptureFixture[str]) -> tuple[str, int]:
    _, created = _run(
        capsys,
        "create",
        str(ledger),
        "--prefix",
        "demo",
        "--title",
        "save a task",
        "--description",
        "When a task arrives, I want it saved, so I can find the work later.",
        "--acceptance",
        "\n".join(f"{index}. {text}" for index, text in enumerate(CRITERIA, start=1)),
        "--requirements",
        "Use the existing ledger.",
    )
    record = created["record"]
    _run(capsys, "comment", str(ledger), record, "Check each criterion with its own script.")
    seq = _run(capsys, "show", str(ledger), record)[1]["comment_log"][0]["seq"]
    return record, seq


def _review(
    ledger: Path, record: str, seq: int, texts: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict]:
    checks = [
        {"criterion": text, "command": ["python3", f"check_{index}.py"], "expected": "ok"}
        for index, text in enumerate(texts)
    ]
    evidence = {"invest": dict(INVEST), "conversation": [seq], "checks": checks}
    return _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(evidence))


def test_review_accepts_numbered_criteria_without_the_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record, seq = _numbered_card(ledger, capsys)
    code, report = _review(ledger, record, seq, list(CRITERIA), capsys)
    assert code == cli.EXIT_OK, report


def test_a_review_saved_with_numbers_still_confirms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record, seq = _numbered_card(ledger, capsys)
    numbered = [f"{index}. {text}" for index, text in enumerate(CRITERIA, start=1)]
    assert _review(ledger, record, seq, numbered, capsys)[0] == cli.EXIT_OK
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    checks = [
        {
            "criterion": text,
            "command": ["python3", f"check_{index}.py"],
            "result": "ok",
            "exit_code": 0,
        }
        for index, text in enumerate(CRITERIA)
    ]
    code, report = _run(
        capsys, "confirm", str(ledger), record, "--evidence", json.dumps({"checks": checks})
    )
    assert code == cli.EXIT_OK, report
    closed = _run(capsys, "close", str(ledger), record, "--reason", "Saved titles are readable.")
    assert closed[0] == cli.EXIT_OK, closed[1]
