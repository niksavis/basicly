from __future__ import annotations

from pathlib import Path

import pytest

from basicly import cost_rollup, decompose, policy, run_record


@pytest.fixture
def marker(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    written: list[dict] = []

    def _record(_root: Path, issue: str, **fields: object) -> str:
        written.append({"issue": issue, **fields})
        return "marker-1"

    monkeypatch.setattr(run_record, "record_cost_marker", _record)
    monkeypatch.setattr(policy, "rework_recorded", lambda _root, _issue: 1)
    monkeypatch.setattr(decompose, "bead_class_and_scope", lambda _root, _issue: None)
    return written


def _sizing(source: str) -> decompose.DispatchSizing:
    return decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(
            scope_tokens=8_000, overhead_tokens=2_000, build_factor=2.0
        ),
        source=source,
    )


def _resolved(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    monkeypatch.setattr(
        decompose,
        "resolve_dispatch_sizing",
        lambda _root, _issue: decompose.SizingLookup(sizing=_sizing(source)),
    )


def test_a_sized_package_records_the_forecast_it_was_priced_with(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {"basicly-a": [object()]})
    monkeypatch.setattr(run_record, "cost_rollup", lambda _history, rework: {"rework": rework})
    _resolved(monkeypatch, decompose.FROZEN_FORECAST)

    assert cost_rollup.record(tmp_path, "basicly-a")
    forecast = marker[0]["forecast"]
    assert forecast.tokens == 18_000
    assert forecast.source == decompose.FROZEN_FORECAST
    assert marker[0]["task_class"] == "task" and marker[0]["scope_tokens"] == 8_000


def test_a_forecast_computed_after_the_fact_is_not_labelled_a_dispatch_forecast(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {"basicly-a": [object()]})
    monkeypatch.setattr(run_record, "cost_rollup", lambda _history, rework: {"rework": rework})
    _resolved(monkeypatch, decompose.DISPATCH_FORECAST)

    assert cost_rollup.record(tmp_path, "basicly-a")
    assert marker[0]["forecast"].source == cost_rollup.ROLLUP_FORECAST
    assert marker[0]["forecast"].source != decompose.DISPATCH_FORECAST


def test_an_unsized_package_records_which_absence_explains_it(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
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

    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {})

    assert cost_rollup.record(tmp_path, "basicly-a") is False
    assert marker == []


def test_a_dispatched_package_records_its_actual(
    tmp_path: Path, marker: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_record, "dispatch_history", lambda _root: {"basicly-a": [object()]})
    monkeypatch.setattr(run_record, "cost_rollup", lambda _history, rework: {"rework": rework})

    assert cost_rollup.record(tmp_path, "basicly-a") is True
    assert marker[0]["issue"] == "basicly-a"
    assert marker[0]["task_class"] is None


def test_a_tracker_failure_is_swallowed_because_the_package_has_already_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _boom(_root: Path) -> dict:
        raise RuntimeError("the tracker is unreachable")

    monkeypatch.setattr(run_record, "dispatch_history", _boom)

    assert cost_rollup.record(tmp_path, "basicly-a") is False
