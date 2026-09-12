from __future__ import annotations

from pathlib import Path

import pytest

from basicly import artifact_record, handoff, plan_gate, tracker
from tests.test_artifact_record import record_marker
from tests.test_handoff import decomposition, fake_br, spec, summary

__all__ = ["fake_br", "spec"]


@pytest.fixture(autouse=True)
def _open_the_synthetic_records(request: pytest.FixtureRequest) -> None:
    if "work_repo" not in request.fixturenames:
        return
    repo = request.getfixturevalue("work_repo")
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in ("proj-feat", "proj-i")
        ],
    )


def test_a_hand_corrupted_plan_is_refused_naming_the_failing_field(work_repo: Path) -> None:
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["integrity"] = "L9"
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert "integrity" in verdict.reason and "L9" in verdict.reason


def test_a_task_naming_no_demonstration_is_refused_though_a_recorded_bead_is_not(
    work_repo: Path,
) -> None:

    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["demonstration"]
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert plan_gate.DEMONSTRATION_FIELD in verdict.reason


def test_a_plan_whose_payload_is_not_json_is_refused_not_ignored(work_repo: Path) -> None:
    record_marker(work_repo, "proj-feat", f"{artifact_record.MARKER} kind=implementation-plan {{n")
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted and "is not of type 'object'" in verdict.reason


def test_every_violation_is_reported_at_once(work_repo: Path) -> None:
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = []
    payload["tasks"][1]["budget_tokens"] = 0
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert len(verdict.violations) == 2


def test_a_hand_corrupted_change_summary_is_refused_naming_the_failing_field(
    work_repo: Path,
) -> None:
    payload = summary()
    payload["self_check"]["passed"] = "yes"
    artifact_record.write(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY)
    assert not verdict.admitted and "passed" in verdict.reason
