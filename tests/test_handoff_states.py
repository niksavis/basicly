"""The loop states that produce and consume a handoff artifact (basicly-u2hl.18).

The other half of ``test_handoff.py``, split on the same boundary as the module: this
file advances a phase and asserts what the engine wrote or refused to accept, and
re-asserts nothing about the schema. The acceptance criterion for this track is spelled
across three of them — a decompose run writes a plan that validates, build entry refuses
a hand-corrupted one naming the failing field, and a finished build hands VERIFY a
summary of what it did.

Each refusal carries a positive control: the collaborator the refusal is meant to
prevent reaching (``_build_children``, ``verify.run_verify``) is replaced with a
``pytest.fail``, so a gate that stopped binding fails here rather than passing quietly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import artifact_record, br, decompose, handoff, loop, merge, verify, worktree
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus
from tests.test_handoff import _FakeBr, _Proc, decomposition, fake_br, spec, summary

__all__ = ["fake_br"]  # re-exported so the fixture resolves in this module

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


# --- decompose writes the artifact ------------------------------------------


class _DecomposeBr(_FakeBr):
    """The comment fake plus the create/dep surface one decomposition needs."""

    def __init__(self) -> None:
        super().__init__()
        self.counter = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["create"]:
            self.counter += 1
            return _Proc(json.dumps({"id": f"feat.{self.counter}"}))
        if args[:1] == ["show"]:
            return _Proc(json.dumps([{"id": args[1], "labels": []}]))
        if args[:2] == ["dep", "add"]:
            return _Proc("")
        if args[:2] == ["dep", "cycles"]:
            return _Proc(json.dumps({"cycles": [], "count": 0}))
        return super().__call__(_repo_root, args, _check=_check)


def test_a_decompose_run_writes_an_implementation_plan_that_validates(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion's first half, through ``decompose`` itself."""
    fake = _DecomposeBr()
    monkeypatch.setattr(br, "run_br", fake)
    monkeypatch.setattr(br, "try_run_br", fake)
    result = decompose.decompose(work_repo, "feat", (spec("a"), spec("b")))
    assert handoff.entry_verdict(work_repo, "feat", handoff.IMPLEMENTATION_PLAN).admitted
    recorded = artifact_record.read(work_repo, "feat", handoff.IMPLEMENTATION_PLAN)
    assert isinstance(recorded, dict)
    assert [task["issue_id"] for task in recorded["tasks"]] == list(result.serial_order)


# --- the loop refuses a corrupted artifact at the consuming state ------------


def _state(phase: str, **kw) -> NodeState:
    return NodeState(
        issue_id="i",
        status="in_progress",
        issue_type=kw.pop("issue_type", "task"),
        phase=phase,
        worktree=kw.pop("worktree", None),
        gates=GateStatus(phase == "verify", (), (), (), ()),
        checkpoints=(),
        rework={},
        has_children=kw.pop("has_children", False),
        title=kw.pop("title", "carry the plan into build"),
    )


@pytest.fixture
def landing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything a landing touches outside the artifact seam, stubbed to succeed."""
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop.policy, "checkpoint_approved", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: None)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))


def _advance(repo_root: Path, state: NodeState, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: state)
    return loop.advance(repo_root, "i", config=CONFIG)


@pytest.mark.usefixtures("landing")
def test_build_entry_refuses_a_corrupted_plan_naming_the_failing_field(
    work_repo: Path, fake_br: _FakeBr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion's second half: the fan-out never starts on a broken plan."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["scope"] = []
    fake_br.comments["i"] = [artifact_record.marker_body(handoff.IMPLEMENTATION_PLAN, payload)]
    monkeypatch.setattr(loop, "_build_children", lambda _ctx: pytest.fail("fanned out anyway"))
    result = _advance(work_repo, _state("decompose", has_children=True), monkeypatch)
    assert result.blocked and result.needs_input == "artifact"
    assert "scope" in result.detail


@pytest.mark.usefixtures("fake_br", "landing")
def test_build_entry_admits_a_sound_plan(work_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the same fan-out proceeds when the artifact validates."""
    handoff.record(
        work_repo, "i", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    monkeypatch.setattr(loop, "_build_children", lambda ctx: loop._moved(ctx, "build", "built"))
    result = _advance(work_repo, _state("decompose", has_children=True), monkeypatch)
    assert result.action == "built"


@pytest.mark.usefixtures("landing")
def test_verify_entry_refuses_a_corrupted_change_summary(
    work_repo: Path, fake_br: _FakeBr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VERIFY holds before it spends a check run on a build whose summary is broken."""
    payload = summary()
    del payload["commit"]
    fake_br.comments["i"] = [artifact_record.marker_body(handoff.CHANGE_SUMMARY, payload)]
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: pytest.fail("verify ran anyway"))
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "closed")])
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    result = _advance(work_repo, _state("decompose", has_children=True), monkeypatch)
    assert result.blocked and result.needs_input == "artifact"
    assert "commit" in result.detail


@pytest.mark.usefixtures("fake_br", "landing")
def test_a_landing_writes_the_change_summary_it_hands_verify(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finished build records what changed, why, the commit and the self-check verdict."""
    session = worktree.Session(
        name="i",
        branch="harness/i",
        base="main",
        base_head="abc",
        worktree_path=str(work_repo),
        created_at="2026-08-08T00:00:00Z",
    )
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: session)
    monkeypatch.setattr(merge, "branch_head", lambda *_a, **_k: "deadbee")
    monkeypatch.setattr(merge, "branch_changed_paths", lambda *_a, **_k: ("src/basicly/loop.py",))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    monkeypatch.setattr(loop, "_scope_block", lambda *_a, **_k: None)
    state = _state("build", worktree=WorktreeBinding("i", "harness/i"))
    result = _advance(work_repo, state, monkeypatch)
    assert result.to_phase == "verify"
    assert artifact_record.read(work_repo, "i", handoff.CHANGE_SUMMARY) == {
        "schema_version": 1,
        "issue": "i",
        "why": "carry the plan into build",
        "commit": "deadbee",
        "changed": ["src/basicly/loop.py"],
        "self_check": {"status": "merged", "passed": True, "detail": "landed"},
    }
