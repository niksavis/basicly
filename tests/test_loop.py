from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import (
    classify,
    decisions,
    decompose,
    dispatch_brief,
    loop,
    loop_state,
    merge,
    needs_input,
    policy,
    repair_brief,
    retrospective,
    roles,
    run_record,
    runner,
    verify,
    working_set,
    worktree,
)
from basicly.config import (
    DEFAULT_WORKING_SET_MIN,
    LOOP_PHASES,
    PolicyConfig,
    RunnerConfig,
    SizingConfig,
    WorktreeConfig,
)
from basicly.loop_state import NodeState, RankedNode, WorktreeBinding
from basicly.policy import DoRResult, GateStatus
from basicly.working_set import WorkingSetAdmission
from basicly.worktree import Session
from tests import fake_tracker

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _gate(can_advance: bool) -> GateStatus:
    return GateStatus(can_advance, (), (), () if can_advance else ("verify",), ())


def _state(
    phase: str,
    *,
    issue_type: str = "task",
    worktree: WorktreeBinding | None = None,
    has_children: bool = False,
    gates: GateStatus | None = None,
) -> NodeState:
    return NodeState(
        issue_id="i",
        status="in_progress",
        issue_type=issue_type,
        phase=phase,
        worktree=worktree,
        gates=gates if gates is not None else _gate(can_advance=phase == "verify"),
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
def tracker_commits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    calls: list[tuple[str, str | None]] = []

    def _record(_repo_root, bead, **kwargs):
        calls.append((bead, kwargs.get("action")))
        return True

    monkeypatch.setattr(loop.merge, "commit_tracker_state", _record)
    return calls


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:

    items: list[tuple[str, str]] = []
    monkeypatch.setattr(
        loop.decisions, "enqueue", lambda _r, issue, kind, *_a, **_k: items.append((issue, kind))
    )
    return items


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


def test_intake_blocks_without_work_type(at, tmp_path: Path) -> None:
    at(_state("intake"))
    result = _advance(tmp_path)
    assert result.blocked and result.needs_input == "work_type"


def test_intake_records_type_then_waits_for_checkpoint(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("intake"))
    recorded = {}

    def _classify(_r, _i, wt, _s):
        recorded["wt"] = wt
        return classify.ClassifyResult("i", wt, DoRResult(True, ()))

    monkeypatch.setattr(classify, "classify", _classify)
    result = _advance(tmp_path, work_type="feature")
    assert recorded["wt"] == "feature"
    assert result.blocked and "classify checkpoint" in result.detail


def test_classify_blocks_when_dor_incomplete(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("classify"))
    monkeypatch.setattr(
        policy, "definition_of_ready", lambda *_a: DoRResult(False, ("## Acceptance Criteria",))
    )
    result = _advance(tmp_path)
    assert result.blocked and "definition of ready" in result.detail


def test_classify_dor_block_names_the_scaffold_for_the_recorded_type(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("classify", issue_type="bug"))
    monkeypatch.setattr(
        policy, "definition_of_ready", lambda *_a: DoRResult(False, ("## Steps to Reproduce",))
    )
    detail = _advance(tmp_path).detail
    assert "## Steps to Reproduce" in detail
    assert "basicly policy scaffold --type bug" in detail


def _pin_runner(monkeypatch: pytest.MonkeyPatch, default: str) -> None:
    monkeypatch.setattr(
        loop,
        "load_runner_config",
        lambda *_a: RunnerConfig(specs=runner.BUILTIN_RUNNERS, default=default),
    )


def _pin_finding_sets(
    monkeypatch: pytest.MonkeyPatch, *verdicts: policy.Convergence
) -> list[tuple[str, str, tuple[str, ...]]]:

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


def _stalled(rounds: int, *members: str) -> policy.Convergence:
    return policy.Convergence(policy.STALLED, members, members, rounds)


def _ready_leaf(at, monkeypatch: pytest.MonkeyPatch) -> dict:
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
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)

    def _show(_repo_root: Path, args: list[str], **_k) -> SimpleNamespace:
        payload = json.dumps([{"id": "i", "title": "i", "description": "prose\n"}])
        return SimpleNamespace(stdout=payload if args[:1] == ["show"] else "{}", returncode=0)

    fake_tracker.install(monkeypatch, _show)
    return created


def test_classify_leaf_provisions_worktree(
    at,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracker_commits: list[tuple[str, str | None]],
) -> None:
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


def _never_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner, "run", lambda *_a, **_k: pytest.fail("a refused dispatch must not spawn a runner")
    )


def test_a_halted_grant_does_not_refuse_the_interactive_dispatch(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    spawned: list[str] = []

    def _run(spec, *_a, **_k):
        spawned.append(spec.name)
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    monkeypatch.setattr(
        loop.policy,
        "spend_status",
        lambda *_a, **_k: policy.SpendStatus(
            grant=policy.Grant(level="L1", token_budget=100),
            spent_tokens=500,
            halted=True,
            detail="L1 grant token_budget spent (500/100 tokens under this grant)",
        ),
    )

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="epic")

    assert spawned == ["claude"], "a spent budget still refused the dispatch"
    assert result.needs_input != "grant"
    assert "refused before it started" not in result.detail


def test_a_dispatch_with_no_session_root_is_ungated_as_before(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        loop.policy,
        "spend_status",
        lambda *_a, **_k: pytest.fail("no session root means no ledger to consult"),
    )
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0
        ),
    )

    result = _advance(tmp_path)

    assert result.blocked and "finished in worktree" in result.detail


def test_an_oversized_bead_is_refused_by_the_band_at_the_interactive_dispatch(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    _never_runs(monkeypatch)
    monkeypatch.setattr(loop.policy, "spend_status", lambda *_a, **_k: _unhalted())
    monkeypatch.setattr(
        working_set,
        "admit_working_set",
        lambda *_a, **_k: WorkingSetAdmission(
            "i", None, "child 'i' estimates 900000 working-set tokens, above 64000", refused=True
        ),
    )
    monkeypatch.setattr(working_set, "escalate_working_set", lambda *_a, **_k: None)

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="epic")

    assert result.blocked and result.needs_input == "scope"
    assert "900000 working-set tokens" in result.detail


def test_a_scopeless_bead_still_dispatches_but_is_escalated(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(loop.policy, "spend_status", lambda *_a, **_k: _unhalted())
    escalated: list[str] = []
    monkeypatch.setattr(
        working_set,
        "admit_working_set",
        lambda *_a, **_k: WorkingSetAdmission(
            "i",
            None,
            "declares no scope the estimator can read",
            refused=False,
            absence="undeclared",
        ),
    )
    monkeypatch.setattr(
        working_set,
        "escalate_working_set",
        lambda _r, admission: escalated.append(admission.issue_id),
    )
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0
        ),
    )

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="epic")

    assert escalated == ["i"], "the never-checked notice must be recorded here too"
    assert result.blocked and "finished in worktree" in result.detail


def _unhalted() -> policy.SpendStatus:
    return policy.SpendStatus(
        grant=policy.Grant(level="L1", token_budget=1_000_000), spent_tokens=0, halted=False
    )


class _CeilingBr:
    def __init__(self) -> None:
        self.created: list[list[str]] = []
        self.deps: list[tuple[str, ...]] = []
        self.comments: dict[str, list[str]] = {}

    @staticmethod
    def _ok(stdout: str) -> SimpleNamespace:

        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    def __call__(self, _repo_root: Path, args: list[str], **_k) -> SimpleNamespace:
        if args[:1] == ["show"]:
            return self._ok(json.dumps([_CEILING_ISSUE | {"id": args[1]}]))
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            return self._ok(json.dumps([{"text": text} for text in texts]))
        if args[:2] == ["comments", "add"]:
            self.comments.setdefault(args[2], []).append(args[3])
            return self._ok("{}")
        if args[:1] == ["create"]:
            self.created.append(args)
            return self._ok(json.dumps({"id": f"new-{len(self.created)}"}))
        if args[:2] == ["dep", "add"]:
            self.deps.append(tuple(args[2:]))
            return self._ok("{}")
        raise AssertionError(f"unexpected br call: {args}")


_CEILING_ISSUE = {
    "status": "in_progress",
    "title": "Build the parser",
    "issue_type": "task",
    "priority": 0,
    "acceptance_criteria": "- parses all three formats",
    "description": "Work.\n\n## Scope\n\n- `src/a/**`\n",
}


def _pin_ceiling(monkeypatch: pytest.MonkeyPatch, ceiling: float) -> _CeilingBr:
    monkeypatch.setattr(
        loop,
        "load_sizing_config",
        lambda *_a: SizingConfig(
            working_set_min=8_000,
            working_set_max=64_000,
            build_factors={},
            calibration_min_samples=10,
            calibration_window=50,
            context_ceiling=ceiling,
        ),
    )
    fake = _CeilingBr()
    fake_tracker.install(monkeypatch, fake)
    return fake


def _occupying(monkeypatch: pytest.MonkeyPatch, tokens: int) -> None:
    turn = json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": tokens}}})
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, stdout=turn
        ),
    )


def test_a_single_track_dispatch_over_the_ceiling_observes_and_spins_nothing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    fake = _pin_ceiling(monkeypatch, 0.01)
    _occupying(monkeypatch, 12_000)

    result = _advance(tmp_path)

    assert fake.created == [], "no follow-up bead"
    assert fake.deps == [], "no gating edge"
    written = [text for texts in fake.comments.values() for text in texts]
    assert not any(text.startswith("[harness-overrun]") for text in written)
    assert '"context_tokens": 12000' in written[0] and '"context_window": 1000000' in written[0]
    assert result.blocked
    assert "12000" in result.detail and "10000" in result.detail
    assert "observed, not enforced" in result.detail
    assert "advance again to land it" in result.detail


def test_a_single_track_dispatch_under_the_ceiling_observes_nothing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    fake = _pin_ceiling(monkeypatch, 0.05)
    _occupying(monkeypatch, 9_999)

    result = _advance(tmp_path)

    assert fake.created == []
    assert result.blocked and "finished in worktree" in result.detail
    assert "ceiling" not in result.detail


def test_dispatch_prompt_documents_the_needs_input_protocol(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    wt = tmp_path / "wt"
    (wt / needs_input.SENTINEL_FILE.parent).mkdir(parents=True)
    sentinel = wt / needs_input.SENTINEL_FILE
    at(_state("classify", issue_type="task"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)

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
    assert not sentinel.exists()
    assert traced == [("i", "prod db dialect")]
    assert queued == [("i", "needs-input", "prod db dialect")]


def test_dispatch_writes_a_run_record_keyed_by_bead(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name,
            tuple(runner.format_command(spec, _a[0])),
            executed=True,
            returncode=0,
            duration_s=0.5,
        ),
    )
    _advance(tmp_path)

    records = run_record.load_run_records(tmp_path)
    assert records is not None
    entry = records["i"][0]
    assert entry["agent"] == "claude"
    assert entry["outcome"] == "executed"
    assert entry["duration_s"] == 0.5
    assert entry["model"] is None
    assert run_record.REDACTED_PROMPT in entry["command"]
    assert not any("AGENTS.md" in part for part in entry["command"])


def test_dispatch_record_stamps_model_provenance(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert records["i"][0]["command"] == ["x", "--headless"]


def test_dispatch_record_captures_a_handoff(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    _advance(tmp_path)

    records = run_record.load_run_records(tmp_path)
    assert records is not None
    entry = records["i"][0]
    assert entry["outcome"] == "handoff"
    assert entry["command"] == []
    assert entry["duration_s"] is None
    assert entry["tokens"] is None and entry["cost"] is None and entry["estimated"] is None


def test_dispatch_record_captures_token_telemetry(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
            spec.name,
            tuple(runner.format_command(spec, _prompt, capture_usage=True)),
            executed=True,
            returncode=0,
            stdout=stdout,
        )

    monkeypatch.setattr(runner, "run", _run)
    _advance(tmp_path)

    assert seen["capture_usage"] is True
    records = run_record.load_run_records(tmp_path)
    assert records is not None
    entry = records["i"][0]
    assert (entry["tokens"], entry["cost"], entry["estimated"]) == (140, 0.25, False)
    assert entry["command"][-4:] == [
        "--output-format",
        "stream-json",
        "--verbose",
        "--forward-subagent-text",
    ]


def test_classify_leaf_reports_failed_runner(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_classify_leaf_blocks_at_the_concurrency_cap(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    monkeypatch.setattr(
        loop,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(base_branch=None, concurrency=2),
    )
    live = [_session_at(tmp_path / name, name) for name in ("a", "b")]
    for session in live:
        session.path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: live)
    result = _advance(tmp_path)
    assert result.blocked and "concurrency cap" in result.detail
    assert "n" not in created


def test_classify_feature_blocks_without_children(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("classify", issue_type="feature"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    result = _advance(tmp_path)
    assert result.blocked and result.needs_input == "children"


def test_classify_feature_decomposes(at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    at(_state("classify", issue_type="feature"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    monkeypatch.setattr(
        decompose, "decompose", lambda *_a: decompose.DecomposeResult("i", (), (("i.1",),))
    )
    child = decompose.ChildSpec("t", ("ac",), ("s",))
    result = _advance(tmp_path, children=(child,))
    assert result.to_phase == "decompose" and result.action == "decomposed"


def test_classify_feature_names_a_collapsing_path_in_the_advance_detail(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("classify", issue_type="feature"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    collapsing = (
        decompose.CollapsingPath(
            "pyproject.toml", (0, 1), groups=1, groups_without=2, neutralized=False
        ),
        decompose.CollapsingPath("uv.lock", (0, 1), groups=1, groups_without=2, neutralized=True),
    )
    monkeypatch.setattr(
        decompose,
        "decompose",
        lambda *_a: decompose.DecomposeResult("i", (), (("i.1", "i.2"),), collapsing),
    )
    result = _advance(tmp_path, children=(decompose.ChildSpec("t", ("ac",), ("s",)),))
    assert "1 group(s)" in result.detail
    assert "`pyproject.toml`" in result.detail
    assert "uv.lock" not in result.detail


def _proposer(monkeypatch: pytest.MonkeyPatch, stdout: str, **spec_kw) -> dict:

    calls: dict = {}
    spec = runner.RunnerSpec(
        "fake",
        runner.HEADLESS,
        ("fake", runner.PROMPT_PLACEHOLDER),
        deny_style=runner.DENY_TOOL_FLAG,
        **spec_kw,
    )
    monkeypatch.setattr(
        loop, "load_runner_config", lambda *_a: RunnerConfig(specs=(spec,), default="fake")
    )

    def _run(dispatched, prompt, cwd, **kwargs):
        calls["spec"], calls["prompt"], calls["cwd"] = dispatched, prompt, cwd
        calls["kwargs"] = kwargs
        return runner.RunResult("fake", ("fake",), executed=True, returncode=0, stdout=stdout)

    monkeypatch.setattr(runner, "run", _run)
    return calls


def _delegated(monkeypatch: pytest.MonkeyPatch, level: str = "L3") -> None:
    monkeypatch.setattr(
        policy, "proposal_delegated", lambda *_a, **_k: policy.ProposalGrant(True, level=level)
    )
    monkeypatch.setattr(decisions, "intake_corpus", lambda *_a: "Ship the parser.")


def test_intake_proposes_the_work_type_under_a_grant(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("intake"))
    _delegated(monkeypatch)
    _proposer(monkeypatch, json.dumps({"work_type": "epic", "rationale": "it decomposes"}))
    recorded = {}

    def _classify(_r, _i, wt, _s):
        recorded["wt"] = wt
        return classify.ClassifyResult("i", wt, DoRResult(True, ()))

    monkeypatch.setattr(classify, "classify", _classify)

    result = _advance(tmp_path)

    assert recorded["wt"] == "epic"
    assert result.blocked and result.checkpoint == "classify"
    assert "proposed under the L3 grant" in result.detail


def test_intake_falls_back_to_the_block_when_no_grant_delegates_it(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("intake"))
    _never_runs(monkeypatch)
    monkeypatch.setattr(
        policy,
        "proposal_delegated",
        lambda *_a, **_k: policy.ProposalGrant(
            False, "the active L1 grant on i approves the checkpoint but does not originate it"
        ),
    )

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"
    assert "classify needs an agent-proposed work type" in result.detail
    assert "does not originate it" in result.detail


def test_an_unreadable_grant_ledger_blocks_rather_than_failing_the_advance(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("intake"))
    _never_runs(monkeypatch)

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"
    assert "grant ledger could not be read" in result.detail


def test_an_invalid_proposed_work_type_never_reaches_the_tracker(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("intake"))
    _delegated(monkeypatch)
    _proposer(monkeypatch, json.dumps({"work_type": "banana"}))
    monkeypatch.setattr(
        classify, "classify", lambda *_a: pytest.fail("an invalid type must not be recorded")
    )

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"
    assert "'banana' is not one of" in result.detail


def test_an_unparseable_proposal_falls_back_to_the_block(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("intake"))
    _delegated(monkeypatch)
    _proposer(monkeypatch, "I think this is probably an epic, but let me check.")

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"


def test_the_proposer_dispatch_is_confined_and_metered(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("intake"))
    _delegated(monkeypatch)
    calls = _proposer(monkeypatch, json.dumps({"work_type": "task"}))
    monkeypatch.setattr(
        classify,
        "classify",
        lambda _r, _i, wt, _s: classify.ClassifyResult("i", wt, DoRResult(True, ())),
    )

    _advance(tmp_path)

    assert calls["kwargs"]["capture_usage"] is True
    assert calls["spec"].deny_tools, "the dispatched spec carries the confinement overlay"
    assert calls["cwd"] == tmp_path
    assert "Ship the parser." in calls["prompt"], "bounded to the bead's own requirement"
    records = run_record.dispatch_history(tmp_path).get("i", [])
    assert [r.get("phase") for r in records] == [run_record.PROPOSE_PHASE]


def _persona_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:

    phases: list[str] = []

    def _resolve(_repo_root, _spec, phase: str) -> str:
        phases.append(phase)
        return f"persona-for-{phase}"

    monkeypatch.setattr(loop.roles, "resolve_role", _resolve)
    return phases


def test_the_work_type_proposal_dispatches_as_the_classify_persona(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("intake"))
    _delegated(monkeypatch)
    calls = _proposer(monkeypatch, json.dumps({"work_type": "task"}))
    phases = _persona_spy(monkeypatch)
    monkeypatch.setattr(
        classify,
        "classify",
        lambda _r, _i, wt, _s: classify.ClassifyResult("i", wt, DoRResult(True, ())),
    )

    _advance(tmp_path)

    assert phases == ["classify"]
    assert calls["kwargs"]["role"] == "persona-for-classify"


def test_the_child_plan_proposal_dispatches_as_the_decompose_persona(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _feature_at_classify(at, monkeypatch)
    _delegated(monkeypatch)
    calls = _proposer(monkeypatch, json.dumps(_PLAN))
    phases = _persona_spy(monkeypatch)
    monkeypatch.setattr(
        decompose,
        "decompose",
        lambda _r, _f, _c: decompose.DecomposeResult("i", (), (("i.1",),)),
    )

    _advance(tmp_path)

    assert phases == ["decompose"]
    assert calls["kwargs"]["role"] == "persona-for-decompose"


def test_a_family_that_cannot_select_a_persona_dispatches_unspecialised(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("intake"))
    _delegated(monkeypatch)
    calls = _proposer(monkeypatch, json.dumps({"work_type": "task"}))
    monkeypatch.setattr(
        classify,
        "classify",
        lambda _r, _i, wt, _s: classify.ClassifyResult("i", wt, DoRResult(True, ())),
    )

    _advance(tmp_path)

    assert calls["kwargs"]["role"] is None


def test_an_unconfinable_runner_never_dispatches_a_proposer(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("intake"))
    _delegated(monkeypatch)
    _never_runs(monkeypatch)
    bare = runner.RunnerSpec("bare", runner.HEADLESS, ("bare", runner.PROMPT_PLACEHOLDER))
    monkeypatch.setattr(
        loop, "load_runner_config", lambda *_a: RunnerConfig(specs=(bare,), default="bare")
    )

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"
    assert "no known tool-confinement overlay" in result.detail


def test_a_manual_handoff_runner_proposes_nothing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("intake"))
    _delegated(monkeypatch)
    _never_runs(monkeypatch)
    _pin_runner(monkeypatch, "manual")

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"
    assert "manual handoff" in result.detail


def _feature_at_classify(at, monkeypatch: pytest.MonkeyPatch) -> None:
    at(_state("classify", issue_type="feature"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))


_PLAN = {
    "children": [
        {
            "title": "parse the header",
            "acceptance": ["given a header when parsed then the fields land"],
            "scope": ["src/header/**"],
            "depends_on": [],
            "budget_tokens": 40000,
            "integrity": "L2",
            "demonstration": "run `basicly loop status` and read the header fields",
        }
    ]
}


def test_classify_proposes_the_child_plan_under_a_grant(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _feature_at_classify(at, monkeypatch)
    _delegated(monkeypatch)
    _proposer(monkeypatch, json.dumps(_PLAN))
    planned = {}

    def _decompose(_r, feature_id, children):
        planned["feature"], planned["children"] = feature_id, children
        return decompose.DecomposeResult("i", (), (("i.1",),))

    monkeypatch.setattr(decompose, "decompose", _decompose)

    result = _advance(tmp_path)

    assert planned["feature"] == "i"
    assert planned["children"] == (
        decompose.ChildSpec(
            "parse the header",
            ("given a header when parsed then the fields land",),
            ("src/header/**",),
            depends_on=(),
            budget_tokens=40_000,
            integrity="L2",
            demonstration="run `basicly loop status` and read the header fields",
        ),
    )
    assert result.to_phase == "decompose" and result.action == "decomposed"
    assert "proposed under the L3 grant" in result.detail


def test_a_plan_failing_the_schema_falls_back_to_the_block(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _feature_at_classify(at, monkeypatch)
    _delegated(monkeypatch)
    _proposer(monkeypatch, json.dumps({"children": [{"title": "t", "acceptance": ["a"]}]}))
    monkeypatch.setattr(
        decompose, "decompose", lambda *_a: pytest.fail("an invalid plan must not be recorded")
    )

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "children"
    assert "failed the plan schema" in result.detail
    assert "'scope'" in result.detail


def test_a_proposal_missing_a_plan_gate_field_blocks_rather_than_crashes(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _feature_at_classify(at, monkeypatch)
    _delegated(monkeypatch)
    proposed = json.loads(json.dumps(_PLAN))
    del proposed["children"][0]["integrity"]
    _proposer(monkeypatch, json.dumps(proposed))
    monkeypatch.setattr(
        decompose, "decompose", lambda *_a: pytest.fail("an ungated plan must not be recorded")
    )

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "children"
    assert "integrity" in result.detail


def test_a_plan_under_the_working_set_floor_falls_back_to_the_block(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _feature_at_classify(at, monkeypatch)
    _delegated(monkeypatch)
    tiny = tmp_path / "src" / "tiny.py"
    tiny.parent.mkdir(parents=True)
    tiny.write_text("x = 1\n", encoding="utf-8")
    plan = {
        "children": [
            {
                "title": "t",
                "acceptance": ["a"],
                "scope": ["src/tiny.py"],
                "depends_on": [],
                "budget_tokens": 40000,
                "integrity": "L2",
                "demonstration": "run `basicly loop status`",
            }
        ]
    }
    _proposer(monkeypatch, json.dumps(plan))
    monkeypatch.setattr(
        decompose, "decompose", lambda *_a: pytest.fail("a refused plan must not be recorded")
    )

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "children"
    assert "sizing governor refused the proposed plan" in result.detail
    assert f"below working_set_min {DEFAULT_WORKING_SET_MIN}" in result.detail


def test_decompose_blocks_on_pending_checkpoint(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: False)
    result = _advance(tmp_path)
    assert result.blocked and "decompose checkpoint" in result.detail


def test_decompose_builds_children_and_blocks_while_open(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(
        loop, "_child_states", lambda _ctx: [("i.1", "in_progress"), ("i.2", "closed")]
    )
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    result = _advance(tmp_path)
    assert result.blocked and "1 child track(s) still open" in result.detail


def test_decompose_fan_in_does_not_wait_on_a_deferred_child(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(
        loop, "_child_states", lambda _ctx: [("i.1", "closed"), ("i.2", "deferred")]
    )
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))

    result = _advance(tmp_path)

    assert result.to_phase == "verify" and result.action == "merged"
    assert "still open" not in result.detail


def test_decompose_merges_children_when_all_closed(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_a_refused_verify_gate_blocks_instead_of_deriving_back_to_build(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("decompose", has_children=True))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "closed"), ("i.2", "closed")])
    monkeypatch.setattr(loop, "_ensure_child_worktrees", lambda *_a: None)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [_session("i-1")])
    monkeypatch.setattr(
        merge,
        "merge_queue",
        lambda *_a, **_k: [merge.QueueResult(merge.MergeResult("i-1", "merged", "ok"))],
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(
        verify,
        "report_gate",
        lambda *_a, **_k: (False, "br gate report failed: no configured transition"),
    )

    result = _advance(tmp_path)

    assert result.blocked
    assert result.to_phase != "verify"
    assert "verify gate not recorded" in result.detail
    assert "no configured transition" in result.detail


def test_decompose_skips_self_landed_children(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_build_leaf_lands_and_records_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    attempt = merge.MergeResult("i", "not-ready", "commit the work on 'harness/i' before landing")
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)

    result = _advance(tmp_path)

    assert result.blocked and result.landing is attempt and not attempt.conflicted


def test_a_landing_interrupted_before_the_gate_resumes_at_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    attempt = merge.MergeResult(
        "i", merge.ALREADY_LANDED, "harness/i is already an ancestor of main"
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)
    charged: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    recorded: list = []
    monkeypatch.setattr(verify, "report_gate", lambda *a, **_k: recorded.append(a) or (True, "ok"))

    result = _advance(tmp_path)

    assert not result.blocked
    assert result.to_phase == "verify" and result.action == "merged"
    assert charged == []
    assert recorded, "the missing verify gate is what this resumes to record"


def test_an_unreliable_gate_spends_no_rework_and_records_the_flake(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    attempt = merge.MergeResult(
        "i", merge.VERIFY_UNRELIABLE, "verify full failed on pytest but passed on re-run"
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)
    charged: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)
    flakes: list = []
    monkeypatch.setattr(policy, "record_unreliable_gate", lambda *a, **_k: flakes.append(a) or 1)

    result = _advance(tmp_path)

    assert charged == []
    assert result.blocked and result.action == "blocked"
    assert result.landing is attempt
    assert [(a[1], a[2]) for a in flakes] == [("i", merge.MERGE_GATE)]


def test_build_leaf_reworks_on_failed_merge(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda *_a, **_k: merge.MergeResult("i", "merge-conflicts", "conflicts in x.py"),
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 2)
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        loop.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: queued.append((issue, kind)),
    )
    result = _advance(tmp_path)
    assert result.action == "escalated" and "merge failed" in result.detail
    assert queued == [("i", "escalation")]


def _killed_dispatch(cwd: Path) -> loop._Dispatch:
    spec = runner.RunnerSpec("claude", command=("claude", "-p"))
    return loop._Dispatch(
        spec=spec,
        result=runner.RunResult("claude", spec.command, executed=True, timed_out=True),
        cwd=cwd,
        timeout=1800.0,
    )


def test_a_killed_dispatch_commits_its_worktree_and_points_at_the_next_advance(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    salvaged: list[tuple[Path, str, str]] = []

    def fake_salvage(cwd, bead, *, reason):
        salvaged.append((Path(cwd), bead, reason))
        return loop.commit.Salvage("committed", "the worktree was committed as abc1234")

    monkeypatch.setattr(loop.commit, "salvage", fake_salvage)
    ctx = loop._Ctx(tmp_path, "i", _state("build"), CONFIG, loop.Inputs())

    held = loop._runner_block(
        ctx, _killed_dispatch(tmp_path / "wt"), issue_id="i", target="worktree 'i'"
    )

    assert salvaged == [(tmp_path / "wt", "i", "runner_timeout after 1800s")]
    assert held is not None and held.action == "blocked"
    assert "stopped on runner_timeout after 1800s" in held.detail
    assert "the worktree was committed as abc1234; advance again to judge it" in held.detail


def test_a_killed_dispatch_the_salvage_refused_still_asks_for_a_re_dispatch(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        loop.commit,
        "salvage",
        lambda *_a, **_k: loop.commit.Salvage("empty", "the worktree held no uncommitted work"),
    )
    ctx = loop._Ctx(tmp_path, "i", _state("build"), CONFIG, loop.Inputs())

    held = loop._runner_block(
        ctx, _killed_dispatch(tmp_path / "wt"), issue_id="i", target="worktree 'i'"
    )

    assert held is not None
    assert "no uncommitted work; inspect the worktree and re-dispatch" in held.detail


def _red_landing(at, monkeypatch: pytest.MonkeyPatch, status: str = "verify-failed") -> list[tuple]:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda *_a, **_k: merge.MergeResult("i", status, "verify full failed: pytest"),
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    monkeypatch.setattr(loop.decisions, "enqueue", lambda *_a, **_k: None)
    refunds: list[tuple] = []

    def refund(_repo_root, issue_id, gate):
        refunds.append((issue_id, gate))
        return True

    monkeypatch.setattr(policy, "spend_convergence_refund", refund)
    monkeypatch.setattr(policy, "rework_charged", lambda *_a: 0)
    return refunds


def test_one_stalled_rework_round_warns_on_the_bead_and_keeps_spending(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    refunds = _red_landing(at, monkeypatch)
    _pin_finding_sets(monkeypatch, _stalled(1, "pytest"))

    result = _advance(tmp_path)

    assert result.action == "blocked" and refunds == []
    assert "warning:" in result.detail and "changed nothing it reports" in result.detail


def test_a_stalled_round_that_is_also_the_cap_round_queues_the_warning_with_it(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _red_landing(at, monkeypatch)
    monkeypatch.setattr(policy, "record_rework", lambda *_a: CONFIG.max_rework)
    _pin_finding_sets(monkeypatch, _stalled(1, "pytest"))
    queued: list[str] = []
    monkeypatch.setattr(
        loop.decisions,
        "enqueue",
        lambda _r, _issue, _kind, _q, reason, *_a, **_k: queued.append(reason),
    )

    result = _advance(tmp_path)

    assert result.action == "escalated"
    assert len(queued) == 1 and "changed nothing it reports" in queued[0]


def test_a_second_stalled_rework_round_escalates_without_consuming_the_cap(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    refunds = _red_landing(at, monkeypatch)
    _pin_finding_sets(monkeypatch, _stalled(2, "pytest"))
    queued: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        loop.decisions,
        "enqueue",
        lambda _r, issue, kind, question, *_a, **_k: queued.append((issue, kind, question)),
    )

    result = _advance(tmp_path)

    assert result.action == "escalated"
    assert refunds == [("i", merge.MERGE_GATE)]
    assert "this attempt refunded" in result.detail and "not converging" in result.detail
    assert queued == [("i", "escalation", policy.rework_escalation_question(merge.MERGE_GATE))]


def test_a_second_escalation_says_the_refund_is_gone_and_still_stops(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _red_landing(at, monkeypatch)
    monkeypatch.setattr(policy, "spend_convergence_refund", lambda *_a: False)
    monkeypatch.setattr(policy, "rework_charged", lambda *_a: 2)
    _pin_finding_sets(monkeypatch, _stalled(3, "pytest"))

    result = _advance(tmp_path)

    assert result.action == "escalated"
    assert "already spent" in result.detail and "rework 2/2" in result.detail


def test_a_growing_finding_set_escalates_on_its_first_occurrence(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    refunds = _red_landing(at, monkeypatch)
    _pin_finding_sets(
        monkeypatch, policy.Convergence(policy.DIVERGING, ("pytest", "ruff"), ("pytest",), 0)
    )

    result = _advance(tmp_path)

    assert result.action == "escalated" and refunds == [("i", merge.MERGE_GATE)]
    assert "worse, not better" in result.detail


def test_a_landing_reports_its_status_and_the_gates_own_rendering_as_its_findings(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _red_landing(at, monkeypatch)
    recorded = _pin_finding_sets(monkeypatch)

    _advance(tmp_path)

    assert recorded == [
        ("i", merge.MERGE_GATE, ("status=verify-failed", "verify full failed: pytest"))
    ]


def test_a_collided_landing_records_no_finding_set_in_the_loop(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _red_landing(at, monkeypatch, status="merge-conflicts")
    recorded = _pin_finding_sets(monkeypatch)

    result = _advance(tmp_path)

    assert recorded == [] and result.blocked


def test_skill_canary_reaches_the_dispatched_prompt(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "implementer.md").write_text(
        "---\nname: implementer\nskills:\n- python-guidelines\n---\n\nBody.\n",
        encoding="utf-8",
    )
    skill = tmp_path / ".claude" / "skills" / "python-guidelines"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("CANARY-EY58-DISPATCH", encoding="utf-8")

    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(loop.roles, "resolve_role", lambda *_a: "implementer")
    seen: dict = {}

    def _run(spec, prompt, *_a, **_k):
        seen["prompt"] = prompt
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    _advance(tmp_path)

    assert "CANARY-EY58-DISPATCH" in seen["prompt"]
    assert "basicly tracker show i" in seen["prompt"]


_VGATE = "validate-as-consumer"


def _validate_gates(*, failed: bool = False, foreign: str | None = None) -> GateStatus:
    disregarded = (policy.GateVerdict(_VGATE, foreign, True),) if foreign is not None else ()
    return GateStatus(
        False,
        ("verify",),
        (_VGATE,) if failed else (),
        () if failed else (_VGATE,),
        (),
        disregarded,
    )


def test_a_failed_validation_spends_one_bounded_rework_attempt(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("validate", gates=_validate_gates(failed=True)))
    charged: list[tuple] = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)

    result = _advance(tmp_path)

    assert result.blocked
    assert _VGATE in result.detail
    assert charged and charged[0][2] == _VGATE


def test_a_failed_validation_escalates_at_the_rework_cap(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, queued: list[tuple[str, str]]
) -> None:

    seen, _ = _validate_repair(at, monkeypatch, tmp_path, brief=False)
    monkeypatch.setattr(policy, "record_rework", lambda *_a, **_k: 2)

    result = _advance(tmp_path)

    assert seen == [] and result.action == "escalated" and _VGATE in result.detail
    assert queued == [("i", "escalation")]


def test_a_missing_validation_spends_no_rework(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates()))
    charged: list[tuple] = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), repair_dispatch=False)

    assert result.blocked and result.needs_input == "validation"
    assert charged == []


@pytest.mark.usefixtures("queued")
def test_validator_argv_carries_the_role_and_a_non_write_phase(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates()))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(loop.roles, "resolve_role", lambda _r, _s, phase: f"role-for-{phase}")
    seen: list[dict] = []
    recorded: list[object] = []

    def _run(spec, prompt, cwd, **kw):
        seen.append({"role": kw.get("role"), "prompt": prompt, "cwd": cwd})
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    monkeypatch.setattr(
        loop, "record_run", lambda *_a, **kw: recorded.append(kw.get("phase")) or None
    )
    monkeypatch.setattr(policy, "gate_status", lambda *_a: _validate_gates())

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs())

    assert seen[0]["role"] == "role-for-validate"
    assert "Do NOT re-run the gate suite" in seen[0]["prompt"]
    assert set(recorded) == {run_record.VALIDATE_PHASE}
    assert run_record.VALIDATE_PHASE not in run_record.WRITE_PHASES


@pytest.mark.usefixtures("queued")
def test_validate_dispatches_one_reviewer_per_lens_each_carrying_its_own_lens(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates()))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(loop.roles, "resolve_named_role", lambda _r, _s, role: role)
    seen: list[dict] = []
    recorded: list[object] = []

    def _run(spec, prompt, cwd, **kw):
        seen.append({"role": kw.get("role"), "prompt": prompt, "cwd": cwd})
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    monkeypatch.setattr(loop, "record_run", lambda *_a, **kw: recorded.append(kw.get("phase")))
    monkeypatch.setattr(policy, "gate_status", lambda *_a: _validate_gates())

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs())

    reviews = [call for call in seen if call["role"] == "reviewer"]
    assert len(reviews) == len(roles.REVIEW_LENSES)
    for call, lens in zip(reviews, roles.REVIEW_LENSES, strict=True):
        assert f"one axis and one only: {lens}" in call["prompt"]
    assert recorded == [run_record.VALIDATE_PHASE] * (1 + len(roles.REVIEW_LENSES))


@pytest.mark.usefixtures("queued")
def test_each_lens_records_its_own_findings_and_nothing_merges_them(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates()))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(loop.roles, "resolve_named_role", lambda _r, _s, role: role)
    monkeypatch.setattr(loop, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(policy, "gate_status", lambda *_a: _validate_gates())
    lenses = iter(roles.REVIEW_LENSES)

    def _run(spec, _prompt, _cwd, **kw):
        reply = f"finding on {next(lenses)}" if kw.get("role") == "reviewer" else ""
        return runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, stdout=reply
        )

    monkeypatch.setattr(runner, "run", _run)
    comments: list[str] = []
    monkeypatch.setattr(
        loop.lens_review.tracker, "add_comment", lambda _r, _i, b: comments.append(b)
    )

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs())

    assert len(comments) == len(roles.REVIEW_LENSES)
    for body, lens in zip(comments, roles.REVIEW_LENSES, strict=True):
        assert body.startswith(f"{loop.lens_review.MARKER} lens={lens}\n")
        assert body.count(loop.lens_review.MARKER) == 1
        assert f"finding on {lens}" in body


def test_an_l1_or_l2_unit_never_reaches_the_phase_that_pays_for_a_review() -> None:

    landed = GateStatus(True, ("verify",), (), (), (), ())

    phase = loop_state.derive_phase("in_progress", ("ship",), None, landed, False)

    assert phase == "ship"
    assert roles.lens_dispatches(phase) == ()


@pytest.mark.usefixtures("queued")
def test_a_validate_dispatch_that_records_nothing_leaves_the_unit_in_validate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("validate", gates=_validate_gates()))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0
        ),
    )
    monkeypatch.setattr(loop, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(policy, "gate_status", lambda *_a: _validate_gates())

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs())

    assert result.blocked and result.to_phase == "validate"
    assert "recorded no" in result.detail


def test_a_disregarded_validation_result_is_named_rather_than_admitted(at, tmp_path: Path) -> None:
    at(_state("validate", gates=_validate_gates(foreign="some-agent")))

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "validation"
    assert "some-agent" in result.detail and "disregarded" in result.detail


def test_a_refused_validation_advance_has_no_side_effects(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tracker_commits: list
) -> None:
    at(_state("validate", gates=_validate_gates(), worktree=WorktreeBinding("n", "b")))
    calls: list[list[str]] = []
    monkeypatch.setattr(loop, "_write", lambda _r, args, **_k: calls.append(args))
    monkeypatch.setattr(
        loop.worktree, "cleanup", lambda *_a, **_k: pytest.fail("tore down a live worktree")
    )
    monkeypatch.setattr(
        loop.merge, "merge_worktree", lambda *_a, **_k: pytest.fail("merged while refusing")
    )

    result = _advance(tmp_path)

    assert result.blocked
    assert not any(args[:1] == ["close"] for args in calls)
    assert tracker_commits == []


def _brief_after_rework(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    reviews: tuple[tuple[str, str], ...] = (),
) -> repair_brief.RepairBrief | None:

    path = tmp_path / "wt"
    path.mkdir()
    monkeypatch.setattr(
        loop.worktree,
        "load_session",
        lambda *_a, **_k: replace(_session(), worktree_path=str(path)),
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a, **_k: 1)
    monkeypatch.setattr(loop, "lane_rework_spent", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        loop.lens_review.tracker,
        "try_read_comments",
        lambda *_a: [
            {"text": f"{loop.lens_review.MARKER} lens={lens}\n{text}"} for lens, text in reviews
        ],
    )

    _advance(tmp_path)

    return repair_brief.take_repair_brief(path)


def test_a_failed_validation_briefs_its_repair_with_the_findings_per_lens(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates(failed=True), worktree=WorktreeBinding("i", "b")))

    brief = _brief_after_rework(
        monkeypatch,
        tmp_path,
        reviews=(("security", "shell injection (blocker)"), ("correctness", "off-by-one (major)")),
    )

    assert brief is not None and brief.gate == _VGATE
    assert brief.reviews == (
        loop.lens_review.LensFindings("correctness", "off-by-one (major)"),
        loop.lens_review.LensFindings("security", "shell injection (blocker)"),
    )


def test_a_lens_that_recorded_nothing_is_briefed_as_such(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("validate", gates=_validate_gates(failed=True), worktree=WorktreeBinding("i", "b")))

    brief = _brief_after_rework(
        monkeypatch, tmp_path, reviews=(("correctness", "off-by-one (major)"),)
    )

    assert brief is not None
    assert [entry.lens for entry in brief.reviews] == list(roles.REVIEW_LENSES)
    assert repair_brief.NO_REVIEW in repair_brief.repair_prompt(brief)


def test_a_repair_after_a_failed_verify_is_briefed_as_it_always_was(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "b"), has_children=True))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0
        ),
    )
    monkeypatch.setattr(loop, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_child_states", lambda _ctx: [("i.1", "open")])
    monkeypatch.setattr(loop.loop_state, "blocked_ids", lambda *_a: ())
    monkeypatch.setattr(loop.decisions, "has_pending", lambda *_a, **_k: False)
    monkeypatch.setattr(loop, "_subtask_committed", lambda *_a: True)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: SimpleNamespace(stdout="{}"))
    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda _r, m, *_a, **_k: verify.VerifyReport(m, (verify.CheckResult("pytest", "fail", 1),)),
    )
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(
        policy,
        "record_finding_set",
        lambda _r, _i, _g, found: policy.Convergence(
            policy.PROGRESSING, policy.finding_signature(found), (), 0
        ),
    )

    brief = _brief_after_rework(
        monkeypatch, tmp_path, reviews=(("correctness", "off-by-one (major)"),)
    )

    assert brief is not None and brief.gate == verify.DEFAULT_GATE
    assert brief.reviews == ()


def _validate_repair(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, landed: bool = True, brief: bool = True
) -> tuple[list[dict], list[str]]:

    at(_state("validate", gates=_validate_gates(failed=True), worktree=WorktreeBinding("i", "b")))
    path = tmp_path / "wt"
    path.mkdir()
    if brief:
        lens = loop.lens_review.LensFindings
        reviews = (lens("correctness", "off-by-one"), lens("security", "shell injection"))
        repair_brief.write_repair_brief(
            path, repair_brief.RepairBrief("i", _VGATE, "not survived", reviews=reviews)
        )
    session = replace(_session(), worktree_path=str(path))
    monkeypatch.setattr(loop.worktree, "load_session", lambda *_a, **_k: session)
    monkeypatch.setattr(loop.worktree, "git", lambda *_a, **_k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(loop.merge, "is_ancestor", lambda *_a: landed)
    seen = _retro_dispatch(monkeypatch, tmp_path)
    roots: list[str] = []
    monkeypatch.setattr(policy, "spend_status", lambda _r, root: roots.append(root) or _unhalted())
    return seen, roots


@pytest.mark.usefixtures("queued")
def test_a_failed_validation_repairs_against_the_brief_then_re_lands_the_commit(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    seen, roots = _validate_repair(at, monkeypatch, tmp_path)
    done = merge.MergeResult("i", "merged", "landed @ def5678")
    merged: list[str] = []
    monkeypatch.setattr(loop.merge, "merge_worktree", lambda _r, n, **_k: merged.append(n) or done)
    monkeypatch.setattr(loop, "_dispatch_reviews", lambda _ctx: None)
    monkeypatch.setattr(policy, "gate_status", lambda *_a: _validate_gates())

    repair = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="root")
    monkeypatch.setattr(loop.merge, "is_ancestor", lambda *_a: False)
    reland = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="root")

    assert roots == [], "spend is consulted on the repair path again"
    assert [call["cwd"] for call in seen] == [tmp_path / "wt", tmp_path]
    assert "off-by-one" in seen[0]["prompt"] and "shell injection" in seen[0]["prompt"]
    assert seen[1]["prompt"] == dispatch_brief.validate_prompt("i")
    assert merged == ["i"], "the repair commit must not strand"
    assert _VGATE in repair.detail and reland.to_phase == "validate"


def test_verify_blocks_on_pending_ship_checkpoint(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("verify"))
    monkeypatch.setattr(
        policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult("challenge", code="abc"),
    )
    result = _advance(tmp_path)
    assert result.blocked and "ship checkpoint" in result.detail


def test_verify_advances_to_ship_when_approved(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("verify"))
    monkeypatch.setattr(
        policy, "approve_checkpoint_guarded", lambda *_a, **_k: policy.ApprovalResult("approved")
    )
    result = _advance(tmp_path)
    assert result.to_phase == "ship" and result.action == "shipped"


def test_verify_asks_the_grant_not_only_the_marker(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("verify"))
    asked: list[str | None] = []

    def _guarded(_repo, _issue, _name, *, grant_root=None, **_k):
        asked.append(grant_root)
        return policy.ApprovalResult("approved")

    monkeypatch.setattr(policy, "approve_checkpoint_guarded", _guarded)
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: False)

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="epic")

    assert result.to_phase == "ship" and asked == ["epic"]


def test_ship_tears_down_and_closes(at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    torn = {}
    monkeypatch.setattr(worktree, "cleanup", lambda name, **_k: torn.setdefault("n", name))
    closed = {}
    monkeypatch.setattr(loop, "_write", lambda _r, args, **_k: closed.setdefault("args", args))
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

    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: False)

    def _boom(*_a, **_k):
        raise AssertionError("a stranded node must not be closed, torn down, or committed")

    monkeypatch.setattr(worktree, "cleanup", _boom)
    monkeypatch.setattr(loop, "_write", _boom)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", _boom)

    result = _advance(tmp_path)
    assert result.blocked
    assert result.to_phase == result.from_phase
    assert "not merged" in result.detail


def test_advance_refuses_to_close_a_leaf_that_never_built(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    checkpoints = ("ship",)
    gates = _gate(can_advance=False)
    phase = loop.loop_state.derive_phase("open", checkpoints, None, gates, False)
    assert phase != "ship"
    at(
        NodeState(
            issue_id="i",
            status="open",
            issue_type="bug",
            phase=phase,
            worktree=None,
            gates=gates,
            checkpoints=checkpoints,
            rework={},
            has_children=False,
        )
    )

    def _boom(*_a, **_k):
        raise AssertionError("a leaf that never built must not be closed or torn down")

    monkeypatch.setattr(worktree, "cleanup", _boom)
    monkeypatch.setattr(loop, "_write", _boom)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", _boom)

    result = _advance(tmp_path)
    assert result.blocked
    assert result.to_phase != "done"


def test_ship_proceeds_when_the_worktree_landed(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    torn = {}
    monkeypatch.setattr(worktree, "cleanup", lambda name, **_k: torn.setdefault("n", name))
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    result = _advance(tmp_path)
    assert torn["n"] == "i"
    assert result.to_phase == "done" and result.action == "tore-down"


def test_ship_records_the_forecast_and_the_whole_packages_actual_cost(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "cleanup", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        run_record,
        "dispatch_history",
        lambda _repo: {
            "i": [
                {"outcome": "failed", "tokens": 30_000, "cost": 0.6, "duration_s": 400.0},
                {"outcome": "executed", "tokens": 12_000, "cost": 0.2, "duration_s": 100.0},
            ]
        },
    )
    monkeypatch.setattr(policy, "rework_recorded", lambda *_a: 1)
    monkeypatch.setattr(
        decompose, "bead_class_and_scope", lambda *_a: ("task", ("src/basicly/loop.py",))
    )
    monkeypatch.setattr(
        decompose,
        "forecast_for",
        lambda *_a: decompose.CostEstimate(
            scope_tokens=8_000, overhead_tokens=2_000, build_factor=2.0
        ),
    )
    written: dict = {}

    def _record_cost_marker(_repo, bead, **kw):
        written["bead"] = bead
        written.update(kw)
        return f"{bead}#cost"

    monkeypatch.setattr(run_record, "record_cost_marker", _record_cost_marker)

    result = _advance(tmp_path)

    assert result.to_phase == "done" and result.action == "tore-down"
    assert "cost rollup recorded" in result.detail
    assert written["bead"] == "i"
    assert written["task_class"] == "task" and written["scope_tokens"] == 8_000
    assert written["forecast"].tokens == 18_000
    actual = written["actual"]
    assert actual.dispatches == 2 and actual.rework == 1
    assert actual.tokens == 42_000
    assert actual.cost == pytest.approx(0.8)
    assert actual.wall_clock_s == pytest.approx(500.0)


def test_ship_records_the_rollup_before_the_tracker_commit(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("ship"))
    order: list[str] = []
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: order.append("close"))
    monkeypatch.setattr(
        loop.merge, "commit_tracker_state", lambda *_a, **_k: bool(order.append("commit")) or True
    )
    monkeypatch.setattr(run_record, "dispatch_history", lambda _repo: {"i": [{"tokens": 10}]})
    monkeypatch.setattr(policy, "rework_recorded", lambda *_a: 0)
    monkeypatch.setattr(decompose, "bead_class_and_scope", lambda *_a: None)
    monkeypatch.setattr(
        run_record, "record_cost_marker", lambda *_a, **_k: order.append("rollup") or "i#cost"
    )

    _advance(tmp_path)
    assert order == ["rollup", "close", "commit"]


def test_ship_rolls_up_after_the_curation_it_has_to_count(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("ship"))
    order: list[str] = []
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: order.append("close"))
    monkeypatch.setattr(
        loop.merge, "commit_tracker_state", lambda *_a, **_k: bool(order.append("commit")) or True
    )
    monkeypatch.setattr(loop, "_dispatch_curation", lambda _ctx: bool(order.append("curate")) or "")
    monkeypatch.setattr(run_record, "dispatch_history", lambda _repo: {"i": [{"tokens": 10}]})
    monkeypatch.setattr(policy, "rework_recorded", lambda *_a: 0)
    monkeypatch.setattr(decompose, "bead_class_and_scope", lambda *_a: None)
    monkeypatch.setattr(
        run_record, "record_cost_marker", lambda *_a, **_k: order.append("rollup") or "i#cost"
    )

    _advance(tmp_path)
    assert order == ["curate", "rollup", "close", "commit"]


def test_ship_writes_no_rollup_for_a_node_that_was_never_dispatched(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("ship", issue_type="feature", has_children=True))
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)
    monkeypatch.setattr(run_record, "dispatch_history", lambda _repo: {})
    monkeypatch.setattr(
        run_record, "record_cost_marker", lambda *_a, **_k: pytest.fail("no rollup is due")
    )

    result = _advance(tmp_path)
    assert result.action == "tore-down" and "cost rollup" not in result.detail


def test_ship_proceeds_when_the_cost_rollup_cannot_be_written(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("ship"))
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)

    def _boom(*_a, **_k):
        raise RuntimeError("br is unavailable")

    monkeypatch.setattr(run_record, "dispatch_history", _boom)

    result = _advance(tmp_path)
    assert result.to_phase == "done" and result.action == "tore-down"
    assert "cost rollup" not in result.detail


def _curator_reply() -> loop._Dispatch:
    spec = runner.RunnerSpec("claude", command=("claude", "-p"))
    return loop._Dispatch(spec, runner.RunResult("claude", spec.command, executed=True), Path(), 1)


@pytest.mark.parametrize(("adopted", "dispatched"), [(True, ["ship"]), (False, [])])
def test_ship_dispatches_the_curator_only_where_the_contract_is_installed(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, adopted: bool, dispatched: list[str]
) -> None:
    at(_state("ship"))
    seen: list[str] = []
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)
    monkeypatch.setattr(run_record, "dispatch_history", lambda _repo: {})
    monkeypatch.setattr(loop.handoff, "adopted", lambda *_a: adopted)
    monkeypatch.setattr(
        loop, "_run_agent", lambda *_a, **kw: seen.append(kw["phase"]) or _curator_reply()
    )
    monkeypatch.setattr(loop.curate, "record", lambda *_a: "release record: 2 claim(s) bound")

    result = _advance(tmp_path)

    assert seen == dispatched
    assert ("release record" in result.detail) is adopted


def test_worktree_landed_missing_branch_counts_as_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worktree, "git", lambda _args, **_k: SimpleNamespace(returncode=1))
    assert loop._worktree_landed(Path("/x"), WorktreeBinding("i", "harness/i")) is True


def test_worktree_landed_ancestor_of_base_is_landed(monkeypatch: pytest.MonkeyPatch) -> None:

    def git(_args, **_k):
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(worktree, "git", git)
    monkeypatch.setattr(merge, "git", git)
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session("i"))
    assert loop._worktree_landed(Path("/x"), WorktreeBinding("i", "harness/i")) is True


def test_worktree_landed_non_ancestor_is_stranded(monkeypatch: pytest.MonkeyPatch) -> None:

    def git(args, **_k):
        return SimpleNamespace(returncode=0 if args[0] == "show-ref" else 1)

    monkeypatch.setattr(worktree, "git", git)
    monkeypatch.setattr(merge, "git", git)
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session("i"))
    assert loop._worktree_landed(Path("/x"), WorktreeBinding("i", "harness/i")) is False


@pytest.mark.parametrize("phase", ["build", "ship"])
def test_base_checkout_phase_refuses_a_linked_worktree(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str
) -> None:

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
    at(_state("done"))
    result = _advance(tmp_path)
    assert result.to_phase == "done" and result.action == "done"


def test_child_states_parses_parent_child_dependents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    class _Proc:
        stdout = (
            '[{"id":"i","dependents":['
            '{"id":"i.1","status":"open","dependency_type":"parent-child"},'
            '{"id":"x","status":"open","dependency_type":"blocks"}]}]'
        )
        returncode = 0

    fake_tracker.install(monkeypatch, lambda *_a, **_k: _Proc())
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    assert loop._child_states(ctx) == [("i.1", "open")]


def test_run_until_blocked_stops_at_first_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: _state("intake"))
    monkeypatch.setattr(
        classify,
        "classify",
        lambda _r, _i, wt, _s: classify.ClassifyResult("i", wt, DoRResult(True, ())),
    )
    results = loop.run_until_blocked(
        tmp_path, "i", config=CONFIG, inputs=loop.Inputs(work_type="task")
    )
    assert len(results) == 1 and results[0].blocked


def _script_advance(
    monkeypatch: pytest.MonkeyPatch, *results: loop.AdvanceResult
) -> list[loop.AdvanceResult]:
    served: list[loop.AdvanceResult] = []
    pending = list(results)

    def fake_advance(_repo: Path, _issue: str, **_kw: object) -> loop.AdvanceResult:
        result = pending.pop(0) if len(pending) > 1 else pending[0]
        served.append(result)
        return result

    monkeypatch.setattr(loop, "advance", fake_advance)
    return served


def _script_approval(
    monkeypatch: pytest.MonkeyPatch, *approvals: policy.ApprovalResult
) -> list[str]:
    asked: list[str] = []
    pending = list(approvals)

    def fake_guarded(_repo: Path, _issue: str, name: str, **_kw: object) -> policy.ApprovalResult:
        asked.append(name)
        return pending.pop(0) if len(pending) > 1 else pending[0]

    monkeypatch.setattr(loop.policy, "approve_checkpoint_guarded", fake_guarded)
    return asked


def test_run_ceremony_keeps_driving_after_a_step_that_did_not_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "build", "verify", "merged"),
        loop.AdvanceResult("i", "verify", "verify", "blocked", checkpoint="ship"),
        loop.AdvanceResult("i", "ship", "done", "tore-down"),
    )
    asked = _script_approval(monkeypatch, policy.ApprovalResult("approved"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG)
    assert asked == ["ship"]
    assert [step.action for step in result.steps] == ["merged", "blocked", "tore-down"]
    assert result.events[2] == loop.CheckpointApproval("ship")
    assert not result.blocked


def test_run_ceremony_stops_on_a_challenge_and_carries_the_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    served = _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "intake", "intake", "blocked", checkpoint="classify"),
    )
    _script_approval(monkeypatch, policy.ApprovalResult("challenge", code="c0ffee"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG)
    assert result.challenge == ("classify", "c0ffee")
    assert result.blocked
    assert len(served) == 1


def test_run_ceremony_carries_why_a_grant_declined_the_challenge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "verify", "verify", "blocked", checkpoint="ship"),
    )
    _script_approval(
        monkeypatch,
        policy.ApprovalResult("challenge", code="c0ffee", detail="rework escalation on i.2"),
    )

    result = loop.run_ceremony(tmp_path, "i", config=CONFIG)

    assert result.challenge == ("ship", "c0ffee")
    assert result.challenge_reason == "rework escalation on i.2"


def test_run_ceremony_stops_on_a_refused_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "verify", "verify", "blocked", checkpoint="ship"),
    )
    _script_approval(monkeypatch, policy.ApprovalResult("rejected", detail="invalid code"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG, confirms={"ship": "nope"})
    assert result.refused == ("ship", "invalid code")
    assert result.events == result.steps and result.blocked


def test_run_ceremony_leaves_a_non_checkpoint_block_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "classify", "classify", "blocked", "awaiting the agent's work"),
    )
    asked = _script_approval(monkeypatch, policy.ApprovalResult("approved"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG)
    assert asked == []
    assert result.challenge is None and result.refused is None
    assert result.blocked


def test_run_ceremony_does_not_spin_on_a_checkpoint_it_already_approved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    served = _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "verify", "verify", "blocked", checkpoint="ship"),
    )
    asked = _script_approval(monkeypatch, policy.ApprovalResult("approved"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG, max_steps=20)
    assert asked == ["ship"]
    assert len(served) == 2
    assert result.blocked


def test_ensure_child_worktrees_publishes_claims_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracker_commits: list[tuple[str, str | None]],
) -> None:
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


def _pin_provisioning(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ranked: tuple[str, ...],
    concurrency: int,
    refused: frozenset[str] = frozenset(),
    unsized: frozenset[str] = frozenset(),
) -> list[str]:
    created: list[str] = []

    def _create(name: str, **_kwargs: object) -> Session:
        created.append(name)
        return _session(name)

    sized = decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(
            scope_tokens=9_000, overhead_tokens=3_000, build_factor=2.0
        ),
        source=decompose.FROZEN_FORECAST,
    )

    def _admit(_repo_root, issue_id: str, _sizing) -> WorkingSetAdmission:
        return WorkingSetAdmission(
            issue_id,
            None if issue_id in unsized else sized,
            None,
            refused=issue_id in refused,
        )

    monkeypatch.setattr(
        loop,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(base_branch=None, concurrency=concurrency),
    )
    monkeypatch.setattr(worktree, "create", _create)
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        loop.loop_state,
        "ready_ranked",
        lambda *_a, **_k: tuple(
            RankedNode(rank=index, score=0, issue_id=cid, title=cid)
            for index, cid in enumerate(ranked, start=1)
        ),
    )
    monkeypatch.setattr(working_set, "admit_working_set", _admit)
    return created


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_child_worktrees_provisions_in_ranked_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    created = _pin_provisioning(monkeypatch, ranked=("i.9", "i.1"), concurrency=1)

    loop._ensure_child_worktrees(ctx, [("i.1", "in_progress"), ("i.9", "in_progress")])
    assert created == ["i-9"]


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_child_worktrees_skips_a_child_the_band_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    created = _pin_provisioning(
        monkeypatch, ranked=("i.1", "i.2"), concurrency=1, refused=frozenset({"i.1"})
    )

    loop._ensure_child_worktrees(ctx, [("i.1", "in_progress"), ("i.2", "in_progress")])
    assert created == ["i-2"]


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_child_worktrees_provisions_an_unsizeable_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    created = _pin_provisioning(
        monkeypatch, ranked=("i.1",), concurrency=2, unsized=frozenset({"i.1"})
    )

    loop._ensure_child_worktrees(ctx, [("i.1", "in_progress")])
    assert created == ["i-1"]


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_child_worktrees_skips_a_closed_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    created = _pin_provisioning(monkeypatch, ranked=("i.1", "i.2"), concurrency=2)

    loop._ensure_child_worktrees(ctx, [("i.1", "closed"), ("i.2", "in_progress")])
    assert created == ["i-2"]


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_lane_worktrees_provisions_lanes_the_root_never_parented(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    created = _pin_provisioning(monkeypatch, ranked=("origin.1", "other.9"), concurrency=2)
    monkeypatch.setattr(
        worktree, "list_sessions", lambda *_a, **_k: [_session(name) for name in created]
    )
    monkeypatch.setattr(
        loop.loop_state, "read_node_state", lambda *_a, **_k: _state("decompose", issue_type="epic")
    )

    gained = loop.ensure_lane_worktrees(
        tmp_path, "release", [("origin.1", "open"), ("other.9", "open")], config=CONFIG
    )

    assert created == ["origin-1", "other-9"]
    assert gained == ("origin.1", "other.9"), "the ids that gained a worktree, for the routing"


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_lane_worktrees_reports_only_what_it_provisioned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    created = _pin_provisioning(monkeypatch, ranked=("origin.1", "other.9"), concurrency=1)
    monkeypatch.setattr(
        worktree, "list_sessions", lambda *_a, **_k: [_session(name) for name in created]
    )
    monkeypatch.setattr(
        loop.loop_state, "read_node_state", lambda *_a, **_k: _state("decompose", issue_type="epic")
    )

    gained = loop.ensure_lane_worktrees(
        tmp_path, "release", [("origin.1", "open"), ("other.9", "open")], config=CONFIG
    )

    assert created == ["origin-1"], "one slot, spent on the higher-ranked lane"
    assert gained == ("origin.1",)


def test_classify_leaf_forks_from_the_configured_base(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    monkeypatch.setattr(
        loop,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(base_branch="main", concurrency=4),
    )
    _advance(tmp_path)
    assert created["base"] == "main"
    assert created["repo_root"] == tmp_path


def test_ship_warns_and_names_the_paths_when_the_tracker_commit_is_skipped(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "cleanup", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: False)
    monkeypatch.setattr(loop.merge, "foreign_dirt", lambda _r: (".gitignore", "src/x.py"))

    result = _advance(tmp_path)

    assert result.to_phase == "done" and result.action == "tore-down"
    assert "tracker state NOT committed" in result.detail
    assert ".gitignore" in result.detail and "src/x.py" in result.detail
    assert "re-run the advance" in result.detail
    assert "tracker state committed" not in result.detail.replace("NOT committed", "")


def test_ship_stays_quiet_when_there_was_simply_nothing_to_commit(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "cleanup", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: False)
    monkeypatch.setattr(loop.merge, "foreign_dirt", lambda _r: ())

    result = _advance(tmp_path)

    assert result.detail == "worktree torn down and issue closed"


def test_the_claim_commit_also_warns_when_it_is_skipped(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: False)
    monkeypatch.setattr(loop.merge, "foreign_dirt", lambda _r: (".gitignore",))

    result = _advance(tmp_path)

    assert "provisioned" in result.detail
    assert "tracker state NOT committed" in result.detail
    assert ".gitignore" in result.detail


def test_a_published_claim_adds_no_warning(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tracker_commits: list
) -> None:
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")

    result = _advance(tmp_path)

    assert tracker_commits == [("i", "record the claim before provisioning")]
    assert "NOT committed" not in result.detail


def _evidence_config(**declarations: str) -> PolicyConfig:
    return PolicyConfig(required_gates=("verify",), max_rework=2, evidence=dict(declarations))


def _session_at(path: Path, name: str = "i") -> Session:
    return Session(
        name=name,
        branch=f"harness/{name}",
        base="main",
        base_head="abc",
        worktree_path=str(path),
        created_at="2026-07-14T00:00:00Z",
    )


def test_the_default_configuration_records_no_evidence(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("verify"))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(
        policy, "record_evidence", lambda *_a: pytest.fail("nothing was declared to record")
    )
    assert _advance(tmp_path).to_phase == "ship"


def test_a_declared_artifact_refuses_the_advance_before_the_handler_runs(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("verify"))
    monkeypatch.setattr(
        policy,
        "checkpoint_approved",
        lambda *_a: pytest.fail("the phase handler must not run without its evidence"),
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(verify="run.log"))
    assert result.blocked and result.needs_input == "evidence"
    assert "run.log" in result.detail


def test_a_present_artifact_lets_the_phase_advance_and_records_its_path(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("verify"))
    (tmp_path / "run.log").write_text("2 passed", encoding="utf-8")
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        policy,
        "record_evidence",
        lambda _r, _i, phase, declared: bool(recorded.append((phase, declared))),
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(verify="run.log"))
    assert result.to_phase == "ship"
    assert recorded == [("verify", "run.log")]


def test_the_evidence_marker_is_written_before_ship_commits_the_tracker(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("ship"))
    (tmp_path / "ship.log").write_text("shipped", encoding="utf-8")
    events: list[str] = []
    monkeypatch.setattr(
        policy, "record_evidence", lambda *_a: bool(events.append("evidence")) or True
    )
    monkeypatch.setattr(loop, "_write", lambda _r, args, **_k: events.append(args[0]))
    monkeypatch.setattr(
        loop.merge, "commit_tracker_state", lambda *_a, **_k: bool(events.append("commit")) or True
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(ship="ship.log"))
    assert result.to_phase == "done"
    assert events == ["evidence", "close", "commit"]


def test_a_misspelled_phase_refuses_the_advance_of_every_phase(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("verify"))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    config = PolicyConfig(required_gates=("verify",), max_rework=2, evidence={"verfiy": "run.log"})
    result = loop.advance(tmp_path, "i", config=config)
    assert result.blocked and "verfiy" in result.detail


def test_a_missing_build_artifact_refuses_before_the_merge(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    checkout = tmp_path / "wt"
    checkout.mkdir()
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session_at(checkout))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: pytest.fail("the merge must not be attempted")
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(build="build.log"))
    assert result.blocked and result.needs_input == "evidence"


def test_a_build_artifact_is_looked_for_in_the_lane_worktree_not_the_base(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    checkout = tmp_path / "wt"
    checkout.mkdir()
    (checkout / "build.log").write_text("built", encoding="utf-8")
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session_at(checkout))
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(policy, "record_evidence", lambda *_a: True)
    result = loop.advance(tmp_path, "i", config=_evidence_config(build="build.log"))
    assert result.to_phase == "verify" and result.action == "merged"


def _lane(has_children: bool = True) -> NodeState:
    return _state("build", worktree=WorktreeBinding("i", "harness/i"), has_children=has_children)


def _pin_lane(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subtasks: list[tuple[str, str]],
    committed: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    pending: tuple[str, ...] = (),
) -> dict:
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

    monkeypatch.setattr(loop, "_write", _br)

    def _run_verify(_root, mode, *_a, **_k):
        calls["verify"].append(mode)
        return verify.VerifyReport(mode, ())

    monkeypatch.setattr(verify, "run_verify", _run_verify)

    def _report(_root, issue_id, report, **_k):
        calls["gates"].append((issue_id, report.mode))
        return True, "ok"

    monkeypatch.setattr(verify, "report_gate", _report)
    return calls


def test_a_declared_build_artifact_does_not_block_a_lanes_own_subtasks(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_lane())
    calls = _pin_lane(monkeypatch, subtasks=[("i.1", "open"), ("i.2", "open")], committed=("i.1",))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner, "run", lambda *_a, **_k: pytest.fail("a committed sub-task must not re-dispatch")
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(build="build.log"))
    assert result.action == "sub-task" and not result.blocked
    assert calls["closed"] == ["i.1"]


def test_a_build_declaration_without_a_session_record_fails_closed(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: None)
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: pytest.fail("the merge must not be attempted")
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(build="build.log"))
    assert result.blocked and "no session record" in result.detail


def test_every_loop_phase_has_a_handler_and_vice_versa() -> None:

    assert set(loop._HANDLERS) == set(LOOP_PHASES)


def _scope_config(collision: str = "block") -> PolicyConfig:
    return PolicyConfig(required_gates=("verify",), max_rework=2, scope_collision=collision)


def _pin_scope(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scopes: dict[str, tuple[str, ...] | None],
    changed: tuple[str, ...],
    live: tuple[str, ...] = ("i",),
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:

    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session())
    monkeypatch.setattr(
        decompose,
        "bead_class_and_scope",
        lambda _r, bead: None if scopes.get(bead) is None else ("task", scopes[bead]),
    )
    monkeypatch.setattr(
        merge, "branch_changed_paths", lambda *_a: pytest.fail("the diff must not be read")
    )
    if scopes.get("i") is not None:
        monkeypatch.setattr(merge, "branch_changed_paths", lambda *_a: changed)
    monkeypatch.setattr(merge, "known_bead_ids", lambda _r: set(scopes))
    monkeypatch.setattr(worktree, "list_sessions", lambda _r: [_session(name) for name in live])
    recorded: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def _record(_repo_root, _issue, paths, colliding=()):
        recorded.append((tuple(paths), tuple(colliding)))
        return True

    monkeypatch.setattr(policy, "record_scope_violation", _record)
    return recorded


def _pin_clean_landing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))


def test_an_out_of_scope_edit_into_a_live_lanes_ground_refuses_the_landing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(
        monkeypatch,
        scopes={"i": ("src/a.py",), "j": ("src/b.py",)},
        changed=("src/a.py", "src/b.py"),
        live=("i", "j"),
    )
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: pytest.fail("the merge must not be attempted")
    )
    result = loop.advance(tmp_path, "i", config=_scope_config())
    assert result.blocked and result.needs_input == "scope"
    assert "src/b.py" in result.detail and "j" in result.detail
    assert recorded == [(("src/b.py",), ("j",))]


def test_an_out_of_scope_edit_nobody_else_declared_is_recorded_and_lands(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(
        monkeypatch,
        scopes={"i": ("src/a.py",), "j": ("src/b.py",)},
        changed=("src/a.py", "docs/x.md"),
        live=("i", "j"),
    )
    _pin_clean_landing(monkeypatch)
    result = loop.advance(tmp_path, "i", config=_scope_config())
    assert result.to_phase == "verify" and result.action == "merged"
    assert recorded == [(("docs/x.md",), ())]


def test_a_collision_with_a_torn_down_lane_is_not_a_collision(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(
        monkeypatch,
        scopes={"i": ("src/a.py",), "j": ("src/b.py",)},
        changed=("src/b.py",),
        live=("i",),
    )
    _pin_clean_landing(monkeypatch)
    result = loop.advance(tmp_path, "i", config=_scope_config())
    assert result.action == "merged"
    assert recorded == [(("src/b.py",), ())]


def test_the_configured_policy_can_land_on_the_collision_instead(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(
        monkeypatch,
        scopes={"i": ("src/a.py",), "j": ("src/b.py",)},
        changed=("src/b.py",),
        live=("i", "j"),
    )
    _pin_clean_landing(monkeypatch)
    result = loop.advance(tmp_path, "i", config=_scope_config("warn"))
    assert result.action == "merged"
    assert recorded == [(("src/b.py",), ("j",))]


def test_a_bead_that_declared_no_scope_is_not_checked_at_all(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(monkeypatch, scopes={"i": None}, changed=("anything.py",))
    _pin_clean_landing(monkeypatch)
    result = loop.advance(tmp_path, "i", config=_scope_config())
    assert result.action == "merged"
    assert recorded == []


def test_a_lane_that_stayed_inside_its_scope_writes_nothing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(
        monkeypatch,
        scopes={"i": ("src/**",)},
        changed=("src/a.py", "src/b.py", ".basicly/ledger/events-0001.jsonl"),
    )
    _pin_clean_landing(monkeypatch)
    result = loop.advance(tmp_path, "i", config=_scope_config())
    assert result.action == "merged"
    assert recorded == []


def test_the_dispatch_records_its_forecast_beside_the_scope_it_measured(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        decompose,
        "dispatch_sizing",
        lambda *_a: decompose.DispatchSizing(
            task_class="task",
            estimate=decompose.CostEstimate(
                scope_tokens=9_000, overhead_tokens=3_000, build_factor=2.0
            ),
            source=decompose.FROZEN_FORECAST,
        ),
    )
    recorded: dict = {}
    monkeypatch.setattr(loop, "record_run", lambda *_a, **kw: recorded.update(kw))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0
        ),
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: pytest.fail("no landing here"))

    loop._run_agent(loop._Ctx(tmp_path, "i", _state("build"), CONFIG, loop.Inputs()), "i", tmp_path)

    assert recorded["scope_tokens"] == 9_000
    assert recorded["forecast_tokens"] == 21_000
    assert recorded["task_class"] == "task"
    assert recorded["forecast_source"] == decompose.FROZEN_FORECAST


def test_the_interactive_dispatch_records_a_write_phase_and_a_seeded_factor(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        decompose,
        "dispatch_sizing",
        lambda *_a: decompose.DispatchSizing(
            task_class="task",
            estimate=decompose.CostEstimate(
                scope_tokens=9_000,
                overhead_tokens=3_000,
                build_factor=2.0,
                build_factor_source=decompose.BUILD_FACTOR_SEED,
            ),
            source=decompose.FROZEN_FORECAST,
        ),
    )
    recorded: dict = {}
    monkeypatch.setattr(loop, "record_run", lambda *_a, **kw: recorded.update(kw))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0
        ),
    )

    loop._run_agent(loop._Ctx(tmp_path, "i", _state("build"), CONFIG, loop.Inputs()), "i", tmp_path)

    assert recorded["phase"] == run_record.BUILD_PHASE
    assert run_record.is_write_phase(recorded["phase"])
    assert recorded["build_factor_source"] == decompose.BUILD_FACTOR_SEED


def test_a_dispatch_that_failed_still_records_the_scope_it_was_sized_on(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        decompose,
        "dispatch_sizing",
        lambda *_a: decompose.DispatchSizing(
            task_class="task",
            estimate=decompose.CostEstimate(
                scope_tokens=9_000, overhead_tokens=3_000, build_factor=2.0
            ),
            source=decompose.FROZEN_FORECAST,
        ),
    )
    recorded: dict = {}
    monkeypatch.setattr(loop, "record_run", lambda *_a, **kw: recorded.update(kw))
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=143
        ),
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: pytest.fail("no landing here"))

    loop._run_agent(loop._Ctx(tmp_path, "i", _state("build"), CONFIG, loop.Inputs()), "i", tmp_path)

    assert recorded["scope_tokens"] == 9_000
    assert recorded["forecast_tokens"] == 21_000


def test_sizing_at_dispatch_is_empty_when_the_bead_declares_no_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(decompose, "dispatch_sizing", lambda *_a: None)
    assert loop.sizing_at_dispatch(tmp_path, "i") == {}


def test_sizing_at_dispatch_never_raises_on_a_tracker_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def _boom(*_a):
        raise RuntimeError("br is unavailable")

    monkeypatch.setattr(decompose, "dispatch_sizing", _boom)
    assert loop.sizing_at_dispatch(tmp_path, "i") == {}


def _ledger(monkeypatch: pytest.MonkeyPatch, *counts: int) -> None:
    points = tuple(retrospective.Point(f"u{i}", n) for i, n in enumerate(counts))
    monkeypatch.setattr(loop.retrospective, "read_ledger", lambda *_a: points)


def _retro_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict]:
    monkeypatch.setattr(policy, "record_rework", lambda *_a, **_k: 1)
    monkeypatch.setattr(policy, "spend_status", lambda *_a, **_k: _unhalted())
    monkeypatch.setattr(loop.retrospective, "claim", lambda *_a: True)
    monkeypatch.setattr(loop.retrospective, "settle", lambda *_a: "control tier: a gate")
    _pin_runner(monkeypatch, "claude")
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "retrospector.md").write_text("---\nname: retrospector\n---\n", encoding="utf-8")
    seen: list[dict] = []

    def _run(spec, prompt, cwd, **kw):
        seen.append({"role": kw.get("role"), "prompt": prompt, "cwd": cwd})
        return runner.RunResult(spec.name, tuple(spec.command), executed=True, returncode=0)

    monkeypatch.setattr(runner, "run", _run)
    return seen


def test_a_special_cause_dispatches_the_retrospector_and_prices_it_as_a_read(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates(failed=True)))
    seen = _retro_dispatch(monkeypatch, tmp_path)
    _ledger(monkeypatch, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3)
    recorded: list[object] = []
    monkeypatch.setattr(loop, "record_run", lambda *_a, **kw: recorded.append(kw.get("phase")))

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="root")

    assert [call["role"] for call in seen] == ["retrospector"]
    assert "rule: beyond-limits" in seen[0]["prompt"] and "point: u9" in seen[0]["prompt"]
    assert recorded == [retrospective.PHASE]
    assert retrospective.PHASE not in run_record.WRITE_PHASES
    assert "retrospective fired (beyond-limits on u9)" in result.detail
    assert "[10 units, centre 0.30, sigma 0.55, upper limit 1.94]" in result.detail
    assert "outcome: control tier: a gate" in result.detail


def test_a_single_gate_failure_dispatches_no_retrospective(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates(failed=True)))
    seen = _retro_dispatch(monkeypatch, tmp_path)
    claimed: list[object] = []
    monkeypatch.setattr(loop.retrospective, "claim", lambda *a: claimed.append(a) or True)
    _ledger(monkeypatch, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="root")

    assert seen == [] and claimed == []
    assert "retrospective" not in result.detail


def test_a_claimed_signal_is_never_dispatched_twice(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("validate", gates=_validate_gates(failed=True)))
    seen = _retro_dispatch(monkeypatch, tmp_path)
    monkeypatch.setattr(loop.retrospective, "claim", lambda *_a: False)
    _ledger(monkeypatch, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3)

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="root")

    assert seen == []


def test_the_supervised_landing_pass_fires_no_retrospective(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates(failed=True)))
    seen = _retro_dispatch(monkeypatch, tmp_path)
    _ledger(monkeypatch, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3)

    loop.advance(
        tmp_path,
        "i",
        config=CONFIG,
        inputs=loop.Inputs(),
        grant_root="root",
        repair_dispatch=False,
    )

    assert seen == []


def test_a_session_that_named_no_root_reads_no_ledger(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at(_state("validate", gates=_validate_gates(failed=True)))
    seen = _retro_dispatch(monkeypatch, tmp_path)
    monkeypatch.setattr(
        loop.retrospective, "read_ledger", lambda *_a: pytest.fail("no session, no ledger")
    )

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs())

    assert seen == []


def test_a_halted_grant_still_pays_for_a_retrospective(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    at(_state("validate", gates=_validate_gates(failed=True)))
    seen = _retro_dispatch(monkeypatch, tmp_path)
    _ledger(monkeypatch, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3)
    claimed: list[object] = []
    monkeypatch.setattr(loop.retrospective, "claim", lambda *a: claimed.append(a) or True)
    monkeypatch.setattr(
        policy,
        "spend_status",
        lambda *_a, **_k: replace(_unhalted(), halted=True, detail="grant exhausted"),
    )

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="root")

    assert claimed, "a spent budget still held the retrospective back"
    assert seen, "and the signal was never dispatched"
