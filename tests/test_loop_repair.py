from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import (
    decisions,
    loop,
    merge,
    policy,
    repair_brief,
    rubrics,
    runner,
    supervise,
    verify,
    worktree,
)
from basicly.config import PolicyConfig, RunnerConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus
from basicly.worktree import Session

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)

FAILING_CHECK = json.dumps([
    "python",
    "-c",
    "import sys; sys.stdout.write('E   assert 1 == 2\\n'); sys.exit(1)",
])


def _state(*, has_children: bool = False) -> NodeState:
    return NodeState(
        issue_id="i",
        status="in_progress",
        issue_type="task",
        phase="build",
        worktree=WorktreeBinding("i", "harness/i"),
        gates=GateStatus(False, (), (), ("verify",), ()),
        checkpoints=(),
        rework={},
        has_children=has_children,
    )


@pytest.fixture
def at(monkeypatch: pytest.MonkeyPatch):

    def _pin(state: NodeState) -> None:
        monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: state)

    return _pin


@pytest.fixture(autouse=True)
def _no_tracker_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: SimpleNamespace(stdout="{}"))
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [])


def _worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, checks: str = "") -> Path:

    path = tmp_path / "wt"
    path.mkdir()
    if checks:
        (path / "basicly.toml").write_text(checks, encoding="utf-8")
    session = Session(
        name="i",
        branch="harness/i",
        base="main",
        base_head="abc",
        worktree_path=str(path),
        created_at="2026-08-07T00:00:00Z",
    )
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: session)

    def _no_create(*_a, **_k):
        pytest.fail("a repair must never provision a worktree")

    monkeypatch.setattr(worktree, "create", _no_create)
    return path


def _pin_runner(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path]]:
    monkeypatch.setattr(
        loop,
        "load_runner_config",
        lambda *_a: RunnerConfig(specs=runner.BUILTIN_RUNNERS, default="claude"),
    )
    seen: list[tuple[str, Path]] = []

    def _run(spec, prompt, cwd, *_a, **_k):
        seen.append((prompt, Path(cwd)))
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    return seen


def _pin_rework(monkeypatch: pytest.MonkeyPatch, *, charged: int = 1, spent: int = 1) -> None:
    monkeypatch.setattr(policy, "record_rework", lambda *_a, **_k: charged)
    monkeypatch.setattr(
        policy,
        "record_finding_set",
        lambda _r, _i, _g, findings: policy.Convergence(
            policy.PROGRESSING, policy.finding_signature(findings), (), 0
        ),
    )
    monkeypatch.setattr(loop, "lane_rework_spent", lambda *_a, **_k: spent)


def _pin_failing_subtask(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "open")])
    monkeypatch.setattr(loop.loop_state, "blocked_ids", lambda *_a: ())
    monkeypatch.setattr(loop.decisions, "has_pending", lambda *_a, **_k: False)
    monkeypatch.setattr(loop, "_subtask_committed", lambda *_a: True)
    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda _r, m, *_a, **_k: verify.VerifyReport(m, (verify.CheckResult("pytest", "fail", 1),)),
    )
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))


def test_a_failed_gate_repairs_in_the_same_worktree_the_lane_already_has(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    seen = _pin_runner(monkeypatch)

    first = loop.advance(tmp_path, "i", config=CONFIG)
    assert first.blocked and "briefed a repair" in first.detail
    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file()

    second = loop.advance(tmp_path, "i", config=CONFIG)

    assert [c for _p, c in seen] == [cwd]
    assert "repaired i.1 in place" in second.detail and second.blocked


def test_a_repair_brief_is_consumed_so_one_failure_cannot_dispatch_twice(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    _pin_runner(monkeypatch)

    loop.advance(tmp_path, "i", config=CONFIG)
    loop.advance(tmp_path, "i", config=CONFIG)

    assert not (cwd / repair_brief.REPAIR_BRIEF_FILE).exists()
    assert repair_brief.take_repair_brief(cwd) is None


def test_a_supervised_dispatch_repairs_in_the_same_worktree_the_lane_has(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    cwd = _worktree(tmp_path, monkeypatch)
    brief = repair_brief.RepairBrief(
        issue_id="i",
        gate=verify.DEFAULT_GATE,
        reason="verify full failed: pytest",
        findings=("pytest",),
    )
    assert repair_brief.write_repair_brief(cwd, brief)
    monkeypatch.setattr(supervise, "_show_issue", lambda *_a, **_k: {})
    monkeypatch.setattr(supervise, "found_info_records", lambda *_a, **_k: ())
    monkeypatch.setattr(supervise, "answered_decisions", lambda *_a, **_k: ())

    bundle = supervise.build_bundle(tmp_path, "i", cwd=cwd)

    assert bundle.prompt == repair_brief.repair_prompt(brief)
    assert bundle.prompt != loop.dispatch_prompt("i")
    assert not (cwd / repair_brief.REPAIR_BRIEF_FILE).exists()
    assert supervise.build_bundle(tmp_path, "i", cwd=cwd).prompt == loop.dispatch_prompt("i")


def test_a_supervised_landing_leaves_the_repair_to_the_dispatch_step(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    monkeypatch.setattr(
        runner, "run", lambda *_a, **_k: pytest.fail("a landing pass must not dispatch")
    )

    loop.advance(tmp_path, "i", config=CONFIG, repair_dispatch=False)

    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file()


def test_the_repair_prompt_carries_the_gates_findings_not_the_build_text(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    checks = f'[[verify.checks]]\nname = "pytest"\ncommand = {FAILING_CHECK}\nmodes = ["fast"]\n'
    _worktree(tmp_path, monkeypatch, checks=checks)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    seen = _pin_runner(monkeypatch)

    loop.advance(tmp_path, "i", config=CONFIG)
    loop.advance(tmp_path, "i", config=CONFIG)

    prompt = seen[-1][0]
    assert f"Gate: {verify.DEFAULT_GATE}" in prompt
    assert "verify fast failed: pytest" in prompt
    assert "- pytest" in prompt
    assert "python -c" in prompt
    assert "E   assert 1 == 2" in prompt
    assert "Read AGENTS.md" not in prompt
    assert "do not re-plan the work" in prompt


def test_a_rubric_failure_briefs_the_deterministic_findings_only() -> None:
    verdicts = [
        rubrics.CheckVerdict("ac-1", rubrics.DETERMINISTIC, rubrics.NO, "no test names it"),
        rubrics.CheckVerdict(
            "ac-2", rubrics.JUDGED, rubrics.NO, "reads wrong to me", rubrics.SEVERITIES[0]
        ),
    ]

    evidence = loop._rubric_evidence(verdicts)

    assert [e.check for e in evidence] == ["ac-1"]
    assert evidence[0].output == "no test names it"


def test_a_landing_verify_failure_briefs_the_command_that_reproduces_it() -> None:
    failed = merge.MergeResult("i", "verify-failed", "verify full failed: pytest, ruff")

    evidence = loop._landing_evidence(failed, "full")

    assert [e.command for e in evidence] == ["basicly verify --mode full"]
    conflict = merge.MergeResult("i", "merge-conflicts", "conflicts in x.py", ("x.py",))
    assert loop._landing_evidence(conflict, "full") == ()


def test_per_gate_allowances_stop_compounding_at_the_lane_ceiling(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch, charged=1, spent=loop.lane_rework_ceiling(CONFIG))
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: pytest.fail("no repair past the ceiling"))
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        decisions, "enqueue", lambda _r, issue, kind, *_a, **_k: queued.append((issue, kind))
    )

    result = loop.advance(tmp_path, "i", config=CONFIG)

    assert result.action == "escalated"
    assert "total ceiling of 4" in result.detail
    assert queued == [("i.1", policy.REWORK_ESCALATION_KIND)]


def test_a_lane_inside_the_ceiling_keeps_repairing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch, charged=1, spent=loop.lane_rework_ceiling(CONFIG) - 1)
    monkeypatch.setattr(decisions, "enqueue", lambda *_a, **_k: pytest.fail("nothing to escalate"))

    result = loop.advance(tmp_path, "i", config=CONFIG)

    assert result.action == "blocked"
    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file()


def test_the_ceiling_is_never_stricter_than_the_per_gate_cap_it_bounds() -> None:
    for max_rework in range(0, 6):
        config = PolicyConfig(required_gates=("verify",), max_rework=max_rework)
        assert loop.lane_rework_ceiling(config) >= max_rework


def test_a_stale_brief_is_discarded_and_the_landing_runs_in_the_same_invocation(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    cwd = _worktree(tmp_path, monkeypatch)
    at(_state())
    brief = repair_brief.RepairBrief("i", verify.DEFAULT_GATE, "failed", branch_head="aaa1111")
    assert repair_brief.write_repair_brief(cwd, brief)
    monkeypatch.setattr(merge, "branch_head", lambda *_a, **_k: "bbb2222")
    done = merge.MergeResult("i", "merged", "landed @ bbb2222")
    merged: list[str] = []
    monkeypatch.setattr(loop.merge, "merge_worktree", lambda _r, n, **_k: merged.append(n) or done)
    notes: list[str] = []
    monkeypatch.setattr(
        loop, "_add_comment", lambda _r, _i, body: notes.append(body), raising=False
    )
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: pytest.fail("no repair on a stale brief"))

    result = loop.advance(tmp_path, "i", config=CONFIG)

    assert merged == ["i"], "the landing must run in this same invocation"
    assert result.needs_input is None
    assert notes and "may already be fixed" in notes[0] and "discarding it" in notes[0]
    assert "worktree 'i'" in notes[0]
