from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import decisions, loop, loop_state, policy, runner, supervise, validate_gate, wip
from basicly.config import PolicyConfig

if TYPE_CHECKING:
    import pytest

_MANUAL_SPEC = runner.RunnerSpec("manual", runner.HANDOFF)

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
    monkeypatch.setattr(
        wip,
        "load_policy_config",
        lambda _r: PolicyConfig(required_gates=("verify",), max_rework=2, max_downstream_wip=limit),
    )


def _phases(monkeypatch: pytest.MonkeyPatch, phases: dict[str, str]) -> None:
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
            has_children=False,
        ),
    )


def test_downstream_units_counts_every_parked_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _phases(
        monkeypatch,
        {"a": "build", "b": "verify", "c": "ship", "d": "done", "e": "decompose", "f": "validate"},
    )

    assert wip.downstream_units(Path(), ("a", "b", "c", "d", "e", "f")) == ("b", "c", "f")


def test_a_unit_parked_in_validate_is_counted_and_driven_by_one_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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


def test_admit_does_not_charge_the_pass_for_its_own_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _limit(monkeypatch, 3)
    _phases(monkeypatch, {"epic.9": "verify"})
    ready = (_lane("epic.1"), _lane("epic.2"), _lane("epic.3"))

    bound = wip.admit(Path(), ready, (*ready, _lane("epic.9")))

    assert [lane.issue_id for lane in bound.admitted] == ["epic.1", "epic.2", "epic.3"]
    assert bound.refused == ()
    assert bound.downstream == ("epic.9",)
    assert not bound.stalled


def test_admit_takes_a_full_cohort_while_the_review_queue_has_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _limit(monkeypatch, 5)
    _phases(monkeypatch, {})
    ready = tuple(_lane(f"epic.{index}") for index in range(1, 11))

    bound = wip.admit(Path(), ready, ready)

    assert len(bound.admitted) == 10
    assert bound.refused == ()
    assert bound.downstream == ()
    assert "0/5 unlanded downstream of build" in bound.coverage
    assert "10 lane(s) admitted" in bound.coverage


def test_admit_holds_the_whole_cohort_once_the_limit_stands_downstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _limit(monkeypatch, 5)
    parked = tuple(_lane(f"epic.{index}") for index in range(1, 6))
    _phases(monkeypatch, dict.fromkeys((lane.issue_id for lane in parked), "verify"))
    ready = (_lane("epic.6"), _lane("epic.7"))

    bound = wip.admit(Path(), ready, (*ready, *parked))

    assert bound.admitted == ()
    assert [lane.issue_id for lane in bound.refused] == ["epic.6", "epic.7"]
    assert len(bound.downstream) == 5
    assert bound.stalled
    assert "5 unit(s) past build" in bound.reason


def test_admit_names_the_limit_in_the_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
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
    _limit(monkeypatch, 1)
    _phases(monkeypatch, {"epic": "verify"})
    ready = (_lane("epic.1"),)

    bound = wip.admit(Path(), ready, (*ready, _lane("epic")), exclude="epic")

    assert bound.downstream == ()
    assert [lane.issue_id for lane in bound.admitted] == ["epic.1"]


def test_coverage_reports_the_bound_even_when_it_admits_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    ranking = loop_state.Ranking(nodes=(), schema="tracker.scheduler.v1", fallback_sort="id ASC")
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


def test_a_ten_lane_cohort_dispatches_whole_against_a_limit_of_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    lanes = tuple(_lane(f"epic.{index}") for index in range(1, 11))
    session = _session(*lanes)
    _ready(monkeypatch)
    _limit(monkeypatch, 5)
    _phases(monkeypatch, {})
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
                detail="test",
            )
        ),
    )

    outcomes = supervise.dispatch_lanes(Path(), session, cap=10)

    assert sorted(dispatched) == sorted(lane.issue_id for lane in lanes)
    assert len(outcomes) == 10
    assert not any(outcome.refused for outcome in outcomes)
