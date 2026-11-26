from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests import test_tracker_process_evidence

if TYPE_CHECKING:
    import pytest

cli = test_tracker_process_evidence.cli
_run = test_tracker_process_evidence._run

VOID = "the recorded review is void because the record changed after it"


def _task(ledger: Path, capsys: pytest.CaptureFixture[str], title: str) -> str:
    code, saved = _run(
        capsys,
        "create",
        str(ledger),
        "--prefix",
        "demo",
        "--title",
        title,
        "--field",
        "issue_type=task",
        "--description",
        f"## Context\n\n{title} is missing.",
        "--acceptance",
        f"When {title} runs, it shall pass.",
    )
    assert code == cli.EXIT_OK, saved
    return saved["record"]


def _review(ledger: Path, capsys: pytest.CaptureFixture[str], record: str, title: str) -> None:
    check = {"criterion": f"When {title} runs, it shall pass.", "command": ["true"]}
    evidence = json.dumps({"checks": [{**check, "expected": "passes"}]})
    assert _run(capsys, "review", str(ledger), record, "--evidence", evidence)[0] == cli.EXIT_OK


def test_an_edge_that_voids_a_review_says_so_in_its_remedy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    first, second = _task(ledger, capsys, "first"), _task(ledger, capsys, "second")
    _review(ledger, capsys, first, "first")

    assert _run(capsys, "dep", str(ledger), first, second)[0] == cli.EXIT_OK

    code, refused = _run(capsys, "close", str(ledger), first, "--reason", "done")

    assert code == cli.EXIT_REFUSED
    assert VOID in refused["refused"]
    assert "review --evidence" in refused["refused"]


def test_a_record_never_reviewed_is_not_told_its_review_is_void(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    first, second = _task(ledger, capsys, "first"), _task(ledger, capsys, "second")

    assert _run(capsys, "dep", str(ledger), first, second)[0] == cli.EXIT_OK

    code, refused = _run(capsys, "close", str(ledger), first, "--reason", "done")

    assert code == cli.EXIT_REFUSED
    assert "## Confirmation Plan" in refused["refused"]
    assert VOID not in refused["refused"]
