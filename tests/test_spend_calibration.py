"""Tests for the spend calibration (basicly-jr0l.21).

The three ratios that turn a working-set estimate into predicted spend, and the
rule that a seeded ratio never reads as a measured one. Split out of
``test_run_record.py`` with :mod:`basicly.spend_calibration` itself, and renamed
here from ``test_run_record_spend.py`` when the §9.4 naming gate was made binding
(basicly-u2hl.14) — the file was already this module's, under the name of the one
it was extracted from. The samples are still ``run_record.ForecastError`` values,
because that is the recorded type the protocol this module calibrates from is
satisfied by.

The phase filter these tests rely on is :mod:`basicly.dispatch_phase`'s, and its
vocabulary is pinned in ``test_dispatch_phase.py``; what is asserted here is that
the calibration *applies* it.
"""

from __future__ import annotations

import pytest

from basicly import run_record, spend_calibration


def _pair(**overrides) -> run_record.ForecastError:
    """One paired record: 200x the forecast, at 0.80 USD and 100 s per million tokens."""
    fields = {
        "bead": "b-1",
        "timestamp": "2026-07-26T09:00:00+00:00",
        "forecast_tokens": 50_000,
        "actual_tokens": 10_000_000,
        "task_class": "task",
        "model": "claude-opus-5",
        "actual_cost": 8.0,
        "actual_wall_clock_s": 1_000.0,
        # A calibration sample must be a write dispatch (basicly-tcmy.5); the helper
        # builds the eligible shape so a test about a *ratio* is not silently a test
        # about the phase filter.
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
    """With no history the declared prior stands, and says so on every ratio."""
    calibration = _calibrate(_report())
    prior = spend_calibration.DECLARED_SPEND_PRIOR
    assert calibration.tokens_per_working_set_token.value == prior.tokens_per_working_set_token
    assert calibration.usd_per_million_tokens.value == prior.usd_per_million_tokens
    assert calibration.seconds_per_million_tokens.value == prior.seconds_per_million_tokens
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO
    assert calibration.measured is False
    # The prior travels with the forecast: a seeded number that reads as measured is
    # worse than no number.
    assert calibration.prior is prior
    assert calibration.pairs == 0


def test_calibrate_spend_replaces_the_prior_past_the_minimum() -> None:
    """Past min_samples the measured median per (model, class) replaces every ratio."""
    pairs = (
        _pair(actual_tokens=5_000_000, actual_cost=5.0, actual_wall_clock_s=250.0),
        _pair(actual_tokens=10_000_000, actual_cost=8.0, actual_wall_clock_s=1_000.0),
        _pair(actual_tokens=20_000_000, actual_cost=24.0, actual_wall_clock_s=4_000.0),
    )
    calibration = _calibrate(_report(*pairs))
    assert calibration.measured is True
    # Ratios 100x / 200x / 400x -> median 200x, not the 233x mean.
    assert calibration.tokens_per_working_set_token.value == pytest.approx(200.0)
    assert calibration.tokens_per_working_set_token.source == spend_calibration.MEASURED_RATIO
    assert calibration.tokens_per_working_set_token.samples == 3
    # 1.00 / 0.80 / 1.20 USD per million -> median 1.00.
    assert calibration.usd_per_million_tokens.value == pytest.approx(1.0)
    # 50 / 100 / 200 seconds per million -> median 100.
    assert calibration.seconds_per_million_tokens.value == pytest.approx(100.0)
    assert calibration.pairs == 3


def test_calibrate_spend_holds_the_prior_below_the_minimum() -> None:
    """Two pairs under a minimum of three leave the seed in place, counted honestly."""
    calibration = _calibrate(_report(_pair(), _pair()))
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO
    assert calibration.tokens_per_working_set_token.samples == 2
    assert calibration.pairs == 2


def test_calibrate_spend_never_borrows_another_models_history() -> None:
    """The same work costs different amounts per model, so a foreign sample cannot key in."""
    pairs = tuple(_pair(model="some-other-model") for _ in range(5))
    calibration = _calibrate(_report(*pairs))
    assert calibration.pairs == 0
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO


def test_calibrate_spend_refuses_the_records_with_no_model_recorded() -> None:
    """An unrecorded model is unknown provenance, not a match for an unresolved one.

    All 122 historical records carried a null model (basicly-kjc5.59), and pooling
    those would rebuild exactly the cross-model average this keying exists to avoid.
    """
    pairs = tuple(_pair(model=None) for _ in range(5))
    assert _calibrate(_report(*pairs), model=None).pairs == 0
    assert _calibrate(_report(*pairs)).pairs == 0


def test_calibrate_spend_never_borrows_another_task_class() -> None:
    """A chore's spend does not predict a task's, so the class is part of the key."""
    pairs = tuple(_pair(task_class="chore") for _ in range(5))
    assert _calibrate(_report(*pairs)).pairs == 0


def test_calibrate_spend_excludes_a_chars_over_four_estimate() -> None:
    """A chars/4 actual is too weak to calibrate money with (design 7.5)."""
    pairs = tuple(_pair(estimated=True) for _ in range(5))
    assert _calibrate(_report(*pairs)).pairs == 0


@pytest.mark.parametrize(
    "phase",
    [run_record.VALIDATE_PHASE, run_record.DECIDE_PHASE, None, "", "probe"],
)
def test_calibrate_spend_refuses_a_dispatch_that_is_not_a_lane(phase: str | None) -> None:
    """AC: a rubric judge and the decider are not samples of what the work costs.

    They are dispatched on the same bead and land in the same record stream, so a
    filter on model and class alone admits them (basicly-tcmy.5) — and the ratio would
    then be a helper's spend over a lane's working set, dragging the multiplier the
    band and the budget are both computed from. A record whose phase was never written
    is refused on the same rule: unknown provenance fails closed.
    """
    pairs = tuple(_pair(phase=phase) for _ in range(5))
    calibration = _calibrate(_report(*pairs))
    assert calibration.pairs == 0
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO


def test_calibrate_spend_keeps_only_the_newest_window() -> None:
    """The window is a rolling one: an old sample must not outvote the recent ones."""
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
    """History accumulates unevenly, so each ratio names its own source.

    An adapter that bills in credits reports no USD at all (basicly-2rn9), so a
    forecast is routinely measured in tokens and still seeded in money.
    """
    pairs = tuple(_pair(actual_cost=None) for _ in range(3))
    calibration = _calibrate(_report(*pairs))
    assert calibration.tokens_per_working_set_token.source == spend_calibration.MEASURED_RATIO
    assert calibration.usd_per_million_tokens.source == spend_calibration.PRIOR_RATIO
    assert calibration.usd_per_million_tokens.samples == 0
    assert calibration.measured is True


def test_calibrate_spend_reports_no_number_for_an_undeclared_ratio() -> None:
    """No prior and no history is indeterminate — it must not publish a confident zero."""
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
    """The seed is derived, not invented: the medians of the three metered packages.

    Pinned so the numbers cannot be quietly edited away from the evidence in
    basicly-jr0l.21 — the lanes are kjc5.32/.50/.51 and the medians are the middle
    lane's token multiplier, USD per million and seconds per million.
    """
    prior = spend_calibration.DECLARED_SPEND_PRIOR
    assert prior.tokens_per_working_set_token == pytest.approx(16_002_352 / 47_847, rel=1e-3)
    assert prior.usd_per_million_tokens == pytest.approx(11.729733 / 16.002352, rel=1e-3)
    assert prior.seconds_per_million_tokens == pytest.approx(1_474.7 / 16.002352, rel=1e-3)
    assert "basicly-u6jq.1" in prior.basis


def test_calibrate_spend_never_medians_an_empty_sample_set() -> None:
    """A minimum of zero must not make "no samples" satisfy the minimum and raise."""
    calibration = _calibrate(_report(), min_samples=0)
    assert calibration.tokens_per_working_set_token.source == spend_calibration.PRIOR_RATIO
    assert calibration.pairs == 0
