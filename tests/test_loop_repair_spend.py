from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import loop, merge, policy, rubrics, verify
from basicly.config import PolicyConfig

if TYPE_CHECKING:
    import pytest

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def test_an_unreadable_tracker_does_not_invent_a_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def _refuse(*_a, **_k):
        raise RuntimeError("br unavailable")

    monkeypatch.setattr(policy, "rework_charged", _refuse)

    assert loop.lane_rework_spent(tmp_path, "i", CONFIG) is None


def test_the_lane_total_sums_every_gate_that_can_charge_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    charged = {verify.DEFAULT_GATE: 1, rubrics.RUBRIC_GATE: 2, merge.MERGE_GATE: 1}
    monkeypatch.setattr(policy, "rework_charged", lambda _r, _i, gate: charged.get(gate, 0))

    assert loop.lane_rework_spent(tmp_path, "i", CONFIG) == 4
