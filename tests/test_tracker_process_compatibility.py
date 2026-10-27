from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_tracker_process_evidence import cli


def test_dor_v2_review_and_confirmation_remain_current_without_rewriting_events(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger"
    shutil.copytree(Path(__file__).parent / "fixtures/tracker-dor-v2", ledger)
    before = cli.events.read_events(ledger)[0]
    shown = cli.record_view.read_record(ledger, "legacy-task")
    proof = shown["process"]
    assert proof["review"]["revision"] != proof["revision"]
    assert proof["review_current"] and proof["confirmation_current"]
    assert proof["owed"] == []
    assert cli.commands.review.process.closing_owed(before, "legacy-task", shown["fields"]) == ()
    assert cli.events.read_events(ledger)[0] == before


@pytest.mark.parametrize("record", ["legacy-task", "legacy-parent"])
def test_dor_v2_evidence_still_expires_after_real_contract_changes(
    tmp_path: Path, record: str
) -> None:
    ledger = tmp_path / "ledger"
    shutil.copytree(Path(__file__).parent / "fixtures/tracker-dor-v2", ledger)
    assert cli.record_view.read_record(ledger, "legacy-task")["process"]["review_current"]
    assert (
        cli.main(["update", str(ledger), record, "--field", "title=A changed contract"])
        == cli.EXIT_OK
    )
    proof = cli.record_view.read_record(ledger, "legacy-task")["process"]
    assert not proof["review_current"] and not proof["confirmation_current"]
    assert proof["owed"]
