"""What the two engine gate-report callers say when the ledger already held the write.

`basicly-kn4rip` made :func:`basicly.tracker.write` return whether every draft landed and
left every caller discarding it. These two are the callers where discarding it costs
something: both printed ``recorded`` unconditionally, so a gate that was skipped as a
replay read exactly like one that had just been appended (basicly-wu4w8v).

Driven against a real owned ledger rather than a stubbed seam, because the claim is about
the *ledger's* answer — a fake that returned True would assert the composition and not the
behaviour, and a fake that returned None would pass the old code too.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import rubrics, tracker, verify
from tests import flipped_tracker

if TYPE_CHECKING:
    import pytest


def _gate_events(repo: Path) -> list[object]:
    """Every gate event in *repo*'s ledger, which is the count a report is checked against."""
    kind = tracker.kit(repo).KIND_GATE
    return [event for event in flipped_tracker.ledger_events(repo) if event.kind == kind]


def test_a_second_identical_verify_gate_is_not_reported_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gate report carries no note-free discriminator, so a re-run is a plain replay.

    ``gate report``'s payload is ``{provenance, gate, provider, passed}``, and an event id
    is a content digest — so two genuine runs of one gate with one verdict are byte
    identical and the ledger keeps the first. The report must say that rather than claim
    this run was recorded.
    """
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
    # The report is checked against the record's actual event count, not against the
    # absence of an exception: one row for two calls is the whole defect.
    assert len(_gate_events(repo)) == 1


def test_a_second_identical_rubric_gate_is_not_reported_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves are reported, so both halves have to stop claiming a skipped append."""
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
