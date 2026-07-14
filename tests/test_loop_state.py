"""Tests for resumable loop-state reconstruction (onb.6.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import loop_state, policy
from basicly.config import PolicyConfig
from basicly.loop_state import WorktreeBinding
from basicly.policy import GateStatus

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stateful stand-in for br, routed by subcommand.

    Serves one issue record plus its gate list and comments, so read_node_state
    (which delegates gate/checkpoint/rework reads to the policy engine) resolves
    entirely against this fake when installed on both modules.
    """

    def __init__(
        self,
        *,
        gates: list[dict] | None = None,
        comments: list[str] | None = None,
        ready: list[dict] | None = None,
        blocked: list[dict] | None = None,
        **record: object,
    ) -> None:
        # Any issue field can be overridden by keyword (status, external_ref,
        # agent_context, dependents, ...); an absent agent_context stays absent.
        self.record: dict = {
            "id": "i",
            "status": "in_progress",
            "issue_type": "task",
            "external_ref": None,
            "dependents": [],
        }
        self.record.update(record)
        self.gates = gates or []
        self.comments = comments or []
        self.ready = ready or []
        self.blocked = blocked or []

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            return _Proc(json.dumps([self.record]))
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": self.gates}))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        if args[:1] == ["scheduler"]:
            return _Proc(json.dumps({"recommendations": self.ready}))
        if args[:1] == ["blocked"]:
            return _Proc(json.dumps(self.blocked))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(loop_state, "_run_br", fake)
    monkeypatch.setattr(policy, "_run_br", fake)


def _gate_status(*, can_advance: bool) -> GateStatus:
    if can_advance:
        return GateStatus(True, ("verify",), (), (), ())
    return GateStatus(False, (), (), ("verify",), ())


# --- Worktree binding schema ------------------------------------------------


def test_worktree_ref_roundtrips() -> None:
    """A binding formatted onto external_ref parses back identically."""
    ref = loop_state.format_worktree_ref("loop-state", "harness/loop-state")
    assert loop_state.parse_worktree_ref(ref) == WorktreeBinding("loop-state", "harness/loop-state")


@pytest.mark.parametrize("ref", [None, "", "some-other-ref", "worktree:", "worktree:only"])
def test_worktree_ref_rejects_unset_or_foreign(ref: str | None) -> None:
    """An unset, foreign, or malformed external_ref yields no binding."""
    assert loop_state.parse_worktree_ref(ref) is None


# --- Phase derivation -------------------------------------------------------


# Each case orders the derive_phase inputs then the expected phase:
# status, checkpoints, worktree, can-advance, has-children, then expected.
_PHASE_CASES = [
    ("closed", ("ship",), None, True, True, "done"),
    ("in_progress", ("ship",), None, True, False, "ship"),
    ("in_progress", (), WorktreeBinding("n", "b"), True, False, "verify"),
    ("in_progress", (), WorktreeBinding("n", "b"), False, False, "build"),
    ("in_progress", ("decompose",), None, False, False, "decompose"),
    ("in_progress", (), None, False, True, "decompose"),
    ("in_progress", ("classify",), None, False, False, "classify"),
    ("open", (), None, False, False, "intake"),
]


@pytest.mark.parametrize("case", _PHASE_CASES)
def test_derive_phase_ladder(case: tuple) -> None:
    """Each recorded-evidence combination maps to the furthest reached phase."""
    status, checkpoints, worktree, advance, children, expected = case
    phase = loop_state.derive_phase(
        status, checkpoints, worktree, _gate_status(can_advance=advance), children
    )
    assert phase == expected


# --- Node state reconstruction ----------------------------------------------


def test_read_node_state_folds_all_signals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A bound, green, checkpointed issue reconstructs into a verify-phase state."""
    fake = _FakeBr(
        status="in_progress",
        external_ref=loop_state.format_worktree_ref("feat", "harness/feat"),
        gates=[{"gate": "verify", "provider": "ci", "passed": True}],
        comments=[
            "[harness-policy] checkpoint=classify approved",
            "[harness-policy] rework gate=verify",
        ],
    )
    _install(monkeypatch, fake)

    state = loop_state.read_node_state(tmp_path, "i", CONFIG)

    assert state.worktree == WorktreeBinding("feat", "harness/feat")
    assert state.gates.can_advance is True
    assert state.checkpoints == ("classify",)
    assert state.rework == {"verify": 1}
    assert state.agent_context is None  # absent in br => graceful None
    assert state.phase == "verify"


def test_read_node_state_intake_when_nothing_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh issue with no binding, gates, or checkpoints reads as intake."""
    _install(monkeypatch, _FakeBr(status="open"))
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.phase == "intake"
    assert state.worktree is None
    assert state.checkpoints == ()
    assert state.rework == {"verify": 0}


def test_read_node_state_surfaces_agent_context_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inherited agent_context is surfaced verbatim when the tracker records it."""
    _install(monkeypatch, _FakeBr(agent_context='{"design": "keep it thin"}'))
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.agent_context == '{"design": "keep it thin"}'


def test_read_node_state_decompose_phase_from_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An issue with a parent-child dependent counts as decomposed."""
    _install(
        monkeypatch,
        _FakeBr(dependents=[{"dependency_type": "parent-child", "id": "i.1"}]),
    )
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.has_children is True
    assert state.phase == "decompose"


# --- Ready / blocked sets ---------------------------------------------------


def test_ready_ranked_parses_scheduler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ready_ranked maps scheduler recommendations to ranked nodes in order."""
    _install(
        monkeypatch,
        _FakeBr(
            ready=[
                {"rank": 1, "score": 49, "issue": {"id": "a", "title": "first"}},
                {"rank": 2, "score": 30, "issue": {"id": "b", "title": "second"}},
            ]
        ),
    )
    ranked = loop_state.ready_ranked(tmp_path)
    assert [(n.rank, n.score, n.issue_id, n.title) for n in ranked] == [
        (1, 49, "a", "first"),
        (2, 30, "b", "second"),
    ]


def test_blocked_ids_parses_blocked_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """blocked_ids returns just the ids of blocked issues."""
    _install(monkeypatch, _FakeBr(blocked=[{"id": "x"}, {"id": "y"}]))
    assert loop_state.blocked_ids(tmp_path) == ("x", "y")
