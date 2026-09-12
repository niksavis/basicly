from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import policy, runner, supervise
from tests.test_supervise import _executed_outcome, _lane, _session, decisions_item

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _limited(issue_id: str) -> supervise.LaneOutcome:
    said = "You've hit your session limit · resets 5:50pm (Europe/Vienna)"
    return _executed_outcome(
        issue_id,
        returncode=1,
        detail=f"provider usage limit refused the dispatch: {said}",
        provider_refusal=said,
    )


def test_a_provider_limit_refusal_holds_the_lane_and_charges_no_rework(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    charged: list[str] = []
    monkeypatch.setattr(
        supervise.policy, "record_rework", lambda _r, _i, gate: charged.append(gate) or 1
    )
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, question, *_a: (
            queued.append((kind, question)),
            decisions_item(issue, kind),
        )[1],
    )

    routed = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (_limited("epic.1"),))

    assert [r.route for r in routed] == ["decision"]
    assert charged == []
    assert queued == [("escalation", supervise.provider_limit.LIMIT_QUESTION)]
    assert "resets 5:50pm" in routed[0].detail


def test_a_pass_whose_only_outcome_is_a_provider_refusal_stops_dispatching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(supervise.policy, "record_rework", lambda _r, _i, _g: 1)
    monkeypatch.setattr(
        supervise.decisions, "enqueue", lambda _r, issue, kind, *_a: decisions_item(issue, kind)
    )

    routed = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (_limited("epic.1"),))

    assert not supervise.should_continue(routed)


def test_the_provider_gate_refuses_every_lane_still_waiting_for_a_slot() -> None:
    gate = supervise.ProviderGate()

    assert gate.declined("epic.2", "claude") is None
    gate.latch(_limited("epic.1"))
    declined = gate.declined("epic.2", "claude")

    assert declined is not None
    assert declined.refused and declined.result is None
    assert supervise.provider_limit.LIMIT_QUESTION in declined.detail


def test_the_gate_stays_open_for_a_lane_that_merely_failed() -> None:
    gate = supervise.ProviderGate()

    gate.latch(_executed_outcome("epic.1", returncode=3, detail="runner exited 3"))

    assert gate.declined("epic.2", "claude") is None


def test_the_pass_summary_reports_what_each_dispatch_spent() -> None:
    said: list[str] = []
    outcomes = (
        _executed_outcome("epic.1", spend=runner.Usage(4689345, cost=4.29, estimated=False)),
        _executed_outcome("epic.2", spend=runner.Usage(612, cost=None, estimated=True)),
    )

    supervise.say_dispatch(
        outcomes,
        carried=frozenset(),
        admission=policy.SpendStatus(grant=None, spent_tokens=0, halted=False),
        say=said.append,
    )

    assert "4689345 tokens" in said[0]
    assert "612 tokens (estimated)" in said[1]
    assert "spent:    4689957 tokens this pass, over 2 dispatch(es)" in said


def test_a_pass_that_metered_nothing_prints_no_spend_line() -> None:
    said: list[str] = []

    supervise.say_dispatch(
        (_executed_outcome("epic.1"),),
        carried=frozenset(),
        admission=policy.SpendStatus(grant=None, spent_tokens=0, halted=False),
        say=said.append,
    )

    assert not any(line.startswith("spent:") for line in said)
    assert "tokens" not in said[0]
