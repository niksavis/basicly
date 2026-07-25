"""Tests for the ``basicly loop`` CLI wiring (onb.6.4).

The CLI is a thin driver over the loop state machine (onb.6.3) and the resumable
state model (onb.6.1): it maps the shared agent-input flags onto a
:class:`loop.Inputs`, prints the transition/state, and turns ``blocked`` into a
non-zero exit so scripts and CI can branch on it. These tests fake ``advance`` /
``run_ceremony`` / ``read_node_state`` and assert only that wiring.
"""

from __future__ import annotations

import pytest

from basicly import cli, decompose, loop, loop_state
from basicly.config import CHECKPOINTS
from basicly.decompose import ChildSpec
from basicly.loop import AdvanceResult, Inputs
from basicly.loop_state import NodeState, RankedNode, WorktreeBinding
from basicly.policy import GateStatus


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
