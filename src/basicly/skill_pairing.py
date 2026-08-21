"""Which model-invoked skills no agent role declares, and which are exempt on purpose.

:func:`basicly.agents.unknown_skill_refs` checks the pairing one way — a declaration
naming a skill the catalog does not hold. This checks the other: a shipped skill no agent
declares. The engine inlines a declared skill's body into the dispatch prompt
(``loop._with_role_skills``), so such a skill is guidance no dispatch ever delivers, and
nothing downstream says so.

Model-invoked only. A user-invoked ``tool-*`` entry carries no description by construction
and is reached by a human typing its name, so reaching no agent is its normal state.

Split from :mod:`basicly.agents`, which sits at its frozen module-size baseline; the
violations still return through ``lint_agent_sources``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog_source import rel
from .skill_source import MODEL_INVOKED

if TYPE_CHECKING:
    from collections.abc import Container, Iterable
    from pathlib import Path

    from .skill_source import SkillDefinition

# The key an agent's Claude passthrough declares its preloaded skills under.
CLAUDE_SKILLS_KEY = "skills"

# The model-invoked skills that reach no agent on purpose, each carrying the reason, and
# kept beside the check that reads it so the escape hatch and its argument are one read.
# Two populations, exempt for different reasons: an `operator` skill belongs to the
# session driving `basicly loop` — a lane role must not drive the loop, the engine does —
# and an `environment` skill is reached from the skill listing by whichever role needs the
# platform, so pinning it to one persona would narrow it rather than deliver it.
UNPAIRED_EXEMPTIONS: dict[str, str] = {
    "harness-loop": "operator: the driving session runs the loop, a lane role never does",
    "harness-client": "operator: attaching to a running supervisor is not lane work",
    "worktree-isolation": "operator: the engine provisions a lane's worktree for it",
    "release-process": "operator: a release is cut by a human, never dispatched to a lane",
    "session-finish": "operator: the driving session is the one that closes itself out",
    "catalog-authoring": "operator: catalog sources are authored in the driving session",
    "tier-injection": "operator: installing the tier kit is host setup, not lane work",
    "python": "environment: reached from the listing by whichever role needs the platform",
    "node": "environment: reached from the listing by whichever role needs the platform",
    "wsl": "environment: reached from the listing by whichever role needs the platform",
}


def declared_skill_names(claude: Iterable[tuple[str, object]]) -> tuple[str, ...]:
    """The skill names an agent's Claude passthrough preloads, as authored.

    Takes the passthrough pairs, not the ``AgentDefinition`` holding them: this module
    sits below :mod:`basicly.agents` in the import tiers, and import-linter counts a
    ``TYPE_CHECKING`` annotation as an import, so even the type would break the contract.

    Reads both shapes the host accepts, a list and a bare string, because the passthrough
    is untyped on purpose — its shape is the host's to define. Anything else is a
    malformed source for the schema to refuse, not a name list to guess at.
    """
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
    """Lint violations: every model-invoked skill *declared* misses and no entry exempts."""
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
