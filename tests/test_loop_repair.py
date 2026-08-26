"""Repair in place: a failed gate briefs the run that fixes it (basicly-u2hl.4).

Three properties, one per acceptance criterion, and each is a property of the
*engine* rather than of a prompt string:

- ``same_worktree`` — a repair runs in the worktree the lane already has. Asserted
  by making ``worktree.create`` fail the test if it is ever reached, so the
  property cannot be satisfied by a dispatch that happens to be handed the right
  path.
- ``findings`` — the repair prompt carries the failing gate, the command and the
  output, and is not the fixed build text. Asserted against a real check that
  really fails, so the command and the output are observed rather than composed.
- ``ceiling`` — per-gate allowances stop compounding at a lane total.
"""

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

# A check that always fails and prints something recognisable, so the brief's
# command and output are read off a real run rather than invented (the fixture
# rule basicly-m4zv.6 records: a dependency's output must be observed).
FAILING_CHECK = json.dumps([
    "python",
    "-c",
    "import sys; sys.stdout.write('E   assert 1 == 2\\n'); sys.exit(1)",
])


def _state(*, has_children: bool = False) -> NodeState:
    """A build-phase node bound to its own worktree."""
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
    """Pin the node state the loop resumes from."""

    def _pin(state: NodeState) -> None:
        monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: state)

    return _pin


@pytest.fixture(autouse=True)
def _no_tracker_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests run outside a git repo and must never reach the real tracker."""
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: SimpleNamespace(stdout="{}"))
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [])


def _worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, checks: str = "") -> Path:
    """A real on-disk worktree the loop's session record points at.

    Real, because the brief is a file in it: a fake path would make the write a
    no-op and every assertion below would pass against nothing.
    """
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
    """Pin a headless runner and record every (prompt, cwd) it is dispatched with."""
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
    """Charge *charged* attempts for the failing gate and *spent* across the lane."""
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
    """A lane whose only sub-task is committed and fails its verify gate.

    The sub-task path, because it is the one the loop holds the gate's own report
    on — the engine fixes its mode at ``fast`` (D4), so the test does not choose it.
    """
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


# --- same_worktree ----------------------------------------------------------


def test_a_failed_gate_repairs_in_the_same_worktree_the_lane_already_has(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The red gate briefs the tree, and the next advance repairs in that same tree.

    Two advances, because that is the loop: the first sees the gate fail and the
    second is the one that would previously have re-run the same landing with
    nothing changed.
    """
    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    seen = _pin_runner(monkeypatch)

    first = loop.advance(tmp_path, "i", config=CONFIG)
    assert first.blocked and "briefed a repair" in first.detail
    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file()

    second = loop.advance(tmp_path, "i", config=CONFIG)

    assert [c for _p, c in seen] == [cwd]  # the bound worktree, and only it
    assert "repaired i.1 in place" in second.detail and second.blocked


def test_a_repair_brief_is_consumed_so_one_failure_cannot_dispatch_twice(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The brief is one round's, so a second read finds nothing to repair."""
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
    """The supervisor's bundle reads the brief out of the worktree it dispatches into.

    The supervised path must not repair from inside a landing (that would spawn an
    agent outside the pass's spend bound), so the brief survives the landing and is
    picked up by the next dispatch into the same tree.
    """
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
    assert not (
        cwd / repair_brief.REPAIR_BRIEF_FILE
    ).exists()  # consumed by the dispatch it briefed
    # And with no brief in the tree it is the ordinary build dispatch, unchanged.
    assert supervise.build_bundle(tmp_path, "i", cwd=cwd).prompt == loop.dispatch_prompt("i")


def test_a_supervised_landing_leaves_the_repair_to_the_dispatch_step(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``repair_dispatch=False`` blocks the landing from spawning an agent itself."""
    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    monkeypatch.setattr(
        runner, "run", lambda *_a, **_k: pytest.fail("a landing pass must not dispatch")
    )

    loop.advance(tmp_path, "i", config=CONFIG, repair_dispatch=False)

    # Still there for ``supervise._dispatch_lane`` to read on the next pass.
    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file()


# --- findings ---------------------------------------------------------------


def test_the_repair_prompt_carries_the_gates_findings_not_the_build_text(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gate, command and output reach the run; the fixed build instruction does not.

    The check really runs and really fails, so the command and the output in the
    prompt are the ones the gate produced.
    """
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
    assert "verify fast failed: pytest" in prompt  # the verdict
    assert "- pytest" in prompt  # the finding
    assert "python -c" in prompt  # the command the gate ran
    assert "E   assert 1 == 2" in prompt  # its output
    # Not a build brief: re-planning the work is exactly what a repair must not do.
    assert "Read AGENTS.md" not in prompt
    assert "do not re-plan the work" in prompt


def test_a_rubric_failure_briefs_the_deterministic_findings_only() -> None:
    """A judged ``no`` is a decision a human owns, so it never briefs a repair."""
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
    """The landing gate's finding set is a rendering; the brief adds how to re-run it."""
    failed = merge.MergeResult("i", "verify-failed", "verify full failed: pytest, ruff")

    evidence = loop._landing_evidence(failed, "full")

    assert [e.command for e in evidence] == ["basicly verify --mode full"]
    # A collision is not a check a repair run can re-run.
    conflict = merge.MergeResult("i", "merge-conflicts", "conflicts in x.py", ("x.py",))
    assert loop._landing_evidence(conflict, "full") == ()


# --- ceiling ----------------------------------------------------------------


def test_per_gate_allowances_stop_compounding_at_the_lane_ceiling(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane at the total escalates even though this gate's own allowance is unspent.

    The compounding D12 names: with ``max_rework=2`` and three gates that can
    charge, nothing bounded a lane at 6 attempts because no single counter ever
    reached 2. The ceiling is 4, and the lane stops there.
    """
    _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    # This gate's first attempt — nowhere near its own cap of 2.
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
    """One attempt below the total is still the lane's to spend."""
    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch, charged=1, spent=loop.lane_rework_ceiling(CONFIG) - 1)
    monkeypatch.setattr(decisions, "enqueue", lambda *_a, **_k: pytest.fail("nothing to escalate"))

    result = loop.advance(tmp_path, "i", config=CONFIG)

    assert result.action == "blocked"
    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file()


def test_the_ceiling_is_never_stricter_than_the_per_gate_cap_it_bounds() -> None:
    """A total below ``max_rework`` would escalate a gate before its own allowance."""
    for max_rework in range(0, 6):
        config = PolicyConfig(required_gates=("verify",), max_rework=max_rework)
        assert loop.lane_rework_ceiling(config) >= max_rework


# --- a brief the branch has moved past (basicly-1djm17) -----------------------


def test_a_stale_brief_is_discarded_and_the_landing_runs_in_the_same_invocation(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hand repair moved the head, so the brief is dropped and the landing runs.

    Observed on basicly-0xtzf1: the advance refused with "re-run the gate to raise a brief
    against what is there now", and there was no such step left — ``take_repair_brief``
    consumed the brief on the very read that judged it stale, so the next advance landed
    normally. The refusal cost one invocation and one human read per hand repair.
    """
    cwd = _worktree(tmp_path, monkeypatch)
    at(_state())
    brief = repair_brief.RepairBrief("i", verify.DEFAULT_GATE, "failed", branch_head="aaa1111")
    assert repair_brief.write_repair_brief(cwd, brief)
    monkeypatch.setattr(merge, "branch_head", lambda *_a, **_k: "bbb2222")  # the hand fix
    done = merge.MergeResult("i", "merged", "landed @ bbb2222")
    merged: list[str] = []
    monkeypatch.setattr(loop.merge, "merge_worktree", lambda _r, n, **_k: merged.append(n) or done)
    notes: list[str] = []
    # ``raising=False`` so this reds on the missing landing, not on a missing attribute:
    # the defect is the refusal, and a test that stops earlier never asserts it.
    monkeypatch.setattr(
        loop, "_add_comment", lambda _r, _i, body: notes.append(body), raising=False
    )
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: pytest.fail("no repair on a stale brief"))

    result = loop.advance(tmp_path, "i", config=CONFIG)

    assert merged == ["i"], "the landing must run in this same invocation"
    assert result.needs_input is None
    assert notes and "may already be fixed" in notes[0] and "discarding it" in notes[0]
    assert "worktree 'i'" in notes[0]
