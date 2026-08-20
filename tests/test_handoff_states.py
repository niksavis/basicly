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

import hashlib
from pathlib import Path

import pytest

from basicly import (
    artifact_record,
    decompose,
    handoff,
    loop,
    merge,
    plan_gate,
    tracker,
    verify,
    worktree,
)
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus
from tests.test_artifact_record import record_marker
from tests.test_handoff import _FakeBr, decomposition, fake_br, spec, summary

__all__ = ["fake_br"]  # re-exported so the fixture resolves in this module

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


# --- decompose writes the artifact ------------------------------------------


class _DecomposeBr(_FakeBr):
    """The comment fake plus the record surface one decomposition needs.

    Each method stands in for a named seam rather than for a ``br`` argv, for the reason
    :class:`_FakeBr` gives: since ``[tracker] mode`` became ``owned`` nothing here
    spawns, so a fake below ``br``'s own vocabulary would intercept none of it.
    """

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
    """Route every store seam one decomposition touches at a stateful fake."""
    fake = _DecomposeBr()
    monkeypatch.setattr(tracker, "add_comment", fake.add)
    monkeypatch.setattr(tracker, "read_comments", fake.read)
    monkeypatch.setattr(tracker, "try_add_comment", fake.add_soft)
    monkeypatch.setattr(tracker, "try_read_comments", fake.read)
    monkeypatch.setattr(tracker, "create_record", fake.create)
    monkeypatch.setattr(tracker, "read_record", fake.record)
    monkeypatch.setattr(tracker, "all_records", lambda _r: ())
    # These children declare no edge, so nothing may reach the generic write seam; a
    # refusal states that rather than absorbing a call the fake does not model.
    monkeypatch.setattr(
        tracker, "write", lambda *_a, **_k: pytest.fail("wrote through tracker.write")
    )
    monkeypatch.setattr(decompose.dependency_graph, "blocking_cycles", lambda *_a, **_k: ())
    return fake


@pytest.fixture(autouse=True)
def _open_the_synthetic_records(request: pytest.FixtureRequest) -> None:
    """`test_handoff`'s fixture, for its reason: the artifact write refuses an absent id."""
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
    """The acceptance criterion's first half, through ``decompose`` itself."""
    result = decompose.decompose(work_repo, "proj-feat", (spec("a"), spec("b")))
    assert handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted
    recorded = artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert isinstance(recorded, dict)
    assert [task["issue_id"] for task in recorded["tasks"]] == list(result.serial_order)


# --- the loop refuses a corrupted artifact at the consuming state ------------


def _state(phase: str, **kw) -> NodeState:
    return NodeState(
        issue_id="proj-i",
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
    return loop.advance(repo_root, "proj-i", config=CONFIG)


@pytest.mark.usefixtures("landing")
def test_build_entry_refuses_a_corrupted_plan_naming_the_failing_field(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion's second half: the fan-out never starts on a broken plan."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["scope"] = []
    artifact_record.write(work_repo, "proj-i", handoff.IMPLEMENTATION_PLAN, payload)
    monkeypatch.setattr(loop, "_build_children", lambda _ctx: pytest.fail("fanned out anyway"))
    result = _advance(work_repo, _state("decompose", has_children=True), monkeypatch)
    assert result.blocked and result.needs_input == "artifact"
    assert "scope" in result.detail


@pytest.mark.usefixtures("fake_br", "landing")
def test_build_entry_admits_a_sound_plan(work_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the same fan-out proceeds when the artifact validates."""
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
    """VERIFY holds before it spends a check run on a build whose summary is broken."""
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
    """Pin the branch facts one landing reads, and let the merge succeed.

    *landed_head* is what the merge reports having taken, which is not what the branch
    stood at when the landing began: the rebase replays the work under new shas and the
    regeneration commits on top of it. ``merge.branch_head`` answers with that earlier
    value, so a landing that reads the head before the merge again records the sha the
    merge rewrote and says so here rather than passing.
    """
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
    """A finished build records what changed, why, the commit and the self-check verdict."""
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
    """The recorded ``commit`` must resolve in base, so it is the post-merge head.

    `basicly-gvlpxm`'s own summary recorded `d3422f81` while its branch stood two commits
    later: the rebase had rewritten that commit and the regeneration added one on top.
    Both rewrite the head, and neither touches the changed-path set, which is why only
    half of the branch facts may be read before the merge.
    """
    _pin_landing(monkeypatch, work_repo, landed_head="634c125")
    state = _state("build", worktree=WorktreeBinding("proj-i", "harness/proj-i"))
    assert _advance(work_repo, state, monkeypatch).to_phase == "verify"
    recorded = artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY)
    assert isinstance(recorded, dict)
    assert recorded["commit"] == "634c125"


# --- a corrupted artifact refused at the entry, moved here from `test_handoff`
# because that module reached its size baseline for the fourth time and this is the
# responsibility its siblings already live under (basicly-kmqno2, basicly-8ro0nx).


def test_a_hand_corrupted_plan_is_refused_naming_the_failing_field(work_repo: Path) -> None:
    """The acceptance criterion: an artifact edited out of shape names the field it broke."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["integrity"] = "L9"
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert "integrity" in verdict.reason and "L9" in verdict.reason


def test_a_task_naming_no_demonstration_is_refused_though_a_recorded_bead_is_not(
    work_repo: Path,
) -> None:
    """D18 binds on the artifact and not on ``PLAN_FIELDS``, and the two populations differ.

    A bead recorded before the field existed is admitted by ``plan_entry`` because its
    silence is ambiguous. This artifact has no such population — its only producer is a
    plan ``plan_gate.require_plan`` passed — so here the same silence is a defect.
    """
    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["demonstration"]
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert plan_gate.DEMONSTRATION_FIELD in verdict.reason


def test_a_plan_whose_payload_is_not_json_is_refused_not_ignored(work_repo: Path) -> None:
    """A truncated marker is a corrupted artifact, never a unit that carries none."""
    record_marker(work_repo, "proj-feat", f"{artifact_record.MARKER} kind=implementation-plan {{n")
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted and "is not of type 'object'" in verdict.reason


def test_every_violation_is_reported_at_once(work_repo: Path) -> None:
    """One advance per fixed field is the round-trip cost this gate exists to avoid."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = []
    payload["tasks"][1]["budget_tokens"] = 0
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert len(verdict.violations) == 2


def test_a_hand_corrupted_change_summary_is_refused_naming_the_failing_field(
    work_repo: Path,
) -> None:
    """The BUILD->VERIFY half of the same control pair."""
    payload = summary()
    payload["self_check"]["passed"] = "yes"
    artifact_record.write(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY)
    assert not verdict.admitted and "passed" in verdict.reason
