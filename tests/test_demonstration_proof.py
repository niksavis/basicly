from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from basicly import decompose, demonstration_proof, loop, plan_record, worktree
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import DoRResult, GateStatus
from tests import fake_tracker

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class _Child:
    title: str
    demonstration: str | None


def _target(tmp_path: Path) -> str:

    (tmp_path / "test_demonstrated.py").write_text(
        "def test_alpha() -> None:\n    assert True\n", encoding="utf-8"
    )
    return "test_demonstrated.py"


def _promise(tmp_path: Path) -> str:
    return f"`uv run pytest {_target(tmp_path)} -k beta -q`"


def _bead(demonstration: str | None) -> str:
    lines = [
        "- integrity: `L2`",
        "- budget: `40000`",
        f"- depends on: {plan_record.NOTHING_DECLARED}",
    ]
    if demonstration is not None:
        lines.append(f"- demonstration: {demonstration}")
    return f"{plan_record.PLAN_HEADING}\n\n" + "\n".join(lines) + "\n"


def _tracker(monkeypatch: pytest.MonkeyPatch, body: str | None) -> None:

    class _Proc:
        def __init__(self, stdout: str, returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def _show(_repo: Path, args: list[str], **_kw: object) -> _Proc:
        if body is None:
            return _Proc("", returncode=1)
        return _Proc(json.dumps([{"id": args[1], "description": body}]))

    fake_tracker.install(monkeypatch, _show)


def test_a_selector_matching_nothing_collects_nothing(tmp_path: Path) -> None:
    assert demonstration_proof.collects_nothing(tmp_path, _promise(tmp_path))


def test_a_selector_matching_a_test_does_not(tmp_path: Path) -> None:

    selects_one = f"`uv run pytest {_target(tmp_path)} -k alpha -q`"

    assert not demonstration_proof.collects_nothing(tmp_path, selects_one)


def test_a_target_that_does_not_exist_is_not_an_answer_of_zero(tmp_path: Path) -> None:
    assert not demonstration_proof.collects_nothing(tmp_path, "`uv run pytest test_absent.py -q`")


def test_a_collector_that_will_not_start_answers_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
    assert demonstration_proof._collect_argv(demonstration) is None
    assert not demonstration_proof.collects_nothing(tmp_path, demonstration)


def test_the_argv_is_rebuilt_rather_than_passed_through() -> None:

    argv = demonstration_proof._collect_argv("`uv run pytest tests/a.py -k first -q -x`")

    assert argv == ["tests/a.py", "-k", "first"]
    assert demonstration_proof._collect_argv("`pytest tests/a.py -p sitecustomize`") is None


def test_a_promise_at_plan_time_is_reported(tmp_path: Path) -> None:

    notice = demonstration_proof.plan_notice(
        tmp_path,
        [_Child("writes the unwired test", _promise(tmp_path)), _Child("has none", None)],
    )

    assert "'writes the unwired test'" in notice
    assert "'has none'" not in notice


def test_a_plan_whose_demonstrations_all_collect_says_nothing(tmp_path: Path) -> None:
    collects = f"`uv run pytest {_target(tmp_path)} -k alpha -q`"

    assert demonstration_proof.plan_notice(tmp_path, [_Child("a", collects)]) == ""


def test_a_promise_at_close_time_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _tracker(monkeypatch, _bead(_promise(tmp_path)))

    reason = demonstration_proof.unrun_reason(tmp_path, "feat.1")

    assert "collects no test" in reason
    assert "feat.1" in reason


def test_a_demonstration_that_collects_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _tracker(monkeypatch, _bead(f"`uv run pytest {_target(tmp_path)} -k alpha -q`"))

    assert demonstration_proof.unrun_reason(tmp_path, "feat.1") == ""


def test_a_bead_recorded_before_the_field_existed_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _tracker(monkeypatch, _bead(None))

    assert demonstration_proof.unrun_reason(tmp_path, "feat.1") == ""


def test_a_record_that_cannot_be_read_is_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _tracker(monkeypatch, None)

    assert demonstration_proof.unrun_reason(tmp_path, "feat.1") == ""


def _node(phase: str, issue_type: str = "task") -> NodeState:
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
    config = PolicyConfig(required_gates=("verify",), max_rework=2)
    return loop.advance(tmp_path, "i", config=config, inputs=loop.Inputs(**inputs))  # type: ignore[arg-type]


def test_the_advance_that_creates_the_plan_reports_the_notice_without_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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


def test_ship_refuses_to_close_against_an_unrun_demonstration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _tracker(monkeypatch, _bead(_promise(tmp_path)))
    monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: _node("ship"))
    calls: list[object] = []
    monkeypatch.setattr(worktree, "cleanup", lambda *a, **_k: calls.append(a))
    monkeypatch.setattr(loop, "_write", lambda *a, **_k: calls.append(a))
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *a, **_k: calls.append(a))

    result = _advance(tmp_path)

    assert result.action == "blocked"
    assert result.needs_input == "demonstration"
    assert "collects no test" in result.detail
    assert calls == []
