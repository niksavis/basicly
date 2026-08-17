"""The two read-only reporting surfaces added with `basicly-a4q3.5`.

Its own module rather than an append to `test_cli.py`, which the module-size
ratchet already froze at 20699 tokens.

Both commands are previews of something else — the brief the loop would send, and
the outcomes it already recorded — so the tests that matter assert they do not
drift from their source: the brief is the assembler's own output rather than a
second rendering, and the outcome labels are the engine's own constants.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import dispatch_brief, run_record, tracker_paths, usage_report
from tests.test_cli import run_basicly

if TYPE_CHECKING:
    import pytest


def _a_tracked_id(root: Path) -> str:
    """Any real record id from the checkout's own committed ledger."""
    for log in sorted((root / tracker_paths.LEDGER_DIR_NAME).glob("events-*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return str(json.loads(line)["record"])
    raise AssertionError("the committed ledger is empty, so no id can be briefed")


def test_brief_prints_the_assemblers_own_output(work_repo: Path) -> None:
    """A preview that differs from the dispatch is worse than no preview."""
    issue = _a_tracked_id(work_repo)
    result = run_basicly(work_repo, "brief", issue)

    assert result.returncode == 0, result.stderr
    # Byte-exact, not whitespace-normalised: rich rewraps at the terminal width
    # unless soft_wrap is set, and a normalised compare would call that identical.
    assert result.stdout.rstrip("\n") == dispatch_brief.dispatch_prompt(issue)


def test_brief_requires_an_issue_id(work_repo: Path) -> None:
    """The argument is positional and required, so a bare call cannot print a stub."""
    assert run_basicly(work_repo, "brief").returncode != 0


def test_brief_refuses_an_id_the_tracker_does_not_hold(work_repo: Path) -> None:
    """The brief is a pure function of the id, so a typo renders a plausible lie.

    Without this check `basicly brief basicly-zzz9` printed a complete brief and
    exited 0 — the one failure a preview exists to stop a human reading past.
    """
    result = run_basicly(work_repo, "brief", "basicly-zzz9")

    assert result.returncode == 1
    assert "No tracked issue basicly-zzz9" in result.stderr


def _seed(root: Path, outcomes: list[str]) -> None:
    """Write a run-record ledger holding exactly *outcomes*, one bead each."""
    path = root / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {f"basicly-t{i}": [{"outcome": name}] for i, name in enumerate(outcomes)}
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_outcomes_reports_every_recorded_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every label the engine can write must appear, so none is silently dropped."""
    labels = [run_record.HANDOFF, run_record.EXECUTED, run_record.FAILED, run_record.UNSTARTED]
    _seed(tmp_path, labels)
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    for label in labels:
        assert label in out, f"{label} recorded but not reported"


def test_outcomes_computes_the_failure_share(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failure in four is 25 percent; a hardcoded rate could not say so."""
    _seed(tmp_path, [run_record.FAILED, *([run_record.EXECUTED] * 3)])
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "1/4 = 25.0%" in capsys.readouterr().out


def test_outcomes_states_that_it_is_not_a_lane_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary is the claim's honesty, so it is printed and asserted here.

    Without it the failure share reads as "a quarter of lanes found nothing", which
    the run records do not say and cannot be made to say.
    """
    _seed(tmp_path, [run_record.EXECUTED])
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "no record here says whether a lane reached a result" in capsys.readouterr().out


def test_outcomes_says_so_when_nothing_is_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty tree must explain itself rather than print a zero-row table."""
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "no dispatch has been recorded" in capsys.readouterr().out.lower()


def test_outcomes_survives_a_bead_with_no_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger can exist, parse, and still hold nothing to divide by.

    The first guard tested the file rather than the record count, so a bead whose
    run list is empty reached the failure-share line and raised ZeroDivisionError.
    """
    path = tmp_path / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"basicly-empty": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "no dispatch has been recorded" in capsys.readouterr().out.lower()


def test_outcomes_counts_a_record_with_no_outcome_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed record is named, not dropped — a short total is a wrong rate."""
    path = tmp_path / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"basicly-a": [{}, {"outcome": run_record.FAILED}]}), "utf-8")
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert usage_report.UNLABELLED in out
    assert "1/2 = 50.0%" in out
