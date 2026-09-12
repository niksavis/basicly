from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import roles
from .config import WORK_TYPES
from .schema import ValidationError
from .skill_source import discover_skills

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from .skill_source import SkillDefinition

COVERABLE_PHASES: frozenset[str] = frozenset(roles.ROLE_BY_PHASE)


def covering_skills(
    skills: Iterable[SkillDefinition], work_type: str | None, phase: str | None
) -> tuple[str, ...]:

    named = []
    for skill in skills:
        if not _declares_coverage(skill):
            continue
        if skill.covered_work_types and work_type not in skill.covered_work_types:
            continue
        if skill.covered_phases and phase not in skill.covered_phases:
            continue
        named.append(skill.name)
    return tuple(sorted(named))


def unit_skills(repo_root: Path, work_type: str | None, phase: str | None) -> tuple[str, ...]:

    return covering_skills(_catalog(repo_root), work_type, phase)


def role_skills(repo_root: Path, family: str, role: str) -> tuple[str, ...]:

    entry = roles.AGENT_ROOTS.get(family)
    if entry is None:
        return ()
    root, suffix = entry
    path = repo_root / root / f"{role}{suffix}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    return declared_skills(text)


def declared_skills(text: str) -> tuple[str, ...]:

    names: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("skills:"):
            inside = True
            continue
        if inside and line.startswith("- "):
            names.append(line[2:].strip())
            continue
        if inside:
            break
    return tuple(names)


def delivered_skills(repo_root: Path) -> tuple[str, ...]:

    names = {
        name
        for role in set(roles.ROLE_BY_PHASE.values())
        for family in roles.AGENT_ROOTS
        for name in role_skills(repo_root, family, role)
    }
    names.update(skill.name for skill in _catalog(repo_root) if _declares_coverage(skill))
    return tuple(sorted(names))


def unreachable_skills(repo_root: Path, candidates: Sequence[str]) -> tuple[str, ...]:

    delivered = set(delivered_skills(repo_root))
    return tuple(name for name in candidates if name not in delivered)


@dataclass(frozen=True)
class NeverInvoked:
    delivered: tuple[str, ...]
    unreachable: tuple[str, ...]


def partition_never_invoked(repo_root: Path, names: Sequence[str]) -> NeverInvoked:

    unreachable = unreachable_skills(repo_root, names)
    blind = set(unreachable)
    return NeverInvoked(
        delivered=tuple(name for name in names if name not in blind),
        unreachable=unreachable,
    )


def vocabulary_problems(skills: Iterable[SkillDefinition]) -> list[str]:

    problems = []
    for skill in skills:
        for axis, declared, allowed in (
            ("work_types", skill.covered_work_types, frozenset(WORK_TYPES)),
            ("phases", skill.covered_phases, COVERABLE_PHASES),
        ):
            if unknown := sorted(set(declared) - allowed):
                problems.append(
                    f"{skill.source_path}: covers.{axis} names {', '.join(unknown)}; "
                    f"allowed: {sorted(allowed)}"
                )
    return problems


def _declares_coverage(skill: SkillDefinition) -> bool:
    return bool(skill.covered_work_types or skill.covered_phases)


def _catalog(repo_root: Path) -> list[SkillDefinition]:

    try:
        return discover_skills(repo_root)
    except ValidationError, OSError:
        return []
