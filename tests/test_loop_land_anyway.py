"""Carrying out an escalation's ``land anyway`` (basicly-tcmy.6).

Split out of ``test_loop.py``, where the questions these answers belong to are
raised (``test_loop_escalation.py``).

The defect: answering only released the lane. The landing re-attempted, the same
flaky gate tripped, the count passed the bound again, and the identical question
re-opened under the next generation — an unbounded ladder of questions with the
offered remedy unimplemented.

So an answered ``land anyway`` has to actually land, exactly once, without re-running
the gate it waived — and the tests here fence that override on every side: it is
spent by the landing that used it, it is not spent by a landing that never reached
the gate, it waives only the gate it named, a delegated answer does not skip the
gate at all, and a rework escalation sitting on the queue never overrides a landing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import decisions, loop, merge, policy, verify
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _gate(can_advance: bool) -> GateStatus:
    return GateStatus(can_advance, (), (), () if can_advance else ("verify",), ())


def _state(
    phase: str,
    *,
    issue_type: str = "task",
    worktree: WorktreeBinding | None = None,
    has_children: bool = False,
) -> NodeState:
    return NodeState(
        issue_id="i",
        status="in_progress",
        issue_type=issue_type,
        phase=phase,
        worktree=worktree,
        gates=_gate(can_advance=phase == "verify"),
        checkpoints=(),
        rework={},
        has_children=has_children,
    )


@pytest.fixture
def at(monkeypatch: pytest.MonkeyPatch):
    """Return a helper that pins read_node_state to a given NodeState."""

    def _pin(state: NodeState) -> None:
        monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: state)

    return _pin


@pytest.fixture(autouse=True)
def tracker_commits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """Record engine tracker commits — loop tests run outside a git repo."""
    calls: list[tuple[str, str | None]] = []

    def _record(_repo_root, bead, **kwargs):
        calls.append((bead, kwargs.get("action")))
        return True

    monkeypatch.setattr(loop.merge, "commit_tracker_state", _record)
    return calls


def _advance(tmp_path: Path, **kw) -> loop.AdvanceResult:
    return loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(**kw))


def _escalation_item(answer: str, *, by: str = "human") -> decisions.DecisionItem:
    """One answered unreliable-gate escalation, worded by the code that words it."""
    return decisions.DecisionItem(
        decision_id="i#f1a5e",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.unreliable_gate_escalation_question(merge.MERGE_GATE),
        detail="verify full failed on pytest but passed unchanged on re-run",
        answer=answer,
        answered_by=by,
    )


def _landing_after_answer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    queue: list[decisions.DecisionItem],
    spent: bool = False,
    landing: merge.MergeResult | None = None,
) -> dict:
    """Drive a landing with *queue* already on the bead and the flake at the bound.

    The merge stub reports whether the landing was asked to skip its gate, so the
    override is observed at the boundary it crosses rather than inferred from a
    message. *landing* overrides what the merge returns, for the cases where the
    override cannot have been used.
    """
    seen: dict = {"override_gate": None, "spent": [], "enqueued": []}
    unreliable = merge.MergeResult(
        "i", merge.VERIFY_UNRELIABLE, "verify full failed on pytest but passed unchanged on re-run"
    )
    merged = landing or merge.MergeResult("i", "merged", "merged harness/i into main @ abc1234")

    def _merge(_root, _name, *, bead, verify_mode, override_gate):  # noqa: ARG001
        seen["override_gate"] = override_gate
        return merged if override_gate else unreliable

    monkeypatch.setattr(merge, "merge_worktree", _merge)
    monkeypatch.setattr(decisions, "items_on", lambda *_a, **_k: tuple(queue))
    monkeypatch.setattr(policy, "gate_override_spent", lambda *_a, **_k: spent)
    monkeypatch.setattr(
        policy, "spend_gate_override", lambda _r, _i, gate: seen["spent"].append(gate) or True
    )
    monkeypatch.setattr(policy, "record_unreliable_gate", lambda *_a, **_k: 3)
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _root, _issue, kind, question, *_a, **_k: seen["enqueued"].append((kind, question)),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    return seen


def test_an_answered_land_anyway_lands_once_without_re_running_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The remedy the escalation offers, carried out — it used to do nothing at all."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(monkeypatch, queue=[_escalation_item("land anyway")])

    result = _advance(tmp_path)

    assert seen["override_gate"] is True
    assert seen["spent"] == [merge.MERGE_GATE]  # one-shot, spent at the landing
    assert seen["enqueued"] == []
    assert result.to_phase == "verify" and result.action == "merged"
    # A landing that skipped a gate says so; "merged @ abc1234" alone would read green.
    assert "skipped" in result.detail and merge.MERGE_GATE in result.detail


def test_the_answered_escalation_is_never_re_asked_under_a_new_generation(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ladder: `enqueue` re-opens an *answered* item, so re-asking was unbounded.

    `fix the flake` leaves the flake in place until a human fixes it, so the gate
    trips again on the very next landing. Asking again is the livelock, not the fix.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(monkeypatch, queue=[_escalation_item("fix the flake")])

    result = _advance(tmp_path)

    assert seen["override_gate"] is False  # the other choice authorises nothing
    assert seen["spent"] == []
    assert seen["enqueued"] == []
    assert result.blocked and "already answered" in result.detail
    assert "fix the flake" in result.detail


def test_a_spent_override_does_not_bypass_the_gate_again(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One answer, one landing: a standing `land anyway` must not skip the gate forever."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(monkeypatch, queue=[_escalation_item("land anyway")], spent=True)

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    assert seen["enqueued"] == []
    assert result.blocked and "no longer authorises a landing" in result.detail


def test_a_delegated_land_anyway_does_not_skip_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An autonomy grant may dispose of the question; it may not waive a landing gate."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(
        monkeypatch,
        queue=[_escalation_item("land anyway", by=f"{decisions.DECIDER_BY_PREFIX}opus")],
    )

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    assert seen["enqueued"] == []  # still no ladder — the answer disposed of the ask
    assert result.blocked


def test_an_override_is_not_spent_by_a_landing_that_never_reached_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`not-ready` is operator-fixable and pre-gate, so it must not burn the one shot."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(
        monkeypatch,
        queue=[_escalation_item("land anyway")],
        landing=merge.MergeResult("i", "not-ready", "no committed work on harness/i"),
    )

    result = _advance(tmp_path)

    assert seen["override_gate"] is True  # offered...
    assert seen["spent"] == []  # ...but the gate was never reached, so it survives
    assert result.blocked and "no committed work" in result.detail


def test_land_anyway_on_another_gate_does_not_waive_the_landing_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The override is authorised for the gate the answered question named, and no other.

    Only the landing gate is escalated this way today, so this pins the reading of the
    gate name rather than the assumption behind it.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    other = decisions.DecisionItem(
        decision_id="i#a11e",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.unreliable_gate_escalation_question("rubric"),
        answer="land anyway",
        answered_by="human",
    )
    seen = _landing_after_answer(monkeypatch, queue=[other])

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    assert result.blocked
    # An answered unreliable escalation is on the bead, so the ladder still ends here.
    assert seen["enqueued"] == []


def test_a_rework_escalation_on_the_queue_never_overrides_a_landing_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both escalations ride one decision kind, so only the question may tell them apart.

    A `retry` on the rework question must not be read as permission to skip a gate.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    rework = decisions.DecisionItem(
        decision_id="i#0dd1",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.rework_escalation_question(merge.MERGE_GATE),
        answer="retry",
        answered_by="human",
    )
    seen = _landing_after_answer(monkeypatch, queue=[rework])

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    # No unreliable-gate escalation has been answered, so this one is still asked once.
    assert [q for _, q in seen["enqueued"]] == [
        policy.unreliable_gate_escalation_question(merge.MERGE_GATE)
    ]
    assert result.blocked
