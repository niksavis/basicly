"""Tests for the checkpoint-gated loop state machine (onb.6.3).

The machine derives its phase from br every step, so each test pins a NodeState
(the resume point) and fakes the composed modules. The invariant under test:
every step either blocks or drives a br-state change that moves the derived
phase forward — the handlers and derive_phase never disagree.
"""

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
    loop,
    merge,
    needs_input,
    policy,
    rubrics,
    run_record,
    runner,
    supervise,
    verify,
    worktree,
)
from basicly.config import LOOP_PHASES, PolicyConfig, RunnerConfig, SizingConfig, WorktreeConfig
from basicly.loop_state import NodeState, RankedNode, WorktreeBinding
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


def test_classify_dor_block_names_the_scaffold_for_the_recorded_type(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refusal hands back the remedy, typed (basicly-kjc5.44).

    This message is where an agent used to *discover* the required sections — a
    read, an edit and a re-check each time — though the engine already recorded
    the work type the whole set derives from.
    """
    at(_state("classify", issue_type="bug"))
    monkeypatch.setattr(
        policy, "definition_of_ready", lambda *_a: DoRResult(False, ("## Steps to Reproduce",))
    )
    detail = _advance(tmp_path).detail
    assert "## Steps to Reproduce" in detail
    assert "basicly policy scaffold --type bug" in detail


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


def _stalled(rounds: int, *members: str) -> policy.Convergence:
    """A verdict saying this round reported *members* for the *rounds*-th time running."""
    return policy.Convergence(policy.STALLED, members, members, rounds)


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


def _never_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any dispatch a test failure, so a refusal is proven by nothing spawning."""
    monkeypatch.setattr(
        runner, "run", lambda *_a, **_k: pytest.fail("a refused dispatch must not spawn a runner")
    )


def test_a_halted_grant_refuses_the_interactive_dispatch(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The money defect: an exhausted grant still dispatched a metered agent.

    `policy.spend_status` is D3's one halt predicate, enforced at delegated approval, the
    supervised lane admission and decider delegation — and this path reached `runner.run`
    past all three. Observed live: basicly-jr0l's grant was spent 43599830/21000000 and a
    `loop run` dispatched anyway (basicly-1th1).
    """
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    _never_runs(monkeypatch)
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

    assert result.blocked
    assert result.needs_input == "grant"
    assert "refused before it started" in result.detail
    assert "500/100 tokens" in result.detail, "the halt's own numbers must reach the operator"


def test_a_dispatch_with_no_session_root_is_ungated_as_before(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control: no `--root` means no grant ledger to read, so nothing changes.

    Gating on an absent root would have to invent which bead carries the grant, and
    every caller that never passed one would start refusing.
    """
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
    """The band gate applies here too, using supervise's single admission definition.

    Re-deriving the rule locally is how the number that gates a dispatch and the number
    recorded beside its actual come to disagree (basicly-jr0l.34).
    """
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    _never_runs(monkeypatch)
    monkeypatch.setattr(loop.policy, "spend_status", lambda *_a, **_k: _unhalted())
    monkeypatch.setattr(
        supervise,
        "admit_working_set",
        lambda *_a, **_k: supervise.WorkingSetAdmission(
            "i", None, "child 'i' estimates 900000 working-set tokens, above 64000", refused=True
        ),
    )
    monkeypatch.setattr(supervise, "escalate_working_set", lambda *_a, **_k: None)

    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="epic")

    assert result.blocked and result.needs_input == "scope"
    assert "900000 working-set tokens" in result.detail


def test_a_scopeless_bead_still_dispatches_but_is_escalated(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bead declaring no scope is admitted — and recorded as never checked.

    Refusing it would ban hand-filed work, which is most of a real tracker; admitting it
    silently is the hole basicly-jr0l.60 closed. This path must do the same thing the
    supervised one does, including queuing the notice.
    """
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    monkeypatch.setattr(loop.policy, "spend_status", lambda *_a, **_k: _unhalted())
    escalated: list[str] = []
    monkeypatch.setattr(
        supervise,
        "admit_working_set",
        lambda *_a, **_k: supervise.WorkingSetAdmission(
            "i",
            None,
            "declares no scope the estimator can read",
            refused=False,
            absence="undeclared",
        ),
    )
    monkeypatch.setattr(
        supervise,
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


# --- The context ceiling meters this path too (basicly-7kxq) -----------------
#
# `ceiling_tokens`, `OVERRUN_MARKER` and `finalize_followup` lived in `supervise`
# with a single call site, so the interactive path dispatched unmetered: basicly-23ep
# was driven through `loop run`, recorded 403051 tokens of occupancy against a
# derived trigger of 120000 — 3.4x over — and was neither finalized early nor given
# a follow-up bead. It ran to completion and committed normally. A suite that
# exercised only `supervise` is what let that ship, so these drive the single-track
# path against a stubbed-low ceiling.


class _CeilingBr:
    """br stand-in for the finalize protocol: show, comments, create, dep add."""

    def __init__(self) -> None:
        self.created: list[list[str]] = []
        self.deps: list[tuple[str, ...]] = []
        self.comments: dict[str, list[str]] = {}

    def __call__(self, _repo_root: Path, args: list[str], **_k) -> SimpleNamespace:
        if args[:1] == ["show"]:
            return SimpleNamespace(stdout=json.dumps([_CEILING_ISSUE | {"id": args[1]}]))
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            return SimpleNamespace(stdout=json.dumps([{"text": text} for text in texts]))
        if args[:2] == ["comments", "add"]:
            self.comments.setdefault(args[2], []).append(args[3])
            return SimpleNamespace(stdout="{}")
        if args[:1] == ["create"]:
            self.created.append(args)
            return SimpleNamespace(stdout=json.dumps({"id": f"new-{len(self.created)}"}))
        if args[:2] == ["dep", "add"]:
            self.deps.append(tuple(args[2:]))
            return SimpleNamespace(stdout="{}")
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
    """Pin the finalize trigger at *ceiling* of the window and fake the tracker."""
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
    br = _CeilingBr()
    monkeypatch.setattr(supervise, "_run_br", br)
    return br


def _occupying(monkeypatch: pytest.MonkeyPatch, tokens: int) -> None:
    """Dispatch a claude run whose last streamed turn occupied *tokens* of window."""
    turn = json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": tokens}}})
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, stdout=turn
        ),
    )


def test_a_single_track_dispatch_over_the_ceiling_spins_exactly_one_followup(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The finalize protocol reaches the interactive dispatch, not just the supervised one.

    Same outcome a supervised lane gets: the partial work still lands on the next
    advance, and the remainder becomes one follow-up gated on this bead.
    """
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    br = _pin_ceiling(monkeypatch, 0.05)  # 200000-token window -> a 10000 trigger
    _occupying(monkeypatch, 12_000)

    result = _advance(tmp_path)

    assert len(br.created) == 1, "exactly one follow-up carries the remainder"
    create = br.created[0]
    assert create[1] == "Follow-up: Build the parser (context-ceiling overrun)"
    assert "--parent" not in create, "no session root named means the follow-up is top-level"
    assert ("new-1", "i", "-t", "blocks") in br.deps
    assert br.comments["i"][-1].startswith(supervise.OVERRUN_MARKER)
    assert result.blocked
    assert "crossed the context ceiling" in result.detail
    assert "12000" in result.detail and "10000" in result.detail
    assert "new-1" in result.detail


def test_a_single_track_dispatch_under_the_ceiling_spins_nothing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control: a run inside the window finishes exactly as it did before."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    br = _pin_ceiling(monkeypatch, 0.05)
    _occupying(monkeypatch, 9_999)

    result = _advance(tmp_path)

    assert br.created == []
    assert result.blocked and "finished in worktree" in result.detail
    assert "context ceiling" not in result.detail


def test_a_single_track_overrun_that_landed_nothing_spins_no_followup(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run that stopped on a missing fact lands nothing to follow up (design 7.6).

    It gets re-dispatched once the fact is supplied, and a remainder bead pinned by
    the idempotence marker now would survive that re-dispatch.
    """
    wt = tmp_path / "wt"
    (wt / needs_input.SENTINEL_FILE.parent).mkdir(parents=True)
    at(_state("classify", issue_type="task"))
    monkeypatch.setattr(policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    monkeypatch.setattr(worktree, "list_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    monkeypatch.setattr(
        worktree, "create", lambda name, **_k: replace(_session(name), worktree_path=str(wt))
    )
    _pin_runner(monkeypatch, "claude")
    br = _pin_ceiling(monkeypatch, 0.05)
    _occupying(monkeypatch, 12_000)
    (wt / needs_input.SENTINEL_FILE).write_text(
        '{"fact": "prod db dialect", "detail": "no vendor marker"}', encoding="utf-8"
    )
    monkeypatch.setattr(policy, "record_needs_input", lambda *_a: None)
    monkeypatch.setattr(loop.decisions, "enqueue", lambda *_a, **_k: None)

    result = _advance(tmp_path)

    assert br.created == []
    assert result.blocked and result.needs_input == "prod db dialect"


def test_a_single_track_overrun_parents_its_followup_under_the_session_root(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`loop run --root <epic>` names the session, so the remainder is its sibling.

    The reproduction ran `loop run basicly-23ep --root basicly-yc0x`; a supervised
    lane's remainder is a top-level package under that same root, and this path has
    no other candidate for one.
    """
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "claude")
    br = _pin_ceiling(monkeypatch, 0.05)
    _occupying(monkeypatch, 12_000)
    monkeypatch.setattr(loop.policy, "spend_status", lambda *_a, **_k: _unhalted())
    monkeypatch.setattr(
        supervise,
        "admit_working_set",
        lambda *_a, **_k: supervise.WorkingSetAdmission("i", None, "", refused=False),
    )
    monkeypatch.setattr(supervise, "escalate_working_set", lambda *_a, **_k: None)

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), grant_root="epic")

    create = br.created[0]
    parent_at = create.index("--parent")
    assert tuple(create[parent_at : parent_at + 2]) == ("--parent", "epic")


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


def test_classify_feature_names_a_collapsing_path_in_the_advance_detail(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A group count with no reason for it is where the collapse hid (basicly-jr0l.45).

    The loop is how decompose actually runs in the factory, so a report only
    ``basicly decompose`` prints is a report nobody on that path reads.
    """
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
    # The neutralized one is named by the full report, not by this one-line detail:
    # nothing on it needs acting on.
    assert "uv.lock" not in result.detail


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


def test_decompose_fan_in_does_not_wait_on_a_deferred_child(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every non-deferred child closed means the epic fans in (basicly-toj6).

    ``still_open`` was ``status != "closed"``, so a child somebody deferred parked
    the epic at "1 child track(s) still open" with nothing left that could ever
    close it. The control is the test above: an ``in_progress`` sibling in the same
    position does still hold the epic.
    """
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


def test_a_refused_verify_gate_blocks_instead_of_deriving_back_to_build(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tracker that refuses the gate must stop the loop and say so (basicly-o7z5).

    ``report_gate`` degrades gracefully by design, and both call sites used to
    discard its verdict. Since ``derive_phase`` keys off ``gates.can_advance``,
    an unrecorded gate silently derived the node back to ``build`` and the next
    advance re-ran build->verify forever, with nothing naming the cause. br
    0.2.19 made that reachable in the field: its ``gate report`` rejects the
    harness's call outright.
    """
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


def test_a_landing_interrupted_before_the_gate_resumes_at_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The interruption this recovers: merged, then killed before recording the gate.

    On the retry the branch is already an ancestor of base with nothing ahead of it.
    The landing must finish forward — record the gate and advance — instead of
    blocking on a branch it reads as empty and charging the lane for it
    (basicly-jr0l.50).
    """
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

    assert not result.blocked  # it completes rather than parking the lane
    assert result.to_phase == "verify" and result.action == "merged"
    assert charged == []  # a landing that worked is never charged for working
    assert recorded, "the missing verify gate is what this resumes to record"


def test_an_unreliable_gate_spends_no_rework_and_records_the_flake(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gate that failed and then passed unchanged must not cost the node (basicly-55yh)."""
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

    assert charged == []  # the whole point
    assert result.blocked and result.action == "blocked"  # not "escalated"
    assert result.landing is attempt
    assert [(a[1], a[2]) for a in flakes] == [("i", merge.MERGE_GATE)]


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


# --- a hard-killed dispatch keeps its worktree (basicly-yvx9) ----------------


def _killed_dispatch(cwd: Path) -> loop._Dispatch:
    """The dispatch a wall-clock kill hands back: no returncode, ``timed_out`` set."""
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
    """The kill takes the agent out before its last step, so the harness takes it.

    The advance still blocks — a timeout is a thing an operator should see — but it
    blocks over a *committed* branch, so the next advance judges the diff instead of
    refusing it as not-ready and paying for a second run (basicly-yvx9).
    """
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
    assert "hit runner_timeout (1800s)" in held.detail
    assert "the worktree was committed as abc1234; advance again to judge it" in held.detail


def test_a_killed_dispatch_the_salvage_refused_still_asks_for_a_re_dispatch(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With nothing committed there is nothing to judge, so the old advice stands."""
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


# --- Is the rework loop converging? (basicly-m4zv.5) -------------------------


def _red_landing(at, monkeypatch: pytest.MonkeyPatch, status: str = "verify-failed") -> list[tuple]:
    """A leaf at ``build`` whose landing fails *status*; return the refunds it grants."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda *_a, **_k: merge.MergeResult("i", status, "verify full failed: pytest"),
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)  # inside the cap
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
    """A single repeat is a warning, not an escalation (the AC's first clause).

    The gate reports only what it checks, so this round may have changed something
    real that this gate cannot see — the loop says so and keeps its ordinary cap.
    """
    refunds = _red_landing(at, monkeypatch)
    _pin_finding_sets(monkeypatch, _stalled(1, "pytest"))

    result = _advance(tmp_path)

    assert result.action == "blocked" and refunds == []
    assert "warning:" in result.detail and "changed nothing it reports" in result.detail


def test_a_stalled_round_that_is_also_the_cap_round_queues_the_warning_with_it(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """At ``max_rework=2`` the first repeat *is* the cap round (the observed shape).

    The warning has to reach the queue item and not only the returned detail, or the
    human triaging the cap escalation cannot see that the last attempt learned
    nothing the gate reports — which is the fact that decides between re-dispatching
    and re-scoping.
    """
    _red_landing(at, monkeypatch)
    monkeypatch.setattr(policy, "record_rework", lambda *_a: CONFIG.max_rework)  # at the cap
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
    """The observed defect, closed: attempt 2 re-derived attempt 1's verdict verbatim.

    It escalates before the cap is reached *and* refunds the attempt, so the human's
    answer still has the budget the loop would otherwise have spent re-reporting a
    finding set already on the bead.
    """
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
    assert refunds == [("i", merge.MERGE_GATE)]  # charged, then refunded
    assert "this attempt refunded" in result.detail and "not converging" in result.detail
    # The ordinary rework escalation, so an answered `retry` stays executable.
    assert queued == [("i", "escalation", policy.rework_escalation_question(merge.MERGE_GATE))]


def test_a_second_escalation_says_the_refund_is_gone_and_still_stops(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once the refund is spent the cap is the backstop again, and the node says so.

    Forgiving every round would mean no budget is ever spent, so no cap is ever
    reached — the jr0l.41 livelock under a new name.
    """
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
    """Divergence needs no second round: rework made the work worse (the AC's third clause)."""
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
    """A red landing's finding set is what makes a repeat of it detectable.

    Tagged with the status so a cause can never compare equal to a check name.
    """
    _red_landing(at, monkeypatch)
    recorded = _pin_finding_sets(monkeypatch)

    _advance(tmp_path)

    assert recorded == [
        ("i", merge.MERGE_GATE, ("status=verify-failed", "verify full failed: pytest"))
    ]


def test_a_collided_landing_records_no_finding_set_in_the_loop(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The merge gate's own half belongs to the bounce, which owns its threshold.

    Recording it here too would compare the bounce's round against itself and refund
    twice for one attempt (basicly-bdd4 landed that half in `supervise`).
    """
    _red_landing(at, monkeypatch, status="merge-conflicts")
    recorded = _pin_finding_sets(monkeypatch)

    result = _advance(tmp_path)

    assert recorded == [] and result.blocked


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


def test_advance_refuses_to_close_a_leaf_that_never_built(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An out-of-order ship approval must not close an unstarted leaf (basicly-jr0l.49).

    ``approve_checkpoint`` enforces no phase ordering, so ``ship`` can be recorded
    on a bead that never built. Such a leaf has no worktree binding — the very
    signal a node torn down after its merge shows — and the ladder read that as
    landed, so the advance closed the bead with zero work done. The
    basicly-o0q3 guard above could not catch it: that guard checks the binding's
    branch, and here there is no binding to check.

    The phase is *derived* rather than pinned, unlike the other tests in this
    file: the defect was in the derivation, so pinning ``ship`` would assert the
    bug back into place.
    """
    checkpoints = ("ship",)
    gates = _gate(can_advance=False)  # the build->verify landing never ran
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
            agent_context=None,
            has_children=False,
        )
    )

    def _boom(*_a, **_k):
        raise AssertionError("a leaf that never built must not be closed or torn down")

    monkeypatch.setattr(worktree, "cleanup", _boom)
    monkeypatch.setattr(loop, "_run_br", _boom)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", _boom)

    result = _advance(tmp_path)
    assert result.blocked
    assert result.to_phase != "done"


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


def test_ship_records_the_forecast_and_the_whole_packages_actual_cost(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bead is the only carrier that survives a clone, so ship writes the ledger.

    basicly-kjc5.50: run-records live in the self-ignored ``.basicly/usage/``, so
    without this a fresh clone forecasts from the seed factors and never learns
    what a package cost. The actual must span every dispatch — the failed attempt
    included — or the packages whose cheap dispatch produced an expensive result
    are exactly the ones understated.
    """
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "cleanup", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
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
    assert written["forecast"].tokens == 18_000  # 2_000 overhead + 8_000 x 2.0
    actual = written["actual"]
    assert actual.dispatches == 2 and actual.rework == 1
    assert actual.tokens == 42_000
    assert actual.cost == pytest.approx(0.8)
    assert actual.wall_clock_s == pytest.approx(500.0)


def test_ship_records_the_rollup_before_the_tracker_commit(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rollup has to be flushed by the closing commit, or it never leaves the machine."""
    at(_state("ship"))
    order: list[str] = []
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: order.append("close"))
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


def test_ship_writes_no_rollup_for_a_node_that_was_never_dispatched(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A decomposed feature costs what its children cost — it is not a package of its own.

    Recording a zero-dispatch rollup for it would count the same work twice and
    dilute cost-per-landed-package with a null.
    """
    at(_state("ship", issue_type="feature", has_children=True))
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    monkeypatch.setattr(run_record, "dispatch_history", lambda _repo: {})
    monkeypatch.setattr(
        run_record, "record_cost_marker", lambda *_a, **_k: pytest.fail("no rollup is due")
    )

    result = _advance(tmp_path)
    assert result.action == "tore-down" and "cost rollup" not in result.detail


def test_ship_proceeds_when_the_cost_rollup_cannot_be_written(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Evidence is never worth failing a landing for: the ship reports no rollup and goes on."""
    at(_state("ship"))
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)

    def _boom(*_a, **_k):
        raise RuntimeError("br is unavailable")

    monkeypatch.setattr(run_record, "dispatch_history", _boom)

    result = _advance(tmp_path)
    assert result.to_phase == "done" and result.action == "tore-down"
    assert "cost rollup" not in result.detail


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

    # Two seams: `show-ref` runs through worktree.git, while the ancestry proof is
    # now shared with the landing via merge.is_ancestor, which binds git in merge's
    # own namespace (basicly-jr0l.46).
    monkeypatch.setattr(worktree, "git", git)
    monkeypatch.setattr(merge, "git", git)
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: _session("i"))
    assert loop._worktree_landed(Path("/x"), WorktreeBinding("i", "harness/i")) is True


def test_worktree_landed_non_ancestor_is_stranded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing branch not reachable from base HEAD is stranded (never merged)."""

    def git(args, **_k):
        # show-ref exists (0); merge-base --is-ancestor fails (1) => not merged
        return SimpleNamespace(returncode=0 if args[0] == "show-ref" else 1)

    monkeypatch.setattr(worktree, "git", git)
    monkeypatch.setattr(merge, "git", git)
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


# --- The whole-boundary ceremony (basicly-kjc5.41) ---------------------------
#
# These pin the driver, so they script :func:`loop.advance` rather than the
# tracker: what is under test is which steps the ceremony keeps driving through
# and which checkpoints it hands back to a human.


def _script_advance(
    monkeypatch: pytest.MonkeyPatch, *results: loop.AdvanceResult
) -> list[loop.AdvanceResult]:
    """Make ``loop.advance`` replay *results*, recording the calls it served."""
    served: list[loop.AdvanceResult] = []
    pending = list(results)

    def fake_advance(_repo: Path, _issue: str, **_kw: object) -> loop.AdvanceResult:
        # Repeat the last scripted step forever, so a spinning driver runs to
        # max_steps instead of raising StopIteration and looking like a failure.
        result = pending.pop(0) if len(pending) > 1 else pending[0]
        served.append(result)
        return result

    monkeypatch.setattr(loop, "advance", fake_advance)
    return served


def _script_approval(
    monkeypatch: pytest.MonkeyPatch, *approvals: policy.ApprovalResult
) -> list[str]:
    """Make checkpoint approval replay *approvals*, recording the names asked for."""
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
    """A landing is progress, not a resting point — the ceremony drives on to ship.

    Regression: an early draft ended the boundary on *any* non-blocked step, so
    ``loop run`` stopped right after ``[merged] build -> verify`` and the ship
    checkpoint still needed a hand-issued command — exactly the ceremony this
    command exists to collapse.
    """
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
    assert [approval.checkpoint for approval in result.approvals] == ["ship"]
    assert not result.blocked


def test_run_ceremony_stops_on_a_challenge_and_carries_the_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unauthorized checkpoint ends the boundary once, with the code to relay."""
    served = _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "intake", "intake", "blocked", checkpoint="classify"),
    )
    _script_approval(monkeypatch, policy.ApprovalResult("challenge", code="c0ffee"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG)
    assert result.challenge == ("classify", "c0ffee")
    assert result.blocked
    # One advance, so exactly one challenge is minted per invocation.
    assert len(served) == 1


def test_run_ceremony_carries_why_a_grant_declined_the_challenge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reason must survive the ceremony, or ``loop run`` reprints a bare ask.

    That is the surface the incident was measured on (basicly-5ltn): the operator
    drove ``loop run``, so dropping the detail here would leave the fix invisible.
    """
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
    """A bad code refuses the boundary rather than driving on unapproved."""
    _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "verify", "verify", "blocked", checkpoint="ship"),
    )
    _script_approval(monkeypatch, policy.ApprovalResult("rejected", detail="invalid code"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG, confirms={"ship": "nope"})
    assert result.refused == ("ship", "invalid code")
    assert result.approvals == () and result.blocked


def test_run_ceremony_leaves_a_non_checkpoint_block_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The handoff that awaits the agent's work is the end of the opening boundary.

    It is a block with no checkpoint behind it, so no approval is attempted and
    no confirm code is minted — the ceremony simply stops.
    """
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
    """A checkpoint that blocks again after approval stops the boundary, not the clock."""
    served = _script_advance(
        monkeypatch,
        loop.AdvanceResult("i", "verify", "verify", "blocked", checkpoint="ship"),
    )
    asked = _script_approval(monkeypatch, policy.ApprovalResult("approved"))
    result = loop.run_ceremony(tmp_path, "i", config=CONFIG, max_steps=20)
    assert asked == ["ship"]
    assert len(served) == 2  # the approval is tried once, then the repeat block ends it
    assert result.blocked


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


def _pin_provisioning(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ranked: tuple[str, ...],
    concurrency: int,
    refused: frozenset[str] = frozenset(),
    unsized: frozenset[str] = frozenset(),
) -> list[str]:
    """Pin fan-out provisioning; return the list each created worktree name lands in."""
    created: list[str] = []

    def _create(name: str, **_kwargs: object) -> Session:
        created.append(name)
        return _session(name)

    # The function under test reads only `refused`; a real sizing is built so the
    # unsizeable case is modelled the way admit_working_set reports it — `sizing`
    # None, `refused` False — rather than by a stand-in that cannot type-check.
    sized = decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(
            scope_tokens=9_000, overhead_tokens=3_000, build_factor=2.0
        ),
        source=decompose.FROZEN_FORECAST,
    )

    def _admit(_repo_root, issue_id: str, _sizing) -> supervise.WorkingSetAdmission:
        return supervise.WorkingSetAdmission(
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
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    monkeypatch.setattr(
        loop.loop_state,
        "ready_ranked",
        lambda *_a, **_k: tuple(
            RankedNode(rank=index, score=0, issue_id=cid, title=cid)
            for index, cid in enumerate(ranked, start=1)
        ),
    )
    monkeypatch.setattr(supervise, "admit_working_set", _admit)
    return created


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_child_worktrees_provisions_in_ranked_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cap spends its slots on the highest-ranked children, not br's listing order."""
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    created = _pin_provisioning(monkeypatch, ranked=("i.9", "i.1"), concurrency=1)

    # br lists i.1 first; the scheduler ranks i.9 above it. One slot, so the two
    # orderings disagree on the whole pass.
    loop._ensure_child_worktrees(ctx, [("i.1", "in_progress"), ("i.9", "in_progress")])
    assert created == ["i-9"]


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_child_worktrees_skips_a_child_the_band_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An over-ceiling lane yields its slot instead of holding a worktree nothing runs in."""
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
    """An unreadable scope is not a refusal, so the lane is deferred to dispatch, not dropped."""
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
    """A closed child is never provisioned, even when the scheduler still ranks it."""
    ctx = loop._Ctx(tmp_path, "i", _state("decompose", has_children=True), CONFIG, loop.Inputs())
    created = _pin_provisioning(monkeypatch, ranked=("i.1", "i.2"), concurrency=2)

    loop._ensure_child_worktrees(ctx, [("i.1", "closed"), ("i.2", "in_progress")])
    assert created == ["i-2"]


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
    findings = _pin_finding_sets(monkeypatch)
    result = loop.advance(tmp_path, "i", config=CONFIG)
    assert recorded == ["i"]
    assert result.blocked and "lane validate failed: acceptance" in result.detail
    # The failed checks are the rubric gate's finding set (basicly-m4zv.5).
    assert findings == [("i", rubrics.RUBRIC_GATE, ("acceptance",))]


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


# --- A skipped tracker-state commit is never silent (basicly-f7li) ------------


def test_ship_warns_and_names_the_paths_when_the_tracker_commit_is_skipped(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reported defect: it printed a clean ship and the operator pushed without the tracker."""
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "cleanup", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: False)
    monkeypatch.setattr(loop.merge, "foreign_dirt", lambda _r: (".gitignore", "src/x.py"))

    result = _advance(tmp_path)

    assert result.to_phase == "done" and result.action == "tore-down"  # the ship did happen
    assert "tracker state NOT committed" in result.detail
    assert ".gitignore" in result.detail and "src/x.py" in result.detail
    assert "re-run the advance" in result.detail  # the recovery
    assert "tracker state committed" not in result.detail.replace("NOT committed", "")


def test_ship_stays_quiet_when_there_was_simply_nothing_to_commit(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declined commit with no foreign dirt is 'nothing pending', which is not news."""
    at(_state("ship", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a, **_k: True)
    monkeypatch.setattr(worktree, "cleanup", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: False)
    monkeypatch.setattr(loop.merge, "foreign_dirt", lambda _r: ())

    result = _advance(tmp_path)

    assert result.detail == "worktree torn down and issue closed"


def test_the_claim_commit_also_warns_when_it_is_skipped(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unpublished claim lets two sessions start the same bead, so it warns too."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: False)
    monkeypatch.setattr(loop.merge, "foreign_dirt", lambda _r: (".gitignore",))

    result = _advance(tmp_path)

    assert "provisioned" in result.detail  # the original detail survives
    assert "tracker state NOT committed" in result.detail
    assert ".gitignore" in result.detail


def test_a_published_claim_adds_no_warning(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tracker_commits: list
) -> None:
    """The control: a committed claim leaves the provisioning detail untouched."""
    _ready_leaf(at, monkeypatch)
    _pin_runner(monkeypatch, "manual")

    result = _advance(tmp_path)

    assert tracker_commits == [("i", "record the claim before provisioning")]
    assert "NOT committed" not in result.detail


# --- A chronically unreliable gate escalates (basicly-jr0l.41) -----------------


def _unreliable_landing(monkeypatch: pytest.MonkeyPatch, events: int) -> list[tuple[str, str]]:
    """Drive a landing whose gate is unreliable, with the count already at *events*."""
    attempt = merge.MergeResult(
        "i", merge.VERIFY_UNRELIABLE, "verify full failed on pytest but passed unchanged on re-run"
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)
    monkeypatch.setattr(policy, "record_unreliable_gate", lambda *_a, **_k: events)
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _root, _issue, kind, question, *_a, **_k: enqueued.append((kind, question)),
    )
    return enqueued


def test_a_flaky_gate_below_the_bound_blocks_without_escalating(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One flake is no evidence against the work, so it must not reach a human yet."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    enqueued = _unreliable_landing(monkeypatch, events=1)

    result = _advance(tmp_path)

    assert result.blocked
    assert enqueued == []


def test_a_chronically_unreliable_gate_escalates_instead_of_deferring_forever(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The livelock: no budget is spent, so no cap is reached, so nothing escalated.

    Observed in the field — a br clock defect failed one arbitrary test per run,
    the loop correctly refused to charge rework, and the lane could never land
    because "forgiven" had no exit. The bound gives it one.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    enqueued = _unreliable_landing(monkeypatch, events=policy.MAX_UNRELIABLE_GATE_EVENTS)

    result = _advance(tmp_path)

    assert result.blocked
    assert len(enqueued) == 1
    kind, question = enqueued[0]
    assert kind == policy.REWORK_ESCALATION_KIND
    assert policy.gate_from_unreliable_escalation(question) == merge.MERGE_GATE
    assert "escalated" in result.detail
    # That it is never charged as rework is pinned at the policy level, where the
    # tracker is faked — asserting it here would drag a real br call into a unit test.


# --- A shared-tracker gate is not this lane's failure (basicly-qorx) -----------


def _foreign_landing(
    monkeypatch: pytest.MonkeyPatch, *, queue: tuple[decisions.DecisionItem, ...] = ()
) -> dict:
    """Drive a landing whose gate another lane's record invalidated.

    *queue* is what the bead's decision queue already holds, so the ask-once guard
    can be exercised without a real tracker.
    """
    seen: dict = {"charged": [], "attributed": [], "enqueued": []}
    attempt = merge.MergeResult(
        "i",
        merge.VERIFY_FOREIGN,
        "verify full failed on pytest — invalidated in the shared tracker by "
        "basicly-tcmy.5, not by this lane's diff",
        culprits=("basicly-tcmy.5",),
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: attempt)
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: seen["charged"].append(a) or 1)
    monkeypatch.setattr(
        policy,
        "record_shared_gate_failure",
        lambda *a, **_k: seen["attributed"].append(a) or 1,
    )
    monkeypatch.setattr(decisions, "items_on", lambda *_a, **_k: queue)
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _root, _issue, kind, question, *_a, **_k: seen["enqueued"].append((kind, question)),
    )
    return seen


def test_a_gate_another_lanes_record_failed_spends_no_rework_and_names_that_lane(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The measured defect: two siblings were charged 1/2 for a declaration in neither diff.

    Every lane in a supervised pass shares one `.beads` through the redirect, so the
    working-set ceiling asserted over basicly-tcmy.5's finishing record inside the
    landings of basicly-tcmy.6 and basicly-tcmy.22 as well.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _foreign_landing(monkeypatch)

    result = _advance(tmp_path)

    assert result.blocked
    assert seen["charged"] == []  # the whole point
    assert [(one[1], one[2], one[3]) for one in seen["attributed"]] == [
        ("i", merge.MERGE_GATE, ("basicly-tcmy.5",))
    ]


def test_it_escalates_on_the_first_occurrence_rather_than_after_a_bound(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A flake may clear itself on the next landing; a record in the tracker will not.

    So the bound an unreliable gate gets would only delay the escalation — every
    retry reaches the identical verdict (basicly-jr0l.16's reasoning about a
    deterministic refusal).
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _foreign_landing(monkeypatch)

    result = _advance(tmp_path)

    assert len(seen["enqueued"]) == 1
    kind, question = seen["enqueued"][0]
    assert kind == policy.REWORK_ESCALATION_KIND
    assert policy.gate_from_shared_gate_escalation(question) == merge.MERGE_GATE
    assert "basicly-tcmy.5" in question
    assert "escalated" in result.detail


def test_an_answered_shared_gate_escalation_is_not_asked_again(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ask once: an answered item re-opens under the next generation, which is a ladder.

    The remedies are the human's to carry out and neither is on this lane's side, so
    the answer cannot release the landing — the node holds on the answer it has
    (basicly-tcmy.6's ladder, not repeated).
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    answered = decisions.DecisionItem(
        decision_id="i#f1a5e",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.shared_gate_escalation_question(merge.MERGE_GATE, ("basicly-tcmy.5",)),
        detail="invalidated in the shared tracker by basicly-tcmy.5",
        answer="fixed tcmy.5's record",
        answered_by="human",
    )
    seen = _foreign_landing(monkeypatch, queue=(answered,))

    result = _advance(tmp_path)

    assert result.blocked
    assert seen["enqueued"] == []
    assert "already answered by human" in result.detail
    assert seen["charged"] == []


# --- ...and the escalation's `land anyway` is carried out (basicly-tcmy.6) ------
#
# The defect: answering only released the lane. The landing re-attempted, the same
# flaky gate tripped, the count passed the bound again, and the identical question
# re-opened under the next generation — an unbounded ladder of questions with the
# offered remedy unimplemented.


def _escalation_item(answer: str, *, by: str = "human") -> decisions.DecisionItem:
    """One answered unreliable-gate escalation, worded by the code that words it."""
    return decisions.DecisionItem(
        decision_id="i#f1a5e",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.unreliable_gate_escalation_question(merge.MERGE_GATE),
        detail="verify full failed on pytest but passed unchanged on re-run",
        answer=answer,
        answered_by=by,
    )


def _landing_after_answer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    queue: list[decisions.DecisionItem],
    spent: bool = False,
    landing: merge.MergeResult | None = None,
) -> dict:
    """Drive a landing with *queue* already on the bead and the flake at the bound.

    The merge stub reports whether the landing was asked to skip its gate, so the
    override is observed at the boundary it crosses rather than inferred from a
    message. *landing* overrides what the merge returns, for the cases where the
    override cannot have been used.
    """
    seen: dict = {"override_gate": None, "spent": [], "enqueued": []}
    unreliable = merge.MergeResult(
        "i", merge.VERIFY_UNRELIABLE, "verify full failed on pytest but passed unchanged on re-run"
    )
    merged = landing or merge.MergeResult("i", "merged", "merged harness/i into main @ abc1234")

    def _merge(_root, _name, *, bead, verify_mode, override_gate):  # noqa: ARG001
        seen["override_gate"] = override_gate
        return merged if override_gate else unreliable

    monkeypatch.setattr(merge, "merge_worktree", _merge)
    monkeypatch.setattr(decisions, "items_on", lambda *_a, **_k: tuple(queue))
    monkeypatch.setattr(policy, "gate_override_spent", lambda *_a, **_k: spent)
    monkeypatch.setattr(
        policy, "spend_gate_override", lambda _r, _i, gate: seen["spent"].append(gate) or True
    )
    monkeypatch.setattr(policy, "record_unreliable_gate", lambda *_a, **_k: 3)
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _root, _issue, kind, question, *_a, **_k: seen["enqueued"].append((kind, question)),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))
    return seen


def test_an_answered_land_anyway_lands_once_without_re_running_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The remedy the escalation offers, carried out — it used to do nothing at all."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(monkeypatch, queue=[_escalation_item("land anyway")])

    result = _advance(tmp_path)

    assert seen["override_gate"] is True
    assert seen["spent"] == [merge.MERGE_GATE]  # one-shot, spent at the landing
    assert seen["enqueued"] == []
    assert result.to_phase == "verify" and result.action == "merged"
    # A landing that skipped a gate says so; "merged @ abc1234" alone would read green.
    assert "skipped" in result.detail and merge.MERGE_GATE in result.detail


def test_the_answered_escalation_is_never_re_asked_under_a_new_generation(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ladder: `enqueue` re-opens an *answered* item, so re-asking was unbounded.

    `fix the flake` leaves the flake in place until a human fixes it, so the gate
    trips again on the very next landing. Asking again is the livelock, not the fix.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(monkeypatch, queue=[_escalation_item("fix the flake")])

    result = _advance(tmp_path)

    assert seen["override_gate"] is False  # the other choice authorises nothing
    assert seen["spent"] == []
    assert seen["enqueued"] == []
    assert result.blocked and "already answered" in result.detail
    assert "fix the flake" in result.detail


def test_a_spent_override_does_not_bypass_the_gate_again(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One answer, one landing: a standing `land anyway` must not skip the gate forever."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(monkeypatch, queue=[_escalation_item("land anyway")], spent=True)

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    assert seen["enqueued"] == []
    assert result.blocked and "no longer authorises a landing" in result.detail


def test_a_delegated_land_anyway_does_not_skip_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An autonomy grant may dispose of the question; it may not waive a landing gate."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(
        monkeypatch,
        queue=[_escalation_item("land anyway", by=f"{decisions.DECIDER_BY_PREFIX}opus")],
    )

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    assert seen["enqueued"] == []  # still no ladder — the answer disposed of the ask
    assert result.blocked


def test_an_override_is_not_spent_by_a_landing_that_never_reached_the_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`not-ready` is operator-fixable and pre-gate, so it must not burn the one shot."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    seen = _landing_after_answer(
        monkeypatch,
        queue=[_escalation_item("land anyway")],
        landing=merge.MergeResult("i", "not-ready", "no committed work on harness/i"),
    )

    result = _advance(tmp_path)

    assert seen["override_gate"] is True  # offered...
    assert seen["spent"] == []  # ...but the gate was never reached, so it survives
    assert result.blocked and "no committed work" in result.detail


def test_land_anyway_on_another_gate_does_not_waive_the_landing_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The override is authorised for the gate the answered question named, and no other.

    Only the landing gate is escalated this way today, so this pins the reading of the
    gate name rather than the assumption behind it.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    other = decisions.DecisionItem(
        decision_id="i#a11e",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.unreliable_gate_escalation_question("rubric"),
        answer="land anyway",
        answered_by="human",
    )
    seen = _landing_after_answer(monkeypatch, queue=[other])

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    assert result.blocked
    # An answered unreliable escalation is on the bead, so the ladder still ends here.
    assert seen["enqueued"] == []


def test_a_rework_escalation_on_the_queue_never_overrides_a_landing_gate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both escalations ride one decision kind, so only the question may tell them apart.

    A `retry` on the rework question must not be read as permission to skip a gate.
    """
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    rework = decisions.DecisionItem(
        decision_id="i#0dd1",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=policy.rework_escalation_question(merge.MERGE_GATE),
        answer="retry",
        answered_by="human",
    )
    seen = _landing_after_answer(monkeypatch, queue=[rework])

    result = _advance(tmp_path)

    assert seen["override_gate"] is False
    assert seen["spent"] == []
    # No unreliable-gate escalation has been answered, so this one is still asked once.
    assert [q for _, q in seen["enqueued"]] == [
        policy.unreliable_gate_escalation_question(merge.MERGE_GATE)
    ]
    assert result.blocked


# --- declared evidence artifacts (basicly-m4zv.13) --------------------------


def _evidence_config(**declarations: str) -> PolicyConfig:
    return PolicyConfig(required_gates=("verify",), max_rework=2, evidence=dict(declarations))


def _session_at(path: Path, name: str = "i") -> Session:
    """A worktree session whose checkout is a real directory a test can populate."""
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
    """Opt-in means the ordinary advance neither reads disk nor writes a marker."""
    at(_state("verify"))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    monkeypatch.setattr(
        policy, "record_evidence", lambda *_a: pytest.fail("nothing was declared to record")
    )
    assert _advance(tmp_path).to_phase == "ship"


def test_a_declared_artifact_refuses_the_advance_before_the_handler_runs(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Evidence is a precondition, so it is decided before the phase can spend anything."""
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
    """The satisfied path is recorded on the bead, so a closed issue names its evidence."""
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
    """Order pinned: a marker written after ship's tracker commit would never travel.

    ``_on_ship`` runs ``br close`` and then commits ``.beads/``. A comment added
    after that commit sits in the local db only, which is the failure the ship-time
    cost rollup is ordered around too.
    """
    at(_state("ship"))
    (tmp_path / "ship.log").write_text("shipped", encoding="utf-8")
    events: list[str] = []
    monkeypatch.setattr(
        policy, "record_evidence", lambda *_a: bool(events.append("evidence")) or True
    )
    monkeypatch.setattr(loop, "_run_br", lambda _r, args, **_k: events.append(args[0]))
    monkeypatch.setattr(
        loop.merge, "commit_tracker_state", lambda *_a, **_k: bool(events.append("commit")) or True
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(ship="ship.log"))
    assert result.to_phase == "done"
    assert events == ["evidence", "close", "commit"]


def test_a_misspelled_phase_refuses_the_advance_of_every_phase(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed at the loop boundary, not only inside the status helper."""
    at(_state("verify"))
    monkeypatch.setattr(policy, "checkpoint_approved", lambda *_a: True)
    config = PolicyConfig(required_gates=("verify",), max_rework=2, evidence={"verfiy": "run.log"})
    result = loop.advance(tmp_path, "i", config=config)
    assert result.blocked and "verfiy" in result.detail


def test_a_missing_build_artifact_refuses_before_the_merge(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The build check runs at the landing funnel, so the refusal merges nothing."""
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
    """The build's evidence is produced where the build ran, and is checked there.

    Checking base instead would be unsatisfiable by construction: the merge that
    would bring the artifact into base is the step this check gates.
    """
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


def test_a_declared_build_artifact_does_not_block_a_lanes_own_subtasks(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The deadlock guard: a lane's sub-task steps are steps *within* build.

    Checking evidence before ``_on_build`` would refuse the very dispatches that
    produce a build artifact, so the lane could never satisfy its own requirement.
    The check therefore sits at the build->verify funnel instead.
    """
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
    """Cannot locate the artifact is not the same as does not need one."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: None)
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: pytest.fail("the merge must not be attempted")
    )
    result = loop.advance(tmp_path, "i", config=_evidence_config(build="build.log"))
    assert result.blocked and "no session record" in result.detail


def test_every_loop_phase_has_a_handler_and_vice_versa() -> None:
    """``[policy.evidence]`` is validated against LOOP_PHASES, so it must not drift.

    A phase added to the handler table and not to LOOP_PHASES could never have an
    artifact declared for it; the reverse would refuse every advance as an unknown
    phase. Neither has a symptom that names its cause.
    """
    assert set(loop._HANDLERS) == set(LOOP_PHASES)


# --- declared scope, verified at the landing (basicly-jr0l.44) ---------------


def _scope_config(collision: str = "block") -> PolicyConfig:
    return PolicyConfig(required_gates=("verify",), max_rework=2, scope_collision=collision)


def _pin_scope(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scopes: dict[str, tuple[str, ...] | None],
    changed: tuple[str, ...],
    live: tuple[str, ...] = ("i",),
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Pin the landing's scope inputs; return the violations recorded on the bead.

    *scopes* maps bead id to its declared globs (``None`` for a bead that declared
    none), *changed* is what this lane's branch touched since its merge base, and
    *live* names the beads whose worktree still exists.
    """
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
    """Let the landing itself succeed, so only the scope check can hold it."""
    monkeypatch.setattr(
        merge, "merge_worktree", lambda *_a, **_k: merge.MergeResult("i", "merged", "landed")
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "report_gate", lambda *_a, **_k: (True, "ok"))


def test_an_out_of_scope_edit_into_a_live_lanes_ground_refuses_the_landing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The collision that later becomes a merge conflict is caught while it is free.

    Two lanes were declared parallel-safe on disjoint scopes; this one wrote into
    the other's. Refusing before the merge spends nothing, and the message says
    the plan is wrong rather than the merge.
    """
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
    """An agent's plan is sometimes legitimately incomplete, so this is advisory.

    Blocking every incomplete declaration would convert each one into a rework
    cycle; the finding still travels with the bead.
    """
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
    """Only a lane still holding a worktree can be written out from under."""
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
    """``warn`` trades the refusal for the conflict — but never for the evidence."""
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
    """A hand-filed leaf contradicts no plan, and must not pay a diff to prove it."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(monkeypatch, scopes={"i": None}, changed=("anything.py",))
    _pin_clean_landing(monkeypatch)
    result = loop.advance(tmp_path, "i", config=_scope_config())
    assert result.action == "merged"
    assert recorded == []


def test_a_lane_that_stayed_inside_its_scope_writes_nothing(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The common case costs one diff and no tracker write."""
    at(_state("build", worktree=WorktreeBinding("i", "harness/i")))
    recorded = _pin_scope(
        monkeypatch, scopes={"i": ("src/**",)}, changed=("src/a.py", "src/b.py", ".beads/x.jsonl")
    )
    _pin_clean_landing(monkeypatch)
    result = loop.advance(tmp_path, "i", config=_scope_config())
    assert result.action == "merged"
    assert recorded == []


# --- the forecast reaches the dispatch record (basicly-jr0l.34) --------------


def test_the_dispatch_records_its_forecast_beside_the_scope_it_measured(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dispatch carries the forecast onto the record its actual will land on.

    The join this bead exists for. `forecast_tokens` was a declared field with no
    writer, so the estimate and the outcome lived on disjoint records and the forecast
    error was never computable.
    """
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
    assert recorded["forecast_tokens"] == 21_000  # 3_000 overhead + 9_000 x 2.0
    assert recorded["task_class"] == "task"
    assert recorded["forecast_source"] == decompose.FROZEN_FORECAST


def test_the_interactive_dispatch_records_a_write_phase_and_a_seeded_factor(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: the interactive path's phase is in the one named write set, and says seeded.

    Two facts on one record, because they are recorded by one call. The phase is what
    ``decompose.unsized_lane_tokens`` reads to decide this dispatch is evidence of what
    a lane costs, and the build-factor source is what stops the forecast beside it from
    reading as a measurement (basicly-tcmy.5).
    """
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
    """A dying lane must leave its size behind (basicly-ipx2).

    Every failed dispatch in this repo's history carries `scope_tokens: None`, so any
    analysis that filters on the field being present excludes the whole failure
    population by construction — which is how "zero lanes have failed at any size"
    got committed beside `working_set_max`. The most informative sample about where
    a working-set limit lies is the lane that died, and it is precisely the one the
    telemetry must not drop.
    """
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
        # The signature of all four real failures: SIGTERM, no usable output.
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=143
        ),
    )
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: pytest.fail("no landing here"))

    loop._run_agent(loop._Ctx(tmp_path, "i", _state("build"), CONFIG, loop.Inputs()), "i", tmp_path)

    assert recorded["scope_tokens"] == 9_000
    assert recorded["forecast_tokens"] == 21_000  # 3_000 overhead + 9_000 x 2.0


def test_sizing_at_dispatch_is_empty_when_the_bead_declares_no_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No forecast rather than a fabricated one; the record simply carries neither half."""
    monkeypatch.setattr(decompose, "dispatch_sizing", lambda *_a: None)
    assert loop.sizing_at_dispatch(tmp_path, "i") == {}


def test_sizing_at_dispatch_never_raises_on_a_tracker_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Telemetry sits on the critical path of every dispatch and must never fail one."""

    def _boom(*_a):
        raise RuntimeError("br is unavailable")

    monkeypatch.setattr(decompose, "dispatch_sizing", _boom)
    assert loop.sizing_at_dispatch(tmp_path, "i") == {}
