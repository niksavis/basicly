"""Tests for the ``basicly loop`` CLI wiring (onb.6.4).

The CLI is a thin driver over the loop state machine (onb.6.3) and the resumable
state model (onb.6.1): it maps the shared agent-input flags onto a
:class:`loop.Inputs`, prints the transition/state, and turns ``blocked`` into a
non-zero exit so scripts and CI can branch on it. These tests fake ``advance`` /
``run_ceremony`` / ``read_node_state`` and assert only that wiring.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from basicly import cli, decompose, loop, loop_state, supervise
from basicly.config import (
    CHECKPOINTS,
    LOCAL_CONFIG_FILE,
    RunnerSpec,
    WorktreeConfig,
    load_sizing_config,
)
from basicly.decisions import DecisionItem
from basicly.decompose import ChildSpec
from basicly.loop import AdvanceResult, Inputs
from basicly.loop_state import NodeState, RankedNode, WorktreeBinding
from basicly.policy import GateStatus, Grant, SpendStatus
from basicly.runner import HEADLESS


def _node_state(**overrides: object) -> NodeState:
    defaults: dict[str, object] = {
        "issue_id": "basicly-x",
        "status": "in_progress",
        "issue_type": "task",
        "phase": "build",
        "worktree": WorktreeBinding(name="basicly-x", branch="harness/basicly-x"),
        "gates": GateStatus(False, ("lint",), ("verify",), (), ()),
        "checkpoints": ("classify",),
        "rework": {"verify": 1},
        "agent_context": None,
        "has_children": False,
    }
    defaults.update(overrides)
    return NodeState(**defaults)  # type: ignore[arg-type]


# --- advance ----------------------------------------------------------------


def test_loop_advance_maps_flags_to_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared flags land on a loop.Inputs and reach loop.advance verbatim."""
    captured: dict[str, object] = {}

    def fake_advance(_repo_root, issue_id, *, _config=None, inputs=None):
        captured["issue_id"] = issue_id
        captured["inputs"] = inputs
        return AdvanceResult(issue_id, "intake", "classify", "classified", "recorded task")

    monkeypatch.setattr(loop, "advance", fake_advance)

    assert cli.main(["loop", "advance", "basicly-x", "--work-type", "task", "--mode", "fast"]) == 0
    assert captured["issue_id"] == "basicly-x"
    inputs = captured["inputs"]
    assert isinstance(inputs, Inputs)
    assert inputs.work_type == "task"
    assert inputs.verify_mode == "fast"
    assert inputs.children is None


def test_loop_advance_loads_child_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """--children is resolved through decompose.load_plan_file into Inputs.children."""
    plan = (ChildSpec(title="a", acceptance=("x",), scope=("src/a.py",)),)
    captured: dict[str, object] = {}

    monkeypatch.setattr(decompose, "load_plan_file", lambda _path: plan)

    def fake_advance(_repo_root, issue_id, *, _config=None, inputs=None):
        captured["inputs"] = inputs
        return AdvanceResult(issue_id, "classify", "decompose", "decomposed")

    monkeypatch.setattr(loop, "advance", fake_advance)

    assert cli.main(["loop", "advance", "basicly-x", "--children", "plan.toml"]) == 0
    assert captured["inputs"].children == plan  # type: ignore[union-attr]


def test_loop_advance_exits_nonzero_when_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A blocked step exits 1 and surfaces the needed input so CI can branch."""
    blocked = AdvanceResult(
        "basicly-x", "intake", "intake", "blocked", "needs a work type", needs_input="work_type"
    )
    monkeypatch.setattr(loop, "advance", lambda *_a, **_k: blocked)

    assert cli.main(["loop", "advance", "basicly-x"]) == 1
    out = capsys.readouterr().out
    assert "[blocked]" in out
    assert "needs input: work_type" in out


# --- run --------------------------------------------------------------------


def _ceremony(monkeypatch: pytest.MonkeyPatch, result: loop.CeremonyResult) -> dict[str, object]:
    """Make ``loop.run_ceremony`` return *result*, capturing the kwargs it got."""
    seen: dict[str, object] = {}

    def fake_ceremony(_repo: object, issue: str, **kwargs: object) -> loop.CeremonyResult:
        seen.update(kwargs, issue=issue)
        return result

    monkeypatch.setattr(loop, "run_ceremony", fake_ceremony)
    return seen


def test_loop_run_prints_each_step_and_the_approvals_between_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run renders the boundary in order: steps, with each approval where it happened."""
    _ceremony(
        monkeypatch,
        loop.CeremonyResult((
            AdvanceResult("basicly-x", "intake", "intake", "blocked", checkpoint="classify"),
            loop.CheckpointApproval("classify", "delegated under L2 grant"),
            AdvanceResult("basicly-x", "classify", "classify", "blocked", "awaiting the agent"),
        )),
    )

    assert cli.main(["loop", "run", "basicly-x"]) == 1
    out = capsys.readouterr().out
    assert "checkpoint classify: APPROVED (basicly-x) - delegated under L2 grant" in out
    assert out.index("intake -> intake") < out.index("checkpoint classify: APPROVED")
    assert "[blocked]" in out


def test_loop_run_exits_zero_when_the_track_shipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A boundary that reached done exits 0."""
    _ceremony(
        monkeypatch,
        loop.CeremonyResult((AdvanceResult("basicly-x", "ship", "done", "tore-down"),)),
    )

    assert cli.main(["loop", "run", "basicly-x"]) == 0


def test_loop_run_challenge_reprints_the_whole_command_to_rerun(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The relayed code has to come back on the *same* command, flags and all.

    Printing a bare ``policy checkpoint --approve`` line would approve the
    checkpoint and leave the loop parked, which is the ceremony this command
    exists to collapse (basicly-kjc5.41).
    """
    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "intake", "intake", "blocked", checkpoint="classify"),),
            challenge=("classify", "c0ffee"),
        ),
    )

    exit_code = cli.main(["loop", "run", "basicly-x", "--work-type", "task", "--mode", "fast"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "checkpoint classify: CONFIRMATION REQUIRED (basicly-x)" in err
    assert "basicly loop run basicly-x --work-type task --mode fast --confirm c0ffee" in err
    # The ceremony shares the challenge wording with policy checkpoint/grant
    # (basicly-kjc5.34): the caller may run it once a human approves.
    assert "may run the command themselves" in err


def test_loop_run_prints_why_a_grant_declined_the_challenge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason reaches the operator on the surface the incident was measured on.

    ``loop run`` is what the driver ran (basicly-5ltn); a challenge that carries a
    reason and prints it nowhere would leave the diagnostic in the engine only.
    """
    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "verify", "verify", "blocked", checkpoint="ship"),),
            challenge=("ship", "c0ffee"),
            challenge_reason=(
                "the active L3 grant covers ship but declined it: "
                "rework escalation on basicly-sib (gate verify: 2/2)"
            ),
        ),
    )

    assert cli.main(["loop", "run", "basicly-x", "--root", "basicly-epic"]) == 1

    err = capsys.readouterr().err
    assert "the active L3 grant covers ship but declined it" in err
    assert "rework escalation on basicly-sib (gate verify: 2/2)" in err
    assert "--confirm c0ffee" in err


def test_loop_run_challenge_without_a_grant_prints_no_reason_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No grant, no new line: the ungranted challenge is byte-for-byte as it was."""
    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "verify", "verify", "blocked", checkpoint="ship"),),
            challenge=("ship", "c0ffee"),
        ),
    )

    assert cli.main(["loop", "run", "basicly-x"]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == "checkpoint ship: CONFIRMATION REQUIRED (basicly-x)"
    assert lines[1].startswith("  The merge to the base branch has ALREADY happened")


def test_loop_run_reports_a_refusal_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused checkpoint exits non-zero and says why."""
    _ceremony(
        monkeypatch,
        loop.CeremonyResult(
            (AdvanceResult("basicly-x", "verify", "verify", "blocked", checkpoint="ship"),),
            refused=("ship", "invalid or expired confirm code"),
        ),
    )

    assert cli.main(["loop", "run", "basicly-x", "--confirm", "nope"]) == 1
    assert "checkpoint ship: REFUSED (basicly-x) - invalid or expired" in capsys.readouterr().err


def test_loop_run_passes_the_confirm_code_and_grant_root_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--confirm name=CODE`` targets one checkpoint; ``--root`` scopes the grant."""
    seen = _ceremony(monkeypatch, loop.CeremonyResult())

    cli.main(["loop", "run", "basicly-x", "--confirm", "ship=c0ffee", "--root", "basicly-epic"])
    assert seen["confirms"] == {"ship": "c0ffee"}
    assert seen["grant_root"] == "basicly-epic"


def test_loop_run_bare_confirm_code_is_offered_to_every_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare code needs no checkpoint name — codes only validate where they were minted."""
    seen = _ceremony(monkeypatch, loop.CeremonyResult())

    cli.main(["loop", "run", "basicly-x", "--confirm", "c0ffee"])
    assert seen["confirms"] == dict.fromkeys(CHECKPOINTS, "c0ffee")


# --- status -----------------------------------------------------------------


def test_loop_status_prints_reconstructed_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Status renders the phase, worktree, gates, checkpoints, rework, and ready/blocked."""
    monkeypatch.setattr(loop_state, "read_node_state", lambda *_a, **_k: _node_state())
    monkeypatch.setattr(
        loop_state,
        "ready_ranked",
        lambda *_a, **_k: (RankedNode(rank=1, score=50, issue_id="basicly-y", title="t"),),
    )
    monkeypatch.setattr(loop_state, "blocked_ids", lambda *_a, **_k: ("basicly-z",))

    assert cli.main(["loop", "status", "basicly-x"]) == 0
    out = capsys.readouterr().out
    assert "phase:       build" in out
    assert "basicly-x on harness/basicly-x" in out
    assert "advance BLOCKED" in out
    assert "failed:    verify" in out
    assert "checkpoints: classify" in out
    assert "verify=1" in out
    assert "basicly-y" in out
    assert "basicly-z" in out


# --- session (client attach, basicly-kjc5.8) ---------------------------------


def _observation(**overrides: object) -> supervise.Observation:
    defaults: dict[str, object] = {
        "root_issue": "basicly-epic",
        "root_status": "open",
        "children_total": 3,
        "children_open": 2,
        "done": False,
        "lanes": (
            supervise.LaneView(
                issue_id="basicly-epic.1",
                status="in_progress",
                worktree="basicly-epic-1",
                branch="harness/basicly-epic-1",
                live=True,
                last_agent="claude",
                last_outcome="executed",
                last_run_at="2026-07-25T10:00:00+00:00",
                last_tokens=1200,
            ),
        ),
        "pending_decisions": (
            DecisionItem(
                decision_id="basicly-epic.1#abc123",
                issue_id="basicly-epic.1",
                kind="validate",
                question="ship without the migration?",
            ),
        ),
        "holder": supervise.LockInfo(
            pid=4242, session_id="basicly-epic:live", root_issue="basicly-epic", age_s=3.0
        ),
        "holder_stale": False,
        "holder_on_this_root": True,
        "grant_level": "L2",
        "token_budget": 5000,
        "spent_tokens": 1200,
        "human_wait_s": 5_400,
        "delegated_wait_s": 45,
        "dispatch_s": 92.5,
    }
    defaults.update(overrides)
    return supervise.Observation(**defaults)  # type: ignore[arg-type]


def test_loop_session_prints_the_attach_surface(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Attaching renders the holder, each lane's last run, the queue, and grant spend."""
    monkeypatch.setattr(supervise, "observe", lambda *_a, **_k: _observation())

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    out = capsys.readouterr().out
    assert "root:       basicly-epic (open)" in out
    assert "basicly-epic:live (pid 4242) - heartbeat 3s old" in out
    assert "children:   3 total, 2 open" in out
    assert "basicly-epic.1 (in_progress) -> basicly-epic-1 on harness/basicly-epic-1 [live]" in out
    assert "last run: claude executed at 2026-07-25T10:00:00+00:00, 1200 tokens" in out
    assert "decisions:  1 pending" in out
    assert "ship without the migration?" in out
    assert "grant:      L2, 1200/5000 tokens spent" in out


def test_loop_session_reports_human_wait_apart_from_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wall clock is dominated by waiting on a human, so the rollup says so (kjc5.51).

    Reported beside dispatch and never folded into it: one is the compute the
    session bought, the other is the bottleneck a delivery forecast has to predict.
    """
    monkeypatch.setattr(supervise, "observe", lambda *_a, **_k: _observation())

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    assert "wait:       1.5h human, 45s delegated (dispatch 2m)" in capsys.readouterr().out


def test_loop_session_names_an_unsupervised_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No supervisor is a reportable state and still exits 0 — the read succeeded."""
    monkeypatch.setattr(
        supervise,
        "observe",
        lambda *_a, **_k: _observation(
            holder=None,
            holder_on_this_root=False,
            lanes=(),
            pending_decisions=(),
            grant_level=None,
            token_budget=None,
            spent_tokens=0,
        ),
    )

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    out = capsys.readouterr().out
    assert "supervisor: (none running" in out
    assert "lane:       (no in-flight lanes)" in out
    assert "decisions:  none pending" in out
    assert "grant:      (none) - 0 tokens spent this session" in out


def test_loop_session_warns_that_a_stale_holder_may_be_taken_over(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A crashed holder must read as crashed, or a client waits on a dead session."""
    monkeypatch.setattr(
        supervise,
        "observe",
        lambda *_a, **_k: _observation(
            holder=supervise.LockInfo(
                pid=7, session_id="basicly-epic:crashed", root_issue="basicly-epic", age_s=312.0
            ),
            holder_stale=True,
        ),
    )

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    assert "heartbeat 312s old - STALE" in capsys.readouterr().out


def test_loop_session_names_a_holder_on_another_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lock is a repo singleton, so say whose session the holder actually runs."""
    monkeypatch.setattr(
        supervise,
        "observe",
        lambda *_a, **_k: _observation(
            holder=supervise.LockInfo(
                pid=9, session_id="other:live", root_issue="basicly-other", age_s=2.0
            ),
            holder_on_this_root=False,
        ),
    )

    assert cli.main(["loop", "session", "basicly-epic"]) == 0
    out = capsys.readouterr().out
    assert "supervising basicly-other, not this session; heartbeat 2s old" in out


def test_loop_session_json_emits_the_whole_observation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json is the client's machine surface: every field, nested lanes and queue."""
    monkeypatch.setattr(supervise, "observe", lambda *_a, **_k: _observation())

    assert cli.main(["loop", "session", "basicly-epic", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["root_issue"] == "basicly-epic"
    assert payload["holder"]["session_id"] == "basicly-epic:live"
    assert payload["lanes"][0]["branch"] == "harness/basicly-epic-1"
    assert payload["lanes"][0]["last_tokens"] == 1200
    assert payload["pending_decisions"][0]["kind"] == "validate"
    assert (payload["grant_level"], payload["token_budget"], payload["spent_tokens"]) == (
        "L2",
        5000,
        1200,
    )
    assert (payload["human_wait_s"], payload["delegated_wait_s"], payload["dispatch_s"]) == (
        5_400,
        45,
        92.5,
    )
    # A derived property asdict would drop, and the one flag a machine client acts on.
    assert payload["supervised"] is True


# --- Preflight: the run checklist as a command, not a recollection (basicly-p8ck) ---


@dataclass(frozen=True)
class _Preflight:
    """The probes a preflight makes, pinned so the verdict is the only variable."""

    dirty: str = ""
    grant: Grant | None = None
    halted: bool = False
    metered: str | None = None
    lanes: tuple[object, ...] = ()
    # The root's (issue_id, status) parent-child dependents, as derive_session reads
    # them. Varied so a test can pin a status the candidate rule has to decide on.
    children: tuple[tuple[str, str], ...] = (("c.1", "open"),)
    # issue_id -> the band verdict to pin for it; anything absent sizes to nothing.
    admissions: dict[str, object] = field(default_factory=dict)
    # The root's own derived phase and approved checkpoints, as `loop status` reads
    # them. The default is a decomposed epic with every checkpoint already approved,
    # so a test that says nothing about checkpoints keeps its old verdict.
    phase: str = "decompose"
    checkpoints: tuple[str, ...] = CHECKPOINTS
    # The concurrency cap the fan-out forecast is bounded by. Pinned rather than read
    # off this repo's own basicly.toml, so a retune of the cap cannot turn a forecast
    # assertion into an unrelated failure.
    cap: int = 5
    # The calibration report to pin. Pinned like the band verdicts and for the same
    # reason: computing it walks the tracker export and the local record file, so left
    # live every preflight test would parse this repo's real 4MB of markers.
    calibration: object | None = None
    # The configured append-only paths and each candidate's declared scope, for the
    # contention warning (basicly-o8p0). Both pinned rather than read: left live the
    # paths come off this repo's own basicly.toml — so declaring one there would start
    # deciding unrelated assertions — and the scopes are a real `br show` per lane.
    append_only: tuple[str, ...] = ()
    scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # The configured generated artifacts and their rebuild, for the `regen:` line
    # (basicly-lyro). Pinned for the same reason `append_only` is.
    generated: tuple[str, ...] = ()
    regenerate: tuple[str, ...] = ()


def _calibration(**overrides) -> decompose.CalibrationStatus:
    """A calibration report: nothing measured, which is this repo's real state."""
    fields = {
        "model": "claude-opus-5",
        "min_samples": 10,
        "samples": {"bug": 7, "task": 2},
        "build_factor_sources": {
            "bug": decompose.BUILD_FACTOR_SEED,
            "task": decompose.BUILD_FACTOR_SEED,
        },
    }
    fields.update(overrides)
    return decompose.CalibrationStatus(**fields)


def _preflight_fixture(monkeypatch: pytest.MonkeyPatch, pinned: _Preflight) -> None:
    """Pin every probe `_cmd_loop_preflight` makes against the repo and the tracker."""
    dirty, grant, halted, metered, lanes = (
        pinned.dirty,
        pinned.grant,
        pinned.halted,
        pinned.metered,
        pinned.lanes,
    )
    # Bound rather than constructed in the lambda: `Path(".")` autofixes to `Path()`,
    # which then reads as a redundant lambda, and the two rules chase each other.
    root = Path()
    monkeypatch.setattr(cli, "_repo_root", lambda: root)
    monkeypatch.setattr(
        cli.worktree,
        "git",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, 0, dirty if "status" in argv else "0", ""
        ),
    )
    monkeypatch.setattr(cli.worktree, "list_sessions", lambda _r: [])
    children = pinned.children
    monkeypatch.setattr(
        cli.supervise,
        "derive_session",
        lambda _r, root: supervise.SessionState(root, "open", children, ()),
    )
    monkeypatch.setattr(
        cli.runner, "select_runner", lambda *_a, **_k: RunnerSpec("claude", HEADLESS)
    )
    monkeypatch.setattr(
        cli.policy,
        "spend_status",
        lambda *_a, **_k: SpendStatus(grant=grant, spent_tokens=0, halted=halted),
    )
    monkeypatch.setattr(cli.supervise, "metered_without_a_budget", lambda *_a: metered)
    monkeypatch.setattr(cli.supervise, "ready_lanes", lambda *_a, **_k: lanes)
    cap, generated, regenerate = pinned.cap, pinned.generated, pinned.regenerate
    monkeypatch.setattr(
        cli,
        "load_worktree_config",
        lambda *_a: WorktreeConfig(
            base_branch=None,
            concurrency=cap,
            generated_paths=generated,
            regenerate_command=regenerate,
        ),
    )
    monkeypatch.setattr(cli.decompose, "unsized_lane_tokens", lambda *_a: (1_000, "measured"))
    calibration = pinned.calibration or _calibration()
    monkeypatch.setattr(cli.decompose, "calibration_status", lambda *_a: calibration)
    # Preflight now sizes each candidate for the band table, which is a real `br show`
    # per child unless it is pinned here. Left live, the suite would spawn br for every
    # preflight test — the trap basicly-jr0l's notes call out for any new tracker read
    # on a dispatch path. Pinned per-issue so a test can vary one candidate's verdict.
    monkeypatch.setattr(
        cli.supervise,
        "admit_working_set",
        lambda _r, issue_id, _s: pinned.admissions.get(
            issue_id, supervise.WorkingSetAdmission(issue_id, None, None, refused=False)
        ),
    )
    monkeypatch.setattr(cli.decompose, "append_only_paths", lambda *_a: pinned.append_only)
    scopes = pinned.scopes
    monkeypatch.setattr(
        supervise.merge,
        "declared_scopes",
        lambda _r, beads: {b: scopes[b] for b in beads if b in scopes},
    )
    # Preflight also reads the root's own checkpoint state, the same reconstruction
    # `loop status` prints. Pinned for the same reason as the band verdicts: left live
    # it is a real `br show` plus a marker scan per preflight test.
    monkeypatch.setattr(
        cli.loop_state,
        "read_node_state",
        lambda _r, issue_id, *_a: _node_state(
            issue_id=issue_id,
            issue_type="epic",
            phase=pinned.phase,
            worktree=None,
            checkpoints=pinned.checkpoints,
            has_children=bool(children),
        ),
    )


def test_preflight_is_ready_when_nothing_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The happy path has to exit 0, or a wrapper gating on it can never proceed."""
    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT:   ready" in out


def test_preflight_refuses_an_unrecognised_config_name_before_anything_else(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A bad overlay is a verdict, not a traceback (basicly-1piy).

    Every loader below the first line raises on an unrecognised name, so without
    the early return the checklist would die of an exception partway down and
    answer none of its remaining questions — and preflight exists to answer them.
    Nothing else is pinned here on purpose: reaching a second probe at all would
    mean the refusal came too late.
    """
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    (tmp_path / LOCAL_CONFIG_FILE).write_text("[loop]\nconcurrency = 2\n", encoding="utf-8")

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 1
    assert "config:    INVALID" in out
    assert "'concurrency' is accepted in [worktree]" in out
    assert "VERDICT:   not ready" in out


def _sized(
    issue_id: str,
    total: int,
    *,
    refused: bool,
    violation: str = "",
    scope_tokens: int | None = None,
) -> object:
    """A pinned band verdict carrying a real estimate, for the preflight table."""
    estimate = decompose.CostEstimate(
        scope_tokens=total if scope_tokens is None else scope_tokens,
        overhead_tokens=0 if scope_tokens is None else total,
        build_factor=1.0,
    )
    sizing = decompose.DispatchSizing(task_class="task", estimate=estimate, source="dispatch")
    return supervise.WorkingSetAdmission(issue_id, sizing, violation or None, refused=refused)


def test_preflight_sizes_each_candidate_when_none_is_dispatchable_yet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The aggregate forecast describes no lane when the candidates declare no scope.

    With a halted grant there are no ready lanes, so preflight used to print only
    `forecast: ~N tokens if all 5 lanes start` — the unsizeable-lane assumption times
    the cap, which reads exactly like a measurement. The operator's real question is
    which candidates the band would take, and that has to be answerable *before* a
    budget is minted (basicly-prnm).
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 95_379, refused=True, violation="above")},
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    # Read the band off the config rather than pinning 8000..64000 here: the numbers are
    # deliberately tunable (basicly-3ifz), and a test that hardcodes them turns a
    # calibration change into an unrelated failure.
    band = load_sizing_config(Path())
    assert f"band:      {band.working_set_min}..{band.working_set_max}" in out
    assert "c.1" in out and "95379 tok" in out
    assert "REFUSED - too large, split it" in out


def test_preflight_distinguishes_an_admitted_candidate_from_a_refused_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: a lane inside the band must not read as one the band would refuse.

    Without this the report could mark everything REFUSED and every other assertion
    here would still pass — a table that says the same thing about every row tells the
    operator nothing about which lanes to start.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 12_884, refused=False)},
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "12884 tok  in band" in out
    assert "REFUSED" not in out


def test_preflight_separates_an_under_floor_lane_from_one_inside_the_band(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only the ceiling refuses, so an under-floor lane still dispatches — say both.

    ``admit_working_set`` sets ``refused`` on the ceiling alone, so a lane below
    ``working_set_min`` carries a violation and dispatches anyway. Reading ``refused``
    as the whole verdict printed "in band" for exactly the lanes the band had advised
    merging, which is the opposite of what it said.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 3_512, refused=False, violation="below")},
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "under the floor - dispatches, but merge it with a sibling" in out
    assert "3512 tok  in band" not in out


def test_preflight_leaves_a_deferred_candidate_out_of_the_band_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deferring a child must remove it from the funding decision (basicly-toj6).

    Measured on this repo's own tracker: ``basicly-vkh0.4`` had been deferred since
    2026-07-30, and preflight still reported "2 open child(ren)", sized it into the
    band table, and counted it in the fan-out forecast the operator mints a budget
    against — so a parked bead could escalate a pass its ready siblings could
    afford. ``c.1`` is the control: an open sibling is still sized and still counted.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "deferred")),
            admissions={
                "c.1": _sized("c.1", 12_884, refused=False),
                "c.2": _sized("c.2", 95_379, refused=True, violation="above"),
            },
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "1 open child(ren)" in out
    assert "12884 tok  in band" in out
    assert "c.2" not in out
    assert "REFUSED" not in out


def test_preflight_flags_a_candidate_whose_scope_matched_no_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bare overhead must not read as a comfortably small lane.

    The band's floor is skipped when a scope matches nothing, so a broken glob and a
    greenfield package both estimate to overhead alone and both would otherwise print
    a plain "in band". Measured on the real tracker, four candidates sat at exactly the
    overhead figure — the surface said they were ready to dispatch.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            admissions={"c.1": _sized("c.1", 2_693, refused=False, scope_tokens=0)},
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    assert "in band, but its scope matched no file" in capsys.readouterr().out


def test_preflight_names_a_candidate_the_estimator_cannot_size(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unsized candidate is named, not folded into a count.

    It is the cheapest thing an operator can fix and the largest lever on what a pass
    costs, so a bare tally would hide the one actionable item.
    """
    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "c.1" in out
    assert "no scope the estimator can read" in out


def test_preflight_warns_that_a_pass_will_contend_on_an_undeclared_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one collision knowable before any lane starts, named before one starts.

    The reported pass got `VERDICT: ready` for three lanes whose scopes were disjoint
    and whose `CHANGELOG.md` entries were not, and paid for it with a rework budget in
    the merge queue. Advisory on purpose — the remedy is a build order, and refusing
    the pass would answer a predictable conflict by stopping the factory.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open"), ("c.3", "open")),
            append_only=("CHANGELOG.md",),
            scopes={
                "c.1": ("src/basicly/schema.py",),
                "c.2": ("src/basicly/config.py",),
                "c.3": ("src/basicly/usage.py",),
            },
        ),
    )

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "contend:   append-only: `CHANGELOG.md`" in out
    assert "3 lane(s) will each append to `CHANGELOG.md` and none declares it: c.1, c.2, c.3" in out
    assert code == 0
    assert "VERDICT:   ready" in out


def test_preflight_says_the_contention_check_is_inert_when_nothing_is_declared(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silence would read as "checked, nothing found" on the repo's default config."""
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open")),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    assert "contend:   no append-only path declared" in capsys.readouterr().out


def test_preflight_reports_the_artifacts_a_landing_rebuilds_instead_of_bouncing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the same collision: these paths need no build order (basicly-lyro).

    Beside `contend:` on purpose. An operator who read only that line would conclude a
    shared artifact must serialise the pass, when the merge queue rebuilds this one and
    spends no rework — and today the only place to learn that is the merge queue.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open")),
            generated=(".basicly/generated-manifest.json",),
            regenerate=("basicly", "build"),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "regen:     generated: `.basicly/generated-manifest.json`" in out
    assert "rebuilt with `basicly build`" in out and "spending no rework" in out


def test_preflight_says_the_rebuild_check_is_inert_when_nothing_is_declared(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silence reads as "checked, nothing found", and the undeclared state is the costly one."""
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            children=(("c.1", "open"), ("c.2", "open")),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    assert "regen:     no generated path declared" in capsys.readouterr().out


def test_preflight_refuses_a_dirty_base_before_any_lane_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dirty base refuses the landing *after* the lanes have already cost money."""
    _preflight_fixture(
        monkeypatch,
        _Preflight(dirty="M a.py\nM b.py\n", grant=Grant(level="L1", token_budget=10_000)),
    )

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 1
    assert "base:      DIRTY - 2 path(s)" in out
    assert "base checkout is dirty" in out


def test_preflight_refuses_a_metered_runner_with_no_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The `kkux` hazard, surfaced before the run instead of during it."""
    _preflight_fixture(monkeypatch, _Preflight(metered="claude"))

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 1
    assert "budget:    MISSING" in out


def test_preflight_forecasts_a_full_fan_out_when_no_lane_is_dispatchable_yet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The number a budget must be minted for, before it is minted.

    Sizing a grant is exactly where the old plan went wrong by 2.4x, so the forecast
    has to be available *cold* — with nothing dispatchable yet.
    """
    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "forecast:" in out
    assert "if all" in out and "lanes start" in out


def test_preflight_says_the_forecast_is_still_on_seeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC: the per-class sample counts, and the verdict they add up to (basicly-tcmy.5).

    The forecast above is minted into a budget, and every number behind it is declared:
    the spend ratios stand on a prior until a class has ``calibration_min_samples``
    paired write dispatches, and nothing measures a build factor at all. Both facts were
    recorded and neither was reported, so "is this measured yet?" was answered by reading
    source — a recollection, which is what preflight exists to replace (basicly-p8ck).
    """
    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "spend cal: SEEDS" in out
    assert "claude-opus-5" in out
    assert "bug 7/10" in out and "task 2/10" in out
    assert "factors:   all seeds (never measured)" in out


def test_preflight_names_the_class_whose_spend_stopped_being_a_seed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: a measured class must not read the same as one still on the prior.

    Without it the line could say SEEDS forever and the assertion above would still
    pass — the failure mode of every provenance report.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            calibration=_calibration(samples={"bug": 12, "task": 2}),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "spend cal: measured for bug" in out
    assert "SEEDS" not in out


def test_preflight_says_when_a_build_factor_was_configured_rather_than_seeded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repo that declared its own factor is not reported as running on the seeds."""
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            calibration=_calibration(
                build_factor_sources={
                    "bug": decompose.BUILD_FACTOR_CONFIGURED,
                    "task": decompose.BUILD_FACTOR_SEED,
                }
            ),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "factors:   some configured (never measured)" in out


def test_preflight_says_an_unresolved_model_can_key_no_sample_at_all(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A null model is not zero samples for the model in use — the key does not exist.

    The ratios are keyed per (model, class), so with no model resolved nothing can key
    in whatever history exists. This repo's own preflight is in that state, and rendering
    it as "on None" would read as a model nobody named.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L1", token_budget=10_000),
            calibration=_calibration(model=None, samples={"task": 0}),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "spend cal: SEEDS - no model pinned, so no sample can key in" in out
    assert "task 0/10" in out


# --- Preflight: a verdict of "ready" has to mean a lane can start (basicly-cdhq) ---


def test_preflight_refuses_a_root_whose_decompose_checkpoint_is_unapproved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC: an epic with open children behind an unapproved decompose is not ready.

    Measured 2026-08-04: preflight printed `VERDICT: ready` on a clean base with a live
    L3 grant, and the supervise pass it green-lit dispatched nothing —
    `seed-blocked ... decompose checkpoint awaiting human approval`. The checkpoint
    state was the one precondition preflight never looked at, and it is the one that
    cost the pass.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            phase="decompose",
            checkpoints=("classify",),
        ),
    )

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 1
    assert "checkpts:  decompose UNAPPROVED" in out
    # The exact command, not a description of one: preflight exists so the remedy does
    # not have to be recalled (basicly-p8ck).
    assert "basicly policy checkpoint epic decompose --approve" in out
    assert "VERDICT:   not ready" in out


def test_preflight_separates_a_grant_delegated_checkpoint_from_one_it_cannot_serve(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC: the same L3 grant delegates both, and only one of them still needs a human.

    Reporting every unapproved checkpoint as a blocker would make the verdict noise.
    ``ship`` is genuinely delegated — supervise puts a landed lane's ship approval
    through ``approve_checkpoint_guarded``. The root's own ``decompose`` is not: seeding
    drives the root with ``loop.run_until_blocked``, which stops dead at a checkpoint and
    never consults a grant at all, so an operator reading "I hold L3" reads it wrong.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            phase="decompose",
            checkpoints=("classify",),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert "so the live L3 grant cannot serve it" in out
    assert "checkpts:  ship pending - the live L3 grant delegates it" in out


def test_preflight_is_ready_when_the_blocking_checkpoint_is_approved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: approving decompose has to clear the refusal, not just soften it.

    Without this the checkpoint report could refuse every pass and the assertion above
    would still pass — a gate that never opens is as useless as one that never closes.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            phase="decompose",
            checkpoints=("classify", "decompose"),
        ),
    )

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 0
    assert "UNAPPROVED" not in out
    assert "VERDICT:   ready" in out


def test_preflight_refuses_a_root_with_no_open_child_left_to_provision(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The cheaper reproduction: a decomposed root whose children have all closed.

    Measured 2026-08-05 on ``basicly-yc0x`` at d31f6bd — every child closed the day
    before, so there was nothing to provision and no checkpoint involved at all, and
    preflight still printed `VERDICT: ready`. The checkpoint is one cause of "this pass
    cannot dispatch a lane"; an exhausted child set is another.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            children=(("c.1", "closed"), ("c.2", "closed")),
        ),
    )

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 1
    assert "provision: NONE - 2 child(ren), none open" in out
    assert "VERDICT:   not ready" in out


def test_preflight_refuses_when_every_open_child_is_refused_by_the_band(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Children exist and none of them can dispatch, which is the same dead pass.

    The band table already said so per candidate; the verdict above it did not read the
    table. A row saying REFUSED and a verdict saying ready is the surface disagreeing
    with itself.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            admissions={"c.1": _sized("c.1", 95_379, refused=True, violation="above")},
        ),
    )

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 1
    assert "provision: NONE - every open child is REFUSED by the band" in out


def test_preflight_still_prices_a_childless_root_as_its_own_lane(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the refusal above: no children is a leaf root, not a dead pass.

    Seeding a root with no children provisions the root itself as the single lane
    (``loop._start_build_leaf``), so refusing it — or pricing it at zero — would break
    the leaf case in the name of fixing the epic one.
    """
    _preflight_fixture(
        monkeypatch, _Preflight(grant=Grant(level="L3", token_budget=100_000), children=())
    )

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 0
    assert "provision: NONE" not in out
    assert "if all 1 lanes start" in out


def test_preflight_forecast_never_prices_more_lanes_than_there_are_children(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The forecast has to be referenced to what exists, not to the concurrency cap.

    Measured alongside the refusal above: the same output said `0 open child(ren)` on one
    line and priced five lanes on the next, because the forecast multiplied the cap by
    the per-lane figure without asking what there was to run. An operator minting a
    budget from that number sizes a pass that cannot start.
    """
    _preflight_fixture(
        monkeypatch,
        _Preflight(
            grant=Grant(level="L3", token_budget=100_000),
            children=(("c.1", "open"), ("c.2", "open")),
        ),
    )

    cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    # 1000 per-lane (pinned in the fixture) x 2 open children, whatever the cap is.
    assert "forecast:  ~2000 tokens if all 2 lanes start" in out
