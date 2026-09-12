from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from jsonschema.exceptions import ValidationError

CORE_DIR = Path(".basicly/core")
SKILLS_DIR = CORE_DIR / "skills"
FRAGMENTS_DIR = CORE_DIR / "fragments"
AGENTS_DIR = CORE_DIR / "agents"
HOOKS_DIR = CORE_DIR / "hooks"
RUBRICS_DIR = CORE_DIR / "rubrics"
STYLES_DIR = CORE_DIR / "output-styles"
SCHEMAS_DIR = CORE_DIR / "schemas"


def rel(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def schema_validator(repo_root: Path, name: str) -> Draft202012Validator:

    schema = json.loads((repo_root / SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _missing_required(err: ValidationError) -> str | None:

    if err.validator != "required" or not isinstance(err.instance, dict):
        return None
    required = err.validator_value
    if not isinstance(required, list):
        return None
    return next((p for p in required if p not in err.instance), None)


def schema_violations(
    path: Path,
    validator: Draft202012Validator,
    repo_root: Path,
    *,
    owned_required: frozenset[str] = frozenset(),
) -> list[str]:

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{rel(path, repo_root)}: invalid YAML: {exc}"]
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [
        f"{rel(path, repo_root)}: {err.message}"
        for err in errors
        if _missing_required(err) not in owned_required
    ]


def load_mapping(path: Path) -> dict | None:

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None
