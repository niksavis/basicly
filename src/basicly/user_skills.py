from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from fnmatch import fnmatch
from pathlib import Path

from . import catalog, read_cost, skill_source
from .projection import SyncResult
from .skills import SKILL_FILE_NAME, _is_generated_skill, _project_skill

USER_SKILLS = Path(".claude") / "skills"
USER_LISTING_BUDGET = 200
CATALOG_SKILLS = Path("skills")


class UserSkillsError(ValueError):
    pass


def home_skills(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    return Path(values.get("HOME") or Path.home()) / USER_SKILLS


def _catalog_skills() -> list[skill_source.SkillDefinition]:
    return skill_source.discover_skills(catalog.bundled_catalog_root(), CATALOG_SKILLS)


def selected(patterns: Sequence[str]) -> list[skill_source.SkillDefinition]:

    skills = _catalog_skills()
    chosen = [skill for skill in skills if any(fnmatch(skill.slug, one) for one in patterns)]
    if not chosen:
        known = ", ".join(sorted(skill.slug for skill in skills))
        raise UserSkillsError(f"no catalog skill matches {', '.join(patterns)}; known: {known}")
    return chosen


def listing_cost(skills: Sequence[skill_source.SkillDefinition]) -> int:
    listed = [skill for skill in skills if skill.invocation == skill_source.MODEL_INVOKED]
    return read_cost._text_tokens("".join(f"{one.name}\n{one.description}\n" for one in listed))


def project(patterns: Sequence[str], root: Path, *, dry_run: bool = False) -> list[str]:

    chosen = selected(patterns)
    cost = listing_cost(chosen)
    if cost > USER_LISTING_BUDGET:
        raise UserSkillsError(
            f"the model-invoked skills selected cost {cost} listing tokens, over the "
            f"{USER_LISTING_BUDGET}-token user budget: a user-level description loads in every "
            f"repo, so select fewer model-invoked skills"
        )
    lines = []
    names = {skill.slug for skill in chosen}
    for skill in chosen:
        target = root / skill.slug
        existing = target / SKILL_FILE_NAME
        if existing.is_file() and not _is_generated_skill(existing):
            lines.append(f"kept {skill.slug}: an unmarked skill of that name is yours")
            continue
        if dry_run:
            lines.append(f"would write {skill.slug}")
            continue
        result = SyncResult()
        _project_skill(skill, target, result)
        lines.append(f"{'wrote' if result.written else 'unchanged'} {skill.slug}")
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        marked = _is_generated_skill(folder / SKILL_FILE_NAME)
        if folder.name not in names and marked:
            lines.append(f"{'would prune' if dry_run else 'pruned'} {folder.name}")
            if not dry_run:
                shutil.rmtree(folder)
    return lines


def shadows(project_root: Path, root: Path) -> list[str]:

    found = []
    for skill_md in sorted(project_root.glob(f"*/{SKILL_FILE_NAME}")):
        personal = root / skill_md.parent.name / SKILL_FILE_NAME
        if not personal.is_file():
            continue
        same = personal.read_bytes() == skill_md.read_bytes()
        how = "the same content" if same else "different content"
        found.append(
            f"the personal skill {personal} shadows the project skill {skill_md.parent.name} "
            f"({how}); Claude loads the personal one"
        )
    return found


def cmd_skills_user(args: argparse.Namespace) -> int:

    root = Path(args.home) / USER_SKILLS if args.home else home_skills()
    try:
        lines = project(args.skills, root, dry_run=args.dry_run)
    except UserSkillsError as exc:
        print(f"skills-user: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(f"skills-user: {line}")
    print(f"skills-user: {root} holds the selection; rerun with the same names to update it")
    return 0
