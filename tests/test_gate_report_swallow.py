from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import rubrics, tracker, verify
from tests import flipped_tracker

if TYPE_CHECKING:
    import pytest


def _gate_events(repo: Path) -> list[object]:
    kind = tracker.kit(repo).KIND_GATE
    return [event for event in flipped_tracker.ledger_events(repo) if event.kind == kind]


def test_a_second_identical_verify_gate_is_not_reported_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "seam-1")
    flipped_tracker.refuse_spawn(monkeypatch)
    report = verify.VerifyReport(mode="full", results=(verify.CheckResult("ruff", "pass", 0),))

    first_ok, first = verify.report_gate(repo, "seam-1", report, gate="verify")
    second_ok, second = verify.report_gate(repo, "seam-1", report, gate="verify")

    assert (first_ok, second_ok) == (True, True)
    assert "recorded gate verify=pass" in first
    assert "recorded" not in second
    assert "already held" in second
    assert len(_gate_events(repo)) == 1


def test_a_second_identical_rubric_gate_is_not_reported_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "seam-1")
    flipped_tracker.refuse_spawn(monkeypatch)
    verdicts = [rubrics.CheckVerdict("d", rubrics.DETERMINISTIC, rubrics.YES)]

    first_ok, first = rubrics.report_gate(repo, "seam-1", verdicts)
    second_ok, second = rubrics.report_gate(repo, "seam-1", verdicts)

    assert (first_ok, second_ok) == (True, True)
    assert f"recorded {rubrics.RUBRIC_GATE}=pass" in first
    assert "recorded" not in second
    assert second.count("already held") == 2
    assert len(_gate_events(repo)) == 2
