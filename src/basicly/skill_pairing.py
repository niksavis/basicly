from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog_source import rel
from .skill_source import MODEL_INVOKED

if TYPE_CHECKING:
    from collections.abc import Container, Iterable
    from pathlib import Path

    from .skill_source import SkillDefinition

CLAUDE_SKILLS_KEY = "skills"

UNPAIRED_EXEMPTIONS: dict[str, str] = {
    "harness-loop": "operator: the driving session runs the loop, a lane role never does",
    "harness-client": "operator: attaching to a running supervisor is not lane work",
    "worktree-isolation": "operator: the engine provisions a lane's worktree for it",
    "release-process": "operator: a release is cut by a human, never dispatched to a lane",
    "session-finish": "operator: the driving session is the one that closes itself out",
    "catalog-authoring": "operator: catalog sources are authored in the driving session",
    "tier-injection": "operator: installing the tier kit is host setup, not lane work",
    "retention-probe": (
        "diagnostic: any role runs it on itself when guidance looks missing, so it belongs "
        "to no single role"
    ),
    "python": "environment: reached from the listing by whichever role needs the platform",
    "node": "environment: reached from the listing by whichever role needs the platform",
    "wsl": "environment: reached from the listing by whichever role needs the platform",
}


def declared_skill_names(claude: Iterable[tuple[str, object]]) -> tuple[str, ...]:

    declared = dict(claude).get(CLAUDE_SKILLS_KEY)
    if isinstance(declared, str):
        return (declared,)
    if isinstance(declared, (list, tuple)):
        return tuple(str(name) for name in declared)
    return ()


def unpaired_skills(
    repo_root: Path,
    skills: Iterable[SkillDefinition],
    declared: Container[str],
) -> list[str]:
    return [
        f"{rel(skill.source_path, repo_root)}: model-invoked skill '{skill.slug}' is "
        "declared by no agent, so the engine inlines it into no dispatch prompt and the "
        "guidance reaches no role; add it to an agent's claude.skills, or — if no role "
        "should carry it — to skill_pairing.UNPAIRED_EXEMPTIONS with the reason"
        for skill in sorted(skills, key=lambda skill: skill.slug)
        if skill.invocation == MODEL_INVOKED
        and skill.slug not in declared
        and skill.slug not in UNPAIRED_EXEMPTIONS
    ]
