from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import contention, dispatch_brief, run_record, tracker_paths, usage_report
from tests.test_cli import run_basicly

if TYPE_CHECKING:
    import pytest


def _a_tracked_id(root: Path) -> str:
    for log in sorted((root / tracker_paths.LEDGER_DIR_NAME).glob("events-*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return str(json.loads(line)["record"])
    raise AssertionError("the committed ledger is empty, so no id can be briefed")


def _a_sibling_fenced_id(root: Path) -> str:

    for log in sorted((root / tracker_paths.LEDGER_DIR_NAME).glob("events-*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = str(json.loads(line)["record"])
            if contention.sibling_scopes(root, record):
                return record
    raise AssertionError("no committed record has an open sibling declaring a scope")


def test_brief_prints_the_assemblers_own_output(work_repo: Path) -> None:

    issue = _a_tracked_id(work_repo)
    result = run_basicly(work_repo, "brief", issue)

    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip("\n") == contention.with_scope_fence(
        work_repo, issue, dispatch_brief.dispatch_prompt(issue)
    )


def test_brief_prints_the_ground_an_open_sibling_declares(work_repo: Path) -> None:

    issue = _a_sibling_fenced_id(work_repo)
    result = run_basicly(work_repo, "brief", issue)

    assert result.returncode == 0, result.stderr
    assert "Scope this pass has already handed out" in result.stdout
    assert " owns `" in result.stdout


def test_brief_requires_an_issue_id(work_repo: Path) -> None:
    assert run_basicly(work_repo, "brief").returncode != 0


def test_brief_refuses_an_id_the_tracker_does_not_hold(work_repo: Path) -> None:

    result = run_basicly(work_repo, "brief", "basicly-zzz9")

    assert result.returncode == 1
    assert "No tracked issue basicly-zzz9" in result.stderr


def _seed(root: Path, outcomes: list[str]) -> None:
    path = root / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {f"basicly-t{i}": [{"outcome": name}] for i, name in enumerate(outcomes)}
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_outcomes_reports_every_recorded_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
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
    _seed(tmp_path, [run_record.FAILED, *([run_record.EXECUTED] * 3)])
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "1/4 = 25.0%" in capsys.readouterr().out


def test_outcomes_states_that_it_is_not_a_lane_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    _seed(tmp_path, [run_record.EXECUTED])
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "no record here says whether a lane reached a result" in capsys.readouterr().out


def test_outcomes_says_so_when_nothing_is_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "no dispatch has been recorded" in capsys.readouterr().out.lower()


def test_outcomes_survives_a_bead_with_no_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    path = tmp_path / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"basicly-empty": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    assert "no dispatch has been recorded" in capsys.readouterr().out.lower()


def test_outcomes_counts_a_record_with_no_outcome_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"basicly-a": [{}, {"outcome": run_record.FAILED}]}), "utf-8")
    monkeypatch.chdir(tmp_path)

    assert usage_report.cmd_outcomes(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert usage_report.UNLABELLED in out
    assert "1/2 = 50.0%" in out
