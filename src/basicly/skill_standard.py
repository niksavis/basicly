from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog_source import SKILLS_DIR, load_mapping, rel
from .skill_source import EVAL_SOURCE_FILE, SKILL_SOURCE_FILE

MAX_BODY_LINES = 500
MIN_TASK_CASES = 3
TOC_LINE_THRESHOLD = 100
TOC_WINDOW = 20

DEBT: frozenset[str] = frozenset()

_RESOURCE = r"(?:\.\./[a-z0-9-]+/)?(?:references|scripts|assets)/[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]+"
_LINK_RE = re.compile(r"\]\(([^)#\s]+)\)")
_RESOURCE_RE = re.compile(rf"(?<![\w/.-])({_RESOURCE})")
_SKILL_DIR_RE = re.compile(rf"\$\{{CLAUDE_SKILL_DIR\}}/({_RESOURCE})")
_DEEP_RE = re.compile(r"^(?:references|scripts|assets)/[^/]+/")
_TOC_RE = re.compile(r"^#{1,3}\s+(?:contents|table of contents)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class SkillStandard:
    slug: str
    source: str
    problems: tuple[str, ...]


def _linked_paths(instructions: str) -> set[str]:
    found = {
        target
        for target in _LINK_RE.findall(instructions)
        if "://" not in target and not target.startswith("mailto:")
    }
    found.update(_RESOURCE_RE.findall(instructions))
    found.update(_SKILL_DIR_RE.findall(instructions))
    return found


def _link_problems(skill_dir: Path, instructions: str) -> list[str]:
    problems: list[str] = []
    for target in sorted(_linked_paths(instructions)):
        if not (skill_dir / target).is_file():
            problems.append(f"links '{target}', which is not a file beside {SKILL_SOURCE_FILE}")
        if _DEEP_RE.match(target):
            problems.append(f"links '{target}' more than one level deep; link it from a top folder")
    return problems


def _toc_problems(skill_dir: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted((skill_dir / "references").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if len(text.splitlines()) <= TOC_LINE_THRESHOLD:
            continue
        head = "\n".join(text.splitlines()[:TOC_WINDOW])
        if not _TOC_RE.search(head):
            problems.append(
                f"references/{path.name} is over {TOC_LINE_THRESHOLD} lines with no "
                f"'## Contents' heading in its first {TOC_WINDOW} lines"
            )
    return problems


def _task_problems(skill_dir: Path) -> list[str]:
    path = skill_dir / EVAL_SOURCE_FILE
    if not path.is_file():
        return [
            f"has no {EVAL_SOURCE_FILE}; add trigger cases and at least "
            f"{MIN_TASK_CASES} task cases (see skill-creator)"
        ]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return [f"{EVAL_SOURCE_FILE} is not valid YAML"]
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list) or len(tasks) < MIN_TASK_CASES:
        count = len(tasks) if isinstance(tasks, list) else 0
        return [
            f"{EVAL_SOURCE_FILE} has {count} task cases; add at least {MIN_TASK_CASES}, "
            "each with a query and its expected_behavior"
        ]
    problems: list[str] = []
    for index, task in enumerate(tasks, start=1):
        files = task.get("files", []) if isinstance(task, dict) else []
        problems.extend(
            f"{EVAL_SOURCE_FILE} task {index} names file '{name}', which does not exist"
            for name in files
            if isinstance(name, str) and not (skill_dir / name).is_file()
        )
    return problems


def assess(skill_dir: Path, repo_root: Path) -> SkillStandard:
    source = skill_dir / SKILL_SOURCE_FILE
    data = load_mapping(source) or {}
    instructions = data.get("instructions")
    body = instructions if isinstance(instructions, str) else ""
    problems: list[str] = []
    lines = len(body.splitlines())
    if lines > MAX_BODY_LINES:
        problems.append(
            f"SKILL.md body is {lines} lines; keep it at most {MAX_BODY_LINES} "
            "and move detail into references/"
        )
    problems.extend(_link_problems(skill_dir, body))
    problems.extend(_toc_problems(skill_dir))
    problems.extend(_task_problems(skill_dir))
    return SkillStandard(skill_dir.name, rel(source, repo_root), tuple(problems))


def violations(repo_root: Path, debt: frozenset[str] = DEBT) -> list[str]:
    found: list[str] = []
    for source in sorted((repo_root / SKILLS_DIR).glob(f"*/{SKILL_SOURCE_FILE}")):
        result = assess(source.parent, repo_root)
        if result.slug in debt:
            if not result.problems:
                found.append(
                    f"{result.source}: '{result.slug}' now meets the skill standard; "
                    "remove it from DEBT in src/basicly/skill_standard.py"
                )
            continue
        found.extend(f"{result.source}: {problem}" for problem in result.problems)
    return found
