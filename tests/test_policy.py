"""Tests for the gate & checkpoint policy engine (onb.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import policy
from basicly.config import PolicyConfig


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stateful stand-in for the br CLI, routed by subcommand.

    Holds a mutable comment list so record/approve writes are visible to the
    subsequent list reads, exactly as the real tracker behaves.
    """

    def __init__(self, *, lint_missing: list[str] | None = None, gates: list[dict] | None = None):
        self.lint_missing = lint_missing or []
        self.gates = gates or []
        self.comments: list[str] = []

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["lint"]:
            return _Proc(json.dumps({"results": [{"missing": self.lint_missing}]}))
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": self.gates}))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        if args[:2] == ["comments", "add"]:
            # br comments add <id> <text> — the marker text is the last arg.
            self.comments.append(args[-1])
            return _Proc("")
        raise AssertionError(f"unexpected br call: {args}")


CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(policy, "_run_br", fake)


def test_definition_of_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """DoR is ready only when br lint reports no missing sections."""
    _install(monkeypatch, _FakeBr(lint_missing=[]))
    assert policy.definition_of_ready(tmp_path, "i").ready is True

    _install(monkeypatch, _FakeBr(lint_missing=["## Acceptance Criteria"]))
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is False
    assert result.missing == ("## Acceptance Criteria",)


def test_gate_status_advances_when_required_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A passing required gate advances; an advisory gate never blocks."""
    _install(
        monkeypatch,
        _FakeBr(
            gates=[
                {"gate": "verify", "provider": "ci", "passed": True},
                {"gate": "review", "provider": "ai", "passed": False},
            ]
        ),
    )
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is True
    assert status.required_passed == ("verify",)
    assert [(v.gate, v.passed) for v in status.advisory] == [("review", False)]


def test_gate_status_blocks_on_failed_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed required gate blocks advancement."""
    _install(monkeypatch, _FakeBr(gates=[{"gate": "verify", "provider": "ci", "passed": False}]))
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is False
    assert status.required_failed == ("verify",)


def test_gate_status_blocks_on_missing_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A required gate with no recorded result blocks advancement."""
    _install(monkeypatch, _FakeBr(gates=[]))
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is False
    assert status.required_missing == ("verify",)


def test_rework_counts_and_escalates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rework attempts accumulate and escalate once the cap is reached."""
    _install(monkeypatch, _FakeBr())
    assert policy.rework_attempts(tmp_path, "i", "verify") == 0
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is False

    assert policy.record_rework(tmp_path, "i", "verify") == 1
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is False

    assert policy.record_rework(tmp_path, "i", "verify") == 2
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is True


def test_rework_counter_is_per_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rework markers are scoped to their gate, not shared."""
    _install(monkeypatch, _FakeBr())
    policy.record_rework(tmp_path, "i", "verify")
    assert policy.rework_attempts(tmp_path, "i", "verify") == 1
    assert policy.rework_attempts(tmp_path, "i", "security") == 0


def test_checkpoint_approval_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A checkpoint reads pending until approved, then approved (idempotent)."""
    _install(monkeypatch, _FakeBr())
    assert policy.checkpoint_approved(tmp_path, "i", "decompose") is False
    policy.approve_checkpoint(tmp_path, "i", "decompose")
    policy.approve_checkpoint(tmp_path, "i", "decompose")  # idempotent
    assert policy.checkpoint_approved(tmp_path, "i", "decompose") is True
    # A different checkpoint is unaffected.
    assert policy.checkpoint_approved(tmp_path, "i", "ship") is False


def test_approve_unknown_checkpoint_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Approving a checkpoint outside the fixed three is a loud error."""
    _install(monkeypatch, _FakeBr())
    with pytest.raises(ValueError, match="unknown checkpoint"):
        policy.approve_checkpoint(tmp_path, "i", "deploy")
