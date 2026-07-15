"""Catalog source lint — the deterministic gate that keeps the YAML contract.

Enforces four invariants across the managed core catalog so the double-load fix
and the single-extension decision cannot regress (architecture §4.2):

1. No discoverable-name *sources*: no ``SKILL.md`` under ``core/skills`` and no
   ``*.fragment.md`` under ``core/fragments`` (they belong at target roots only).
2. One YAML extension: no ``*.yml`` under ``core`` (the catalog uses ``.yaml``).
3. Every source validates against its JSON Schema in ``core/schemas``.
4. Enforcement pointer (§3.1): a fragment that declares ``enforced_by`` must cite
   each listed command in its body — point at enforcement, don't restate it.

``README.md`` and other documentation files are not sources and are left alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

CORE_DIR = Path(".basicly/core")
SKILLS_DIR = CORE_DIR / "skills"
FRAGMENTS_DIR = CORE_DIR / "fragments"
SCHEMAS_DIR = CORE_DIR / "schemas"


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _validator(repo_root: Path, name: str) -> Draft202012Validator:
    schema = json.loads((repo_root / SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate(path: Path, validator: Draft202012Validator, repo_root: Path) -> list[str]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{_rel(path, repo_root)}: invalid YAML: {exc}"]
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [f"{_rel(path, repo_root)}: {err.message}" for err in errors]


def _check_enforcement_pointer(path: Path, repo_root: Path) -> list[str]:
    """Flag enforced_by commands (§3.1) that the fragment body does not cite."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []  # schema validation already reports malformed YAML
    if not isinstance(data, dict):
        return []
    commands = data.get("enforced_by") or []
    body = data.get("body") or ""
    if not isinstance(commands, list) or not isinstance(body, str):
        return []  # schema validation already reports the type error
    return [
        f"{_rel(path, repo_root)}: enforced_by command '{command}' is not cited in the body"
        for command in commands
        if isinstance(command, str) and command not in body
    ]


def lint_catalog(repo_root: Path) -> list[str]:
    """Return a list of catalog-lint violations (empty when the catalog is clean)."""
    violations: list[str] = []
    core = repo_root / CORE_DIR
    if not core.exists():
        return violations

    # 1. no discoverable-name sources
    for path in sorted((repo_root / SKILLS_DIR).rglob("SKILL.md")):
        violations.append(
            f"{_rel(path, repo_root)}: skill sources must be skill.yaml, not SKILL.md"
        )
    for path in sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.md")):
        violations.append(
            f"{_rel(path, repo_root)}: fragment sources must be *.fragment.yaml, not *.fragment.md"
        )

    # 2. single YAML extension
    for path in sorted(core.rglob("*.yml")):
        violations.append(f"{_rel(path, repo_root)}: use the .yaml extension, not .yml")

    # 3. schema validation
    skill_validator = _validator(repo_root, "skill.schema.json")
    fragment_validator = _validator(repo_root, "fragment.schema.json")
    for path in sorted((repo_root / SKILLS_DIR).glob("*/skill.yaml")):
        violations.extend(_validate(path, skill_validator, repo_root))
    for path in sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.yaml")):
        violations.extend(_validate(path, fragment_validator, repo_root))

    # 4. enforcement-pointer check (§3.1)
    for path in sorted((repo_root / FRAGMENTS_DIR).rglob("*.fragment.yaml")):
        violations.extend(_check_enforcement_pointer(path, repo_root))

    return violations
