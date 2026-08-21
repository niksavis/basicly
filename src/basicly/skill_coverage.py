"""Which catalog skills reach a dispatch: by the role that runs it, and by the unit it is.

The **role** route already existed — a persona declares ``skills:`` in its projected
frontmatter and ``loop._run_agent`` injects those bodies. The **unit** route is new
(basicly-jcl4rm): a work type in a phase, declared by ``covers:`` in ``skill.yaml``.

Declared, never inferred: a matcher guessing from ``description`` prose misses
silently, and a silent miss is the failure already being paid for. The vocabularies are
the engine's own, ``catalog_lint`` refuses a value outside them, and
:func:`unreachable_skills` prints what neither route delivers.

Delivery is not invocation: the ``tool-usage`` hook observes ``Skill`` tool calls and an
injected body is not one, so the usage report needs this to say which it means.
"""

# comment-density-waiver: cohesion: 908 tokens of code across 10 functions, so the share is set by
# the per-function contract ruff `D` mandates and not by narration - the median body here
# is five lines. Three passes already cut ~1000 characters of prose off this module; what
# is left is why the declaration is explicit rather than inferred, why the Skill-tool
# counter cannot see the injection route, and which two exceptions `discover_skills`
# raises. Those are the claims basicly-jcl4rm was filed about, and deleting them to reach
# 50.0% would leave the gate green over the thing the gate exists to protect.

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

# The phases a dispatch can be composed for: the keys of the table that resolves a
# persona, not `config.LOOP_PHASES` — only a phase that dispatches an agent carries a
# brief, and that table is the one naming `repair` and `retrospective`.
COVERABLE_PHASES: frozenset[str] = frozenset(roles.ROLE_BY_PHASE)


def covering_skills(
    skills: Iterable[SkillDefinition], work_type: str | None, phase: str | None
) -> tuple[str, ...]:
    """Names of the skills whose ``covers:`` declaration matches this unit (pure).

    An empty axis means "any" on it; a skill declaring neither is unreachable by unit.
    An unknown *work_type* or *phase* narrows rather than widens, so an absent tracker
    field cannot pull in every declaration.
    """
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
    """The skills the catalog declares for this unit, () when it carries no catalog.

    Read from the ``skill.yaml`` sources: ``covers:`` is basicly-internal and never
    rendered into a ``SKILL.md``, so the engine is its only reader.
    """
    return covering_skills(_catalog(repo_root), work_type, phase)


def role_skills(repo_root: Path, family: str, role: str) -> tuple[str, ...]:
    """The skill names *role*'s projected definition declares for *family*.

    Read from the projected file rather than the catalog source, for the reason
    :func:`roles.role_is_available` gives: the projected file is what the host loads,
    so a source that was never built declares nothing the dispatch can honour.
    """
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
    """The `skills:` list of a projected agent's frontmatter (pure).

    Hand-parsed rather than handed to a YAML loader because only the frontmatter is
    YAML: the body below it is markdown that would fail to load, and splitting on the
    fence to feed a loader costs more than reading the one list this needs.
    """
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
    """Every skill some dispatch this engine composes would put in front of an agent.

    Both routes over the cross-product the engine can dispatch: each role in
    :data:`roles.ROLE_BY_PHASE` on each family in :data:`roles.AGENT_ROOTS`, plus every
    declared ``covers:``. Independent of whether an agent invoked it.
    """
    names = {
        name
        for role in set(roles.ROLE_BY_PHASE.values())
        for family in roles.AGENT_ROOTS
        for name in role_skills(repo_root, family, role)
    }
    names.update(skill.name for skill in _catalog(repo_root) if _declares_coverage(skill))
    return tuple(sorted(names))


def unreachable_skills(repo_root: Path, candidates: Sequence[str]) -> tuple[str, ...]:
    """Those of *candidates* no role declares and no ``covers:`` block reaches.

    The miss report: nothing the engine composes can name one of these, so it waits on
    an author recalling it — the state basicly-jcl4rm was filed about.
    """
    delivered = set(delivered_skills(repo_root))
    return tuple(name for name in candidates if name not in delivered)


@dataclass(frozen=True)
class NeverInvoked:
    """A never-Skill-invoked set split into the two different claims it holds."""

    delivered: tuple[str, ...]
    unreachable: tuple[str, ...]


def partition_never_invoked(repo_root: Path, names: Sequence[str]) -> NeverInvoked:
    """Split *names* by whether some dispatch delivers them anyway (basicly-jcl4rm).

    A zero in the Skill-tool counter is two claims, and the report merged them into
    "culling candidates" — which left six process skills unexercised. ``delivered`` is
    the instrument's blind spot; ``unreachable`` is the actionable half, and still not a
    culling verdict.
    """
    unreachable = unreachable_skills(repo_root, names)
    blind = set(unreachable)
    return NeverInvoked(
        delivered=tuple(name for name in names if name not in blind),
        unreachable=unreachable,
    )


def vocabulary_problems(skills: Iterable[SkillDefinition]) -> list[str]:
    """Every ``covers:`` value outside the engine's own vocabulary, as lint messages.

    Bound to :data:`basicly.config.WORK_TYPES` and :data:`COVERABLE_PHASES`, not a copy:
    a phase the engine stopped dispatching would else match nothing forever, which reads
    exactly like a skill nobody wrote a trigger for.
    """
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
    """True when *skill* declares either coverage axis."""
    return bool(skill.covered_work_types or skill.covered_phases)


def _catalog(repo_root: Path) -> list[SkillDefinition]:
    """The loaded skill sources, [] when this checkout carries no readable catalog.

    An unreadable catalog costs the unit route, not the run. The two types are all
    ``discover_skills`` raises — it wraps every YAML and shape fault into
    :class:`ValidationError`, and the read is the only other failure — so anything else
    is a defect here rather than a silently thinner brief.
    """
    try:
        return discover_skills(repo_root)
    except ValidationError, OSError:
        return []
