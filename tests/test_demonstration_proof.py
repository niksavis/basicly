"""Running a unit's declared demonstration, and what a zero selection means where.

The pair that carries the whole design is
``test_a_promise_at_plan_time_is_reported`` against
``test_a_promise_at_close_time_is_refused``: the *same* demonstration, the same tree, and
opposite answers, because at plan time the named test is what the child will write and at
close time it was supposed to exist. Every collector assertion below runs the real
``pytest --collect-only`` against a target written under ``tmp_path`` — a count taken
from this repo's own suite moves whenever anyone adds a test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from basicly import br, decompose, demonstration_proof, loop, plan_record, worktree
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import DoRResult, GateStatus

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class _Child:
    """A proposed child, structurally satisfying ``demonstration_proof.Demonstrated``."""

    title: str
    demonstration: str | None


def _target(tmp_path: Path) -> str:
    """Write a one-test module under *tmp_path* and return its name, relative to it.

    Relative on purpose: it is what proves the collector runs in the *repo_root* it is
    handed rather than in the process's own working directory.
    """
    (tmp_path / "test_demonstrated.py").write_text(
        "def test_alpha() -> None:\n    assert True\n", encoding="utf-8"
    )
    return "test_demonstrated.py"


def _promise(tmp_path: Path) -> str:
    """A demonstration naming a test that does not exist in the target yet."""
    return f"`uv run pytest {_target(tmp_path)} -k beta -q`"


def _bead(demonstration: str | None) -> str:
    """A recorded bead body carrying *demonstration* under the plan heading."""
    lines = [
        "- integrity: `L2`",
        "- budget: `40000`",
        f"- depends on: {plan_record.NOTHING_DECLARED}",
    ]
    if demonstration is not None:
        lines.append(f"- demonstration: {demonstration}")
    return f"{plan_record.PLAN_HEADING}\n\n" + "\n".join(lines) + "\n"


def _tracker(monkeypatch: pytest.MonkeyPatch, body: str | None) -> None:
    """Answer ``br show`` with a bead carrying *body*, or refuse to answer at all."""

    class _Proc:
        def __init__(self, stdout: str, returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def _show(_repo: Path, args: list[str], **_kw: object) -> _Proc:
        if body is None:
            return _Proc("", returncode=1)
        return _Proc(json.dumps([{"id": args[1], "description": body}]))

    monkeypatch.setattr(br, "try_run_br", _show)


# --- The instrument ---------------------------------------------------------


def test_a_selector_matching_nothing_collects_nothing(tmp_path: Path) -> None:
    """The measured defect, and the refusal test's premise."""
    assert demonstration_proof.collects_nothing(tmp_path, _promise(tmp_path))


def test_a_selector_matching_a_test_does_not(tmp_path: Path) -> None:
    """The control: without it the instrument could answer True to everything.

    Same file and command, one word different — an instrument reading the collector's
    exit code backwards passes the test above and fails this one.
    """
    selects_one = f"`uv run pytest {_target(tmp_path)} -k alpha -q`"

    assert not demonstration_proof.collects_nothing(tmp_path, selects_one)


def test_a_target_that_does_not_exist_is_not_an_answer_of_zero(tmp_path: Path) -> None:
    """Exit 4 is a usage error: the collector never got as far as counting."""
    assert not demonstration_proof.collects_nothing(tmp_path, "`uv run pytest test_absent.py -q`")


def test_a_collector_that_will_not_start_answers_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail open on a checkout with no pytest: a missing instrument is no fact about a unit."""

    def no_pytest(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("python")

    monkeypatch.setattr(demonstration_proof.subprocess, "run", no_pytest)

    assert not demonstration_proof.collects_nothing(tmp_path, _promise(tmp_path))


@pytest.mark.parametrize(
    "demonstration",
    [
        "run `basicly decompose feat --plan plan.toml` and read the group table",
        "POST to `/v1/plans` and read the refusal in the response body",
    ],
)
def test_a_demonstration_this_module_will_not_run_is_no_finding(
    tmp_path: Path, demonstration: str
) -> None:
    """D18 admits a command and a request; reporting against them would report on the rule."""
    assert demonstration_proof._collect_argv(demonstration) is None
    assert not demonstration_proof.collects_nothing(tmp_path, demonstration)


def test_the_argv_is_rebuilt_rather_than_passed_through() -> None:
    """Target and selector survive, a report-only flag is dropped, any other stops the run.

    The trust boundary: `-p` loads whatever module the plan names, so an unvetted flag has
    to reach no collector at all.
    """
    argv = demonstration_proof._collect_argv("`uv run pytest tests/a.py -k first -q -x`")

    assert argv == ["tests/a.py", "-k", "first"]
    assert demonstration_proof._collect_argv("`pytest tests/a.py -p sitecustomize`") is None


# --- The two moments --------------------------------------------------------


def test_a_promise_at_plan_time_is_reported(tmp_path: Path) -> None:
    """Half the pair: the plan-time call is a note on the detail line, not a refusal.

    `tests/test_handoff.py -k unwired` was refused by the first cut of this gate, and it
    is an honest plan — the file exists and the test is what the work will write.
    """
    notice = demonstration_proof.plan_notice(
        tmp_path,
        [_Child("writes the unwired test", _promise(tmp_path)), _Child("has none", None)],
    )

    assert "'writes the unwired test'" in notice
    assert "'has none'" not in notice


def test_a_plan_whose_demonstrations_all_collect_says_nothing(tmp_path: Path) -> None:
    """The control for the notice: silence is the ordinary case, not an empty report."""
    collects = f"`uv run pytest {_target(tmp_path)} -k alpha -q`"

    assert demonstration_proof.plan_notice(tmp_path, [_Child("a", collects)]) == ""


def test_a_promise_at_close_time_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half: the same demonstration, refused where the work claims to be done."""
    _tracker(monkeypatch, _bead(_promise(tmp_path)))

    reason = demonstration_proof.unrun_reason(tmp_path, "feat.1")

    assert "collects no test" in reason
    assert "feat.1" in reason


def test_a_demonstration_that_collects_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control at the closing boundary: a unit that kept its promise is not blocked."""
    _tracker(monkeypatch, _bead(f"`uv run pytest {_target(tmp_path)} -k alpha -q`"))

    assert demonstration_proof.unrun_reason(tmp_path, "feat.1") == ""


def test_a_bead_recorded_before_the_field_existed_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ratchet: no demonstration line is the whole pre-D18 population, not a defect."""
    _tracker(monkeypatch, _bead(None))

    assert demonstration_proof.unrun_reason(tmp_path, "feat.1") == ""


def test_a_record_that_cannot_be_read_is_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Not a hole: the close runs through the same tracker and fails there instead."""
    _tracker(monkeypatch, None)

    assert demonstration_proof.unrun_reason(tmp_path, "feat.1") == ""


# --- The rungs it binds at --------------------------------------------------


def _node(phase: str, issue_type: str = "task") -> NodeState:
    """A node the loop will route straight into *phase*'s handler."""
    return NodeState(
        issue_id="i",
        status="in_progress",
        issue_type=issue_type,
        phase=phase,
        worktree=WorktreeBinding("i", "harness/i") if phase == "ship" else None,
        gates=GateStatus(True, (), (), (), ()),
        checkpoints=(),
        rework={},
        has_children=False,
    )


def _advance(tmp_path: Path, **inputs: object) -> loop.AdvanceResult:
    """One advance under the same config the loop suite uses."""
    config = PolicyConfig(required_gates=("verify",), max_rework=2)
    return loop.advance(tmp_path, "i", config=config, inputs=loop.Inputs(**inputs))  # type: ignore[arg-type]


def test_the_advance_that_creates_the_plan_reports_the_notice_without_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The plan-time surface: the detail line of the advance that records the children.

    On the loop's own report rather than ``basicly decompose``'s, because that surface is
    the one the factory path never reads — the comment beside this detail line says so.
    """
    _tracker(monkeypatch, _bead(None))
    monkeypatch.setattr(
        loop.loop_state, "read_node_state", lambda *_a, **_k: _node("classify", "feature")
    )
    monkeypatch.setattr(loop.policy, "definition_of_ready", lambda *_a: DoRResult(True, ()))
    monkeypatch.setattr(
        loop.decompose,
        "decompose",
        lambda *_a, **_k: decompose.DecomposeResult("i", (), (("i.1",),)),
    )
    child = _Child("writes the unwired test", _promise(tmp_path))

    result = _advance(tmp_path, children=(child,))

    assert result.action == "decomposed"
    assert "collects nothing today" in result.detail
    assert "'writes the unwired test'" in result.detail


# --- The rung it binds at ---------------------------------------------------


def test_ship_refuses_to_close_against_an_unrun_demonstration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The boundary itself: blocked with no teardown, no close and no tracker commit.

    End to end through the real collector — the bead records the same demonstration the
    plan-time test admits. The guard sits ahead of every side effect in ``_on_ship``, for
    the reason its unmerged-worktree guard does: a bead closed against a demonstration
    nobody ran is exactly what this must not leave behind. The admitting control is
    ``test_loop.py::test_ship_tears_down_and_closes``, which still tears down.
    """
    _tracker(monkeypatch, _bead(_promise(tmp_path)))
    monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: _node("ship"))
    calls: list[object] = []
    monkeypatch.setattr(worktree, "cleanup", lambda *a, **_k: calls.append(a))
    monkeypatch.setattr(loop, "_run_br", lambda *a, **_k: calls.append(a))
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *a, **_k: calls.append(a))

    result = _advance(tmp_path)

    assert result.action == "blocked"
    assert result.needs_input == "demonstration"
    assert "collects no test" in result.detail
    assert calls == []
