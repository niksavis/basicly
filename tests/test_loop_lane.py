"""The lane mini-loop: one bound worktree driving its sub-tasks in turn (basicly-kjc5.9).

Split out of ``test_loop.py`` so the lane's own state machine reads as one file
(factory design D4/D7). A lane is a build-phase node bound to a worktree with
sub-task beads under it, and what these tests pin is the order it does things in:
it records its plan, refuses a plan the sub-task bound cannot hold, dispatches the
next open sub-task into the worktree it already has, fast-verifies each one, and
only integrates with a full verify once every sub-task has closed. Each test fakes
the composed modules and asserts the advance the engine chose, not a prompt string.

The lane's validate gate and its rubric dispute live next door in
``test_loop_lane_validate.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import decompose, loop, merge, policy, runner, verify, worktree
from basicly.config import PolicyConfig, RunnerConfig
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


def _pin_runner(monkeypatch: pytest.MonkeyPatch, default: str) -> None:
    """Pin the loop's runner selection to a built-in adapter by name."""
    monkeypatch.setattr(
        loop,
        "load_runner_config",
        lambda *_a: RunnerConfig(specs=runner.BUILTIN_RUNNERS, default=default),
    )


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


def _no_rubrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """No rubric covers the lane's work class: validate has nothing to check."""
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [])


def test_lane_records_its_subtask_plan_then_blocks(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bound node with a sub-task plan decomposes in place and stays in build."""
    at(_lane(has_children=False))
    planned = {}

    def _decompose(_root, feature_id, children):
        planned["feature"], planned["n"] = feature_id, len(children)
        return decompose.DecomposeResult(feature_id, (), (("i.1",),))

    monkeypatch.setattr(decompose, "decompose", _decompose)
    child = decompose.ChildSpec("t", ("ac",), ("src/x.py",))
    result = _advance(tmp_path, children=(child, child))
    assert planned == {"feature": "i", "n": 2}
    assert result.to_phase == "build" and result.blocked
    assert "advance again to run them in sequence" in result.detail


def test_lane_plan_over_the_subtask_bound_is_refused(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """max_subtasks_per_lane bounds the plan before anything is recorded (design §6)."""
    at(_lane(has_children=False))

    def _no_decompose(*_a, **_k):
        raise AssertionError("an over-bound plan must not be recorded")

    monkeypatch.setattr(decompose, "decompose", _no_decompose)
    config = PolicyConfig(required_gates=("verify",), max_rework=2, max_subtasks_per_lane=2)
    child = decompose.ChildSpec("t", ("ac",), ("src/x.py",))
    result = loop.advance(
        tmp_path, "i", config=config, inputs=loop.Inputs(children=(child, child, child))
    )
    assert result.blocked and "max_subtasks_per_lane bound (2)" in result.detail


def test_lane_with_too_many_subtask_beads_blocks(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sub-task beads created out of band are bounded too, before any dispatch."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[(f"i.{n}", "open") for n in range(3)])
    config = PolicyConfig(required_gates=("verify",), max_rework=2, max_subtasks_per_lane=2)
    result = loop.advance(tmp_path, "i", config=config)
    assert result.blocked and "over the [policy] max_subtasks_per_lane bound (2)" in result.detail


def test_lane_dispatches_the_next_subtask_fresh_and_fast_verifies_it(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One fresh dispatch per sub-task in the lane worktree, then a fast verify (D4/D7)."""
    at(_lane())
    calls = _pin_lane(monkeypatch, subtasks=[("i.1", "open"), ("i.2", "open")])
    _pin_runner(monkeypatch, "claude")
    dispatched = {}

    def _run(spec, prompt, cwd, **_k):
        dispatched["prompt"], dispatched["cwd"] = prompt, cwd
        # The commit lands during the run, as a real dispatch would.
        monkeypatch.setattr(loop, "_subtask_committed", lambda *_a: True)
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    result = loop.advance(tmp_path, "i", config=CONFIG)

    assert "i.1" in dispatched["prompt"] and dispatched["cwd"] == Path("/tmp/i")
    assert calls["verify"] == ["fast"] and calls["gates"] == [("i.1", "fast")]
    assert calls["closed"] == ["i.1"]
    assert result.action == "sub-task" and result.progressed and not result.blocked
    assert result.to_phase == "build" and "sub-task 1/2 (i.1)" in result.detail


def test_lane_runs_subtasks_in_order_skipping_closed_ones(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resumed lane picks up at the first still-open sub-task, never re-running one."""
    at(_lane())
    calls = _pin_lane(
        monkeypatch,
        subtasks=[("i.1", "closed"), ("i.2", "open"), ("i.3", "open")],
        committed=("i.2",),
    )
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner, "run", lambda *_a, **_k: pytest.fail("a committed sub-task must not re-dispatch")
    )
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert calls["closed"] == ["i.2"] and calls["gates"] == [("i.2", "fast")]
    assert "sub-task 2/3 (i.2)" in result.detail


def test_lane_handoff_blocks_for_the_driving_agent(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A handoff runner leaves the sub-task to the driving agent and blocks."""
    at(_lane())
    calls = _pin_lane(monkeypatch, subtasks=[("i.1", "open")])
    _pin_runner(monkeypatch, "manual")
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert result.blocked and "awaiting the agent's work" in result.detail
    assert "sub-task 1/1 (i.1)" in result.detail
    assert calls["closed"] == [] and calls["verify"] == []


def test_lane_subtask_without_a_commit_reworks_the_subtask(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean run that committed nothing is bounded on the sub-task's own record."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "open")])
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0
        ),
    )
    reworked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        policy, "record_rework", lambda _r, issue, gate: reworked.append((issue, gate)) or 1
    )
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert reworked == [("i.1", "verify")]
    assert result.blocked and "without committing anything referencing i.1" in result.detail


def test_lane_subtask_verify_failure_reworks_the_subtask_not_the_lane(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed fast verify bounds the sub-task, so one bad step cannot burn the lane."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "open")], committed=("i.1",))
    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda _r, mode, *_a, **_k: verify.VerifyReport(
            mode, (verify.CheckResult("pytest", "fail", 1),)
        ),
    )
    reworked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        policy, "record_rework", lambda _r, issue, gate: reworked.append((issue, gate)) or 1
    )
    findings = _pin_finding_sets(monkeypatch)
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert reworked == [("i.1", "verify")]
    assert result.blocked and "verify fast failed: pytest" in result.detail
    # The sub-task's own finding set, on its own record, so a repeat is detectable.
    assert findings == [("i.1", "verify", ("pytest",))]


def test_lane_follows_the_dependency_chain_not_the_tracker_order(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The blocks chain decides what runs next, not the order br lists dependents in.

    Same-scope sub-tasks are serialized by a ``blocks`` chain at decompose time, so
    the chain head is the only unblocked one — that is what makes the sequence
    strict (D7), not the order the tracker happens to return.
    """
    at(_lane())
    calls = _pin_lane(
        monkeypatch,
        subtasks=[("i.2", "open"), ("i.1", "open")],
        committed=("i.1",),
        blocked=("i.2",),
    )
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert calls["closed"] == ["i.1"]
    assert result.action == "sub-task" and "(i.1)" in result.detail


def test_lane_holds_a_subtask_waiting_on_a_decision(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sub-task with a queued judgment is not re-dispatched into the same block."""
    at(_lane())
    _pin_lane(monkeypatch, subtasks=[("i.1", "open")], pending=("i.1",))
    monkeypatch.setattr(
        runner, "run", lambda *_a, **_k: pytest.fail("a held sub-task must not dispatch")
    )
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert result.blocked and "waiting on a dependency or a queued decision" in result.detail


def test_lane_integrates_with_full_verify_once_every_subtask_closes(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """All sub-tasks closed: the lane lands under full verify and moves to verify (D4)."""
    at(_lane())
    calls = _pin_lane(monkeypatch, subtasks=[("i.1", "closed"), ("i.2", "closed")])
    _no_rubrics(monkeypatch)
    landed = {}

    def _merge(_root, name, *, bead, verify_mode, override_gate):
        landed["name"], landed["bead"], landed["mode"] = name, bead, verify_mode
        landed["override"] = override_gate
        return merge.MergeResult(name, "merged", "landed")

    monkeypatch.setattr(merge, "merge_worktree", _merge)
    # Even a `fast` mode asked for on the command line cannot downgrade a lane
    # integration: the change class picks the mode, not the caller.
    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(verify_mode="fast"))
    # override False: nothing answered a `land anyway`, so the landing keeps its gate.
    assert landed == {"name": "i", "bead": "i", "mode": "full", "override": False}
    assert calls["verify"] == ["full"] and calls["gates"] == [("i", "full")]
    assert result.to_phase == "verify" and result.action == "merged"
