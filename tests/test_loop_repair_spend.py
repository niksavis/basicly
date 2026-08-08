"""Counting what a lane has already spent, before any ceiling is applied to it.

Split out of ``test_loop_repair.py``, which keeps the ceiling itself: the bound a
lane's per-gate allowances stop compounding at, and the two advances that enforce
it. This is the reading underneath that bound — ``loop.lane_rework_spent`` — and it
is asserted without driving the engine at all.

Two properties, and both are about arithmetic the enforcement half cannot see:

- The total has to read *every* gate that can charge the lane, because per-gate
  counters are the thing that compounds. Summing only the verify gate would let a
  lane spend the ceiling three times over and never reach it.
- An unreadable tracker returns ``None`` rather than a number. The count is skipped,
  not assumed: this runs inside a gate-failure path, and a tracker hiccup there must
  not become a second failure by inventing a ceiling nobody counted to. The per-gate
  cap is still bounding the loop, and the next attempt re-reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import loop, merge, policy, rubrics, verify
from basicly.config import PolicyConfig

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def test_an_unreadable_tracker_does_not_invent_a_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ceiling is skipped, not assumed, when the attempts cannot be counted.

    A tracker hiccup inside a gate-failure path must not become a second failure:
    the per-gate cap is still bounding the loop, and the next attempt re-reads.
    """

    def _refuse(*_a, **_k):
        raise RuntimeError("br unavailable")

    monkeypatch.setattr(policy, "rework_charged", _refuse)

    assert loop.lane_rework_spent(tmp_path, "i", CONFIG) is None


def test_the_lane_total_sums_every_gate_that_can_charge_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per-gate counters are what compound, so the total has to read all of them."""
    charged = {verify.DEFAULT_GATE: 1, rubrics.RUBRIC_GATE: 2, merge.MERGE_GATE: 1}
    monkeypatch.setattr(policy, "rework_charged", lambda _r, _i, gate: charged.get(gate, 0))

    assert loop.lane_rework_spent(tmp_path, "i", CONFIG) == 4
