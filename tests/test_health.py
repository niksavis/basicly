from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import cli, health, run_record, supervise

if TYPE_CHECKING:
    import pytest


def _entry(agent: str, outcome: str, ts: str, bound: str | None = None) -> dict:
    return {
        "agent": agent,
        "outcome": outcome,
        "timestamp": ts,
        "returncode": 0,
        "stopped_bound": bound,
    }


def _ts(i: int) -> str:
    return f"2026-07-{i:02d}T00:00:00+00:00"


def _write_records(repo: Path, data: dict) -> None:
    records = repo / run_record.RUN_RECORDS_FILE
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(json.dumps(data), encoding="utf-8")


def test_agent_health_counts_outcomes_and_failure_rate() -> None:
    records = {
        "basicly-a": [_entry("claude", "executed", _ts(1)), _entry("claude", "failed", _ts(2))],
        "basicly-b": [_entry("claude", "handoff", _ts(3))],
    }
    (h,) = health.agent_health(records)
    assert h.agent == "claude"
    assert (h.runs, h.executed, h.failed, h.handoff) == (3, 1, 1, 1)
    assert h.failure_rate == 0.5


def test_a_bounded_stop_leaves_the_failure_rate_and_is_counted_under_its_bound() -> None:

    records = {
        "basicly-a": [
            _entry("claude", "executed", _ts(1)),
            _entry("claude", "failed", _ts(2)),
            _entry("claude", "failed", _ts(3), bound="spend"),
        ],
        "basicly-b": [_entry("claude", "failed", _ts(4), bound="quiet")],
    }
    (h,) = health.agent_health(records)
    assert h.stopped == 2
    assert h.stopped_bounds == {"quiet": 1, "spend": 1}
    assert (h.executed, h.failed) == (1, 1), "a bounded stop is not a failed dispatch"
    assert h.failure_rate == 0.5
    assert h.runs == 4 == h.executed + h.failed + h.handoff + h.stopped


def test_agent_health_rework_signal_counts_redispatched_beads() -> None:
    records = {
        "basicly-a": [_entry("claude", "failed", _ts(1)), _entry("claude", "executed", _ts(2))],
        "basicly-b": [_entry("claude", "executed", _ts(3))],
    }
    (h,) = health.agent_health(records)
    assert h.rework_beads == 1
    assert h.rework_rate == 0.5


def test_agent_health_perfect_agent_scores_one() -> None:
    records = {"basicly-a": [_entry("codex", "executed", _ts(1))]}
    (h,) = health.agent_health(records)
    assert h.failure_rate == 0.0 and h.rework_beads == 0
    assert h.health_score == 1.0


def test_agent_health_all_failures_score_zero() -> None:
    records = {"basicly-a": [_entry("codex", "failed", _ts(1)), _entry("codex", "failed", _ts(2))]}
    (h,) = health.agent_health(records)
    assert h.failure_rate == 1.0
    assert h.health_score == 0.0


def test_agent_health_rework_discounts_a_succeeding_agent() -> None:
    records = {
        "basicly-a": [_entry("claude", "executed", _ts(1)), _entry("claude", "executed", _ts(2))]
    }
    (h,) = health.agent_health(records)
    assert h.health_score == 0.7


def test_agent_health_sorted_by_agent() -> None:
    records = {
        "basicly-a": [_entry("zeta", "executed", _ts(1)), _entry("alpha", "executed", _ts(2))]
    }
    assert [h.agent for h in health.agent_health(records)] == ["alpha", "zeta"]


def test_agent_drift_flags_regression() -> None:
    entries = [_entry("claude", "executed", _ts(i)) for i in range(1, 4)]
    entries += [_entry("claude", "failed", _ts(i)) for i in range(4, 8)]
    entries += [_entry("claude", "executed", _ts(8))]
    records = {"basicly-a": entries}
    (d,) = health.agent_drift(records, window=5)
    assert d.baseline_runs == 3 and d.recent_runs == 5
    assert d.baseline_failure_rate == 0.0
    assert d.recent_failure_rate == 0.8
    assert d.regressed is True


def test_agent_drift_no_regression_when_stable() -> None:
    records = {"basicly-a": [_entry("claude", "executed", _ts(i)) for i in range(1, 9)]}
    (d,) = health.agent_drift(records, window=5)
    assert d.regressed is False
    assert d.delta == 0.0


def test_agent_drift_insufficient_sample_never_regresses() -> None:
    entries = [_entry("claude", "executed", _ts(1)), _entry("claude", "executed", _ts(2))]
    entries += [_entry("claude", "failed", _ts(i)) for i in range(3, 8)]
    records = {"basicly-a": entries}
    (d,) = health.agent_drift(records, window=5)
    assert d.baseline_runs == 2
    assert d.regressed is False


def _drift_over_four_stops(bound: str | None) -> health.AgentDrift:

    entries = [_entry("claude", "executed", _ts(i)) for i in range(1, 7)]
    entries += [_entry("claude", "failed", _ts(i), bound=bound) for i in range(7, 11)]
    (drift,) = health.agent_drift({"basicly-a": entries}, window=5)
    return drift


def test_a_window_the_ceiling_emptied_cannot_flag_but_the_same_failures_do() -> None:

    bounded = _drift_over_four_stops("spend")
    assert bounded.recent_runs == 1, "four stops leave one scored run in the window"
    assert bounded.recent_runs < health.MIN_WINDOW_SAMPLE
    assert bounded.regressed is False

    control = _drift_over_four_stops(None)
    assert control.recent_runs == 5
    assert control.recent_failure_rate == 0.8
    assert control.regressed is True, "an unbounded failure run is still a regression"


def test_agent_drift_ignores_handoffs() -> None:
    records = {"basicly-a": [_entry("claude", "handoff", _ts(i)) for i in range(1, 6)]}
    (d,) = health.agent_drift(records, window=5)
    assert d.baseline_runs == 0 and d.recent_runs == 0
    assert d.regressed is False


def test_health_report_empty_when_no_records(tmp_path: Path) -> None:
    report = health.health_report(tmp_path)
    assert report["schema_version"] == health.HEALTH_SCHEMA_VERSION
    assert report["agents"] == [] and report["drift"] == []
    assert report["regressions"] == []


def test_health_report_reads_on_disk_records(tmp_path: Path) -> None:
    _write_records(tmp_path, {"basicly-a": [_entry("claude", "failed", _ts(1))]})
    report = health.health_report(tmp_path)
    assert report["agents"][0]["agent"] == "claude"
    assert report["agents"][0]["failure_rate"] == 1.0


def test_health_report_tolerates_corrupt_log(tmp_path: Path) -> None:
    records = tmp_path / run_record.RUN_RECORDS_FILE
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text("{not json", encoding="utf-8")
    report = health.health_report(tmp_path)
    assert report["agents"] == [] and report["regressions"] == []


def test_health_report_surfaces_regression(tmp_path: Path) -> None:
    entries = [_entry("claude", "executed", _ts(i)) for i in range(1, 4)]
    entries += [_entry("claude", "failed", _ts(i)) for i in range(4, 9)]
    _write_records(tmp_path, {"basicly-a": entries})
    report = health.health_report(tmp_path, window=5)
    assert report["regressions"] == ["claude"]


def test_fleet_health_rolls_up_repos(tmp_path: Path) -> None:
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
    assert report["totals"]["regressions"] == 1


def test_fleet_health_empty_root(tmp_path: Path) -> None:
    report = health.fleet_health(tmp_path / "nope")
    assert report["repos"] == []
    assert report["totals"] == {"repos": 0, "regressions": 0}


def _args(**kw) -> argparse.Namespace:
    defaults = {"json": False, "fleet": False, "root": None, "window": health.DEFAULT_WINDOW}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_cmd_health_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _write_records(tmp_path, {"basicly-a": [_entry("claude", "executed", _ts(1))]})
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.cmd_health(_args(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agents"][0]["agent"] == "claude"


def test_cmd_health_rejects_bad_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.cmd_health(_args(window=0)) == 2


def test_cmd_health_text_no_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    assert cli.cmd_health(_args()) == 0
    assert "no run-records" in capsys.readouterr().out


def _coverage_lines(repo: Path) -> list[str]:
    lines: list[str] = []
    spend = supervise.PassSpendAdmission(None, None, (), (), None)
    supervise._report_coverage(lines.append, repo, (), spend)
    return lines


def test_pass_report_carries_agent_health_and_drift(tmp_path: Path) -> None:

    entries = [_entry("claude", "executed", _ts(i)) for i in range(1, 6)]
    entries += [_entry("claude", "failed", _ts(i)) for i in range(6, 11)]
    _write_records(tmp_path, {"basicly-a": entries})

    lines = _coverage_lines(tmp_path)

    assert "health:   claude 0.35 over 10 runs" in lines[2]
    assert "(fail 50%, 0 stopped by a bound, rework 100% — 1 bead(s) re-dispatched)" in lines[2]
    assert lines[3] == (
        "drift:    REGRESSED claude: fail 100% over the recent 5 vs 0% over 5 baseline runs (+1.00)"
    )


def test_the_pass_line_names_the_bounded_stops_and_does_not_flag_them(tmp_path: Path) -> None:

    entries = [_entry("claude", "executed", _ts(i)) for i in range(1, 7)]
    entries += [_entry("claude", "failed", _ts(i), bound="spend") for i in range(7, 11)]
    _write_records(tmp_path, {"basicly-a": entries})

    lines = _coverage_lines(tmp_path)

    assert "(fail 0%, 4 stopped by a bound (spend 4), " in lines[2]
    assert lines[3] == "drift:    no behavioral regression across 1 agent(s)"


def test_pass_report_health_survives_a_repo_with_no_records(tmp_path: Path) -> None:

    lines = _coverage_lines(tmp_path)

    assert lines[2] == "health:   no run-records yet"
    assert lines[3] == "drift:    no history to drift against"
