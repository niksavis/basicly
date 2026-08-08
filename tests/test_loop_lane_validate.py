"""The gate a lane's integration has to pass, and the dispute it can raise.

Split out of ``test_loop.py`` alongside ``test_loop_lane.py``, which holds the lane
mini-loop's sequencing. This half is the judgment attached to it: the validate gate
that stands between a finished lane and its landing, what happens when a rubric
answers ``no`` (a decision is queued and the lane is held, and no rework attempt is
spent on a verdict that is not a defect), and the boundaries around both — an
``unknown`` answer is not a dispute, a bead reference is a whole id and never a
prefix, and a lane whose worktree session has gone blocks rather than improvising.

``test_plain_leaf_build_is_unchanged_by_the_lane_path`` is the control: everything
above must leave a leaf's build exactly where it was.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import loop, merge, policy, rubrics, verify, worktree
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus
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


@pytest.fixture(autouse=True)
def tracker_commits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """Record engine tracker commits — loop tests run outside a git repo."""
    calls: list[tuple[str, str | None]] = []

    def _record(_repo_root, bead, **kwargs):
        calls.append((bead, kwargs.get("action")))
        return True

    monkeypatch.setattr(loop.merge, "commit_tracker_state", _record)
    return calls


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


def _pin_finding_sets(
    monkeypatch: pytest.MonkeyPatch, *verdicts: policy.Convergence
) -> list[tuple[str, str, tuple[str, ...]]]:
    """Hand the loop scripted convergence verdicts; return each finding set it recorded.

    The comparison itself is policy's and is tested there against a fake tracker.
    What a test here asserts is the loop's half: which findings it hands over, and
    what it does with the verdict it gets back. Rounds past *verdicts* progress.
    """
    recorded: list[tuple[str, str, tuple[str, ...]]] = []
    scripted = list(verdicts)

    def record(_repo_root, issue_id, gate, findings):
        members = policy.finding_signature(findings)
        recorded.append((issue_id, gate, members))
        if scripted:
            return scripted.pop(0)
        return policy.Convergence(policy.PROGRESSING, members, (), 0)

    monkeypatch.setattr(policy, "record_finding_set", record)
    return recorded


def _lane(has_children: bool = True) -> NodeState:
    """A lane: a build-phase node bound to its own worktree, with sub-task beads."""
    return _state("build", worktree=WorktreeBinding("i", "harness/i"), has_children=has_children)


def _pin_lane(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subtasks: list[tuple[str, str]],
    committed: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    pending: tuple[str, ...] = (),
) -> dict:
    """Pin a lane's worktree, sub-task states, and its git/decision/verify reads."""
    calls: dict[str, list] = {"closed": [], "gates": [], "verify": []}
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session("i"))
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: list(subtasks))
    monkeypatch.setattr(loop.loop_state, "blocked_ids", lambda *_a: tuple(blocked))
    monkeypatch.setattr(loop.decisions, "has_pending", lambda _r, issue: issue in pending)
    monkeypatch.setattr(loop, "_subtask_committed", lambda sid, _s: sid in committed)

    def _br(_root, args, **_k):
        if args and args[0] == "close":
            calls["closed"].append(args[1])
        return SimpleNamespace(stdout="{}")

    monkeypatch.setattr(loop, "_run_br", _br)

    def _run_verify(_root, mode, *_a, **_k):
        calls["verify"].append(mode)
        return verify.VerifyReport(mode, ())

    monkeypatch.setattr(verify, "run_verify", _run_verify)

    def _report(_root, issue_id, report, **_k):
        calls["gates"].append((issue_id, report.mode))
        return True, "ok"

    monkeypatch.setattr(verify, "report_gate", _report)
    return calls


def test_lane_validate_gate_blocks_the_landing_when_it_fails(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validate is required at lane level: a failing rubric stops the merge (D4)."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "closed")])
    rubric = rubrics.Rubric(
        id="r",
        description="d",
        applies_to=("task",),
        checks=(
            rubrics.RubricCheck("acceptance", "does it?", rubrics.DETERMINISTIC, command="false"),
        ),
    )
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [rubric])
    monkeypatch.setattr(
        loop.rubrics,
        "evaluate",
        lambda *_a, **_k: [
            rubrics.CheckVerdict("acceptance", rubrics.DETERMINISTIC, rubrics.NO, "exit 1")
        ],
    )
    recorded: list[str] = []
    monkeypatch.setattr(
        loop.rubrics,
        "report_gate",
        lambda _r, issue, _v: recorded.append(issue) or (True, "ok"),
    )
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: pytest.fail("validate must gate the landing")
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    findings = _pin_finding_sets(monkeypatch)
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert recorded == ["i"]
    assert result.blocked and "lane validate failed: acceptance" in result.detail
    # The failed checks are the rubric gate's finding set (basicly-m4zv.5).
    assert findings == [("i", rubrics.RUBRIC_GATE, ("acceptance",))]


def test_lane_validate_evaluates_in_the_lane_worktree(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validate judges the lane's own tree, before its work is merged anywhere."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "closed")])
    rubric = rubrics.Rubric(
        id="r",
        description="d",
        applies_to=("task",),
        checks=(rubrics.RubricCheck("tests", "tested?", rubrics.JUDGED),),
    )
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [rubric])
    seen = {}

    def _evaluate(issue_id, _rubric, repo_root, *_a, **_k):
        seen["issue"], seen["cwd"] = issue_id, repo_root
        return [rubrics.CheckVerdict("tests", rubrics.JUDGED, rubrics.YES, "tests present")]

    monkeypatch.setattr(loop.rubrics, "evaluate", _evaluate)
    monkeypatch.setattr(loop.rubrics, "report_gate", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert seen == {"issue": "i", "cwd": Path("/tmp/i")}
    assert result.to_phase == "verify" and result.action == "merged"


def _judged_no_lane(monkeypatch: pytest.MonkeyPatch, answer: str = rubrics.NO) -> None:
    """Pin a lane whose only rubric check is judged and answers *answer*."""
    rubric = rubrics.Rubric(
        id="r",
        description="d",
        applies_to=("task",),
        checks=(rubrics.RubricCheck("acceptance", "met?", rubrics.JUDGED),),
    )
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [rubric])
    monkeypatch.setattr(
        loop.rubrics,
        "evaluate",
        lambda *_a, **_k: [
            rubrics.CheckVerdict(
                "acceptance",
                rubrics.JUDGED,
                answer,
                "criterion 2 unevidenced",
                # Only a judged NO is a finding, and only a finding carries a
                # severity — the record refuses the other combinations outright.
                rubrics.BLOCKER if answer == rubrics.NO else "",
            )
        ],
    )
    monkeypatch.setattr(loop.rubrics, "report_gate", lambda *_a, **_k: (True, "ok"))


def test_judged_no_queues_a_decision_and_holds_the_lane(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A judged NO is a decision, not a test failure (D4 amended, roster R4)."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "closed")])
    _judged_no_lane(monkeypatch)
    queued: list[tuple[str, str, str, str]] = []

    def _enqueue(_repo, issue, kind, question, detail="", **_kwargs):
        queued.append((issue, kind, question, detail))

    monkeypatch.setattr(loop.decisions, "enqueue", _enqueue)
    merged: list[str] = []

    def _merge(*_args, **_kwargs):
        merged.append("merged")
        return merge.MergeResult("i", "merged", "landed")

    monkeypatch.setattr(merge, "merge_worktree", _merge)
    result = loop.advance(tmp_path, "i", config=CONFIG)

    assert len(queued) == 1
    issue, kind, question, detail = queued[0]
    assert (issue, kind) == ("i", "validate")
    # The severity rides onto the queued item: a queue that renders a MINOR and a
    # BLOCKER identically is a queue disposed of in arrival order.
    assert "acceptance (BLOCKER)" in question
    assert detail == "acceptance: criterion 2 unevidenced"
    assert merged == []  # the lane holds: it neither lands nor bounces
    assert result.blocked and result.action == "decision"
    assert "acceptance" in result.detail


def test_judged_no_does_not_spend_a_rework_attempt(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A false NO from a model must not consume the budget kept for real defects."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "closed")])
    _judged_no_lane(monkeypatch)
    monkeypatch.setattr(loop.decisions, "enqueue", lambda *_a, **_k: None)
    attempts: list[str] = []
    monkeypatch.setattr(policy, "record_rework", lambda _r, _i, gate: attempts.append(gate) or 1)
    loop.advance(tmp_path, "i", config=CONFIG)
    assert attempts == []


def test_judged_unknown_is_not_a_dispute(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An UNKNOWN verdict means no agent answered (handoff) — it must not hold the lane."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "closed")])
    _judged_no_lane(monkeypatch, answer=rubrics.UNKNOWN)
    queued: list[str] = []
    monkeypatch.setattr(loop.decisions, "enqueue", lambda *_a, **_k: queued.append("q"))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert queued == []
    assert result.action == "merged"


def test_references_bead_requires_a_whole_id_not_a_prefix() -> None:
    """A sibling id sharing a prefix is not proof of work (i.1 vs i.10)."""
    assert loop.references_bead("fix(loop): do it (basicly-i.1)", "basicly-i.1")
    assert loop.references_bead("basicly-i.1 leads the subject", "basicly-i.1")
    assert not loop.references_bead("fix(loop): do it (basicly-i.10)", "basicly-i.1")
    assert not loop.references_bead("nothing to see here", "basicly-i.1")


def test_lane_blocks_when_its_worktree_session_is_gone(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane whose worktree record vanished is re-provisioned, not dispatched blind."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "open")])
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: None)
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert result.blocked and "no session record" in result.detail


def test_plain_leaf_build_is_unchanged_by_the_lane_path(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A leaf with no sub-task beads still lands its own dispatch directly."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_run_lane", lambda *_a: pytest.fail("a leaf has no lane mini-loop"))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    assert _advance(tmp_path).action == "merged"
