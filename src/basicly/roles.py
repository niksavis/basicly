"""Resolve a loop phase to the agent role that drives it (basicly-4kdm).

The measured gap: the projection works and nothing consumes it. Eleven agent sources are authored,
rendered into both agent roots and vendored to consumers, and no code path asked
the host to run one — every dispatch ended at a bare ``claude -p <prompt>``.

Three properties, and each is a decision rather than an implementation detail.

**The map is data, not judgment.** A phase resolves by table lookup, so the choice
is not gameable, costs no tokens and cannot drift between lanes. It mirrors the
role table in ``architecture.md`` §30, which is the point: a reader who knows the
state knows the role, and a role that is not in the role table has no business
being dispatched by the engine. Two tables, because that table gives VALIDATE two roles:
:data:`ROLE_BY_PHASE` names the one role that *drives* a phase and whose reply the
engine acts on, and :data:`LENS_ROLE_BY_PHASE` the one it fans out beside it, once
per entry in :data:`REVIEW_LENSES`.

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

from dataclasses import dataclass
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


# Phase -> role, mirroring the role table in architecture.md §30. Phases the
# engine has but no role drives are absent rather than mapped to a placeholder:
# VERIFY is deterministic gates with no persona by decision, and `done` is a
# terminal marker rather than work.
#
# All three of VALIDATE, REPAIR and RETROSPECTIVE are dispatched today, each as a `phase=`
# on `loop._run_agent`. Only VALIDATE is a rung of the ladder, so this table is reviewed
# against architecture.md §30, not `config.LOOP_PHASES` or `loop_state.PHASES` (the same ladder
# plus the terminal `done`) — neither of those names repair or retrospective at all.
ROLE_BY_PHASE: dict[str, str] = {
    "classify": "decider",
    "decompose": "decomposer",
    "build": "implementer",
    "validate": "validator",
    "repair": "implementer",
    "retrospective": "retrospector",
    "ship": "curator",
}


@dataclass(frozen=True)
class RoleDispatch:
    """One role dispatched beside a phase's driving role, and the lens it reviews.

    The lens is a required field rather than an optional one: a dispatch with no
    lens is the driving role, which :data:`ROLE_BY_PHASE` already answers.
    """

    role: str
    lens: str


# The whole lens vocabulary: an axis absent here is never dispatched. Two, and the
# count is the decision — each lens is a paid dispatch on every L3 unit, so an axis a
# gate already covers spends tokens restating a green check. `security` is here
# because `basicly.toml` scopes bandit away from `src/` entirely; maintainability is
# refused because ruff, pyright, vulture and the size ratchets bind it mechanically.
# architecture.md §26.1 carries the argument per axis.
REVIEW_LENSES: tuple[str, ...] = ("correctness", "security")

# The role a phase dispatches once per lens, beside the role that drives it. VALIDATE
# is the only such phase (architecture.md §30); a dict rather than a branch, so "no other phase fans
# out" stays a lookup on data like the table above it.
LENS_ROLE_BY_PHASE: dict[str, str] = {"validate": "reviewer"}

# Retired role -> its replacement, applied before the availability check below so a
# name `basicly install` once vendored relocates instead of resolving to nothing. An
# unknown name is returned unchanged, so this narrows nothing (§6.3).
SUPERSEDED_ROLES: dict[str, str] = {"code-reviewer": "reviewer"}

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


def lens_dispatches(phase: str) -> tuple[RoleDispatch, ...]:
    """Every per-lens review *phase* fans out to, in vocabulary order (a lookup).

    Empty for a phase that fans out over nothing — every phase but VALIDATE today —
    so a caller iterates it unconditionally and pays for nothing. The tuple is a
    dispatch list and never a ranking: §6.4 forbids merging lenses, because a change
    can pass one axis and fail another and reranking lets the strong one mask it.
    """
    role = LENS_ROLE_BY_PHASE.get(phase.strip().lower())
    if role is None:
        return ()
    return tuple(RoleDispatch(role, lens) for lens in REVIEW_LENSES)


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
    role = role_for_phase(phase)
    return None if role is None else resolve_named_role(repo_root, spec, role)


def resolve_named_role(repo_root: Path, spec: HasAgentStyle, role: str) -> str | None:
    """*role*'s current name when *spec*'s family can load it, else None.

    :func:`resolve_role` answers for the role a phase's table names; this answers for
    a role the caller already holds, which is what a fan-out needs: at VALIDATE the
    reviewer is dispatched once per lens, and the phase alone no longer identifies
    which role a given dispatch is. A retired name redirects through
    :data:`SUPERSEDED_ROLES` first: asking whether the removed file is available
    answers None, which is the silent capability loss a supersession must not cause.
    """
    if spec.agent_style is None:
        return None
    current = SUPERSEDED_ROLES.get(role.strip().lower(), role)
    return current if role_is_available(repo_root, spec.name, current) else None


# Which roles inherit a seeded session (basicly-2kh170). One entry, covering repair too:
# REPAIR maps to `implementer` in :data:`ROLE_BY_PHASE`. Measured, not assumed — an
# implementer re-reads the same corpus every dispatch and a fork bills it as a cache read.
INHERITING_ROLES: frozenset[str] = frozenset({"implementer"})

# Absence from that set is how a cold role is spelled; a second table would be policy no
# dispatch path reads. Independence is what the judging four are *for* — a reviewer,
# validator, decider or retrospector forked from the session that wrote the code cannot
# refute it. The curator reads the landed diff; the decomposer runs once per feature.


def inherits_context(role: str | None) -> bool:
    """Whether a dispatch as *role* should fork a seeded session rather than start cold.

    None — no persona owns the phase, or the family cannot select one — is False, so an
    unspecialised dispatch behaves as it does today.
    """
    return role is not None and role.strip().lower() in INHERITING_ROLES


def phase_inherits_context(phase: str) -> bool:
    """Whether the role driving *phase* forks a seeded session (the composition).

    The entry point a dispatch path should use, for :func:`resolve_role`'s reason: no call
    site then spells the phase-to-role hop a second time.
    """
    return inherits_context(role_for_phase(phase))
