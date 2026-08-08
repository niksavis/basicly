"""When a failing gate stops being this lane's problem (basicly-jr0l.41, basicly-qorx).

Split out of ``test_loop.py``. Both escalations here answer the same livelock: a
gate that fails for a reason the lane cannot fix spends no rework, so no cap is
ever reached, so nothing ever asks a human and the lane can never land.

- A *chronically unreliable* gate — one that fails and then passes unchanged — is
  forgiven, but only up to a bound; past it the loop escalates instead of deferring
  forever.
- A gate another lane's record invalidated in the shared tracker is not this lane's
  failure at all: it escalates on the first occurrence, names the lane responsible,
  charges nothing, and asks only once.

What the loop does with the answer to either question is
``test_loop_land_anyway.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import decisions, loop, merge, policy
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
        agent_context=None,
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


def _unreliable_landing(monkeypatch: pytest.MonkeyPatch, events: int) -> list[tuple[str, str]]:
    """Drive a landing whose gate is unreliable, with the count already at *events*."""
    attempt = merge.MergeResult(
        "i", merge.VERIFY_UNRELIABLE, "verify full failed on pytest but passed unchanged on re-run"
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)
    monkeypatch.setattr(policy, "record_unreliable_gate", lambda *_a, **_k: events)
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _root, _issue, kind, question, *_a, **_k: enqueued.append((kind, question)),
    )
    return enqueued


def test_a_flaky_gate_below_the_bound_blocks_without_escalating(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One flake is no evidence against the work, so it must not reach a human yet."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    enqueued = _unreliable_landing(monkeypatch, events=1)

    result = _advance(tmp_path)

    assert result.blocked
    assert enqueued == []


def test_a_chronically_unreliable_gate_escalates_instead_of_deferring_forever(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The livelock: no budget is spent, so no cap is reached, so nothing escalated.

    Observed in the field — a br clock defect failed one arbitrary test per run,
    the loop correctly refused to charge rework, and the lane could never land
    because "forgiven" had no exit. The bound gives it one.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    enqueued = _unreliable_landing(monkeypatch, events=policy.MAX_UNRELIABLE_GATE_EVENTS)

    result = _advance(tmp_path)

    assert result.blocked
    assert len(enqueued) == 1
    kind, question = enqueued[0]
    assert kind == policy.REWORK_ESCALATION_KIND
    assert policy.gate_from_unreliable_escalation(question) == merge.MERGE_GATE
    assert "escalated" in result.detail
    # That it is never charged as rework is pinned at the policy level, where the
    # tracker is faked — asserting it here would drag a real br call into a unit test.


# --- A shared-tracker gate is not this lane's failure (basicly-qorx) -----------


def _foreign_landing(
    monkeypatch: pytest.MonkeyPatch, *, queue: tuple[decisions.DecisionItem, ...] = ()
) -> dict:
    """Drive a landing whose gate another lane's record invalidated.

    *queue* is what the bead's decision queue already holds, so the ask-once guard
    can be exercised without a real tracker.
    """
    seen: dict = {"charged": [], "attributed": [], "enqueued": []}
    attempt = merge.MergeResult(
        "i",
        merge.VERIFY_FOREIGN,
        "verify full failed on pytest — invalidated in the shared tracker by "
        "basicly-tcmy.5, not by this lane's diff",
        culprits=("basicly-tcmy.5",),
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: seen["charged"].append(a) or 1)
    monkeypatch.setattr(
        policy,
        "record_shared_gate_failure",
        lambda *a, **_k: seen["attributed"].append(a) or 1,
    )
    monkeypatch.setattr(decisions, "items_on", lambda *_a, **_k: queue)
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _root, _issue, kind, question, *_a, **_k: seen["enqueued"].append((kind, question)),
    )
    return seen


def test_a_gate_another_lanes_record_failed_spends_no_rework_and_names_that_lane(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The measured defect: two siblings were charged 1/2 for a declaration in neither diff.

    Every lane in a supervised pass shares one `.beads` through the redirect, so the
    working-set ceiling asserted over basicly-tcmy.5's finishing record inside the
    landings of basicly-tcmy.6 and basicly-tcmy.22 as well.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _foreign_landing(monkeypatch)

    result = _advance(tmp_path)

    assert result.blocked
    assert seen["charged"] == []  # the whole point
    assert [(one[1], one[2], one[3]) for one in seen["attributed"]] == [
        ("i", merge.MERGE_GATE, ("basicly-tcmy.5",))
    ]


def test_it_escalates_on_the_first_occurrence_rather_than_after_a_bound(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A flake may clear itself on the next landing; a record in the tracker will not.

    So the bound an unreliable gate gets would only delay the escalation — every
    retry reaches the identical verdict (basicly-jr0l.16's reasoning about a
    deterministic refusal).
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _foreign_landing(monkeypatch)

    result = _advance(tmp_path)

    assert len(seen["enqueued"]) == 1
    kind, question = seen["enqueued"][0]
    assert kind == policy.REWORK_ESCALATION_KIND
    assert policy.gate_from_shared_gate_escalation(question) == merge.MERGE_GATE
    assert "basicly-tcmy.5" in question
    assert "escalated" in result.detail


def test_an_answered_shared_gate_escalation_is_not_asked_again(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ask once: an answered item re-opens under the next generation, which is a ladder.

    The remedies are the human's to carry out and neither is on this lane's side, so
    the answer cannot release the landing — the node holds on the answer it has
    (basicly-tcmy.6's ladder, not repeated).
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    answered = decisions.DecisionItem(
        decision_id="i#f1a5e",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.shared_gate_escalation_question(merge.MERGE_GATE, ("basicly-tcmy.5",)),
        detail="invalidated in the shared tracker by basicly-tcmy.5",
        answer="fixed tcmy.5's record",
        answered_by="human",
    )
    seen = _foreign_landing(monkeypatch, queue=(answered,))

    result = _advance(tmp_path)

    assert result.blocked
    assert seen["enqueued"] == []
    assert "already answered by human" in result.detail
    assert seen["charged"] == []
