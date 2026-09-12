from __future__ import annotations

BUILD_PHASE = "build"
LANE_PHASE = "lane"
VALIDATE_PHASE = "validate"
DECIDE_PHASE = "decide"
PROPOSE_PHASE = "propose"

WRITE_PHASES = frozenset({BUILD_PHASE, LANE_PHASE})


def is_write_phase(phase: object) -> bool:

    return isinstance(phase, str) and phase in WRITE_PHASES
