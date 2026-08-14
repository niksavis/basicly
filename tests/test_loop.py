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
    br,
    classify,
    decisions,
    decompose,
    loop,
    loop_state,
    merge,
    needs_input,
    policy,
    roles,
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

    def _classify(_r, _i, wt, _s):
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

    # Serve `show` a body with no `## Plan` heading: the pre-gate population, which the
    # plan gate's ratchet admits. Stubbed because that gate fails closed on an unreadable
    # record, so leaving the read unstubbed refuses every granted dispatch below for a
    # reason none of these tests is about. At the spawn seam rather than at
    # `br.read_record`, so a test that pins its own richer stand-in afterwards still wins.
    def _show(_repo_root: Path, args: list[str], **_k) -> SimpleNamespace:
        payload = json.dumps([{"id": "i", "title": "i", "description": "prose\n"}])
        return SimpleNamespace(stdout=payload if args[:1] == ["show"] else "{}", returncode=0)

    monkeypatch.setattr(br, "try_run_br", _show)
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

    @staticmethod
    def _ok(stdout: str) -> SimpleNamespace:
        """A successful spawn, exit status included.

        The status is not optional: `br.read_record` checks it before it parses, so a
        stand-in without one raises AttributeError inside the seam rather than serving
        the record (basicly-tcmy.14).
        """
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
    fake = _CeilingBr()
    monkeypatch.setattr(supervise, "_run_br", fake)
    # Named `fake` rather than `br` so the module stays reachable here: the record read
    # goes through `br.read_record`, the one reader every consumer shares
    # (basicly-tcmy.14), and leaving it unstubbed makes the finalize path see no bead
    # and spin no follow-up.
    monkeypatch.setattr(br, "try_run_br", fake)
    return fake


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
    assert entry["command"][-4:] == [
        "--output-format",
        "stream-json",
        "--verbose",
        "--forward-subagent-text",
    ]


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


# --- delegated proposals (basicly-u6jq.2) ------------------------------------
#
# Both inputs the loop could not originate. The gate at each was never skipped —
# it was that nothing produced what the gate wanted, so a granted session still
# waited on a human to *request* the phase, and an undecomposed epic sat P0 and
# undispatchable for sessions. What these pin is the whole contract: the grant
# decides whether an agent is asked, the engine validates what comes back against
# the schema and the working-set band `basicly decompose` already enforces, and
# every refusal lands back on the block that was the only behaviour before.


def _proposer(monkeypatch: pytest.MonkeyPatch, stdout: str, **spec_kw) -> dict:
    """Pin a confinable headless runner replying *stdout*; return what it was handed.

    No ``usage_format``, so *stdout* is the reply verbatim (the store-measured arm);
    ``deny_style`` is what makes the fake confinable at all — without one the
    proposer refuses to dispatch it, which has its own test below.
    """
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
    """Let the grant delegate every proposal kind, and give the bead a requirement."""
    monkeypatch.setattr(
        policy, "proposal_delegated", lambda *_a, **_k: policy.ProposalGrant(True, level=level)
    )
    monkeypatch.setattr(decisions, "intake_corpus", lambda *_a: "Ship the parser.")


def test_intake_proposes_the_work_type_under_a_grant(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The loop originates the work type instead of waiting to be handed one."""
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
    """L0/L1 keeps today's human-supplied behaviour, and says a grant was consulted."""
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
    """A tracker that will not answer is not authority to act — and not a crash either.

    The proposer is optional machinery on top of a block that already existed, so
    an unreadable ledger has to fall back to that block. `br` raises here because
    nothing initialized a tracker under tmp_path, which is the real failure.
    """
    at(_state("intake"))
    _never_runs(monkeypatch)

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"
    assert "grant ledger could not be read" in result.detail


def test_an_invalid_proposed_work_type_never_reaches_the_tracker(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A proposal outside the fixed br set falls back rather than being recorded."""
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
    """Fail-closed: a reply that is not the contract proposed nothing."""
    at(_state("intake"))
    _delegated(monkeypatch)
    _proposer(monkeypatch, "I think this is probably an epic, but let me check.")

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "work_type"


def test_the_proposer_dispatch_is_confined_and_metered(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Confined like the decider, metered like every other dispatch.

    Unmetered is not merely untidy: an estimated record counts as an *unmeterable*
    dispatch, which zeroes the remaining budget and halts the whole grant
    (basicly-gczc). And an unconfined proposer holding a write tool can record its
    own answer around the contract it was given.
    """
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
    """Record the phase each dispatch resolves a persona for; return a marker role.

    Spied rather than projected: `tests/test_roles.py` already proves resolution and
    the argv flag end to end, so what is under test here is which phase each dispatch
    path asks about (basicly-4xmu).
    """
    phases: list[str] = []

    def _resolve(_repo_root, _spec, phase: str) -> str:
        phases.append(phase)
        return f"persona-for-{phase}"

    monkeypatch.setattr(loop.roles, "resolve_role", _resolve)
    return phases


def test_the_work_type_proposal_dispatches_as_the_classify_persona(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A work type is a classification judgment, so the decider answers it.

    Before this, `_run_proposer` passed no role at all, so the proposal ran on the
    default runner unspecialised — which is why 0 of 346 recorded dispatches had ever
    carried `--agent`.
    """
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
    """And a child plan is a decomposition, so the decomposer answers that one.

    The two proposals share one dispatch function, so a single role would have given
    both the same persona; the phase is passed per call site for exactly that reason.
    """
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
    """The fail-safe half, asserted against the real resolver rather than a spy.

    The fake spec declares no `agent_style`, so resolution answers None and the
    dispatch carries no flag — an un-upgraded consumer gets an unspecialised loop
    rather than a stopped one, and never a flag its host would silently drop.
    """
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
    """No confinement overlay, no dispatch — D3's drop-to-human stance, not a guess."""
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
    """A handoff runner has no argv and executes nothing, so it cannot originate an input."""
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
            # The plan gate's minimum (basicly-u2hl.1, basicly-u2hl.20): a proposal
            # missing any of them is refused before the loop reaches the governor, which
            # is what `test_a_plan_missing_a_gate_field_falls_back_to_the_block` pins.
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
    """The decompose input the loop could not originate — the stall this bead is about."""
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
    """The same schema `basicly decompose` enforces: a child with no scope is refused.

    A scope is what makes parallel-safety computable, so refusing to guess one is
    the whole point — and a proposer is exactly the caller that would guess.
    """
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
    """The plan gate refuses a proposal here too, and refusing must reach a human.

    The gate raises :class:`plan_gate.PlanGateError`, and this is what makes that a
    ``ValueError``: the loop's fall-back catches ``ValueError`` around the plan load,
    so a new exception type would propagate out of ``advance`` and take the run down
    instead of queueing the decision (basicly-u2hl.1).
    """
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
    """The governor that refused a hand-authored plan refuses a proposed one identically.

    Measured against the real estimator, not a stubbed verdict: the child declares
    a scope that exists and is tiny, so the floor is what refuses it — the same
    refusal a hand-authored plan for basicly-vkh0 took on 2026-08-06 at 4794
    tokens against a floor of 8000.
    """
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
    assert "below working_set_min 8000" in result.detail


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
    assert "stopped on runner_timeout after 1800s" in held.detail
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


# --- a role's declared skills reach its dispatch (basicly-ey58) --------------


def test_skill_canary_reaches_the_dispatched_prompt(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A token existing only inside a declared skill body arrives at `runner.run`.

    The end-to-end half of basicly-ey58: the unit tests prove the prompt is composed,
    this proves the composed prompt is the one actually dispatched. Those are different
    claims, and the defect being fixed was precisely that the declaration existed and
    never reached the spawn.
    """
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
    # The task survives the preamble; a brief that replaced the work would also pass
    # the canary assertion above.
    assert "br show i" in seen["prompt"]


# --- validate (basicly-u2hl.54.2) -------------------------------------------

_VGATE = "validate-as-consumer"


def _validate_gates(*, failed: bool = False, foreign: str | None = None) -> GateStatus:
    """A GateStatus for a unit that landed and owes the consumer gate."""
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
    """A validation that ran and failed is a defect to repair, so the loop is bounded."""
    at(_state("validate", gates=_validate_gates(failed=True)))
    charged: list[tuple] = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)

    result = _advance(tmp_path)

    assert result.blocked
    assert _VGATE in result.detail
    assert charged and charged[0][2] == _VGATE


def test_a_failed_validation_escalates_at_the_rework_cap(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """At max_rework the loop stops dispatching and asks a human instead."""
    at(_state("validate", gates=_validate_gates(failed=True)))
    monkeypatch.setattr(policy, "record_rework", lambda *_a, **_k: 2)  # the CONFIG cap
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        loop.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: queued.append((issue, kind)),
    )

    result = _advance(tmp_path)

    assert result.action == "escalated" and _VGATE in result.detail
    # An escalation is a judgment call: it enters the decision queue (kjc5.4).
    assert queued == [("i", "escalation")]


def test_a_missing_validation_spends_no_rework(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nobody has looked yet, so there is no finding to repair.

    The false-positive half of the pair above: spending an attempt here would burn
    the budget that exists for repairing findings on the absence of any, and the cap
    would then escalate a unit whose validation had never been run once.
    """
    at(_state("validate", gates=_validate_gates()))
    charged: list[tuple] = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)

    # repair_dispatch=False is the supervisor's shape: no agent is spawned from a
    # landing pass, so this exercises the refusal rather than the dispatch.
    result = loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs(), repair_dispatch=False)

    assert result.blocked and result.needs_input == "validation"
    assert charged == []


def test_validator_argv_carries_the_role_and_a_non_write_phase(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The demonstration basicly-u2hl.54.3 names: VALIDATE resolves its own persona.

    Recorded outside ``WRITE_PHASES`` in the same assertion, because the two are one
    fact about the dispatch: a read-only judge priced as a lane would put a helper's
    cost into the sample the spend calibration derives a lane's cost from.

    The validator is the **first** dispatch VALIDATE makes, not the only one: the lens
    reviews beside it are basicly-feje's, and every dispatch the state makes is priced
    the same read-only way, which is what the recorded phases assert.
    """
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


def test_validate_dispatches_one_reviewer_per_lens_each_carrying_its_own_lens(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """basicly-feje's demonstration: `reviewer` is reachable, once per declared lens.

    Before this, `ROLE_BY_PHASE` was phase-to-one-role and VALIDATE resolved `validator`
    alone, so an authored, projected, vendored agent had no route at all. Each review
    must carry *its own* lens — one dispatch told to think broadly is the thing §6.4
    refuses, because one axis then masks the other.
    """
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
    # Every dispatch this state makes is read-priced, reviews included.
    assert recorded == [run_record.VALIDATE_PHASE] * (1 + len(roles.REVIEW_LENSES))


def test_each_lens_records_its_own_findings_and_nothing_merges_them(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§6.4: lens output is reported per lens, never merged into one ranked list.

    A change can pass one axis and fail another, so a merged report lets the strong axis
    mask the weak one. Asserted on the recorded findings rather than on the prompt,
    because the prompt is an instruction and the record is the guarantee.
    """
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
    monkeypatch.setattr(loop.lens_review.br, "add_comment", lambda _r, _i, b: comments.append(b))

    loop.advance(tmp_path, "i", config=CONFIG, inputs=loop.Inputs())

    assert len(comments) == len(roles.REVIEW_LENSES)
    for body, lens in zip(comments, roles.REVIEW_LENSES, strict=True):
        assert body.startswith(f"{loop.lens_review.MARKER} lens={lens}\n")
        assert body.count(loop.lens_review.MARKER) == 1
        assert f"finding on {lens}" in body


def test_an_l1_or_l2_unit_never_reaches_the_phase_that_pays_for_a_review() -> None:
    """The cost bound: a lens dispatch is charged to L3 units and to nothing else.

    ``validate`` is derived only while ``validate-as-consumer`` is outstanding, and
    ``validate_gate.required_config`` promotes that gate only at a level whose selection
    carries it — so an L1 or L2 unit lands straight at ship and the fan-out below it is
    never consulted. Both halves are asserted: the phase it does derive, and that the
    phase buys no review.
    """
    landed = GateStatus(True, ("verify",), (), (), (), ())

    phase = loop_state.derive_phase("in_progress", ("ship",), None, landed, False)

    assert phase == "ship"
    assert roles.lens_dispatches(phase) == ()


def test_a_validate_dispatch_that_records_nothing_leaves_the_unit_in_validate(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-read the gate; having run is not evidence of a verdict."""
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
    """The gate is still missing, but an operator is told which result was ignored."""
    at(_state("validate", gates=_validate_gates(foreign="some-agent")))

    result = _advance(tmp_path)

    assert result.blocked and result.needs_input == "validation"
    assert "some-agent" in result.detail and "disregarded" in result.detail


def test_a_refused_validation_advance_has_no_side_effects(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tracker_commits: list
) -> None:
    """No merge, no teardown, no close, no tracker commit while the gate is outstanding."""
    at(_state("validate", gates=_validate_gates(), worktree=WorktreeBinding("n", "b")))
    calls: list[list[str]] = []
    monkeypatch.setattr(loop, "_run_br", lambda _r, args, **_k: calls.append(args))
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
        # `br.read_record` checks the exit status before it parses, so a stand-in for a
        # successful spawn has to carry one (basicly-tcmy.14).
        returncode = 0

    # `br.try_run_br`, not loop's alias: the record read goes through `br.read_record`,
    # the one reader every consumer in the package shares.
    monkeypatch.setattr(br, "try_run_br", lambda *_a, **_k: _Proc())
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
        lambda _r, _i, wt, _s: classify.ClassifyResult("i", wt, DoRResult(True, ())),
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


@pytest.mark.usefixtures("tracker_commits")
def test_ensure_lane_worktrees_provisions_lanes_the_root_never_parented(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit lane set is provisioned by the same primitive a fan-out root uses.

    ``br`` permits one parent, so a release cut is assembled from beads that already
    have an epic of origin and the pass selects them by label (basicly-1lpo). Nothing
    on the seeding path could provision such a lane: it reaches
    ``_ensure_child_worktrees`` only through the root's own decompose->build advance,
    which reads the ``parent-child`` edge these lanes by definition do not have.
    """
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
    """A lane the cap or the band skipped is not reported as provisioned.

    The seeding route depends on it: a pass that reports lanes it did not build routes
    ``seeded`` and then dispatches nothing, and one that under-reports throws away
    worktrees it just paid for (basicly-jr0l.57).
    """
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
