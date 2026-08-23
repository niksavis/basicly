"""The context-occupancy meter both dispatch paths read (D8 measured, D23 demoted).

Below `loop` and `supervise` on purpose: it was `supervise`'s and `loop` reached it
through a deferred upward import, the declared half of the one engine cycle
(basicly-bom07a). It measures and never enforces — over 79 recorded lanes the ceiling
had zero correct firings as a control (D23), so both callers report the verdict and
`run_record` keeps the pair falsifiable against the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import runner

if TYPE_CHECKING:
    from .config import SizingConfig


def ceiling_tokens(spec: runner.RunnerSpec, sizing: SizingConfig) -> int:
    """The observation threshold for *spec*, in tokens of final context occupancy."""
    return int(spec.context_window * sizing.context_ceiling)


@dataclass(frozen=True)
class CeilingVerdict:
    """What the context meter measured on one finished dispatch."""

    occupancy: int | None
    ceiling: int
    overrun: bool

    @property
    def observation(self) -> str:
        """Both numbers in one clause for the surface that reports the run, else ""."""
        if not self.overrun:
            return ""
        return (
            f"context occupancy {self.occupancy} tokens is over the {self.ceiling}-token "
            "ceiling (observed, not enforced)"
        )


def meter_context_ceiling(
    spec: runner.RunnerSpec, result: runner.RunResult, sizing: SizingConfig
) -> CeilingVerdict:
    """Measure a finished dispatch against *spec*'s ceiling, and only measure it.

    The one metering site both write paths reach (basicly-7kxq): a second copy in
    `loop` is how the two paths came to disagree about a bead's fate for reasons
    unrelated to the bead.
    """
    occupancy = runner.context_occupancy(spec, result)
    ceiling = ceiling_tokens(spec, sizing)
    return CeilingVerdict(
        occupancy=occupancy,
        ceiling=ceiling,
        overrun=occupancy is not None and occupancy >= ceiling,
    )
