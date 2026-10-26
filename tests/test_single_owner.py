from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import classify, cli, loop, policy, repair_brief, runner
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import DoRResult, GateStatus


def _context(root: Path, phase: str = "build", **changes) -> loop._Ctx:
    values = {
        "issue_id": "task",
        "status": "in_progress",
        "issue_type": "task",
        "phase": phase,
        "worktree": WorktreeBinding("task", "harness/task"),
        "gates": GateStatus(False, (), (), ("verify",), ()),
        "checkpoints": (),
        "rework": {},
        "has_children": False,
    }
    values.update(changes)
    return loop._Ctx(
        root,
        "task",
        NodeState(**values),
        PolicyConfig(required_gates=("verify",), max_rework=2),
        loop.Inputs(),
    )


@pytest.mark.parametrize("phase", ["build", "repair", "validate", "ship", "retrospective"])
def test_session_owner_never_launches_a_phase_worker(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: pytest.fail("unexpected worker"))
    monkeypatch.setattr(loop, "load_runner_config", lambda *_a: pytest.fail("worker selection"))
    dispatch = loop._run_agent(_context(tmp_path, phase), "task", tmp_path, phase=phase)
    assert dispatch.result.handoff and not dispatch.result.executed
    assert dispatch.cwd == tmp_path


def test_known_type_is_classified_by_owner_without_model_call(tmp_path, monkeypatch):
    recorded = []
    monkeypatch.setattr(loop, "_declared_scope", lambda *_a: ())
    monkeypatch.setattr(loop, "_proposed_work_type", lambda *_a: pytest.fail("classifier model"))
    monkeypatch.setattr(
        classify,
        "classify",
        lambda _r, _i, kind, _s: recorded.append(kind) or SimpleNamespace(work_type=kind),
    )
    monkeypatch.setattr(
        policy, "approve_checkpoint_guarded", lambda *_a, **_k: policy.ApprovalResult("approved")
    )
    result = loop._on_intake(_context(tmp_path, "intake"))
    assert recorded == ["task"]
    assert result.to_phase == "classify"


def test_simple_feature_keeps_one_owner_without_mandatory_decomposition(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    monkeypatch.setattr(loop, "_proposed_children", lambda *_a: pytest.fail("decomposer model"))
    monkeypatch.setattr(loop, "_start_build_leaf", lambda ctx: loop._blocked(ctx, "owner builds"))
    result = loop._on_classify(_context(tmp_path, "classify", issue_type="feature"))
    assert result.detail == "owner builds"


def test_session_does_not_automatically_propose_a_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: pytest.fail("unexpected proposer"))
    reply, reason = loop._run_proposer(_context(tmp_path), "children", "plan", phase="decompose")
    assert reply == "" and "task owner" in reason


def test_repair_remains_durable_until_owner_changes_branch(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    session = SimpleNamespace(worktree_path=str(tmp_path), branch="harness/task")
    monkeypatch.setattr(loop, "_bound_session", lambda *_a: session)
    head = ["before"]
    monkeypatch.setattr(loop.merge, "branch_head", lambda *_a: head[0])
    brief = repair_brief.RepairBrief("task", "verify", "regression", branch_head="before")
    assert repair_brief.write_repair_brief(tmp_path, brief)
    for _ in range(2):
        result = loop._repair_in_place(ctx, ctx.state.worktree)
        assert result.needs_input == "repair" and "regression" in result.detail
        assert repair_brief.read_repair_brief(tmp_path) == brief
    head[0] = "after"
    assert loop._repair_in_place(ctx, ctx.state.worktree) is None


@pytest.mark.parametrize("phase", ["validate", "ship"])
def test_session_phase_never_auto_dispatches_validation_or_curation(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(loop, "_run_agent", lambda *_a, **_k: pytest.fail("new phase agent"))
    ctx = _context(tmp_path, phase)
    if phase == "validate":
        assert loop._dispatch_validation(ctx, "validate") is None
    else:
        assert loop._dispatch_curation(ctx) == ""


@pytest.mark.parametrize("verb", ["advance", "run"])
@pytest.mark.parametrize("execution", ["session", "worker"])
def test_cli_defaults_to_owner_and_can_explicitly_dispatch(verb, execution):
    parser = cli._build_parser()
    args = parser.parse_args([
        "loop",
        verb,
        "task",
        *(["--execution", execution] if execution == "worker" else []),
    ])
    assert cli._loop_inputs(args).execution == execution
    if verb == "run":
        rerun = cli._ceremony_rerun(args, "approved")
        assert ("--execution worker" in rerun) == (execution == "worker")


def test_invalid_execution_mode_is_refused():
    with pytest.raises(ValueError, match="execution"):
        loop.Inputs(execution="guess")


def test_session_owner_still_obeys_checkpoint_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "_declared_scope", lambda *_a: ())
    monkeypatch.setattr(classify, "classify", lambda *_a: SimpleNamespace(work_type="task"))
    monkeypatch.setattr(
        policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult("refused", "scope needs approval"),
    )
    result = loop._on_intake(_context(tmp_path, "intake"))
    assert result.needs_input == "authorization" and result.to_phase == "intake"


def test_session_owner_still_obeys_dispatch_admission(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    monkeypatch.setattr(
        loop,
        "_dispatch_refused",
        lambda *_a: loop._blocked(ctx, "scope refused", needs_input="scope"),
    )
    monkeypatch.setattr(loop, "_run_agent", lambda *_a: pytest.fail("admission skipped"))
    result = loop._dispatch_runner(ctx, "task", tmp_path)
    assert result.needs_input == "scope"
