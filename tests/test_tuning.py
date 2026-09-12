from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from basicly import config, run_record, tracker, tuning
from tests import flipped_tracker

MARKER = run_record.MARKER


def _write_local(repo_root: Path, records: dict[str, list[dict]]) -> None:
    usage_dir = repo_root / run_record.USAGE_DIR
    usage_dir.mkdir(parents=True, exist_ok=True)
    (repo_root / run_record.RUN_RECORDS_FILE).write_text(json.dumps(records), encoding="utf-8")


def _write_tracker(repo_root: Path, records: dict[str, list[dict]]) -> None:

    flipped_tracker.seed_records(
        repo_root,
        [
            {
                "id": bead_id,
                "comments": [
                    {"text": f"{MARKER} id={bead_id}#run-{index}\n{json.dumps(entry)}"}
                    for index, entry in enumerate(entries)
                ],
            }
            for bead_id, entries in records.items()
        ],
    )


def _lane(stamp: str, **fields: object) -> dict:
    return {
        "agent": "claude",
        "outcome": run_record.EXECUTED,
        "phase": run_record.LANE_PHASE,
        "timestamp": stamp,
        **fields,
    }


def _parameter(report: tuning.TuningReport, key: str) -> tuning.ParameterTuning:
    for parameter in report.parameters:
        if parameter.key == key:
            return parameter
    raise AssertionError(f"{key} is missing from {[p.key for p in report.parameters]}")


def test_every_governed_parameter_is_listed_on_an_empty_corpus(tmp_path: Path) -> None:

    report = tuning.tuning_report(tmp_path)

    assert report.dispatches_read == 0
    assert report.parameters, "an empty corpus must still enumerate the governed set"
    for parameter in report.parameters:
        assert parameter.samples == 0, parameter.key
        assert parameter.recommendation is None, parameter.key
        assert parameter.status == tuning.UNOBSERVED, parameter.key
    assert _parameter(report, "runner.runner_timeout").in_force == 3600.0
    assert _parameter(report, "worktree.concurrency").in_force == float(
        config.DEFAULT_WORKTREE_CONCURRENCY
    )


def test_a_parameter_nothing_records_is_listed_beside_measured_ones(tmp_path: Path) -> None:
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(f"2026-07-2{index}T09:00:00+00:00", duration_s=100.0 + index)
                for index in range(9)
            ]
        },
    )

    report = tuning.tuning_report(tmp_path)

    measured = _parameter(report, "runner.runner_timeout")
    assert measured.samples == 9
    unobserved = _parameter(report, "runner.stall_after")
    assert unobserved.samples == 0
    assert unobserved.recommendation is None
    assert unobserved.status == tuning.UNOBSERVED
    assert "no dispatch record carries a signal" in unobserved.basis


def test_a_measured_recommendation_carries_its_statistic_and_sample_size(
    tmp_path: Path,
) -> None:
    durations = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0]
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(f"2026-07-{index + 10:02d}T09:00:00+00:00", duration_s=duration)
                for index, duration in enumerate(durations)
            ]
        },
    )

    parameter = _parameter(tuning.tuning_report(tmp_path), "runner.runner_timeout")

    assert parameter.status == tuning.MEASURED
    assert parameter.samples == len(durations)
    assert parameter.recommendation == max(durations) * tuning.BACKSTOP_HEADROOM
    assert parameter.outcomes == {run_record.EXECUTED: 10}
    assert [cohort.in_force for cohort in parameter.cohorts] == ["3600"]


def test_a_sample_under_the_minimum_is_seeded_from_the_prior(tmp_path: Path) -> None:

    durations = [100.0, 200.0, 300.0]
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(f"2026-07-{index + 10:02d}T09:00:00+00:00", duration_s=duration)
                for index, duration in enumerate(durations)
            ]
        },
    )

    parameter = _parameter(tuning.tuning_report(tmp_path), "runner.runner_timeout")

    assert parameter.samples == 3
    assert parameter.samples < parameter.min_samples
    assert parameter.status == tuning.SEEDED
    assert parameter.recommendation == parameter.prior
    assert parameter.recommendation != max(durations) * tuning.BACKSTOP_HEADROOM
    assert parameter.in_force == 3600.0


def test_both_corpora_are_read_and_each_sample_names_its_source(tmp_path: Path) -> None:

    shared = _lane("2026-07-10T09:00:00+00:00", duration_s=100.0)
    _write_tracker(
        tmp_path,
        {
            "b-1": [shared],
            "b-2": [_lane("2026-07-11T09:00:00+00:00", duration_s=200.0)],
        },
    )
    _write_local(
        tmp_path,
        {
            "b-1": [shared],
            "b-3": [_lane("2026-07-12T09:00:00+00:00", duration_s=300.0)],
        },
    )

    report = tuning.tuning_report(tmp_path)

    assert report.dispatches_read == 3
    assert report.sources == {tuning.BOTH: 1, tuning.LOCAL: 1, tuning.TRACKER: 1}
    parameter = _parameter(report, "runner.runner_timeout")
    assert parameter.sources == {tuning.BOTH: 1, tuning.LOCAL: 1, tuning.TRACKER: 1}
    assert {(item.bead, item.source) for item in parameter.observations} == {
        ("b-1", tuning.BOTH),
        ("b-2", tuning.TRACKER),
        ("b-3", tuning.LOCAL),
    }


def test_the_report_writes_nothing(tmp_path: Path) -> None:

    (tmp_path / "basicly.toml").write_text(
        "[worktree]\nconcurrency = 3\n\n[policy.sizing]\nworking_set_max = 99000\n",
        encoding="utf-8",
    )
    (tmp_path / "basicly.local.toml").write_text(
        "[runner]\nrunner_timeout = 900\n", encoding="utf-8"
    )
    _write_local(tmp_path, {"b-1": [_lane("2026-07-10T09:00:00+00:00", duration_s=100.0)]})
    _write_tracker(tmp_path, {"b-2": [_lane("2026-07-11T09:00:00+00:00", duration_s=200.0)]})
    before = _tree_digest(tmp_path)

    report = tuning.tuning_report(tmp_path)

    assert report.dispatches_read == 2, "the report must have really read the corpus"
    assert _tree_digest(tmp_path) == before


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_configured_value_is_the_one_reported_in_force(tmp_path: Path) -> None:
    (tmp_path / "basicly.toml").write_text(
        "[worktree]\nconcurrency = 3\n\n[policy.sizing]\nworking_set_max = 99000\n",
        encoding="utf-8",
    )

    report = tuning.tuning_report(tmp_path)

    concurrency = _parameter(report, "worktree.concurrency")
    assert concurrency.in_force == 3.0
    assert concurrency.prior == float(config.DEFAULT_WORKTREE_CONCURRENCY)
    assert _parameter(report, "policy.sizing.working_set_max").in_force == 99000.0


def test_a_session_override_makes_its_own_cohort(tmp_path: Path) -> None:

    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane("2026-07-10T09:00:00+00:00", duration_s=100.0),
                _lane(
                    "2026-07-11T09:00:00+00:00",
                    duration_s=200.0,
                    outcome=run_record.FAILED,
                    config_overrides=["runner.runner_timeout=900"],
                ),
            ]
        },
    )

    parameter = _parameter(tuning.tuning_report(tmp_path), "runner.runner_timeout")

    assert [(cohort.in_force, cohort.samples) for cohort in parameter.cohorts] == [
        ("3600", 1),
        ("900", 1),
    ]
    assert parameter.cohorts[1].outcomes == {run_record.FAILED: 1}


@pytest.mark.parametrize(
    "phase",
    [run_record.VALIDATE_PHASE, run_record.DECIDE_PHASE, run_record.PROPOSE_PHASE, None],
    ids=["validate", "decide", "propose", "unrecorded"],
)
def test_a_helper_dispatch_is_not_a_lane_sample(tmp_path: Path, phase: str | None) -> None:

    _write_local(
        tmp_path,
        {"b-1": [{**_lane("2026-07-10T09:00:00+00:00", duration_s=100.0), "phase": phase}]},
    )

    report = tuning.tuning_report(tmp_path)

    assert report.dispatches_read == 1, "the dispatch is read; it just is not evidence"
    assert _parameter(report, "runner.runner_timeout").samples == 0


def test_a_handoff_is_not_evidence_of_how_long_work_takes(tmp_path: Path) -> None:
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(
                    "2026-07-10T09:00:00+00:00",
                    outcome=run_record.HANDOFF,
                    duration_s=100.0,
                )
            ]
        },
    )

    assert _parameter(tuning.tuning_report(tmp_path), "runner.runner_timeout").samples == 0


def test_rework_is_counted_from_attempts_per_bead(tmp_path: Path) -> None:
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(f"2026-07-{day:02d}T09:00:00+00:00", duration_s=10.0) for day in range(10, 13)
            ],
            "b-2": [
                _lane(f"2026-07-{day:02d}T10:00:00+00:00", duration_s=10.0) for day in range(10, 17)
            ],
        },
    )

    parameter = _parameter(tuning.tuning_report(tmp_path), "policy.max_rework")

    assert parameter.status == tuning.MEASURED
    assert parameter.recommendation == 5.0


def test_the_build_factor_is_fitted_to_measured_working_set(tmp_path: Path) -> None:
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(
                    f"2026-07-{day:02d}T09:00:00+00:00",
                    task_class="task",
                    scope_tokens=1_000,
                    context_tokens=4_000,
                    tokens=4_000_000,
                )
                for day in range(10, 20)
            ]
        },
    )

    parameter = _parameter(tuning.tuning_report(tmp_path), "policy.sizing.build_factor.task")

    assert parameter.status == tuning.MEASURED
    assert parameter.recommendation == 4.0


def test_the_occupancy_recommendation_never_exceeds_the_whole_window(tmp_path: Path) -> None:
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(
                    f"2026-07-{day:02d}T09:00:00+00:00",
                    context_tokens=180_000,
                    context_window=200_000,
                )
                for day in range(10, 20)
            ]
        },
    )

    parameter = _parameter(tuning.tuning_report(tmp_path), "policy.sizing.context_ceiling")

    assert parameter.status == tuning.MEASURED
    assert parameter.recommendation == 1.0


def test_a_dispatch_with_no_timestamp_is_dropped(tmp_path: Path) -> None:

    _write_local(
        tmp_path,
        {
            "b-1": [
                {"agent": "claude", "outcome": run_record.EXECUTED, "phase": "lane"},
                _lane("2026-07-10T09:00:00+00:00", duration_s=100.0),
            ]
        },
    )

    assert tuning.tuning_report(tmp_path).dispatches_read == 1


def test_a_corrupt_corpus_reads_as_no_evidence(tmp_path: Path) -> None:
    usage_dir = tmp_path / run_record.USAGE_DIR
    usage_dir.mkdir(parents=True)
    (tmp_path / run_record.RUN_RECORDS_FILE).write_text("{not json", encoding="utf-8")
    repo = flipped_tracker.flipped_repo(tmp_path)
    (tracker.ledger_dir(repo) / "events-0001.jsonl").write_text(
        "not json either\n", encoding="utf-8"
    )

    report = tuning.tuning_report(tmp_path)

    assert report.dispatches_read == 0
    assert all(parameter.recommendation is None for parameter in report.parameters)


def test_render_value_prints_a_value_a_reader_can_paste() -> None:
    assert tuning.render_value(3600.0) == "3600"
    assert tuning.render_value(0.6) == "0.6"
    assert tuning.render_value(222_481.0) == "222481"
    assert tuning.render_value(10.6789123) == "10.68"
