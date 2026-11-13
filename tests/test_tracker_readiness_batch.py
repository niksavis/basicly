from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import test_tracker_process_evidence as fixture
from tests.test_tracker_process_evidence_compact import _compact


@pytest.mark.parametrize("change", ["card", "dependency", "template"])
def test_a_new_readiness_batch_refuses_a_review_after_its_contract_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    ledger = tmp_path / "ledger"
    source, target = (fixture._card(ledger, capsys) for _ in range(2))
    for record in (source, target):
        assert (
            fixture._run(capsys, "update", str(ledger), record, "--field", "issue_type=task")[0]
            == 0
        )
    assert fixture._run(capsys, "dep", str(ledger), source, target)[0] == 0
    for record in (source, target):
        assert (
            fixture._run(
                capsys, "review", str(ledger), record, "--evidence", json.dumps(_compact())
            )[0]
            == 0
        )
    kit = fixture.cli
    process = kit.commands.review.process
    found = kit.events.read_events(ledger)[0]
    fields = {record: state.fields for record, state in kit.events.fold(found).records.items()}
    before = process.readiness_many(found, fields)
    assert before == {source: (), target: ()}

    if change == "template":
        (ledger / "template.json").write_text(json.dumps({"sections": ["## Risks"]}))
    else:
        changed = target if change == "dependency" else source
        assert (
            fixture._run(
                capsys,
                "update",
                str(ledger),
                changed,
                "--field",
                "requirements=Keep the saved identity.",
            )[0]
            == 0
        )
    found[:] = kit.events.read_events(ledger)[0]
    fields = {record: state.fields for record, state in kit.events.fold(found).records.items()}
    template = kit.scheduler.templates.load(ledger)
    after = process.readiness_many(found, fields, template=template)

    assert after[source] == ("## Confirmation Plan",)
    assert after == {
        record: process.readiness(found, record, held, template=template)
        for record, held in fields.items()
    }
    assert before == {source: (), target: ()}
    if change == "card":
        assert after[target] == ()
