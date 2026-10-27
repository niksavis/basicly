from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def load_results() -> Any:
    path = Path(__file__).resolve().parents[1] / ".scripts/harness_trial_results.py"
    spec = importlib.util.spec_from_file_location("harness_trial_results", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trial(identifier: str, **changes: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "family": "codex",
        "model": "fixture-model",
        "effort": "medium",
        "arm": "baseline",
        "case": "boundary",
        "outcome": "passed",
        "duration_s": 10.0,
        "tokens": 100,
        "cost": 0.1,
        **changes,
    }


def test_harness_trial_results_include_failed_attempt_costs() -> None:
    result = load_results().summarize([
        trial("one"),
        trial("two", outcome="failed", cost=0.2, duration_s=30.0),
        trial("three", outcome="infrastructure_error", cost=0.3),
    ])[0]
    assert result["attempts"] == 3
    assert result["passed"] == 1
    assert result["outcomes"]["infrastructure_error"] == 1
    assert result["cost_per_success"] == pytest.approx(0.6)
    assert result["duration_s"]["median"] == 10.0
    assert result["duration_s"]["observed_max"] == 30.0
    assert "p95" not in result["duration_s"]


def test_harness_trial_results_do_not_fill_unknowns_with_zero() -> None:
    result = load_results().summarize([trial("one"), trial("two", cost=None, tokens=None)])[0]
    assert result["cost"]["known_sum"] == 0.1
    assert result["cost"]["missing"] == 1
    assert result["cost_per_success"] is None
    assert result["tokens_per_success"] is None


def test_harness_trial_results_zero_success_has_no_unit_cost() -> None:
    result = load_results().summarize([trial("one", outcome="timed_out")])[0]
    assert result["passed"] == 0
    assert result["cost_per_success"] is None


def test_harness_trial_results_separate_acceptance_from_process_success() -> None:
    result = load_results().summarize([
        trial("one", outcome="timed_out", acceptance={"outcome": "passed"}),
        trial("two", outcome="infrastructure_error", acceptance={"outcome": "failed"}),
        trial("three", outcome="infrastructure_error"),
    ])[0]
    assert result["passed"] == 0
    assert result["acceptance_outcomes"] == {"failed": 1, "passed": 1}
    assert result["acceptance_missing"] == 1
    assert result["cost_per_success"] is None


@pytest.mark.parametrize("acceptance", [True, {}, {"outcome": "timed_out"}])
def test_harness_trial_results_refuse_invalid_acceptance(acceptance: Any) -> None:
    with pytest.raises(ValueError, match="acceptance"):
        load_results().summarize([trial("one", acceptance=acceptance)])


def test_harness_trial_results_keep_models_cases_and_arms_separate() -> None:
    rows = [
        trial("one"),
        trial("two", model="other-model"),
        trial("three", arm="candidate"),
        trial("four", case="repair"),
    ]
    assert len(load_results().summarize(rows)) == 4


@pytest.mark.parametrize(
    "field,value", [("cost", -1), ("tokens", True), ("duration_s", float("nan"))]
)
def test_harness_trial_results_reject_invalid_measurements(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        load_results().summarize([trial("one", **{field: value})])


def test_harness_trial_results_reject_duplicate_attempts() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_results().summarize([trial("one"), trial("one")])


def test_harness_trial_results_reject_empty_evidence() -> None:
    with pytest.raises(ValueError, match="no trials"):
        load_results().summarize([])
