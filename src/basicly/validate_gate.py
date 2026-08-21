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

import re
from collections.abc import Iterable, Iterator
from dataclasses import replace
from typing import TYPE_CHECKING

from . import decisions, integrity, policy
from .config import VERIFY_GATE_PROVIDER
from .dispatch_brief import VERDICT_PREFIX

# Re-exported rather than respelled: consumers read ``validate_gate.VALIDATE_GATE``,
# and the name belongs to :mod:`basicly.integrity`, which decides the gate set a
# level selects (basicly-7jb5).
from .integrity import VALIDATE_GATE
from .tracker import read_comments as _read_comments
from .tracker import write as _write

if TYPE_CHECKING:
    from pathlib import Path

    from .config import PolicyConfig

_LEVEL_FIELD = "level="

# The kind :data:`basicly.decision_marker.KINDS` reserves for a validation a human has
# to dispose of, which is what a reply carrying no verdict leaves behind.
VALIDATE_DECISION_KIND = "validate"

# Markdown a verdict line arrives dressed in, dropped anywhere on the line and not only at
# its ends: the ``**VALIDATION:** PASS`` shape puts the runs *between* prefix and answer.
# Emphasis, headings and list markers around a ``label: value`` line are all in this tree's
# own agent-written ledger; single ``*`` and ``__`` runs around one are extrapolated.
_MARKUP = re.compile(r"[*_`]+")
_MARKER = re.compile(r"^[#>\-+\s]+")


def _texts(repo_root: Path, issue_id: str) -> Iterator[str]:
    """*issue_id*'s marker bodies, the one shape the pure readers below take."""
    return (str(comment.get("text", "")) for comment in _read_comments(repo_root, issue_id))


def level_in(texts: Iterable[str]) -> str | None:
    """The level *texts* records, or None when unmarked (pure).

    Read back from the marker, never re-derived from scope, which would disagree
    with the record whenever the path rules changed after classify ran. Markers
    arrive oldest-first, so the last one is the standing verdict.
    """
    level: str | None = None
    for body in texts:
        text = body.strip()
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


def required_in(texts: Iterable[str], config: PolicyConfig) -> PolicyConfig:
    """*config* with ``validate-as-consumer`` required when *texts* records L3 (pure).

    *config* itself back where nothing is promoted, so a caller can still tell the two
    apart by identity — which `test_validate_gate` asserts, because a fresh equal copy
    would hide a promotion that silently rebuilt the required set.
    """
    if not requires_validation(level_in(texts)):
        return config
    if VALIDATE_GATE in config.required_gates:
        return config
    return replace(config, required_gates=(*config.required_gates, VALIDATE_GATE))


def required_config(repo_root: Path, issue_id: str, config: PolicyConfig) -> PolicyConfig:
    """*config* with ``validate-as-consumer`` required when *issue_id* recorded L3."""
    return required_in(_texts(repo_root, issue_id), config)


def outstanding(gates: policy.GateStatus) -> bool:
    """True when the validate gate is required of this unit and is not green.

    Per-gate fields rather than ``can_advance``, which cannot say which gate holds.
    """
    return VALIDATE_GATE in gates.required_failed or VALIDATE_GATE in gates.required_missing


def has_foreign_result(gates: policy.GateStatus) -> bool:
    """True when a provider outside the engine's own recorded a validation."""
    return any(v.gate == VALIDATE_GATE for v in gates.disregarded)


def refusal_reason(gates: policy.GateStatus) -> str:
    """Why the advance out of VALIDATE is refused while the gate is not green.

    A foreign result does not satisfy a required gate (the jr0l.51 stance), so the
    gate is still missing — but reporting it as plain missing while ``br gate list``
    shows a pass leaves an operator nothing to act on.
    """
    foreign = sorted({v.provider or "(none)" for v in gates.disregarded if v.gate == VALIDATE_GATE})
    if foreign:
        return (
            f"{VALIDATE_GATE} has no engine result: a result from provider "
            f"{', '.join(foreign)} was disregarded because a required gate counts only "
            "the engine's own — re-run the validation through the harness"
        )
    return (
        f"{VALIDATE_GATE} is required at the recorded integrity level and has no engine "
        "result: exercise the change as a consumer would (the validate-as-consumer "
        "skill), then record the gate"
    )


def queue_unreadable_verdict(repo_root: Path, issue_id: str, reply: str) -> str:
    """Queue a validator reply carrying no verdict for a human, and say why we hold.

    The fail-silent this closes (basicly-xd79u3): a validator that executed, was
    charged for and ended with no ``VALIDATION:`` line recorded no gate event, queued
    nothing and spent no rework, so the only surface that showed the run at all was
    the spend. An unreadable verdict is a fact an operator can dispose of; silence is
    not, and the disposition has to be in the question because nothing else holds the
    fact — the reply is not stored anywhere and the run record carries usage, not text.

    So *reply* rides on the item as the only copy of what the validator said. The
    caller clips it, because clipping is :mod:`basicly.repair_brief`'s and this module
    sits below it.
    """
    decisions.enqueue(
        repo_root,
        issue_id,
        VALIDATE_DECISION_KIND,
        f"the validator for {issue_id} ran and its reply carries no "
        f"`{VERDICT_PREFIX} PASS`/`{VERDICT_PREFIX} FAIL` line, so {VALIDATE_GATE} has "
        "no result: re-run the validation, record the gate by hand, or rework?",
        reply or "the validator's reply was empty",
    )
    return (
        f"the validator recorded no {VALIDATE_GATE} result; the unit stays in validate "
        "— queued as a decision (dispose of it with `basicly loop answer`)"
    )


def verdict_from_reply(text: str) -> bool | None:
    """The validator's stated verdict, or None when its reply carries none (pure).

    Read off the agent's own words rather than its exit code: a validator that ran
    cleanly and found the change unusable exits 0, so the process outcome answers a
    different question. A reply with no verdict line returns None, which leaves the
    unit in VALIDATE instead of guessing at what it meant.
    """
    for line in reversed(text.splitlines()):
        stripped = _MARKER.sub("", _MARKUP.sub("", line)).strip()
        if not stripped.upper().startswith(VERDICT_PREFIX):
            continue
        answer = stripped[len(VERDICT_PREFIX) :].strip().upper()
        if answer.startswith("PASS"):
            return True
        if answer.startswith("FAIL"):
            return False
    return None


def record_verdict(repo_root: Path, issue_id: str, *, passed: bool) -> None:
    """Record *passed* as the engine's own validate gate.

    The engine writes it, never the agent: `br gate report` authenticates nothing and
    a dispatched agent reaches the real tracker, so an agent-written result on a
    required gate is self-certification (basicly-jr0l.51). Reported under the engine's
    provider for the same reason `gate_status` counts only that one.
    """
    _write(
        repo_root,
        [
            "gate",
            "report",
            issue_id,
            "--gate",
            VALIDATE_GATE,
            "--provider",
            VERIFY_GATE_PROVIDER,
            "--status",
            "pass" if passed else "fail",
        ],
    )
