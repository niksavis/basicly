from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import runner

if TYPE_CHECKING:
    from .config import SizingConfig


def ceiling_tokens(spec: runner.RunnerSpec, sizing: SizingConfig) -> int:
    return int(spec.context_window * sizing.context_ceiling)


@dataclass(frozen=True)
class CeilingVerdict:
    occupancy: int | None
    ceiling: int
    overrun: bool

    @property
    def observation(self) -> str:
        if not self.overrun:
            return ""
        return (
            f"context occupancy {self.occupancy} tokens is over the {self.ceiling}-token "
            "ceiling (observed, not enforced)"
        )


def meter_context_ceiling(
    spec: runner.RunnerSpec, result: runner.RunResult, sizing: SizingConfig
) -> CeilingVerdict:

    occupancy = runner.context_occupancy(spec, result)
    ceiling = ceiling_tokens(spec, sizing)
    return CeilingVerdict(
        occupancy=occupancy,
        ceiling=ceiling,
        overrun=occupancy is not None and occupancy >= ceiling,
    )
