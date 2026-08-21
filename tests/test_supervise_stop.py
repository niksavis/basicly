"""Tests for stopping a supervisor session: `loop stop`, and `--max-passes`.

A working supervisor had no stop short of a signal, and the lanes are `claude -p`
subprocesses of it — so a signal leaves them killed mid-write or orphaned against a
grant nothing meters (basicly-o40x). These pin the alternative: a marker read at the
round boundary, the pass limit beside it, and the record of who asked for the stop.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import cli, supervise
from basicly.policy import SpendStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


# --- Stop request: the round boundary, not a signal (basicly-o40x) ------------


def test_a_stop_marker_records_who_asked_and_why(tmp_path: Path) -> None:
    """AC: the session's stop is inspectable afterwards, naming its requester.

    Read-and-clear, because the marker outlives the process it stops: left behind, it
    would end the next supervisor started here before it had run a round.
    """
    path = supervise.request_stop(tmp_path, "epic", requested_by="operator", reason="budget")

    assert json.loads(path.read_text(encoding="utf-8"))["requested_by"] == "operator"
    assert supervise.take_stop_request(tmp_path, "epic") == supervise.StopRequest(
        root_issue="epic", requested_by="operator", reason="budget"
    )
    assert not path.exists()
    assert supervise.take_stop_request(tmp_path, "epic") is None


def test_a_stop_asked_of_another_session_is_left_where_it_was(tmp_path: Path) -> None:
    """The lock is a repo singleton; a stop names the session it was asked of.

    Consuming another root's marker here would swallow the operator's request
    silently — the supervisor it was meant for would never see it.
    """
    supervise.request_stop(tmp_path, "other-epic", requested_by="operator", reason="budget")

    assert supervise.take_stop_request(tmp_path, "epic") is None
    assert supervise.take_stop_request(tmp_path, "other-epic") is not None


def test_holds_lock_is_false_once_a_successor_owns_the_path(tmp_path: Path) -> None:
    """What a stop waits on: ownership by content, as the heartbeat fences it."""
    supervise.acquire(tmp_path, "epic:first", "epic")

    assert supervise.holds_lock(tmp_path, "epic:first")
    assert not supervise.holds_lock(tmp_path, "epic:second")


def test_the_session_end_reason_names_the_bound_that_ended_it(tmp_path: Path) -> None:
    """Both bounds report at the same boundary, and neither reaches a running lane."""
    state = supervise.SessionState("epic", "open", (("epic.1", "open"), ("epic.2", "open")), ())

    assert supervise.session_end_reason(tmp_path, state, passes=1, limit=None) is None
    limited = supervise.session_end_reason(tmp_path, state, passes=1, limit=1)
    assert limited == "stopped:  --max-passes 1 reached after 1 pass(es), 2 child(ren) still open"

    supervise.request_stop(tmp_path, "epic", requested_by="operator", reason="budget")

    # The operator's reason wins over the bound they also happened to hit, and a lane
    # held unlanded is named rather than left to read as landed.
    asked = supervise.session_end_reason(
        tmp_path, state, passes=1, limit=1, carried=frozenset({"epic.2"})
    )
    assert (
        asked == "stopped:  requested by operator - budget; green and committed, not landed: epic.2"
    )


# --- The loop's own boundary ------------------------------------------------


class _Heartbeat:
    """A heartbeat that does nothing, so the loop under test owns no timing."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        """Accept the lock, the session id and the board wiring the command supplies."""

    def start(self) -> None:
        """Started and stopped by the command; there is nothing to beat."""

    def check(self) -> None:
        """The lock is never contended in a test, so this never raises."""

    def stop(self) -> None:
        """Nothing to join."""


def _lane_outcome(issue_id: str) -> supervise.LaneOutcome:
    return supervise.LaneOutcome(
        issue_id=issue_id,
        runner_name="manual",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        detail="dispatched",
    )


def _stub_rounds(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    mid_round: Callable[[], object] | None = None,
) -> None:
    """Run the *real* pass with each collaborator it calls stubbed to a fixed answer.

    The round has to be the real one. With ``_supervise_pass`` itself replaced, "the
    next round seeded zero lanes" degrades into "a function was not called again",
    which cannot see a lane seeded inside a pass. *mid_round* runs while both lanes
    are in flight — where an operator's stop actually arrives.
    """
    monkeypatch.setattr(cli.supervise, "HeartbeatThread", _Heartbeat)
    monkeypatch.setattr(cli.supervise, "new_session_id", lambda _root: "epic:0001")
    monkeypatch.setattr(
        cli.supervise,
        "derive_session",
        lambda *_a, **_k: supervise.SessionState(
            "epic", "open", (("epic.1", "open"), ("epic.2", "open")), ()
        ),
    )
    monkeypatch.setattr(
        cli.policy,
        "spend_status",
        lambda *_a, **_k: SpendStatus(grant=None, spent_tokens=0, halted=False),
    )
    for quiet in ("delegate_decisions", "propose_coupling_edges", "repair_stale_bindings"):
        monkeypatch.setattr(cli.supervise, quiet, lambda *_a, **_k: ())
    monkeypatch.setattr(cli.supervise, "advance_parked", lambda *_a, **_k: ())

    def seed(*_a: object, **_k: object) -> tuple[supervise.RoutedOutcome, ...]:
        calls.append("seed")
        return ()

    def dispatch(*_a: object, **_k: object) -> tuple[supervise.LaneOutcome, ...]:
        calls.append("dispatch")
        # Every round here makes progress and the session is never done, so a loop
        # that ignored its bound would hang the suite rather than fail it.
        if calls.count("dispatch") > 2:
            raise AssertionError("the supervisor ran past the bound under test")
        if mid_round is not None:
            mid_round()
        return (_lane_outcome("epic.1"), _lane_outcome("epic.2"))

    def route(
        _repo: Path,
        _state: supervise.SessionState,
        outcomes: tuple[supervise.LaneOutcome, ...],
        **_k: object,
    ) -> tuple[supervise.RoutedOutcome, ...]:
        calls.append("route")
        return tuple(
            supervise.RoutedOutcome(one.issue_id, "merged", "landed, ship pending")
            for one in outcomes
        )

    monkeypatch.setattr(cli.supervise, "seed_lanes", seed)
    monkeypatch.setattr(cli.supervise, "dispatch_lanes", dispatch)
    monkeypatch.setattr(cli.supervise, "route_outcomes", route)


def test_a_stop_lands_the_round_in_flight_then_seeds_no_further_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC: a stop requested with lanes in flight costs none of their work.

    Asked for from inside the dispatch, which is the moment a signal to the
    supervisor would have killed two running agents. Both lanes still reach a
    terminal route, and the round after it seeds nothing at all — a marker is only
    ever read between rounds.
    """
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    _stub_rounds(
        monkeypatch,
        calls,
        mid_round=lambda: supervise.request_stop(
            tmp_path, "epic", requested_by="operator", reason="the grant is nearly spent"
        ),
    )

    code = cli._cmd_loop_supervise(argparse.Namespace(issue="epic", label=None))

    out = capsys.readouterr().out
    assert code == 1, out
    assert calls == ["seed", "dispatch", "route"], calls
    assert "routed:   epic.1 -> merged - landed, ship pending" in out
    assert "routed:   epic.2 -> merged - landed, ship pending" in out
    assert "stopped:  requested by operator - the grant is nearly spent" in out
    # Consumed, or the next supervisor started here stops before running a round.
    assert not (tmp_path / supervise.STOP_FILE).exists()


def test_max_passes_returns_after_the_nth_round_with_children_still_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC: a launch commits to a bounded number of rounds instead of intervening.

    Both children stay open and every round makes progress, so the loop's own exit
    conditions never fire — the bound is the only thing that returns it, and the
    reason is what an operator reading the log has to find there.
    """
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    _stub_rounds(monkeypatch, calls)

    code = cli._cmd_loop_supervise(argparse.Namespace(issue="epic", label=None, max_passes=1))

    out = capsys.readouterr().out
    assert code == 1, out
    assert calls == ["seed", "dispatch", "route"], calls
    assert "stopped:  --max-passes 1 reached after 1 pass(es), 2 child(ren) still open" in out


def _observation(**overrides: object) -> supervise.Observation:
    """A session as `loop stop` observes it: supervised here unless overridden."""
    defaults: dict[str, object] = {
        "root_issue": "epic",
        "root_status": "open",
        "children_total": 2,
        "children_open": 2,
        "done": False,
        "lanes": (supervise.LaneView("epic.1", "in_progress", "epic-1", "harness/epic.1", True),),
        "pending_decisions": (),
        "holder": supervise.LockInfo(pid=7, session_id="epic:0001", root_issue="epic", age_s=1.0),
        "holder_on_this_root": True,
    }
    return supervise.Observation(**(defaults | overrides))  # type: ignore[arg-type]


def _stop_args(**overrides: object) -> argparse.Namespace:
    return argparse.Namespace(
        **{"issue": "epic", "label": None, "reason": "the grant is nearly spent", "by": "operator"}
        | overrides
    )


def test_a_stop_records_the_request_and_names_the_lanes_it_waits_to_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC: the marker names who asked and why, and the operator sees what is landing."""
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli.supervise, "observe", lambda *_a, **_k: _observation())
    monkeypatch.setattr(cli.supervise, "await_session_return", lambda *_a, **_k: None)

    assert cli._cmd_loop_stop(_stop_args()) == 0

    out = capsys.readouterr().out
    assert "stop: requested by operator - the grant is nearly spent" in out
    assert "landing: epic.1 (in_progress) on harness/epic.1" in out
    assert supervise.take_stop_request(tmp_path, "epic") == supervise.StopRequest(
        root_issue="epic", requested_by="operator", reason="the grant is nearly spent"
    )


def test_a_stop_with_no_supervisor_running_is_refused_and_writes_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A marker nobody reads stops the *next* session here before it runs a round."""
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli.supervise, "observe", lambda *_a, **_k: _observation(holder=None, lanes=())
    )

    assert cli._cmd_loop_stop(_stop_args()) == 1
    assert "not supervised" in capsys.readouterr().err
    assert not (tmp_path / supervise.STOP_FILE).exists()
