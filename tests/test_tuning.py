"""The advisory tuning report: recorded outcomes against the parameters in force.

Every test here holds one of the report's promises. The two that matter most are the
ones a plausible-looking report would quietly break: that a sample under the calibration
minimum yields the declared prior labelled *seeded* rather than a number fitted to three
dispatches, and that a parameter nothing measures still prints — with a sample size of
zero and no recommendation — instead of vanishing into a table that then reads as
"everything is fine".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from basicly import config, run_record, tuning

MARKER = run_record.MARKER


def _write_local(repo_root: Path, records: dict[str, list[dict]]) -> None:
    """Seed ``.basicly/usage/run-records.json`` — the self-ignored, per-machine corpus."""
    usage_dir = repo_root / run_record.USAGE_DIR
    usage_dir.mkdir(parents=True, exist_ok=True)
    (repo_root / run_record.RUN_RECORDS_FILE).write_text(json.dumps(records), encoding="utf-8")


def _write_tracker(repo_root: Path, records: dict[str, list[dict]]) -> None:
    """Seed the committed tracker export with one ``[harness-run]`` marker per dispatch.

    The travelling corpus: this is what a fresh clone has, and it is written as real
    marker comments rather than injected further down so the report is exercised through
    the same reader a teammate's clone would use.
    """
    beads = repo_root / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({
            "id": bead_id,
            "comments": [
                {"text": f"{MARKER} id={bead_id}#run-{index}\n{json.dumps(entry)}"}
                for index, entry in enumerate(entries)
            ],
        })
        for bead_id, entries in records.items()
    ]
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _lane(stamp: str, **fields: object) -> dict:
    """One executed write dispatch, with only the fields a test cares about set."""
    return {
        "agent": "claude",
        "outcome": run_record.EXECUTED,
        "phase": run_record.LANE_PHASE,
        "timestamp": stamp,
        **fields,
    }


def _parameter(report: tuning.TuningReport, key: str) -> tuning.ParameterTuning:
    """The row for *key*, failing loudly when the report omits a governed parameter."""
    for parameter in report.parameters:
        if parameter.key == key:
            return parameter
    raise AssertionError(f"{key} is missing from {[p.key for p in report.parameters]}")


def test_every_governed_parameter_is_listed_on_an_empty_corpus(tmp_path: Path) -> None:
    """No dispatches at all: every parameter still prints its value in force (AC5).

    The failure this forbids is the report that only lists what it can advise on — a
    parameter dropped for want of evidence reads identically to one that is fine.
    """
    report = tuning.tuning_report(tmp_path)

    assert report.dispatches == 0
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
    """`stall_after` has no ledger signal even when the corpus is full (AC5)."""
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
    # The row has to say *why* it cannot advise, or "no evidence" is indistinguishable
    # from "no problem" — the state that let `quiet_after` be declared unchallenged.
    assert "no dispatch record carries a signal" in unobserved.basis


def test_a_measured_recommendation_carries_its_statistic_and_sample_size(
    tmp_path: Path,
) -> None:
    """Past the minimum, `runner_timeout` is the worst run with headroom (AC1)."""
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
    # The value that governed the dispatches, not just the one configured now.
    assert [cohort.in_force for cohort in parameter.cohorts] == ["3600"]


def test_a_sample_under_the_minimum_is_seeded_from_the_prior(tmp_path: Path) -> None:
    """Three dispatches is history, not evidence: the declared prior stands (AC2).

    The number the statistic *would* have produced is asserted absent. A recommendation
    fitted to three samples and merely labelled "seeded" is still read as a measurement
    by anyone skimming the column, which is the whole failure mode.
    """
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
    # "the prior it would displace": the value in force is carried on the same row.
    assert parameter.in_force == 3600.0


def test_both_corpora_are_read_and_each_sample_names_its_source(tmp_path: Path) -> None:
    """Local-only, tracker-only and shared dispatches are all read, and labelled (AC4).

    ``.basicly/usage/`` never leaves the machine that wrote it while a ``[harness-run]``
    marker travels with a clone, so a reader deciding whether a recommendation is
    shareable needs to know which side each sample came from.
    """
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

    # Three dispatches, not four: the shared one is one sample, labelled `both`.
    assert report.dispatches == 3
    assert report.sources == {tuning.BOTH: 1, tuning.LOCAL: 1, tuning.TRACKER: 1}
    parameter = _parameter(report, "runner.runner_timeout")
    assert parameter.sources == {tuning.BOTH: 1, tuning.LOCAL: 1, tuning.TRACKER: 1}
    assert {(item.bead, item.source) for item in parameter.observations} == {
        ("b-1", tuning.BOTH),
        ("b-2", tuning.TRACKER),
        ("b-3", tuning.LOCAL),
    }


def test_the_report_writes_nothing(tmp_path: Path) -> None:
    """Advisory, not self-modifying: every file is byte-identical afterwards (AC3).

    Hashed over the whole tree rather than over ``basicly.toml`` alone, so a tuner that
    "helpfully" rewrote the local overlay, the usage file or the tracker export would
    fail this too.
    """
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

    assert report.dispatches == 2, "the report must have really read the corpus"
    assert _tree_digest(tmp_path) == before


def _tree_digest(root: Path) -> dict[str, str]:
    """Every file under *root* as path -> content digest, for a byte-identity check."""
    return {
        str(path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_configured_value_is_the_one_reported_in_force(tmp_path: Path) -> None:
    """A repo that declares a value is reported against that value, not the default."""
    (tmp_path / "basicly.toml").write_text(
        "[worktree]\nconcurrency = 3\n\n[policy.sizing]\nworking_set_max = 99000\n",
        encoding="utf-8",
    )

    report = tuning.tuning_report(tmp_path)

    concurrency = _parameter(report, "worktree.concurrency")
    assert concurrency.in_force == 3.0
    # The prior stays the shipped default, so a reader can audit the declared value
    # against the seed it displaced.
    assert concurrency.prior == float(config.DEFAULT_WORKTREE_CONCURRENCY)
    assert _parameter(report, "policy.sizing.working_set_max").in_force == 99000.0


def test_a_session_override_makes_its_own_cohort(tmp_path: Path) -> None:
    """Dispatches run under an override are not pooled with dispatches that were not.

    An override changes what a dispatch is while every committed file stays identical,
    so pooling would report an outcome distribution under a value that never governed
    half of it.
    """
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
    """A judge, a decider and a phase nobody recorded are all excluded.

    Same rule the spend calibration keeps: a helper reads and answers, it does not do a
    node's build, and unknown provenance fails closed rather than being assumed a lane.
    """
    _write_local(
        tmp_path,
        {"b-1": [{**_lane("2026-07-10T09:00:00+00:00", duration_s=100.0), "phase": phase}]},
    )

    report = tuning.tuning_report(tmp_path)

    assert report.dispatches == 1, "the dispatch is read; it just is not evidence"
    assert _parameter(report, "runner.runner_timeout").samples == 0


def test_a_handoff_is_not_evidence_of_how_long_work_takes(tmp_path: Path) -> None:
    """Nothing executed, so its duration is not an observation of a lane's runtime."""
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
    """`max_rework` is advised from write dispatches per bead, less the first attempt."""
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
    # b-1 numbers 1..3 and b-2 numbers 1..7; the 0.9 quantile of those ten attempt
    # numbers is 6, and the first attempt is not rework.
    assert parameter.recommendation == 5.0


def test_the_build_factor_is_fitted_to_measured_working_set(tmp_path: Path) -> None:
    """The ratio is context_tokens over scope_tokens, per class, and never over spend."""
    _write_local(
        tmp_path,
        {
            "b-1": [
                _lane(
                    f"2026-07-{day:02d}T09:00:00+00:00",
                    task_class="task",
                    scope_tokens=1_000,
                    context_tokens=4_000,
                    # Spend on the same record, orders of magnitude larger. A factor
                    # fitted to this instead is the 216x defect basicly-z2wi removed.
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
    """A context ceiling above 1.0 is not a fraction; the backstop clamps."""
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
    """It can be neither deduplicated nor ordered, so it is not counted.

    A sample that might be a duplicate is worse than a missing one in a report whose
    whole claim is its sample size.
    """
    _write_local(
        tmp_path,
        {
            "b-1": [
                {"agent": "claude", "outcome": run_record.EXECUTED, "phase": "lane"},
                _lane("2026-07-10T09:00:00+00:00", duration_s=100.0),
            ]
        },
    )

    assert tuning.tuning_report(tmp_path).dispatches == 1


def test_a_corrupt_corpus_reads_as_no_evidence(tmp_path: Path) -> None:
    """Telemetry is read best-effort everywhere; a bad file is never a crash."""
    usage_dir = tmp_path / run_record.USAGE_DIR
    usage_dir.mkdir(parents=True)
    (tmp_path / run_record.RUN_RECORDS_FILE).write_text("{not json", encoding="utf-8")
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "issues.jsonl").write_text("not json either\n", encoding="utf-8")

    report = tuning.tuning_report(tmp_path)

    assert report.dispatches == 0
    assert all(parameter.recommendation is None for parameter in report.parameters)


def test_render_value_prints_a_value_a_reader_can_paste() -> None:
    """Whole numbers lose the decimal point; fractions keep four significant figures."""
    assert tuning.render_value(3600.0) == "3600"
    assert tuning.render_value(0.6) == "0.6"
    assert tuning.render_value(222_481.0) == "222481"
    assert tuning.render_value(10.6789123) == "10.68"
