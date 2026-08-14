"""Tests for BUILD's downstream-WIP entry predicate (basicly-u2hl.23).

Requirements 3.1 gives BUILD two entry conditions and only the plan gate existed:
``concurrency`` bounds how many lanes run at once, and nothing bounded how much
finished-but-unlanded work piled up behind them. These pin the missing half — the
count, the arithmetic, the refusal naming the limit, and the dispatch-level property
the acceptance criterion states: with the bound at one, a second ready lane is
refused while the first is unlanded and admitted once it lands.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import decisions, loop, loop_state, policy, runner, supervise, validate_gate, wip
from basicly.config import PolicyConfig

if TYPE_CHECKING:
    import pytest

_MANUAL_SPEC = runner.RunnerSpec("manual", runner.HANDOFF)

# No grant, so D3's spend ceiling admits everything: the WIP bound is what these
# tests are about, and a second refusing gate would hide which one refused.
_UNGRANTED = policy.SpendStatus(grant=None, spent_tokens=0, halted=False)


def _lane(issue_id: str) -> supervise.AdoptedLane:
    return supervise.AdoptedLane(
        issue_id=issue_id,
        status="in_progress",
        binding=loop_state.WorktreeBinding(issue_id, f"harness/{issue_id}"),
        live=True,
    )


def _session(*lanes: supervise.AdoptedLane) -> supervise.SessionState:
    return supervise.SessionState(
        root_issue="epic",
        root_status="open",
        children=tuple((lane.issue_id, lane.status) for lane in lanes),
        adopted=lanes,
    )


def _limit(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    """Declare the bound without a basicly.toml, at the loader every reader shares."""
    monkeypatch.setattr(
        wip,
        "load_policy_config",
        lambda _r: PolicyConfig(required_gates=("verify",), max_rework=2, max_downstream_wip=limit),
    )


def _phases(monkeypatch: pytest.MonkeyPatch, phases: dict[str, str]) -> None:
    """Answer the phase read from a table instead of ``br``, unknown ids at build."""
    monkeypatch.setattr(
        wip.loop_state,
        "read_node_state",
        lambda _r, issue_id, *_a: loop_state.NodeState(
            issue_id=issue_id,
            status="in_progress",
            issue_type="task",
            phase=phases.get(issue_id, "build"),
            worktree=None,
            gates=policy.GateStatus(True, ("verify",), (), (), ()),
            checkpoints=(),
            rework={},
            agent_context=None,
            has_children=False,
        ),
    )


def test_downstream_units_counts_every_parked_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merged, validating and awaiting-ship are unlanded; building and done are not.

    The population is exactly the one ``supervise.advance_parked`` drives, which is
    what makes the bound drain instead of wedge: a lane still building has produced
    nothing to review, and a closed one has landed.
    """
    _phases(
        monkeypatch,
        {"a": "build", "b": "verify", "c": "ship", "d": "done", "e": "decompose", "f": "validate"},
    )

    assert wip.downstream_units(Path(), ("a", "b", "c", "d", "e", "f")) == ("b", "c", "f")


def test_a_unit_parked_in_validate_is_counted_and_driven_by_one_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An L3 unit resting in validate is both counted here and advanced by a pass.

    The defect this pins (basicly-xab3): ``validate`` was in the counted set and not in
    the driven one, so five units owing a consumer check refused every further dispatch
    against a bound no pass could drain. Both halves are asserted from one phase read,
    which is what makes them one population rather than two that agree today.

    The grant root goes in with the drive because a validate advance dispatches the
    validator, and ``policy.spend_status`` is inert on a step that names no session.
    """
    gates = policy.GateStatus(
        can_advance=False,
        required_passed=("verify",),
        required_failed=(),
        required_missing=(validate_gate.VALIDATE_GATE,),
        advisory=(),
    )
    assert (
        loop_state.derive_phase(
            "in_progress", checkpoints=(), worktree=None, gates=gates, has_children=False
        )
        == "validate"
    )
    _phases(monkeypatch, {"epic.1": "validate"})
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: False)
    monkeypatch.setattr(decisions, "has_pending", lambda _r, _i: False)
    steps = [
        loop.AdvanceResult("epic.1", "validate", "ship", "validated", "recorded green"),
        loop.AdvanceResult("epic.1", "ship", "ship", "blocked", "awaiting ship", checkpoint="ship"),
    ]
    driven: list[tuple[str, str | None]] = []

    def fake_advance(_repo: Path, issue_id: str, **kwargs: object) -> loop.AdvanceResult:
        grant = kwargs["grant_root"]
        driven.append((issue_id, grant if isinstance(grant, str) else None))
        return steps[len(driven) - 1]

    monkeypatch.setattr(supervise.loop, "advance", fake_advance)

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert wip.downstream_units(tmp_path, ("epic.1",)) == ("epic.1",)
    assert driven == [("epic.1", "epic"), ("epic.1", "epic")]
    assert [(item.issue_id, item.route) for item in routed] == [("epic.1", "merged")]


def test_admit_takes_the_pass_own_lanes_off_the_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A limit of three with one unlanded admits two more, in dispatch order.

    The pass's own admissions count, which is the whole difference between a bound
    on unlanded work and a bound that resets whenever the queue happens to be empty.
    """
    _limit(monkeypatch, 3)
    _phases(monkeypatch, {"epic.9": "verify"})
    ready = (_lane("epic.1"), _lane("epic.2"), _lane("epic.3"))

    bound = wip.admit(Path(), ready, (*ready, _lane("epic.9")))

    assert [lane.issue_id for lane in bound.admitted] == ["epic.1", "epic.2"]
    assert [lane.issue_id for lane in bound.refused] == ["epic.3"]
    assert bound.downstream == ("epic.9",)
    assert not bound.stalled


def test_admit_names_the_limit_in_the_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The AC's "naming the limit": a held lane says which bound holds it, and why."""
    _limit(monkeypatch, 1)
    _phases(monkeypatch, {"epic.9": "ship"})
    ready = (_lane("epic.1"),)

    bound = wip.admit(Path(), ready, (*ready, _lane("epic.9")))

    assert bound.stalled
    assert "max_downstream_wip" in bound.reason and "limit of 1" in bound.reason
    assert "1 unit(s) past build" in bound.reason
    assert "epic.1" in bound.detail
    assert "1/1 unlanded downstream of build" in bound.coverage
    assert "waiting: epic.9" in bound.coverage


def test_admit_excludes_the_session_root_from_the_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root anchors the pass; counting it would charge a session for its own epic."""
    _limit(monkeypatch, 1)
    _phases(monkeypatch, {"epic": "verify"})
    ready = (_lane("epic.1"),)

    bound = wip.admit(Path(), ready, (*ready, _lane("epic")), exclude="epic")

    assert bound.downstream == ()
    assert [lane.issue_id for lane in bound.admitted] == ["epic.1"]


def test_coverage_reports_the_bound_even_when_it_admits_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbounded pass must never look like a checked one — the jr0l.22 rule."""
    _limit(monkeypatch, 5)
    _phases(monkeypatch, {})
    ready = (_lane("epic.1"),)

    coverage = wip.admit(Path(), ready, ready).coverage

    assert "0/5 unlanded downstream of build" in coverage
    assert "1 lane(s) admitted" in coverage
    assert "REFUSED" not in coverage


def test_record_refusal_queues_only_when_the_pass_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partly-dispatched pass needs no human; a fully held one would look idle.

    Both halves matter: queuing on every refusal pages an operator about a bound
    working as designed, and queuing on none of them leaves a client reading a
    stalled session as "no ready lanes".
    """
    queued: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _r, issue, kind, question, detail="": (
            queued.append((issue, kind, detail))
            or decisions.DecisionItem(
                decision_id="d1", issue_id=issue, kind=kind, question=question, detail=detail
            )
        ),
    )
    partial = wip.WipAdmission(
        limit=2, downstream=("x",), admitted=(_lane("a"),), refused=(_lane("b"),)
    )
    stalled = wip.WipAdmission(limit=1, downstream=("x",), admitted=(), refused=(_lane("b"),))

    assert wip.record_refusal(Path(), "epic", partial) is None
    assert queued == []
    assert wip.record_refusal(Path(), "epic", stalled) is not None
    assert queued == [("epic", "escalation", stalled.detail)]


def _ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the session's lanes dispatchable without a tracker: the rest is not the test."""
    ranking = loop_state.Ranking(nodes=(), schema="br.scheduler.v1", fallback_sort="id ASC")
    monkeypatch.setattr(supervise.loop_state, "blocked_ids", lambda _r: ())
    monkeypatch.setattr(supervise.loop_state, "ready_ranking", lambda _r, *_a: ranking)
    monkeypatch.setattr(supervise.loop_state, "ready_ranked", lambda _r: ())
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: False)
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "build")
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: False)
    monkeypatch.setattr(supervise.policy, "spend_status", lambda *_a, **_k: _UNGRANTED)
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(supervise.decisions, "enqueue", lambda *_a, **_k: None)
    monkeypatch.setattr(
        supervise,
        "admit_working_set",
        lambda _r, issue_id, _s: supervise.WorkingSetAdmission(
            issue_id=issue_id, sizing=None, violation=None, refused=False
        ),
    )
    monkeypatch.setattr(
        supervise,
        "admit_pass_spend",
        lambda *_a: supervise.PassSpendAdmission(0, None, (), (), None),
    )


def test_a_second_lane_is_refused_while_the_first_is_unlanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The acceptance criterion, end to end through ``dispatch_lanes``.

    With the bound at one and ``epic.1`` merged but parked in verify, the ready
    ``epic.2`` must not start; once ``epic.1`` closes it must. Asserted on the same
    session both times, because the point is that only the *phase* changed — the
    lane was ready, funded and unblocked throughout.
    """
    first, second = _lane("epic.1"), _lane("epic.2")
    session = _session(first, second)
    _ready(monkeypatch)
    _limit(monkeypatch, 1)
    dispatched: list[str] = []
    monkeypatch.setattr(
        supervise,
        "_dispatch_lane",
        lambda _r, _s, lane, *_a, **_k: (
            dispatched.append(lane.issue_id)
            or supervise.LaneOutcome(
                issue_id=lane.issue_id,
                runner_name="manual",
                result=None,
                needs_fact=None,
                occupancy=None,
                overrun=False,
                followup_id=None,
                detail="test",
            )
        ),
    )

    _phases(monkeypatch, {"epic.1": "verify"})
    refused = supervise.dispatch_lanes(Path(), session, skip=frozenset({"epic.1"}))

    assert dispatched == []
    assert [outcome.issue_id for outcome in refused] == ["epic.2"]
    assert refused[0].refused and refused[0].result is None
    assert "limit of 1" in refused[0].detail
    assert "land or review epic.1" in refused[0].detail

    _phases(monkeypatch, {"epic.1": "done"})
    admitted = supervise.dispatch_lanes(Path(), session, skip=frozenset({"epic.1"}))

    assert dispatched == ["epic.2"]
    assert [outcome.issue_id for outcome in admitted] == ["epic.2"]
    assert not admitted[0].refused
