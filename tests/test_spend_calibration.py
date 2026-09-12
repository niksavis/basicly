from __future__ import annotations

import pytest

from basicly import run_record, spend_calibration


def _pair(**overrides) -> run_record.ForecastError:
    fields = {
        "bead": "b-1",
        "timestamp": "2026-07-26T09:00:00+00:00",
        "forecast_tokens": 50_000,
        "actual_tokens": 10_000_000,
        "task_class": "task",
        "model": "claude-opus-5",
        "actual_cost": 8.0,
        "actual_wall_clock_s": 1_000.0,
        "phase": run_record.LANE_PHASE,
    }
    fields.update(overrides)
    return run_record.ForecastError(**fields)


def _report(*pairs: run_record.ForecastError) -> run_record.ForecastErrorReport:
    return run_record.ForecastErrorReport(errors=pairs)


def _calibrate(report: run_record.ForecastErrorReport, **overrides):
    kwargs = {
        "model": "claude-opus-5",
        "task_class": "task",
        "min_samples": 3,
        "window": 50,
    }
    kwargs.update(overrides)
    return spend_calibration.calibrate_spend(report, **kwargs)


def test_calibrate_spend_seeds_every_ratio_from_the_declared_prior() -> None:
    calibration = _calibrate(_report())
    prior = spend_calibration.DECLARED_SPEND_PRIOR
    assert calibration.tokens_per_working_set_token.value == prior.tokens_per_working_set_token
    assert calibration.usd_per_million_tokens.value == prior.usd_per_million_tokens
    assert calibration.seconds_per_million_tokens.value == prior.seconds_per_million_tokens
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO
    assert calibration.measured is False
    assert calibration.prior is prior
    assert calibration.pairs == 0


def test_calibrate_spend_replaces_the_prior_past_the_minimum() -> None:
    pairs = (
        _pair(actual_tokens=5_000_000, actual_cost=5.0, actual_wall_clock_s=250.0),
        _pair(actual_tokens=10_000_000, actual_cost=8.0, actual_wall_clock_s=1_000.0),
        _pair(actual_tokens=20_000_000, actual_cost=24.0, actual_wall_clock_s=4_000.0),
    )
    calibration = _calibrate(_report(*pairs))
    assert calibration.measured is True
    assert calibration.tokens_per_working_set_token.value == pytest.approx(200.0)
    assert calibration.tokens_per_working_set_token.source == spend_calibration.MEASURED_RATIO
    assert calibration.tokens_per_working_set_token.samples == 3
    assert calibration.usd_per_million_tokens.value == pytest.approx(1.0)
    assert calibration.seconds_per_million_tokens.value == pytest.approx(100.0)
    assert calibration.pairs == 3


def test_calibrate_spend_holds_the_prior_below_the_minimum() -> None:
    calibration = _calibrate(_report(_pair(), _pair()))
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO
    assert calibration.tokens_per_working_set_token.samples == 2
    assert calibration.pairs == 2


def test_calibrate_spend_never_borrows_another_models_history() -> None:
    pairs = tuple(_pair(model="some-other-model") for _ in range(5))
    calibration = _calibrate(_report(*pairs))
    assert calibration.pairs == 0
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO


def test_calibrate_spend_refuses_the_records_with_no_model_recorded() -> None:

    pairs = tuple(_pair(model=None) for _ in range(5))
    assert _calibrate(_report(*pairs), model=None).pairs == 0
    assert _calibrate(_report(*pairs)).pairs == 0


def test_calibrate_spend_never_borrows_another_task_class() -> None:
    pairs = tuple(_pair(task_class="chore") for _ in range(5))
    assert _calibrate(_report(*pairs)).pairs == 0


def test_calibrate_spend_excludes_a_chars_over_four_estimate() -> None:
    pairs = tuple(_pair(estimated=True) for _ in range(5))
    assert _calibrate(_report(*pairs)).pairs == 0


@pytest.mark.parametrize(
    "phase",
    [run_record.VALIDATE_PHASE, run_record.DECIDE_PHASE, None, "", "probe"],
)
def test_calibrate_spend_refuses_a_dispatch_that_is_not_a_lane(phase: str | None) -> None:

    pairs = tuple(_pair(phase=phase) for _ in range(5))
    calibration = _calibrate(_report(*pairs))
    assert calibration.pairs == 0
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO


def test_calibrate_spend_keeps_only_the_newest_window() -> None:
    old = tuple(
        _pair(timestamp=f"2026-07-0{day}T09:00:00+00:00", actual_tokens=50_000_000)
        for day in (1, 2, 3)
    )
    recent = tuple(
        _pair(timestamp=f"2026-07-2{day}T09:00:00+00:00", actual_tokens=10_000_000)
        for day in (4, 5, 6)
    )
    calibration = _calibrate(_report(*old, *recent), window=3)
    assert calibration.pairs == 3
    assert calibration.tokens_per_working_set_token.value == pytest.approx(200.0)


def test_calibrate_spend_measures_tokens_while_money_stays_seeded() -> None:

    pairs = tuple(_pair(actual_cost=None) for _ in range(3))
    calibration = _calibrate(_report(*pairs))
    assert calibration.tokens_per_working_set_token.source == spend_calibration.MEASURED_RATIO
    assert calibration.usd_per_million_tokens.source == spend_calibration.PRIOR_RATIO
    assert calibration.usd_per_million_tokens.samples == 0
    assert calibration.measured is True


def test_calibrate_spend_reports_no_number_for_an_undeclared_ratio() -> None:
    undeclared = spend_calibration.SpendPrior(
        tokens_per_working_set_token=None,
        usd_per_million_tokens=None,
        seconds_per_million_tokens=None,
        basis="nothing declared",
    )
    calibration = _calibrate(_report(), prior=undeclared)
    assert calibration.tokens_per_working_set_token.value is None
    assert calibration.tokens_per_working_set_token.source == spend_calibration.UNDECLARED_RATIO
    assert calibration.measured is False


def test_the_declared_prior_matches_the_measured_proof_run() -> None:

    prior = spend_calibration.DECLARED_SPEND_PRIOR
    assert prior.tokens_per_working_set_token == pytest.approx(16_002_352 / 47_847, rel=1e-3)
    assert prior.usd_per_million_tokens == pytest.approx(11.729733 / 16.002352, rel=1e-3)
    assert prior.seconds_per_million_tokens == pytest.approx(1_474.7 / 16.002352, rel=1e-3)
    assert "basicly-u6jq.1" in prior.basis


def test_calibrate_spend_never_medians_an_empty_sample_set() -> None:
    calibration = _calibrate(_report(), min_samples=0)
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO
    assert calibration.pairs == 0
