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

from basicly import (
    classify,
    decompose,
    loop,
    merge,
    needs_input,
    policy,
    rubrics,
    run_record,
    runner,
    verify,
    worktree,
)
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

    def _create(name: str, base: str | None = None, repo_root: Path | str | None = None) -> Session:
        created["n"] = name
        created["base"] = base
        created["repo_root"] = repo_root
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


def test_dispatch_prompt_documents_the_needs_input_protocol(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dispatch prompt tells the agent how to signal an unresolved fact (basicly-o774)."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    seen = {}

    def _run(spec, prompt, *_a, **_k):
        seen["prompt"] = prompt
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    _advance(tmp_path)
    assert needs_input.SENTINEL_FILE.as_posix() in seen["prompt"]
    assert "do NOT guess" in seen["prompt"].lower() or "not guess" in seen["prompt"].lower()


def test_dispatch_blocks_on_needs_input_sentinel(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean run that leaves a needs-input sentinel blocks the loop instead of landing.

    Exercised end-to-end against a real worktree dir: the agent writes the
    sentinel, the loop reads and consumes it, and surfaces the missing fact.
    """
    wt = tmp_path / "wt"
    (wt / needs_input.SENTINEL_FILE.parent).mkdir(parents=True)
    sentinel = wt / needs_input.SENTINEL_FILE
    at(_state("classify", issue_type="task"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)

    def _create(name: str, **_k) -> Session:
        return Session(
            name=name,
            branch=f"harness/{name}",
            base="main",
            base_head="abc",
            worktree_path=str(wt),
            created_at="2026-07-14T00:00:00Z",
        )

    monkeypatch.setattr(worktree, "create", _create)
    _pin_runner(monkeypatch, "claude")

    def _run(spec, *_a, **_k):
        # The agent gives up cleanly, leaving a structured needs-input signal.
        sentinel.write_text(
            '{"fact": "prod db dialect", "detail": "schema.sql has no vendor marker"}',
            encoding="utf-8",
        )
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    traced: list[tuple[str, str]] = []
    monkeypatch.setattr(
        policy, "record_needs_input", lambda _r, issue, fact: traced.append((issue, fact))
    )
    queued: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        loop.decisions,
        "enqueue",
        lambda _r, issue, kind, question, _detail="", **_k: queued.append((issue, kind, question)),
    )
    result = _advance(tmp_path)
    assert result.blocked
    assert result.needs_input == "prod db dialect"
    assert "needs input" in result.detail
    assert "schema.sql has no vendor marker" in result.detail
    # Consumed so a re-dispatch (once the fact is supplied) starts clean...
    assert not sentinel.exists()
    # ...which makes the durable marker the L3 audit trace (basicly-kjc5.3).
    assert traced == [("i", "prod db dialect")]
    # And the same event enters the one decision queue (basicly-kjc5.4).
    assert queued == [("i", "needs-input", "prod db dialect")]


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
    # Nothing executed: no transcript to meter (basicly-kjc5.1).
    assert entry["tokens"] is None and entry["cost"] is None and entry["estimated"] is None


def test_dispatch_record_captures_token_telemetry(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A usage-capturing dispatch lands adapter-reported tokens/cost on the record."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    stdout = (
        '{"type":"assistant","message":{"usage":'
        '{"input_tokens": 100, "output_tokens": 40}}}\n'
        '{"type": "result", "result": "ok", "total_cost_usd": 0.25,'
        ' "usage": {"input_tokens": 100, "output_tokens": 40}}'
    )
    seen = {}

    def _run(spec, _prompt, _cwd, **kwargs):
        seen["capture_usage"] = kwargs.get("capture_usage")
        return runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, stdout=stdout
        )

    monkeypatch.setattr(runner, "run", _run)
    _advance(tmp_path)

    assert seen["capture_usage"] is True  # the loop opts into usage reporting
    records = run_record.load_run_records(tmp_path)
    assert records is not None
    entry = records["i"][0]
    assert (entry["tokens"], entry["cost"], entry["estimated"]) == (140, 0.25, False)
    # The redacted command reflects the usage-capturing argv actually dispatched.
    assert entry["command"][-3:] == ["--output-format", "stream-json", "--verbose"]


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


def test_landing_gate_is_attributed_to_the_runner(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The verify gate recorded at landing carries the dispatched runner + model (basicly-140a)."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    run_record.record(
        tmp_path,
        "i",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude", "-p", run_record.REDACTED_PROMPT),
            model="opus",
        ),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **k: captured.update(k) or (True, "ok"))
    _advance(tmp_path)
    assert captured.get("actor") == "claude"


def test_blocked_landing_carries_the_merge_attempt(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A blocked landing exposes the merge result, so a driver need not parse text.

    The supervisor routes a scope collision differently from a red gate or an
    uncommitted worktree (basicly-kjc5.20) and reads the shape off this field.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    attempt = merge.MergeResult("i", "merge-conflicts", "conflicts in: x.py", conflicts=("x.py",))
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)

    result = _advance(tmp_path)

    assert result.blocked and result.landing is attempt
    assert attempt.conflicted and attempt.conflicts == ("x.py",)


def test_not_ready_landing_carries_the_merge_attempt(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An uncommitted worktree is identifiable as such without reading the message."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    attempt = merge.MergeResult("i", "not-ready", "commit the work on 'harness/i' before landing")
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)

    result = _advance(tmp_path)

    assert result.blocked and result.landing is attempt and not attempt.conflicted


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
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        loop.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: queued.append((issue, kind)),
    )
    result = _advance(tmp_path)
    assert result.action == "escalated" and "merge failed" in result.detail
    # An escalation is a judgment call: it enters the decision queue (kjc5.4).
    assert queued == [("i", "escalation")]


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
    """The loop passes [worktree].base_branch and its repo root to create."""
    created = _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    monkeypatch.setattr(
        loop,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(base_branch="main", concurrency=4),
    )
    _advance(tmp_path)
    assert created["base"] == "main"
    # The node's repo root, not the process cwd: provisioning must follow the
    # repository the advance was handed (basicly-kjc5.27).
    assert created["repo_root"] == tmp_path


# --- lane mini-loop (basicly-kjc5.9, factory design D4/D7) ------------------


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
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert reworked == [("i.1", "verify")]
    assert result.blocked and "verify fast failed: pytest" in result.detail


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

    def _merge(_root, name, *, bead, verify_mode):
        landed["name"], landed["bead"], landed["mode"] = name, bead, verify_mode
        return merge.MergeResult(name, "merged", "landed")

    monkeypatch.setattr(merge, "merge_worktree", _merge)
    # Even a `fast` mode asked for on the command line cannot downgrade a lane
    # integration: the change class picks the mode, not the caller.
    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(verify_mode="fast"))
    assert landed == {"name": "i", "bead": "i", "mode": "full"}
    assert calls["verify"] == ["full"] and calls["gates"] == [("i", "full")]
    assert result.to_phase == "verify" and result.action == "merged"


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
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert recorded == ["i"]
    assert result.blocked and "lane validate failed: acceptance" in result.detail


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
            rubrics.CheckVerdict("acceptance", rubrics.JUDGED, answer, "criterion 2 unevidenced")
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
    assert "acceptance" in question
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
