"""Tests for per-agent health scoring and behavioral drift (basicly-y886).

Health and drift are pure functions of the run-record map, so most tests feed a
literal map; a few exercise the on-disk read and the fleet rollup. Drift is a
rolling baseline off the records' own timestamps — recent window vs everything
older — so the fixtures stamp increasing timestamps to control the split.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from basicly import cli, health, run_record


def _entry(agent: str, outcome: str, ts: str) -> dict:
    return {"agent": agent, "outcome": outcome, "timestamp": ts, "returncode": 0}


def _ts(i: int) -> str:
    """A sortable ISO-ish timestamp keyed by a monotonically increasing index."""
    return f"2026-07-{i:02d}T00:00:00+00:00"


def _write_records(repo: Path, data: dict) -> None:
    records = repo / run_record.RUN_RECORDS_FILE
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(json.dumps(data), encoding="utf-8")


# --- agent_health -----------------------------------------------------------


def test_agent_health_counts_outcomes_and_failure_rate() -> None:
    """Outcomes are tallied; failure rate excludes handoffs from the denominator."""
    records = {
        "basicly-a": [_entry("claude", "executed", _ts(1)), _entry("claude", "failed", _ts(2))],
        "basicly-b": [_entry("claude", "handoff", _ts(3))],
    }
    (h,) = health.agent_health(records)
    assert h.agent == "claude"
    assert (h.runs, h.executed, h.failed, h.handoff) == (3, 1, 1, 1)
    assert h.failure_rate == 0.5  # 1 failed of 2 dispatched; the handoff is excluded


def test_agent_health_rework_signal_counts_redispatched_beads() -> None:
    """A bead with more than one record for the agent is a rework bead."""
    records = {
        "basicly-a": [_entry("claude", "failed", _ts(1)), _entry("claude", "executed", _ts(2))],
        "basicly-b": [_entry("claude", "executed", _ts(3))],
    }
    (h,) = health.agent_health(records)
    assert h.rework_beads == 1  # basicly-a re-dispatched
    assert h.rework_rate == 0.5  # 1 of 2 beads


def test_agent_health_perfect_agent_scores_one() -> None:
    """No failures and no rework yields a perfect score."""
    records = {"basicly-a": [_entry("codex", "executed", _ts(1))]}
    (h,) = health.agent_health(records)
    assert h.failure_rate == 0.0 and h.rework_beads == 0
    assert h.health_score == 1.0


def test_agent_health_all_failures_score_zero() -> None:
    """A fully-failing agent scores zero regardless of rework."""
    records = {"basicly-a": [_entry("codex", "failed", _ts(1)), _entry("codex", "failed", _ts(2))]}
    (h,) = health.agent_health(records)
    assert h.failure_rate == 1.0
    assert h.health_score == 0.0


def test_agent_health_rework_discounts_a_succeeding_agent() -> None:
    """Churn on a succeeding agent applies the multiplicative rework penalty."""
    records = {
        "basicly-a": [_entry("claude", "executed", _ts(1)), _entry("claude", "executed", _ts(2))]
    }
    (h,) = health.agent_health(records)
    # failure_rate 0, rework_rate 1.0 -> 1.0 * (1 - 0.3) = 0.7
    assert h.health_score == 0.7


def test_agent_health_sorted_by_agent() -> None:
    """Multiple agents come back sorted by name."""
    records = {
        "basicly-a": [_entry("zeta", "executed", _ts(1)), _entry("alpha", "executed", _ts(2))]
    }
    assert [h.agent for h in health.agent_health(records)] == ["alpha", "zeta"]


# --- agent_drift ------------------------------------------------------------


def test_agent_drift_flags_regression() -> None:
    """A recent failure-rate jump over the baseline is flagged as a regression."""
    # 3 clean baseline runs, then 5 recent runs that are mostly failures.
    entries = [_entry("claude", "executed", _ts(i)) for i in range(1, 4)]
    entries += [_entry("claude", "failed", _ts(i)) for i in range(4, 8)]  # 4 failed
    entries += [_entry("claude", "executed", _ts(8))]  # 1 executed -> recent window of 5
    records = {"basicly-a": entries}
    (d,) = health.agent_drift(records, window=5)
    assert d.baseline_runs == 3 and d.recent_runs == 5
    assert d.baseline_failure_rate == 0.0
    assert d.recent_failure_rate == 0.8  # 4 of 5
    assert d.regressed is True


def test_agent_drift_no_regression_when_stable() -> None:
    """A stable failure rate across windows is not a regression."""
    records = {"basicly-a": [_entry("claude", "executed", _ts(i)) for i in range(1, 9)]}
    (d,) = health.agent_drift(records, window=5)
    assert d.regressed is False
    assert d.delta == 0.0


def test_agent_drift_insufficient_sample_never_regresses() -> None:
    """Too few runs in a window cannot trip a regression even if all recent fail."""
    # Only 2 baseline runs (< MIN_WINDOW_SAMPLE); recent all-fail.
    entries = [_entry("claude", "executed", _ts(1)), _entry("claude", "executed", _ts(2))]
    entries += [_entry("claude", "failed", _ts(i)) for i in range(3, 8)]
    records = {"basicly-a": entries}
    (d,) = health.agent_drift(records, window=5)
    assert d.baseline_runs == 2  # below MIN_WINDOW_SAMPLE
    assert d.regressed is False


def test_agent_drift_ignores_handoffs() -> None:
    """Handoffs carry no outcome, so they never enter the drift windows."""
    records = {"basicly-a": [_entry("claude", "handoff", _ts(i)) for i in range(1, 6)]}
    (d,) = health.agent_drift(records, window=5)
    assert d.baseline_runs == 0 and d.recent_runs == 0
    assert d.regressed is False


# --- health_report / on-disk + resilience -----------------------------------


def test_health_report_empty_when_no_records(tmp_path: Path) -> None:
    """A repo with no run-record file reports empty agents/drift, no regressions."""
    report = health.health_report(tmp_path)
    assert report["schema_version"] == health.HEALTH_SCHEMA_VERSION
    assert report["agents"] == [] and report["drift"] == []
    assert report["regressions"] == []


def test_health_report_reads_on_disk_records(tmp_path: Path) -> None:
    """health_report reads the run-record file and surfaces per-agent health."""
    _write_records(tmp_path, {"basicly-a": [_entry("claude", "failed", _ts(1))]})
    report = health.health_report(tmp_path)
    assert report["agents"][0]["agent"] == "claude"
    assert report["agents"][0]["failure_rate"] == 1.0


def test_health_report_tolerates_corrupt_log(tmp_path: Path) -> None:
    """A corrupt records file degrades to an empty report, never raises."""
    records = tmp_path / run_record.RUN_RECORDS_FILE
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text("{not json", encoding="utf-8")
    report = health.health_report(tmp_path)
    assert report["agents"] == [] and report["regressions"] == []


def test_health_report_surfaces_regression(tmp_path: Path) -> None:
    """A regressing agent lands in the regressions list."""
    entries = [_entry("claude", "executed", _ts(i)) for i in range(1, 4)]
    entries += [_entry("claude", "failed", _ts(i)) for i in range(4, 9)]
    _write_records(tmp_path, {"basicly-a": entries})
    report = health.health_report(tmp_path, window=5)
    assert report["regressions"] == ["claude"]


# --- fleet_health -----------------------------------------------------------


def test_fleet_health_rolls_up_repos(tmp_path: Path) -> None:
    """The fleet rollup reports each repo's health and totals the regressions."""
    good = tmp_path / "good"
    (good / ".basicly").mkdir(parents=True)
    _write_records(good, {"basicly-a": [_entry("claude", "executed", _ts(1))]})

    bad = tmp_path / "bad"
    (bad / ".basicly").mkdir(parents=True)
    entries = [_entry("codex", "executed", _ts(i)) for i in range(1, 4)]
    entries += [_entry("codex", "failed", _ts(i)) for i in range(4, 9)]
    _write_records(bad, {"basicly-b": entries})

    report = health.fleet_health(tmp_path, window=5)
    assert [r["name"] for r in report["repos"]] == ["bad", "good"]
    assert report["totals"]["repos"] == 2
    assert report["totals"]["regressions"] == 1  # only 'bad' regressed


def test_fleet_health_empty_root(tmp_path: Path) -> None:
    """A root with no basicly repos yields an empty, valid rollup."""
    report = health.fleet_health(tmp_path / "nope")
    assert report["repos"] == []
    assert report["totals"] == {"repos": 0, "regressions": 0}


# --- CLI --------------------------------------------------------------------


def _args(**kw) -> argparse.Namespace:
    defaults = {"json": False, "fleet": False, "root": None, "window": health.DEFAULT_WINDOW}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_cmd_health_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """--json prints the health payload for the current repo."""
    _write_records(tmp_path, {"basicly-a": [_entry("claude", "executed", _ts(1))]})
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.cmd_health(_args(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agents"][0]["agent"] == "claude"


def test_cmd_health_rejects_bad_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A window below 1 is a usage error."""
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.cmd_health(_args(window=0)) == 2


def test_cmd_health_text_no_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The text path with no records reports cleanly and exits 0."""
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.cmd_health(_args()) == 0
    assert "no run-records" in capsys.readouterr().out
