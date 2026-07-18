"""Tests for the checkpoint-gated loop state machine (onb.6.3).

The machine derives its phase from br every step, so each test pins a NodeState
(the resume point) and fakes the composed modules. The invariant under test:
every step either blocks or drives a br-state change that moves the derived
phase forward — the handlers and derive_phase never disagree.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import classify, decompose, loop, merge, policy, run_record, runner, verify, worktree
from basicly.config import PolicyConfig, RunnerConfig, WorktreeConfig
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


def _pin_runner(monkeypatch: pytest.MonkeyPatch, default: str) -> None:
    """Pin the loop's runner selection to a built-in adapter by name."""
    monkeypatch.setattr(
        loop,
        "load_runner_config",
        lambda *_a: RunnerConfig(specs=runner.BUILTIN_RUNNERS, default=default),
    )


def _ready_leaf(at, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Pin a ready leaf at classify with a fake worktree; return the create record."""
    at(_state("classify", issue_type="task"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    created = {}

    def _create(name: str, base: str | None = None) -> Session:
        created["n"] = name
        created["base"] = base
        return _session(name)

    monkeypatch.setattr(worktree, "create", _create)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    return created


def test_classify_leaf_provisions_worktree(
    at,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracker_commits: list[tuple[str, str | None]],
) -> None:
    """A ready leaf publishes its claim, then provisions; the handoff blocks unchanged."""
    created = _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    result = _advance(tmp_path)
    assert created["n"] == "i"
    assert tracker_commits == [("i", "record the claim before provisioning")]
    assert result.blocked and "provisioned" in result.detail
    assert "awaiting the agent's work" in result.detail


def test_classify_leaf_dispatches_headless_runner(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A headless runner is dispatched in the worktree with the agent-neutral prompt."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    calls = {}

    def _run(spec, prompt, cwd, **_k):
        calls["spec"], calls["prompt"], calls["cwd"] = spec, prompt, cwd
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    result = _advance(tmp_path)
    assert calls["spec"].name == "claude"
    assert calls["cwd"] == Path("/tmp/i")
    assert "i" in calls["prompt"] and "AGENTS.md" in calls["prompt"]
    assert "Do not merge" in calls["prompt"]
    assert result.blocked and "runner 'claude' finished" in result.detail


def test_dispatch_writes_a_run_record_keyed_by_bead(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every dispatch persists a metadata-only run-record under the bead id."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, duration_s=0.5
        ),
    )
    _advance(tmp_path)

    records = run_record.load_run_records(tmp_path)
    assert records is not None
    entry = records["i"][0]
    assert entry["agent"] == "claude"
    assert entry["outcome"] == "executed"
    assert entry["duration_s"] == 0.5
    assert entry["model"] is None  # this runner pins no model
    # Redaction: the persisted command carries the placeholder, never the prompt.
    assert run_record.REDACTED_PROMPT in entry["command"]
    assert not any("AGENTS.md" in part for part in entry["command"])


def test_dispatch_record_stamps_model_provenance(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A runner pinning a model stamps it as provenance on the run-record (basicly-45ld)."""
    _ready_leaf(at, monkeypatch)
    pinned = runner.RunnerSpec(
        "claude", runner.HEADLESS, ("claude", "-p", "{prompt}"), model="opus"
    )
    monkeypatch.setattr(
        loop, "load_runner_config", lambda *_a: RunnerConfig(specs=(pinned,), default="claude")
    )
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, duration_s=0.2
        ),
    )
    _advance(tmp_path)

    records = run_record.load_run_records(tmp_path)
    assert records is not None
    assert records["i"][0]["model"] == "opus"


def test_dispatch_record_redacts_a_stdin_runner(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stdin runner injects no prompt into argv, so none can reach the record."""
    _ready_leaf(at, monkeypatch)
    stdin_spec = runner.RunnerSpec("x", runner.HEADLESS, ("x", "--headless"), prompt_via="stdin")
    monkeypatch.setattr(
        loop, "load_runner_config", lambda *_a: RunnerConfig(specs=(stdin_spec,), default="x")
    )
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, duration_s=0.1
        ),
    )
    _advance(tmp_path)

    records = run_record.load_run_records(tmp_path)
    assert records is not None
    assert records["i"][0]["command"] == ["x", "--headless"]  # bare argv: nothing to redact


def test_dispatch_record_captures_a_handoff(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A handoff dispatch still records — with an empty command and handoff outcome."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    _advance(tmp_path)

    records = run_record.load_run_records(tmp_path)
    assert records is not None
    entry = records["i"][0]
    assert entry["outcome"] == "handoff"
    assert entry["command"] == []
    assert entry["duration_s"] is None


def test_classify_leaf_reports_failed_runner(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failing headless run blocks with the runner name and exit code."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "codex")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, (), executed=True, returncode=2, stderr="boom\n"
        ),
    )
    result = _advance(tmp_path)
    assert result.blocked
    assert "runner 'codex' failed" in result.detail
    assert "exit 2" in result.detail and "boom" in result.detail


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
    """Ship cleans up the worktree, closes the issue, and commits the tracker."""
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    torn = {}
    monkeypatch.setattr(worktree, "cleanup", lambda name, **_k: torn.setdefault("n", name))
    closed = {}
    monkeypatch.setattr(loop, "_run_br", lambda _r, args, **_k: closed.setdefault("args", args))
    committed = {}
    monkeypatch.setattr(
        loop.merge,
        "commit_tracker_state",
        lambda _r, bead, **_k: committed.setdefault("bead", bead) or True,
    )
    result = _advance(tmp_path)
    assert torn["n"] == "i"
    assert closed["args"][:2] == ["close", "i"]
    assert committed["bead"] == "i"
    assert result.to_phase == "done" and result.action == "tore-down"
    assert "tracker state committed" in result.detail


def test_ship_refuses_an_unmerged_worktree(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ship blocks with no side effects when the worktree branch never landed.

    Regression (basicly-o0q3): recording the verify gate out-of-band skips the
    build->verify merge, so the code is stranded on the harness branch. Ship must
    refuse to close/teardown rather than close a bead whose work never merged.
    """
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: False)

    def _boom(*_a, **_k):
        raise AssertionError("a stranded node must not be closed, torn down, or committed")

    monkeypatch.setattr(worktree, "cleanup", _boom)
    monkeypatch.setattr(loop, "_run_br", _boom)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", _boom)

    result = _advance(tmp_path)
    assert result.blocked
    assert result.to_phase == result.from_phase  # stays at ship, not "done"
    assert "not merged" in result.detail


def test_ship_proceeds_when_the_worktree_landed(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A landed worktree ships normally: the guard permits close + teardown."""
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    torn = {}
    monkeypatch.setattr(worktree, "cleanup", lambda name, **_k: torn.setdefault("n", name))
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    result = _advance(tmp_path)
    assert torn["n"] == "i"
    assert result.to_phase == "done" and result.action == "tore-down"


def test_worktree_landed_missing_branch_counts_as_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A branch that no longer exists was merged and cleaned (git branch -d) -> landed."""
    monkeypatch.setattr(
        worktree, "git", lambda _args, **_k: SimpleNamespace(returncode=1)
    )  # show-ref: not found
    assert loop._worktree_landed(Path("/x"), WorktreeBinding("i", "harness/i")) is True


def test_worktree_landed_ancestor_of_base_is_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing branch whose tip is an ancestor of base HEAD has landed."""

    def git(_args, **_k):
        return SimpleNamespace(returncode=0)  # show-ref exists (0), merge-base is-ancestor (0)

    monkeypatch.setattr(worktree, "git", git)
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session("i"))
    assert loop._worktree_landed(Path("/x"), WorktreeBinding("i", "harness/i")) is True


def test_worktree_landed_non_ancestor_is_stranded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing branch not reachable from base HEAD is stranded (never merged)."""

    def git(args, **_k):
        # show-ref exists (0); merge-base --is-ancestor fails (1) => not merged
        return SimpleNamespace(returncode=0 if args[0] == "show-ref" else 1)

    monkeypatch.setattr(worktree, "git", git)
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session("i"))
    assert loop._worktree_landed(Path("/x"), WorktreeBinding("i", "harness/i")) is False


@pytest.mark.parametrize("phase", ["build", "ship"])
def test_base_checkout_phase_refuses_a_linked_worktree(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str
) -> None:
    """build/ship advanced from a linked worktree blocks without merging or shipping.

    Regression (basicly-9niw): advancing from inside a loop worktree once stranded
    a commit (child closed but unmerged); the guard refuses and mutates nothing.
    """
    at(_state(phase, worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(worktree, "is_linked_checkout", lambda *_a, **_k: True)

    def _boom(*_a, **_k):
        raise AssertionError("must not merge/ship from a linked worktree")

    monkeypatch.setattr(merge, "merge_worktree", _boom)
    monkeypatch.setattr(worktree, "cleanup", _boom)

    result = _advance(tmp_path)
    assert result.blocked and result.needs_input == "base-checkout"
    assert result.to_phase == result.from_phase
    assert "base checkout" in result.detail


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


def test_ensure_child_worktrees_publishes_claims_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracker_commits: list[tuple[str, str | None]],
) -> None:
    """Fan-out provisioning publishes pending tracker claims before any worktree."""
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    monkeypatch.setattr(
        loop,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(base_branch=None, concurrency=4),
    )
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(loop.loop_state, "ready_ranked", lambda *_a, **_k: ())

    loop._ensure_child_worktrees(ctx, [("i.1", "in_progress")])
    assert tracker_commits == [("i", "record the claim before provisioning")]


def test_classify_leaf_forks_from_the_configured_base(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The loop passes [worktree].base_branch to create, like the CLI path."""
    created = _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    monkeypatch.setattr(
        loop,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(base_branch="main", concurrency=4),
    )
    _advance(tmp_path)
    assert created["base"] == "main"


def test_classify_leaf_blocks_at_the_concurrency_cap(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A leaf refuses to provision past [worktree].concurrency."""
    created = _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    monkeypatch.setattr(
        loop,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(base_branch=None, concurrency=2),
    )
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [_session("a"), _session("b")])
    result = _advance(tmp_path)
    assert result.blocked and "concurrency cap" in result.detail
    assert "n" not in created
