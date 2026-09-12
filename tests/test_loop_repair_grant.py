from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from basicly import loop, policy, repair_brief
from tests.test_loop_repair import (
    CONFIG,
    _pin_failing_subtask,
    _pin_rework,
    _pin_runner,
    _state,
    _worktree,
)

if TYPE_CHECKING:
    from basicly.loop_state import NodeState


@pytest.fixture
def at(monkeypatch: pytest.MonkeyPatch):

    def _pin(state: NodeState) -> None:
        monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: state)

    return _pin


@pytest.fixture(autouse=True)
def _no_tracker_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: SimpleNamespace(stdout="{}"))
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [])


def _status(*, halted: bool) -> policy.SpendStatus:
    return policy.SpendStatus(
        grant=policy.Grant(level="L3", token_budget=300_000_000),
        spent_tokens=315_547_342,
        halted=halted,
        detail="L3 grant token_budget spent (315547342/300000000 tokens under this grant)",
    )


def test_a_repair_dispatches_even_when_the_grant_is_spent(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    seen = _pin_runner(monkeypatch)

    first = loop.advance(tmp_path, "i", config=CONFIG)
    assert first.blocked and "briefed a repair" in first.detail
    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file()

    monkeypatch.setattr(policy, "spend_status", lambda *_a, **_k: _status(halted=True))

    second = loop.advance(tmp_path, "i", config=CONFIG, grant_root="epic")

    assert seen, "a spent grant still refused to spawn the repair"
    assert second.needs_input != "grant"
    assert "repaired i.1 in place" in second.detail


def test_a_repair_dispatches_normally_while_the_grant_covers_it(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    cwd = _worktree(tmp_path, monkeypatch)
    at(_state(has_children=True))
    _pin_failing_subtask(monkeypatch)
    _pin_rework(monkeypatch)
    seen = _pin_runner(monkeypatch)
    monkeypatch.setattr(policy, "spend_status", lambda *_a, **_k: _status(halted=False))

    loop.advance(tmp_path, "i", config=CONFIG)
    second = loop.advance(tmp_path, "i", config=CONFIG, grant_root="epic")

    assert [c for _p, c in seen] == [cwd]
    assert "repaired i.1 in place" in second.detail
