"""The shipped package's forecast-vs-actual rollup, and the three times it writes nothing.

Split out of `test_loop.py` with the module itself. The ship advance's own behaviour —
that a failed rollup never blocks a close — stays there, because that is a fact about
shipping; what is here is which inputs produce a rollup at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import cost_rollup, decompose, policy, run_record


@pytest.fixture
def marker(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture what would be written, so no test needs a tracker."""
    written: list[dict] = []

    def _record(_root: Path, issue: str, **fields: object) -> str:
        written.append({"issue": issue, **fields})
        return "marker-1"

    monkeypatch.setattr(run_record, "record_cost_marker", _record)
    monkeypatch.setattr(policy, "rework_recorded", lambda _root, _issue: 1)
    monkeypatch.setattr(decompose, "bead_class_and_scope", lambda _root, _issue: None)
    return written


def _sizing(source: str) -> decompose.DispatchSizing:
    """A resolved dispatch sizing whose forecast provenance is *source*."""
    return decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(
            scope_tokens=8_000, overhead_tokens=2_000, build_factor=2.0
        ),
        source=source,
    )


def _resolved(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    """Resolve every package as a sized dispatch whose forecast came from *source*."""
    monkeypatch.setattr(
        decompose,
        "resolve_dispatch_sizing",
        lambda _root, _issue: decompose.SizingLookup(sizing=_sizing(source)),
    )


def test_a_sized_package_records_the_forecast_it_was_priced_with(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dispatch resolution answers, so no rollup carries a bare null (basicly-agzx.4).

    The ownership-scope lookup this module used to make is keyed on different
    globs from the estimate it was searching for, so it missed on every bead
    declaring a ``## Working Set`` — 185 of 202 landed packages.
    """
    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {"basicly-a": [object()]})
    monkeypatch.setattr(run_record, "cost_rollup", lambda _history, rework: {"rework": rework})
    _resolved(monkeypatch, decompose.FROZEN_FORECAST)

    assert cost_rollup.record(tmp_path, "basicly-a")
    forecast = marker[0]["forecast"]
    assert forecast.tokens == 18_000  # 2_000 overhead + 8_000 x 2.0
    assert forecast.source == decompose.FROZEN_FORECAST
    assert marker[0]["task_class"] == "task" and marker[0]["scope_tokens"] == 8_000


def test_a_forecast_computed_after_the_fact_is_not_labelled_a_dispatch_forecast(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolution with no frozen estimate prices with *today's* factors.

    This module runs after the merge, so reusing the dispatch label would let a
    consumer pair a past actual against a present estimator and read the
    difference as forecast error.
    """
    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {"basicly-a": [object()]})
    monkeypatch.setattr(run_record, "cost_rollup", lambda _history, rework: {"rework": rework})
    _resolved(monkeypatch, decompose.DISPATCH_FORECAST)

    assert cost_rollup.record(tmp_path, "basicly-a")
    assert marker[0]["forecast"].source == cost_rollup.ROLLUP_FORECAST
    assert marker[0]["forecast"].source != decompose.DISPATCH_FORECAST


def test_an_unsized_package_records_which_absence_explains_it(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A null forecast with no reason cannot be told from a lookup that missed."""
    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {"basicly-a": [object()]})
    monkeypatch.setattr(run_record, "cost_rollup", lambda _history, rework: {"rework": rework})
    monkeypatch.setattr(
        decompose,
        "resolve_dispatch_sizing",
        lambda _root, _issue: decompose.SizingLookup(
            sizing=None, absence=decompose.SCOPE_UNDECLARED
        ),
    )

    assert cost_rollup.record(tmp_path, "basicly-a")
    forecast = marker[0]["forecast"]
    assert forecast.tokens is None
    assert forecast.source == decompose.SCOPE_UNDECLARED


def test_a_node_that_was_never_dispatched_gets_no_rollup(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decomposed feature's cost is its children's, so counting it double-counts.

    It would also dilute cost-per-landed-package with a null, which is the figure the
    sizing governor calibrates against.
    """
    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {})

    assert cost_rollup.record(tmp_path, "basicly-a") is False
    assert marker == []


def test_a_dispatched_package_records_its_actual(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control: without it every assertion above passes on a dead function."""
    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {"basicly-a": [object()]})
    monkeypatch.setattr(run_record, "cost_rollup", lambda _history, rework: {"rework": rework})

    assert cost_rollup.record(tmp_path, "basicly-a") is True
    assert marker[0]["issue"] == "basicly-a"
    assert marker[0]["task_class"] is None


def test_a_tracker_failure_is_swallowed_because_the_package_has_already_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence is never worth failing a landing for — the ship has to proceed."""

    def _boom(_root: Path) -> dict:
        raise RuntimeError("the tracker is unreachable")

    monkeypatch.setattr(run_record, "dispatch_history", _boom)

    assert cost_rollup.record(tmp_path, "basicly-a") is False
