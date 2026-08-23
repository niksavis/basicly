"""The context-occupancy meter, below both of its former callers (basicly-bom07a).

Moved from `test_supervise.py` with the code: the meter was `supervise`'s and `loop`
reached it through a deferred upward import, the declared half of the engine's one
import cycle.
"""

from __future__ import annotations

import dataclasses
import json

from basicly import context_meter, runner
from basicly.config import SizingConfig


def _sizing(ceiling: float = 0.6) -> SizingConfig:
    return SizingConfig(
        working_set_min=8_000,
        working_set_max=64_000,
        build_factors={},
        calibration_min_samples=10,
        calibration_window=50,
        context_ceiling=ceiling,
    )


def _codex() -> runner.RunnerSpec:
    return next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")


def _codex_events(tokens: int) -> str:
    """A codex stream whose last turn occupies *tokens* of the window."""
    event = {"type": "turn.completed", "usage": {"input_tokens": tokens, "output_tokens": 0}}
    return json.dumps(event)


def _finished(spec: runner.RunnerSpec, stdout: str) -> runner.RunResult:
    return runner.RunResult(
        spec.name, tuple(spec.command), executed=True, returncode=0, stdout=stdout
    )


def test_ceiling_tokens_is_the_window_fraction() -> None:
    """The observation threshold is context_ceiling of the runner's window.

    Against a window written down here rather than whatever the adapter ships today
    (basicly-89hm moved it): the arithmetic is the subject, and a shipped default that
    moves must not silently rewrite what this test asserts.
    """
    claude = dataclasses.replace(
        next(s for s in runner.BUILTIN_RUNNERS if s.name == "claude"), context_window=200_000
    )
    assert context_meter.ceiling_tokens(claude, _sizing(0.6)) == 120_000


def test_meter_context_ceiling_over_the_ceiling_reads_the_tracker_not_at_all() -> None:
    """The meter takes no repo and no issue, so it cannot spawn anything (D23).

    The signature is the guarantee: a demoted control has nothing to write to. It used
    to take the repo root and the lane's id so it could create the follow-up bead, and
    the eighteen it created came from a trigger at a fifth of its intended point.
    """
    codex = _codex()
    verdict = context_meter.meter_context_ceiling(
        codex, _finished(codex, _codex_events(250_000)), _sizing()
    )

    assert verdict.overrun is True
    assert verdict.occupancy == 250_000
    assert verdict.ceiling == 240_000
    assert verdict.observation == (
        "context occupancy 250000 tokens is over the 240000-token ceiling (observed, not enforced)"
    )


def test_meter_context_ceiling_under_the_ceiling_observes_nothing() -> None:
    """The control: a run inside the ceiling carries no observation to report."""
    codex = _codex()
    verdict = context_meter.meter_context_ceiling(
        codex, _finished(codex, _codex_events(239_999)), _sizing()
    )

    assert verdict.overrun is False
    assert verdict.observation == ""
