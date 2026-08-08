"""Tests for the recorded-usage reports (`basicly usage ...`).

Run through the real CLI against a copied repo rather than by calling the report
functions: every one of these reports exists to be read by a human at a terminal, and
the claim under test is what that human sees — a table, a bucket named rather than
hidden, an advice line that survives a narrow console's cell folding.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tests.test_cli import run_basicly


def test_cli_usage_report_tables_counters_and_flags_unused_skills(work_repo: Path) -> None:
    """Usage report joins the counters against the catalog's skills."""
    usage_dir = work_repo / ".basicly" / "usage"
    # The fixture copies the live repo, which may carry real telemetry.
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
    assert "Never-used catalog skills" in result.stdout


def test_cli_usage_report_names_the_bucket_the_unparsed_heads_go_to(work_repo: Path) -> None:
    """A head that names no command is reported as a parser miss, not as a tool.

    The table is read as culling evidence, so `PYEOF` sitting in it at 33
    executions is a fabricated tool — and the bucket has to be named on the
    surface, because a reader who cannot see where those heads went reads their
    absence as the parser having nothing to report (basicly-3ymj).
    """
    usage_dir = work_repo / ".basicly" / "usage"
    # The fixture copies the live repo, which may carry real telemetry.
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
    """A repo without the hook's counter file gets a note, not an error."""
    shutil.rmtree(work_repo / ".basicly" / "usage", ignore_errors=True)
    result = run_basicly(work_repo, "usage", "report")
    assert result.returncode == 0, result.stderr
    assert "No usage data" in result.stdout


def _run_records(work_repo: Path, records: dict) -> None:
    """Seed the whole dispatch history the report reads, and nothing else.

    Both halves are replaced: `dispatch_history` unions the local log with the
    committed tracker markers (D11), and the fixture copies this repo's real export,
    so leaving it in place would mix ~90 live dispatches into the counts.
    """
    usage_dir = work_repo / ".basicly" / "usage"
    shutil.rmtree(usage_dir, ignore_errors=True)
    usage_dir.mkdir(parents=True)
    (usage_dir / "run-records.json").write_text(json.dumps(records), encoding="utf-8")
    beads = work_repo / ".beads"
    # The redirect is why blanking the export alone is not enough: `br` follows it to
    # the base checkout's tracker, so a fixture copied out of a harness worktree reads
    # the live repo's ~90 dispatches however empty its own export is.
    (beads / "redirect").unlink(missing_ok=True)
    (beads / "issues.jsonl").write_text("", encoding="utf-8")


def test_cli_usage_forecast_reports_the_ratio_per_paired_dispatch(work_repo: Path) -> None:
    """The forecast error report, over a dispatch that carries both halves (jr0l.34)."""
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
    # The ratio must never be presented as pure estimator error: the actual is total
    # spend and the forecast is a working set, so the turn multiplier is in there too.
    assert "turn multiplier" in result.stdout


def test_cli_usage_forecast_explains_an_empty_report(work_repo: Path) -> None:
    """An empty table alone would read as a healthy forecast; the counts say otherwise."""
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
    """*count* executed lane dispatches on one bead, each with a distinct duration."""
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
    """The unfolded advice line for *key*.

    Asserted on rather than the table above it: rich folds a wide table's cells across
    lines on a narrow terminal, so the table is scannable and the advice line is the
    one a consumer can grep whole.
    """
    for line in stdout.splitlines():
        if line.strip().startswith(f"{key}: "):
            return line
    raise AssertionError(f"{key} is missing from the report:\n{stdout}")


def test_cli_usage_tuning_advises_each_governed_parameter(work_repo: Path) -> None:
    """The report names the value in force, the sample size, and measured-or-seeded."""
    _run_records(work_repo, _lane_records(10))
    result = run_basicly(work_repo, "usage", "tuning")
    assert result.returncode == 0, result.stderr
    # 10 samples, the longest run is 1900s, and the backstop doubles it.
    advice = _advice(result.stdout, "runner.runner_timeout")
    assert "3600 s in force" in advice
    assert "advised 3800 s from 10 sample(s) (measured)" in advice
    assert "10 local" in result.stdout


def test_cli_usage_tuning_labels_a_thin_sample_as_seeded(work_repo: Path) -> None:
    """Under the calibration minimum the declared prior stands, and says what it displaces."""
    _run_records(work_repo, _lane_records(3))
    result = run_basicly(work_repo, "usage", "tuning")
    assert result.returncode == 0, result.stderr
    assert "advised 3600 s from 3 sample(s) (seeded)" in _advice(
        result.stdout, "runner.runner_timeout"
    )
    assert "it would displace the value in force" in result.stdout
    # The three durations are 1000s, 1100s and 1200s, so the backstop statistic would
    # have produced 2400. It must not appear anywhere: a number fitted to three samples
    # and merely labelled "seeded" is still read as a measurement.
    assert "2400" not in result.stdout


def test_cli_usage_tuning_lists_a_parameter_nothing_measures(work_repo: Path) -> None:
    """A bound with no recorded signal prints with zero samples rather than vanishing."""
    _run_records(work_repo, _lane_records(10))
    result = run_basicly(work_repo, "usage", "tuning")
    assert result.returncode == 0, result.stderr
    advice = _advice(result.stdout, "runner.stall_after")
    assert "900 s in force" in advice
    assert "no recommendation (unobserved)" in advice
    assert "no dispatch record carries a signal" in advice


def test_cli_usage_tuning_changes_no_configuration(work_repo: Path) -> None:
    """Advisory, not self-modifying: every config file survives the run byte-identical."""
    _run_records(work_repo, _lane_records(10))
    configs = ["basicly.toml", "pyproject.toml", ".importlinter"]
    before = {name: (work_repo / name).read_bytes() for name in configs}

    result = run_basicly(work_repo, "usage", "tuning")

    assert result.returncode == 0, result.stderr
    assert {name: (work_repo / name).read_bytes() for name in configs} == before
    assert "This report changed nothing" in result.stdout
