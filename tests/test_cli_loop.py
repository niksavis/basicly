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
from basicly.config import CHECKPOINTS, RunnerSpec, load_sizing_config
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
    # The calibration report to pin. Pinned like the band verdicts and for the same
    # reason: computing it walks the tracker export and the local record file, so left
    # live every preflight test would parse this repo's real 4MB of markers.
    calibration: object | None = None


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


def test_preflight_is_ready_when_nothing_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The happy path has to exit 0, or a wrapper gating on it can never proceed."""
    _preflight_fixture(monkeypatch, _Preflight(grant=Grant(level="L1", token_budget=10_000)))

    code = cli._cmd_loop_preflight(argparse.Namespace(issue="epic"))

    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT:   ready" in out


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
