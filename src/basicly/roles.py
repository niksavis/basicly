"""Resolve a loop phase to the agent role that drives it (basicly-4kdm).

The gap this closes was measured for two days and stated the same way each time:
the projection works and nothing consumes it. Eleven agent sources are authored,
rendered into both agent roots and vendored to consumers, and no code path asked
the host to run one — every dispatch ended at a bare ``claude -p <prompt>``.

Three properties, and each is a decision rather than an implementation detail.

**The map is data, not judgment.** A phase resolves to exactly one role by table
lookup, so the choice is not gameable, costs no tokens and cannot drift between
lanes. It mirrors the state table in ``factory-loop.md`` §3.1, which is the point:
a reader who knows the state knows the role, and a role that is not in the state
table has no business being dispatched by the engine.

**A role that is not projected resolves to nothing.** The engine names a role, the
host loads it from the agent root ``basicly install`` wrote, and a missing file
means the dispatch falls back to the default runner rather than failing. That is
deliberate: a consumer who has not upgraded still gets a working loop, one that is
merely unspecialised. The alternative — refusing to dispatch — turns a cosmetic
version skew into a stopped harness.

**Repair is the implementer's second state, not a role.** D5 admits a persona only
when it differs in tier, tools or artifact, and repair differs in none of them —
only in prompt. So REPAIR maps to ``implementer`` too, and the mode travels in the
brief.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class HasAgentStyle(Protocol):
    """The part of a runner spec this module needs, named rather than imported.

    A structural type keeps the layer direction honest: ``roles`` sits below
    ``runner`` in the declared stack, so it must not import it. The protocol is the
    contract both sides already satisfy.
    """

    @property
    def name(self) -> str:
        """The runner family, which is the key into :data:`AGENT_ROOTS`."""
        ...

    @property
    def agent_style(self) -> str | None:
        """How this family spells role selection, or None when it cannot."""
        ...


# Phase -> role, mirroring the state table in factory-loop.md §3.1. Phases the
# engine has but no role drives are absent rather than mapped to a placeholder:
# VERIFY is deterministic gates with no persona by decision, and `done` is a
# terminal marker rather than work.
#
# The three states with a role and no phase yet — VALIDATE, REPAIR, RETROSPECTIVE
# — are recorded here anyway. They cost nothing while unreachable and they are the
# thing that makes this table reviewable against §3.1 rather than against
# `loop_state.PHASES`, which is the narrower of the two and the one that is behind.
ROLE_BY_PHASE: dict[str, str] = {
    "classify": "decider",
    "decompose": "decomposer",
    "build": "implementer",
    "validate": "validator",
    "repair": "implementer",
    "retrospective": "retrospector",
    "ship": "curator",
}

# Where a projected agent lands per family, relative to the repo root. Both roots
# are written by `basicly agents-build` and vendored by `basicly install`; a family
# absent from this map cannot select a role at all (codex ships no subagent root),
# which is a parity gap declared here rather than discovered at dispatch.
AGENT_ROOTS: dict[str, tuple[Path, str]] = {
    "claude": (Path(".claude/agents"), ".md"),
    "copilot": (Path(".github/agents"), ".agent.md"),
}


def role_for_phase(phase: str) -> str | None:
    """The role that drives *phase*, or None when no persona owns it.

    None is a real answer and not an error: VERIFY is deterministic gates by
    decision (D4 of the factory design), and a phase with no role dispatches the
    default runner exactly as it does today.
    """
    return ROLE_BY_PHASE.get(phase.strip().lower())


def role_is_available(repo_root: Path, family: str, role: str) -> bool:
    """Whether *family* can load *role* from the agent root in *repo_root*.

    Checked against the projected file rather than the catalog source, because the
    projected file is what the host reads — a source that exists but was never
    built is a role the host cannot see, and reporting it as available would put a
    flag on the argv naming an agent that does not resolve.
    """
    entry = AGENT_ROOTS.get(family)
    if entry is None:
        return False
    root, suffix = entry
    return (repo_root / root / f"{role}{suffix}").is_file()


def resolve_role(repo_root: Path, spec: HasAgentStyle, phase: str) -> str | None:
    """The role *spec*'s family should be dispatched as for *phase*, if it can be.

    The composition of the two checks above, and the only entry point a dispatch
    path should use: it answers "what do I put on the argv" with a value that is
    known to resolve, or with None, and never with a name that will be silently
    dropped by a host that cannot find it.

    It takes the **spec** rather than a family name so "can this family select a
    role" is asked of the runner spec that owns the answer, instead of being
    encoded a second time here. Two copies of that fact would drift, and the
    direction they drift in is a flag emitted for a host that ignores it.
    """
    if spec.agent_style is None:
        return None
    role = role_for_phase(phase)
    if role is None or not role_is_available(repo_root, spec.name, role):
        return None
    return role
