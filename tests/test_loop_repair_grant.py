"""Whether a repair is allowed to spawn at all (basicly-dbbh).

The third split of the repair tests, along the responsibility the module-size gate asked
for rather than a `_part2`:

- `test_loop_repair.py` — what a failed gate briefs, and what the repair does with it.
- `test_loop_repair_spend.py` — the rework-count arithmetic underneath the per-gate
  ceiling, asserted without driving the engine.
- this module — D3's grant halt admitting or refusing the dispatch, which does drive it.

`at` and the tracker stub are redeclared because an autouse fixture does not reach another
module and `at` is module-local; the rest is imported from the sibling.
"""

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
    """Pin the node state the loop resumes from."""

    def _pin(state: NodeState) -> None:
        monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: state)

    return _pin


@pytest.fixture(autouse=True)
def _no_tracker_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests run outside a git repo and must never reach the real tracker."""
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop, "_run_br", lambda *_a, **_k: SimpleNamespace(stdout="{}"))
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [])


def _status(*, halted: bool) -> policy.SpendStatus:
    """A grant status, spelled from the real numbers the defect was observed under."""
    return policy.SpendStatus(
        grant=policy.Grant(level="L3", token_budget=300_000_000),
        spent_tokens=315_547_342,
        halted=halted,
        detail="L3 grant token_budget spent (315547342/300000000 tokens under this grant)",
    )


def test_a_repair_is_refused_when_the_grant_cannot_cover_it(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A repair is a metered dispatch, so D3's halt binds on it (observed on basicly-ef7t).

    The brief is consumed on read, so a refusal that ate it would turn "no budget" into
    "the failure is forgotten" — hence the last assertion.
    """
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

    assert seen == [], "no runner may be spawned once the grant is spent"
    assert second.blocked and second.needs_input == "grant"
    assert "315547342/300000000" in second.detail
    assert (cwd / repair_brief.REPAIR_BRIEF_FILE).is_file(), "the brief survives a refusal"


def test_a_repair_dispatches_normally_while_the_grant_covers_it(
    at, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The false-positive case, with `grant_root` set so the guard really runs.

    A refusal that never fires looks identical to one correctly silent, and this one also
    caught a first attempt that reused the composite refusal and so re-admitted a repair
    against the plan gate and the working-set band.
    """
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
