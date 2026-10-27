from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

from . import skill_coverage, skills


def skill_path(repo_root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or path.as_posix() != relative or len(path.parts) != 4:
        raise ValueError("Expected a projected skill path: <host>/skills/<name>/SKILL.md")
    if (
        Path(*path.parts[:2]) not in skills.DEFAULT_SKILL_ROOTS
        or path.name != skills.SKILL_FILE_NAME
    ):
        raise ValueError("Expected a projected skill path: <host>/skills/<name>/SKILL.md")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", path.parts[2]):
        raise ValueError("Invalid projected skill name")
    target = repo_root / path
    resolved = target.resolve()
    if not resolved.is_relative_to(repo_root.resolve()) or not resolved.is_relative_to(
        (repo_root / path.parts[0] / "skills").resolve()
    ):
        raise ValueError("Expected a projected skill inside its host skills directory")
    return resolved


def read_skill_reference(repo_root: Path, relative: str, sha256: str) -> str:
    path = skill_path(repo_root, relative)
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Expected a SHA256 digest from the dispatch context manifest")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValueError(
            f"Projected skill {relative} is unavailable; refresh the dispatch brief"
        ) from error
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(
            f"Projected skill {relative} changed; refresh the dispatch brief before continuing"
        )
    try:
        return data.decode("utf-8")
    except UnicodeError as error:
        raise ValueError(
            f"Projected skill {relative} is not UTF-8; repair its projection"
        ) from error


def skill_brief(
    repo_root: Path, names: Sequence[str], *, family: str = "claude"
) -> tuple[str, tuple[str, ...]]:
    references, missing = [], []
    roots = (
        tuple(reversed(skills.DEFAULT_SKILL_ROOTS))
        if family == "codex"
        else skills.DEFAULT_SKILL_ROOTS
    )
    for name in dict.fromkeys(names):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError("Invalid projected skill name")
        for root in roots:
            relative = (root / name / skills.SKILL_FILE_NAME).as_posix()
            path = skill_path(repo_root, relative)
            try:
                data = path.read_bytes()
                data.decode("utf-8")
            except OSError, UnicodeError:
                continue
            digest = hashlib.sha256(data).hexdigest()
            references.append({
                "name": name,
                "path": relative,
                "sha256": digest,
                "bytes": len(data),
                "delivery": "deferred",
                "reason": "Declared by role or work unit",
                "retrieve": ["basicly", "skill-read", relative, "--sha256", digest],
            })
            break
        else:
            missing.append(name)
    manifest = {
        "schema_version": 1,
        "skills": references,
        "coverage": "Declared skill references only; host-loaded guidance is not measured.",
    }
    return json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ) if references else "", tuple(missing)


def brief_skills(
    repo_root: Path, family: str, role: str | None, work_type: str | None, phase: str | None
) -> tuple[str, ...]:

    declared = skill_coverage.role_skills(repo_root, family, role) if role else ()
    ordered = [*declared, *skill_coverage.unit_skills(repo_root, work_type, phase)]
    return tuple(dict.fromkeys(ordered))


def with_skills(prompt: str, brief: str, missing: Sequence[str] = ()) -> str:
    if not brief and not missing:
        return prompt
    parts = []
    if brief:
        parts.append(
            "Context manifest: these declared skill bodies are deferred, not delivered. "
            "Retrieve each applicable skill before acting on its procedure, using the listed "
            "argv in this worktree; follow its linked references when needed. If a digest "
            "is stale, refresh the brief and reassess changed guidance before continuing. "
            "Do not treat this index as a complete contract.\n\n" + brief
        )
    if missing:
        parts.append(
            f"Unavailable declared skills: {', '.join(missing)}. "
            "Resolve their projection or an explicitly available host skill before relying "
            "on them. If required guidance cannot be retrieved, stop and report the missing fact."
        )
    return "\n\n".join([*parts, "---", prompt])
