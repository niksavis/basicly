from __future__ import annotations

import json
import shutil
from pathlib import Path

from basicly import tracker_paths
from basicly.config import load_runner_config
from tests.test_cli import run_basicly


def test_cli_usage_report_tables_counters_and_flags_unused_skills(work_repo: Path) -> None:
    usage_dir = work_repo / ".basicly" / "usage"
    shutil.rmtree(usage_dir, ignore_errors=True)
    usage_dir.mkdir(parents=True)
    (usage_dir / "tool-usage.json").write_text(
        json.dumps({
            "rg": {"count": 7, "last_used": "2026-07-16"},
            "skill:conventional-commits": {"count": 2, "last_used": "2026-07-16"},
        }),
        encoding="utf-8",
    )
    result = run_basicly(work_repo, "usage", "report")
    assert result.returncode == 0, result.stderr
    assert "rg" in result.stdout and "7" in result.stdout
    assert "conventional-commits" in result.stdout
    assert "Never invoked through the Skill tool" in result.stdout


def test_cli_usage_report_separates_unexercised_from_unwanted(work_repo: Path) -> None:

    usage_dir = work_repo / ".basicly" / "usage"
    shutil.rmtree(usage_dir, ignore_errors=True)
    usage_dir.mkdir(parents=True)
    (usage_dir / "tool-usage.json").write_text(
        json.dumps({"skill:conventional-commits": {"count": 2, "last_used": "2026-07-16"}}),
        encoding="utf-8",
    )
    result = run_basicly(work_repo, "usage", "report")
    assert result.returncode == 0, result.stderr
    assert "culling candidates" not in result.stdout
    assert "Not a culling list" in result.stdout

    _, _, tail = result.stdout.partition("Never invoked through the Skill tool")
    delivered, _, unreachable = tail.partition("unreachable")
    assert "root-cause" in delivered and "worktree-isolation" in delivered
    assert "tool-jq" in unreachable
    assert "root-cause" not in unreachable


def test_cli_usage_report_names_the_bucket_the_unparsed_heads_go_to(work_repo: Path) -> None:

    usage_dir = work_repo / ".basicly" / "usage"
    shutil.rmtree(usage_dir, ignore_errors=True)
    usage_dir.mkdir(parents=True)
    (usage_dir / "tool-usage.json").write_text(
        json.dumps({
            "rg": {"count": 7, "last_used": "2026-07-16"},
            "PYEOF": {"count": 33, "last_used": "2026-07-16"},
        }),
        encoding="utf-8",
    )
    result = run_basicly(work_repo, "usage", "report")
    assert result.returncode == 0, result.stderr
    tools, _, unresolved = result.stdout.partition("Unresolved heads")
    assert unresolved, result.stdout
    assert "rg" in tools and "PYEOF" not in tools
    assert "PYEOF" in unresolved and "33" in unresolved


def test_cli_usage_report_notes_missing_data(work_repo: Path) -> None:
    shutil.rmtree(work_repo / ".basicly" / "usage", ignore_errors=True)
    result = run_basicly(work_repo, "usage", "report")
    assert result.returncode == 0, result.stderr
    assert "No usage data" in result.stdout


def _run_records(work_repo: Path, records: dict) -> None:

    usage_dir = work_repo / ".basicly" / "usage"
    shutil.rmtree(usage_dir, ignore_errors=True)
    usage_dir.mkdir(parents=True)
    (usage_dir / "run-records.json").write_text(json.dumps(records), encoding="utf-8")
    ledger = work_repo / tracker_paths.LEDGER_DIR_NAME
    (ledger / tracker_paths.REDIRECT_NAME).unlink(missing_ok=True)
    for log in ledger.glob("events-*.jsonl"):
        log.write_text("", encoding="utf-8")


def test_cli_usage_forecast_reports_the_ratio_per_paired_dispatch(work_repo: Path) -> None:
    _run_records(
        work_repo,
        {
            "b-1": [
                {
                    "agent": "claude",
                    "outcome": "executed",
                    "timestamp": "2026-07-26T09:00:00+00:00",
                    "forecast_tokens": 50_000,
                    "tokens": 200_000,
                    "task_class": "task",
                    "forecast_source": "dispatch",
                }
            ]
        },
    )
    result = run_basicly(work_repo, "usage", "forecast")
    assert result.returncode == 0, result.stderr
    assert "b-1" in result.stdout and "4.00x" in result.stdout
    assert "Median actual/forecast" in result.stdout
    assert "turn multiplier" in result.stdout


def test_cli_usage_forecast_explains_an_empty_report(work_repo: Path) -> None:
    _run_records(
        work_repo,
        {
            "b-1": [
                {
                    "agent": "claude",
                    "outcome": "executed",
                    "timestamp": "2026-07-26T09:00:00+00:00",
                    "tokens": 200_000,
                }
            ]
        },
    )
    result = run_basicly(work_repo, "usage", "forecast")
    assert result.returncode == 0, result.stderr
    assert "no forecast error is computable yet" in result.stdout
    assert "1 actual with no forecast" in result.stdout


def _lane_records(count: int) -> dict:
    return {
        "b-1": [
            {
                "agent": "claude",
                "outcome": "executed",
                "phase": "lane",
                "timestamp": f"2026-07-{day:02d}T09:00:00+00:00",
                "duration_s": 100.0 * day,
            }
            for day in range(10, 10 + count)
        ]
    }


def _advice(stdout: str, key: str) -> str:

    for line in stdout.splitlines():
        if line.strip().startswith(f"{key}: "):
            return line
    raise AssertionError(f"{key} is missing from the report:\n{stdout}")


def test_cli_usage_tuning_advises_each_governed_parameter(work_repo: Path) -> None:
    _run_records(work_repo, _lane_records(10))
    result = run_basicly(work_repo, "usage", "tuning")
    assert result.returncode == 0, result.stderr
    advice = _advice(result.stdout, "runner.runner_timeout")
    in_force = int(load_runner_config(work_repo).runner_timeout)
    assert f"{in_force} s in force" in advice
    assert "advised 3800 s from 10 sample(s) (measured)" in advice
    assert "10 local" in result.stdout


def test_cli_usage_tuning_labels_a_thin_sample_as_seeded(work_repo: Path) -> None:
    _run_records(work_repo, _lane_records(3))
    result = run_basicly(work_repo, "usage", "tuning")
    assert result.returncode == 0, result.stderr
    assert "advised 3600 s from 3 sample(s) (seeded)" in _advice(
        result.stdout, "runner.runner_timeout"
    )
    assert "it would displace the value in force" in result.stdout
    assert "2400" not in result.stdout


def test_cli_usage_tuning_lists_a_parameter_nothing_measures(work_repo: Path) -> None:
    _run_records(work_repo, _lane_records(10))
    result = run_basicly(work_repo, "usage", "tuning")
    assert result.returncode == 0, result.stderr
    advice = _advice(result.stdout, "runner.stall_after")
    assert "900 s in force" in advice
    assert "no recommendation (unobserved)" in advice
    assert "no dispatch record carries a signal" in advice


def test_cli_usage_tuning_changes_no_configuration(work_repo: Path) -> None:
    _run_records(work_repo, _lane_records(10))
    configs = ["basicly.toml", "pyproject.toml", ".importlinter"]
    before = {name: (work_repo / name).read_bytes() for name in configs}

    result = run_basicly(work_repo, "usage", "tuning")

    assert result.returncode == 0, result.stderr
    assert {name: (work_repo / name).read_bytes() for name in configs} == before
    assert "This report changed nothing" in result.stdout
