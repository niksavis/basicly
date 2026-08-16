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
