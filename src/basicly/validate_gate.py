"""Which gates a unit's recorded integrity level makes required (basicly-u2hl.54.1).

VALIDATE is a state every unit passes through and a gate only L3 units owe — the
split that let the phase be added without refusing work already in flight.

Only ``validate-as-consumer`` is promoted, never ``evidence-binding``: ``full`` is a
``basicly verify`` mode and nothing in this tree records an ``evidence-binding``
result, so requiring it would rest every L3 unit against a gate with no producer.
basicly-u2hl.54.3 wires the promoted gate's producer.

Kept out of :mod:`basicly.policy`: ``gate_status`` classifies rows against a
required set; *which* set a unit owes is a question about the unit.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from . import integrity, policy
from .br import read_comments as _read_comments

if TYPE_CHECKING:
    from pathlib import Path

    from .config import PolicyConfig

VALIDATE_GATE = "validate-as-consumer"

_LEVEL_FIELD = "level="


def recorded_level(repo_root: Path, issue_id: str) -> str | None:
    """The level classify recorded for *issue_id*, or None when unmarked.

    Read back from the marker, never re-derived from scope, which would disagree
    with the record whenever the path rules changed after classify ran. Markers
    arrive oldest-first, so the last one is the standing verdict.
    """
    level: str | None = None
    for comment in _read_comments(repo_root, issue_id):
        text = str(comment.get("text", "")).strip()
        if not text.startswith(integrity.CLASSIFICATION_MARKER):
            continue
        for field in text.split():
            if field.startswith(_LEVEL_FIELD):
                level = field[len(_LEVEL_FIELD) :]
    # Unreadable reads as unmarked, not an error: the alternative wedges the unit.
    return level if level in integrity.LEVELS else None


def requires_validation(level: str | None) -> bool:
    """True when *level*'s selection makes ``validate-as-consumer`` a gate (pure)."""
    if level is None:
        return False
    return VALIDATE_GATE in integrity.selection_for(level).gates


def required_config(repo_root: Path, issue_id: str, config: PolicyConfig) -> PolicyConfig:
    """*config* with ``validate-as-consumer`` required when *issue_id* recorded L3."""
    if not requires_validation(recorded_level(repo_root, issue_id)):
        return config
    if VALIDATE_GATE in config.required_gates:
        return config
    return replace(config, required_gates=(*config.required_gates, VALIDATE_GATE))


def outstanding(gates: policy.GateStatus) -> bool:
    """True when the validate gate is required of this unit and is not green.

    Per-gate fields rather than ``can_advance``, which cannot say which gate holds.
    """
    return VALIDATE_GATE in gates.required_failed or VALIDATE_GATE in gates.required_missing
