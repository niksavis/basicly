from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class HasAgentStyle(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def agent_style(self) -> str | None: ...


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
    role: str
    lens: str


REVIEW_LENSES: tuple[str, ...] = ("correctness", "security")

LENS_ROLE_BY_PHASE: dict[str, str] = {"validate": "reviewer"}

SUPERSEDED_ROLES: dict[str, str] = {"code-reviewer": "reviewer"}

AGENT_ROOTS: dict[str, tuple[Path, str]] = {
    "claude": (Path(".claude/agents"), ".md"),
    "copilot": (Path(".github/agents"), ".agent.md"),
}


def role_for_phase(phase: str) -> str | None:

    return ROLE_BY_PHASE.get(phase.strip().lower())


def lens_dispatches(phase: str) -> tuple[RoleDispatch, ...]:

    role = LENS_ROLE_BY_PHASE.get(phase.strip().lower())
    if role is None:
        return ()
    return tuple(RoleDispatch(role, lens) for lens in REVIEW_LENSES)


def role_is_available(repo_root: Path, family: str, role: str) -> bool:

    entry = AGENT_ROOTS.get(family)
    if entry is None:
        return False
    root, suffix = entry
    return (repo_root / root / f"{role}{suffix}").is_file()


def resolve_role(repo_root: Path, spec: HasAgentStyle, phase: str) -> str | None:

    role = role_for_phase(phase)
    return None if role is None else resolve_named_role(repo_root, spec, role)


def resolve_named_role(repo_root: Path, spec: HasAgentStyle, role: str) -> str | None:

    if spec.agent_style is None:
        return None
    current = SUPERSEDED_ROLES.get(role.strip().lower(), role)
    return current if role_is_available(repo_root, spec.name, current) else None


INHERITING_ROLES: frozenset[str] = frozenset({"implementer"})


def inherits_context(role: str | None) -> bool:

    return role is not None and role.strip().lower() in INHERITING_ROLES


def phase_inherits_context(phase: str) -> bool:

    return inherits_context(role_for_phase(phase))
