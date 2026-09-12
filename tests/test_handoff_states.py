from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from basicly import (
    artifact_record,
    decompose,
    handoff,
    loop,
    merge,
    tracker,
    validate_gate,
    verify,
    worktree,
)
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus
from tests.test_handoff import _FakeBr, decomposition, fake_br, spec, summary

__all__ = ["fake_br"]

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


class _DecomposeBr(_FakeBr):
    def __init__(self) -> None:
        super().__init__()
        self.counter = 0

    def create(self, _repo_root: Path, _args: list[str]) -> str:
        self.counter += 1
        return f"proj-feat.{self.counter}"

    def record(self, _repo_root: Path, issue_id: str) -> dict:
        return {"id": issue_id, "labels": []}

    def add_soft(self, repo_root: Path, issue_id: str, body: str) -> bool:
        self.add(repo_root, issue_id, body)
        return True


@pytest.fixture
def decompose_br(monkeypatch: pytest.MonkeyPatch) -> _DecomposeBr:
    fake = _DecomposeBr()
    monkeypatch.setattr(tracker, "add_comment", fake.add)
    monkeypatch.setattr(tracker, "read_comments", fake.read)
    monkeypatch.setattr(tracker, "try_add_comment", fake.add_soft)
    monkeypatch.setattr(tracker, "try_read_comments", fake.read)
    monkeypatch.setattr(tracker, "create_record", fake.create)
    monkeypatch.setattr(tracker, "read_record", fake.record)
    monkeypatch.setattr(tracker, "all_records", lambda _r: ())
    monkeypatch.setattr(
        tracker, "write", lambda *_a, **_k: pytest.fail("wrote through tracker.write")
    )
    monkeypatch.setattr(decompose.dependency_graph, "blocking_cycles", lambda *_a, **_k: ())
    return fake


@pytest.fixture(autouse=True)
def _open_the_synthetic_records(request: pytest.FixtureRequest) -> None:
    if "work_repo" not in request.fixturenames:
        return
    repo = request.getfixturevalue("work_repo")
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in ("proj-feat", "proj-i")
        ],
    )


@pytest.mark.usefixtures("decompose_br")
def test_a_decompose_run_writes_an_implementation_plan_that_validates(work_repo: Path) -> None:
    result = decompose.decompose(work_repo, "proj-feat", (spec("a"), spec("b")))
    assert handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted
    recorded = artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert isinstance(recorded, dict)
    assert [task["issue_id"] for task in recorded["tasks"]] == list(result.serial_order)


def _state(phase: str, **kw) -> NodeState:
    return NodeState(
        issue_id="proj-i",
        status="in_progress",
        issue_type=kw.pop("issue_type", "task"),
        phase=phase,
        worktree=kw.pop("worktree", None),
        gates=kw.pop("gates", GateStatus(phase == "verify", (), (), (), ())),
        checkpoints=(),
        rework={},
        has_children=kw.pop("has_children", False),
        title=kw.pop("title", "carry the plan into build"),
    )


@pytest.fixture
def landing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop.policy, "checkpoint_approved", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: None)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))


def _advance(repo_root: Path, state: NodeState, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: state)
    return loop.advance(repo_root, "proj-i", config=CONFIG)


@pytest.mark.usefixtures("landing")
def test_build_entry_refuses_a_corrupted_plan_naming_the_failing_field(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["scope"] = []
    artifact_record.write(work_repo, "proj-i", handoff.IMPLEMENTATION_PLAN, payload)
    monkeypatch.setattr(loop, "_build_children", lambda _ctx: pytest.fail("fanned out anyway"))
    result = _advance(work_repo, _state("decompose", has_children=True), monkeypatch)
    assert result.blocked and result.needs_input == "artifact"
    assert "scope" in result.detail


@pytest.mark.usefixtures("fake_br", "landing")
def test_build_entry_admits_a_sound_plan(work_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    handoff.record(
        work_repo, "proj-i", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    monkeypatch.setattr(loop, "_build_children", lambda ctx: loop._moved(ctx, "build", "built"))
    result = _advance(work_repo, _state("decompose", has_children=True), monkeypatch)
    assert result.action == "built"


@pytest.mark.usefixtures("landing")
def test_verify_entry_refuses_a_corrupted_change_summary(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = summary()
    del payload["commit"]
    artifact_record.write(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: pytest.fail("verify ran anyway"))
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("proj-i.1", "closed")])
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    result = _advance(work_repo, _state("decompose", has_children=True), monkeypatch)
    assert result.blocked and result.needs_input == "artifact"
    assert "commit" in result.detail


def _pin_landing(
    monkeypatch: pytest.MonkeyPatch, work_repo: Path, *, landed_head: str = "deadbee"
) -> None:

    session = worktree.Session(
        name="proj-i",
        branch="harness/proj-i",
        base="main",
        base_head="abc",
        worktree_path=str(work_repo),
        created_at="2026-08-08T00:00:00Z",
    )
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: session)
    monkeypatch.setattr(merge, "branch_head", lambda *_a, **_k: "d3422f8")
    monkeypatch.setattr(merge, "branch_changed_paths", lambda *_a, **_k: ("src/basicly/loop.py",))
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda *_a, **_k: merge.MergeResult("proj-i", "merged", "landed", landed_head=landed_head),
    )
    monkeypatch.setattr(loop, "_scope_block", lambda *_a, **_k: None)


@pytest.mark.usefixtures("fake_br", "landing")
def test_a_landing_writes_the_change_summary_it_hands_verify(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_landing(monkeypatch, work_repo)
    state = _state("build", worktree=WorktreeBinding("proj-i", "harness/proj-i"))
    result = _advance(work_repo, state, monkeypatch)
    assert result.to_phase == "verify"
    assert artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY) == {
        "schema_version": 1,
        "issue": "proj-i",
        "why": "carry the plan into build",
        "commit": "deadbee",
        "changed_count": 1,
        "changed_digest": hashlib.sha256(b"src/basicly/loop.py").hexdigest(),
        "self_check": {"status": "merged", "passed": True, "detail": "landed"},
    }


@pytest.mark.usefixtures("fake_br", "landing")
def test_a_landing_records_the_head_the_merge_took_not_the_one_it_started_from(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _pin_landing(monkeypatch, work_repo, landed_head="634c125")
    state = _state("build", worktree=WorktreeBinding("proj-i", "harness/proj-i"))
    assert _advance(work_repo, state, monkeypatch).to_phase == "verify"
    recorded = artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY)
    assert isinstance(recorded, dict)
    assert recorded["commit"] == "634c125"


@pytest.mark.usefixtures("fake_br", "landing")
def test_repair_reland_records_the_head_that_merge_took(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _pin_landing(monkeypatch, work_repo, landed_head="7381a14")
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: False)
    monkeypatch.setattr(loop, "_dispatch_validation", lambda *_a, **_k: None)
    merged = []
    monkeypatch.setattr(
        merge,
        "branch_changed_paths",
        lambda *_a, **_k: ("src/basicly/anything_else.py",) if merged else ("src/basicly/loop.py",),
    )
    real_merge = merge.merge_worktree

    def _merge_then_mark(*args: Any, **kwargs: Any) -> merge.MergeResult:
        result = real_merge(*args, **kwargs)
        merged.append(True)
        return result

    monkeypatch.setattr(merge, "merge_worktree", _merge_then_mark)
    state = _state(
        "validate",
        worktree=WorktreeBinding("proj-i", "harness/proj-i"),
        gates=GateStatus(False, (), (validate_gate.VALIDATE_GATE,), (), ()),
    )
    _advance(work_repo, state, monkeypatch)

    recorded = artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY)
    assert isinstance(recorded, dict)
    assert recorded["commit"] == "7381a14"
    assert recorded["changed_digest"] == hashlib.sha256(b"src/basicly/loop.py").hexdigest()


@pytest.mark.usefixtures("fake_br", "landing")
def test_repair_reland_failed_records_no_change_summary(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_landing(monkeypatch, work_repo)
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: False)
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda *_a, **_k: merge.MergeResult(
            "proj-i", "conflict", "both modified x", landed_head=""
        ),
    )
    state = _state(
        "validate",
        worktree=WorktreeBinding("proj-i", "harness/proj-i"),
        gates=GateStatus(False, (), (validate_gate.VALIDATE_GATE,), (), ()),
    )
    _advance(work_repo, state, monkeypatch)

    assert artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY) is None
