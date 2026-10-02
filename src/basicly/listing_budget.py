from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import claude_settings, skill_source, skills
from .context_window import ADAPTER_WINDOWS, CODEX_FALLBACK_WINDOW
from .schema import ValidationError

DISABLE_MODEL_INVOCATION = "disable-model-invocation"
CODEX_LISTING_FRACTION = 0.02
CODEX_SKILL_ROOT = ".agents/skills"


@dataclass(frozen=True)
class _Host:
    name: str
    window: int
    fraction: float
    fraction_source: str
    entry: Callable[[skill_source.SkillDefinition, str], str]
    listed: Callable[[skill_source.SkillDefinition], bool]
    overflow: str


def _claude_entry(skill: skill_source.SkillDefinition, description: str) -> str:
    return f"{skill.name}\n{description}\n"


def _codex_entry(skill: skill_source.SkillDefinition, description: str) -> str:
    return f"{skill.name}\n{description}\n{CODEX_SKILL_ROOT}/{skill.name}/SKILL.md\n"


def model_invoked(skill: skill_source.SkillDefinition) -> bool:
    return dict(skill.claude).get(DISABLE_MODEL_INVOCATION) is not True


def hosts(repo_root: Path) -> tuple[_Host, ...]:
    return (
        _Host(
            "Claude Code",
            ADAPTER_WINDOWS["claude"].tokens,
            claude_settings.skill_listing_budget(repo_root),
            f"{claude_settings.SKILL_LISTING_BUDGET_KEY} in {claude_settings.CLAUDE_SETTINGS_PATH}",
            _claude_entry,
            model_invoked,
            "drops descriptions least-invoked first",
        ),
        _Host(
            "Codex",
            CODEX_FALLBACK_WINDOW,
            CODEX_LISTING_FRACTION,
            "fixed by Codex",
            _codex_entry,
            lambda _skill: True,
            "shortens descriptions first, then leaves skills out",
        ),
    )


def listing_characters(entries: Sequence[skill_source.SkillDefinition], host: _Host) -> int:
    return sum(
        len(host.entry(skill, skills.skill_description(skill)))
        for skill in entries
        if host.listed(skill)
    )


def listing_budget_warnings(repo_root: Path) -> list[str]:

    entries = skill_source.discover_skills(repo_root)
    if not entries:
        return []
    warnings: list[str] = []
    for host in hosts(repo_root):
        try:
            characters = listing_characters(entries, host)
        except ValidationError:
            return []
        budget = int(host.window * host.fraction)
        if characters <= budget:
            continue
        listed = sum(1 for skill in entries if host.listed(skill))
        warnings.append(
            f"{host.name} skill listing is {characters} characters against a "
            f"{budget}-character budget ({host.fraction:.0%} of the {host.window}-token window, "
            f"{host.fraction_source}), from {listed} entries. The host {host.overflow}, so "
            f"the entries this overrun silences are the ones already hardest to reach. Retire "
            f"a dead skill, shorten a description, or set {DISABLE_MODEL_INVOCATION} on a skill "
            f"that only a person starts."
        )
    return warnings
