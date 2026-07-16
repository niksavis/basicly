"""Tests for the checkpoint-gated loop state machine (onb.6.3).

The machine derives its phase from br every step, so each test pins a NodeState
(the resume point) and fakes the composed modules. The invariant under test:
every step either blocks or drives a br-state change that moves the derived
phase forward — the handlers and derive_phase never disagree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import classify, decompose, loop, merge, policy, verify, worktree
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import DoRResult, GateStatus
from basicly.worktree import Session

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


def _session(name: str = "i") -> Session:
    return Session(
        name=name,
        branch=f"harness/{name}",
        base="main",
        base_head="abc",
        worktree_path=f"/tmp/{name}",
        created_at="2026-07-14T00:00:00Z",
    )


def _advance(tmp_path: Path, **kw) -> loop.AdvanceResult:
    return loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(**kw))


# --- intake -----------------------------------------------------------------


def test_intake_blocks_without_work_type(at, tmp_path: Path) -> None:
    """Intake needs an agent-proposed work type before it can classify."""
    at(_state("intake"))
    result = _advance(tmp_path)
    assert result.blocked and result.needs_input == "work_type"


def test_intake_records_type_then_waits_for_checkpoint(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given a type, intake records it and blocks on the classify checkpoint."""
    at(_state("intake"))
    recorded = {}

    def _classify(_r, _i, wt):
        recorded["wt"] = wt
        return classify.ClassifyResult("i", wt, DoRResult(True, ()))

    monkeypatch.setattr(classify, "classify", _classify)
    result = _advance(tmp_path, work_type="feature")
    assert recorded["wt"] == "feature"
    assert result.blocked and "classify checkpoint" in result.detail


# --- classify (checkpoint already approved => derived phase is "classify") ---


def test_classify_blocks_when_dor_incomplete(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An incomplete DoR blocks the exit from classify."""
    at(_state("classify"))
    monkeypatch.setattr(
        policy, "definition_of_ready", lambda *_a: DoRResult(False, ("## Acceptance Criteria",))
    )
    result = _advance(tmp_path)
    assert result.blocked and "definition of ready" in result.detail


def test_classify_leaf_provisions_worktree(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ready leaf type provisions its worktree and blocks for the agent's work."""
    at(_state("classify", issue_type="task"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    created = {}

    def _create(name: str) -> Session:
        created["n"] = name
        return _session(name)

    monkeypatch.setattr(worktree, "create", _create)
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    result = _advance(tmp_path)
    assert created["n"] == "i"
    assert result.blocked and "provisioned" in result.detail


def test_classify_feature_blocks_without_children(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ready feature needs an agent-proposed child plan to decompose."""
    at(_state("classify", issue_type="feature"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    result = _advance(tmp_path)
    assert result.blocked and result.needs_input == "children"


def test_classify_feature_decomposes(at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A ready feature with a plan decomposes and moves to the decompose phase."""
    at(_state("classify", issue_type="feature"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    monkeypatch.setattr(
        decompose, "decompose", lambda *_a: decompose.DecomposeResult("i", (), (("i.1",),))
    )
    child = decompose.ChildSpec("t", ("ac",), ("s",))
    result = _advance(tmp_path, children=(child,))
    assert result.to_phase == "decompose" and result.action == "decomposed"


# --- decompose --------------------------------------------------------------


def test_decompose_blocks_on_pending_checkpoint(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A decomposed node waits for the decompose checkpoint."""
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: False)
    result = _advance(tmp_path)
    assert result.blocked and "decompose checkpoint" in result.detail


def test_decompose_builds_children_and_blocks_while_open(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the checkpoint approved, open child tracks keep the feature building."""
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(
        loop, "_child_states", lambda _ctx: [("i.1", "in_progress"), ("i.2", "closed")]
    )
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    result = _advance(tmp_path)
    assert result.blocked and "1 child track(s) still open" in result.detail


def test_decompose_merges_children_when_all_closed(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once every child closes, the merge queue lands them and verify records the gate."""
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "closed"), ("i.2", "closed")])
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(
        worktree, "list_sessions", lambda *_a, **_k: [_session("i-1"), _session("i-2")]
    )
    monkeypatch.setattr(
        merge,
        "merge_queue",
        lambda *_a, **_k: [
            merge.QueueResult(merge.MergeResult("i-1", "merged", "ok")),
            merge.QueueResult(merge.MergeResult("i-2", "merged", "ok")),
        ],
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    result = _advance(tmp_path)
    assert result.to_phase == "verify" and result.action == "merged"


def test_decompose_skips_self_landed_children(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Self-landed children have no worktree left; fan-in treats them as merged."""
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "closed"), ("i.2", "closed")])
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])

    def _no_queue(*_a, **_k):
        raise AssertionError("merge_queue must not run when no child worktree is live")

    monkeypatch.setattr(merge, "merge_queue", _no_queue)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    result = _advance(tmp_path)
    assert result.to_phase == "verify" and result.action == "merged"
    assert "2 already self-landed" in result.detail


def test_decompose_merges_only_live_children(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mix of live and self-landed children queues only the live worktrees."""
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "closed"), ("i.2", "closed")])
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [_session("i-2")])
    queued = {}

    def _queue(_root, items, **_k):
        queued["items"] = items
        return [merge.QueueResult(merge.MergeResult(name, "merged", "ok")) for name, _ in items]

    monkeypatch.setattr(merge, "merge_queue", _queue)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    result = _advance(tmp_path)
    assert queued["items"] == [("i-2", "i.2")]
    assert result.to_phase == "verify" and result.action == "merged"
    assert "merged 1 child worktree(s); 1 already self-landed" in result.detail


def test_decompose_escalates_on_merge_failure(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A merge-queue failure surfaces as an escalation when the queue flags it."""
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "closed")])
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [_session("i-1")])
    monkeypatch.setattr(
        merge,
        "merge_queue",
        lambda *_a, **_k: [
            merge.QueueResult(
                merge.MergeResult("i-1", "merge-conflicts", "conflicts"), attempts=2, escalate=True
            )
        ],
    )
    result = _advance(tmp_path)
    assert result.action == "escalated" and "merge failed" in result.detail


# --- build (leaf worktree bound) --------------------------------------------


def test_build_leaf_lands_and_records_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bound leaf lands via merge and records the verify gate, moving to verify."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    result = _advance(tmp_path)
    assert result.to_phase == "verify" and result.action == "merged"


def test_build_leaf_reworks_on_failed_merge(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed merge records rework and escalates at the cap."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda *_a, **_k: merge.MergeResult("i", "merge-conflicts", "conflicts in x.py"),
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 2)  # at the default cap
    result = _advance(tmp_path)
    assert result.action == "escalated" and "merge failed" in result.detail


# --- verify / ship / done ---------------------------------------------------


def test_verify_blocks_on_pending_ship_checkpoint(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After landing, the verify phase waits for the human ship checkpoint."""
    at(_state("verify"))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: False)
    result = _advance(tmp_path)
    assert result.blocked and "ship checkpoint" in result.detail


def test_verify_advances_to_ship_when_approved(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An approved ship checkpoint advances to ship."""
    at(_state("verify"))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    result = _advance(tmp_path)
    assert result.to_phase == "ship" and result.action == "shipped"


def test_ship_tears_down_and_closes(at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ship cleans up the worktree and closes the issue, reaching done."""
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    torn = {}
    monkeypatch.setattr(worktree, "cleanup", lambda name, **_k: torn.setdefault("n", name))
    closed = {}
    monkeypatch.setattr(loop, "_run_br", lambda _r, args, **_k: closed.setdefault("args", args))
    result = _advance(tmp_path)
    assert torn["n"] == "i"
    assert closed["args"][:2] == ["close", "i"]
    assert result.to_phase == "done" and result.action == "tore-down"


def test_done_is_terminal(at, tmp_path: Path) -> None:
    """A closed track reports done without further work."""
    at(_state("done"))
    result = _advance(tmp_path)
    assert result.to_phase == "done" and result.action == "done"


# --- child-state parsing & driver ------------------------------------------


def test_child_states_parses_parent_child_dependents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_child_states reads parent-child dependents (and ignores other dep types)."""

    class _Proc:
        stdout = (
            '[{"id":"i","dependents":['
            '{"id":"i.1","status":"open","dependency_type":"parent-child"},'
            '{"id":"x","status":"open","dependency_type":"blocks"}]}]'
        )

    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: _Proc())
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    assert loop._child_states(ctx) == [("i.1", "open")]


def test_run_until_blocked_stops_at_first_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The driver advances until a step blocks, then stops (never spins).

    Intake records the type and blocks on the classify checkpoint in one step —
    since recording a type does not by itself leave intake, the loop halts.
    """
    monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: _state("intake"))
    monkeypatch.setattr(
        classify,
        "classify",
        lambda _r, _i, wt: classify.ClassifyResult("i", wt, DoRResult(True, ())),
    )
    results = loop.run_until_blocked(
        tmp_path, "i", config=CONFIG, inputs=loop.Inputs(work_type="task")
    )
    assert len(results) == 1 and results[0].blocked
