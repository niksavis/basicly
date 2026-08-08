"""A catalog source on disk: where one lives, and what reading it yields.

One responsibility, and it is the read. Every gate over the catalog starts from the
same two questions — *where does this kind of source live* and *did this file parse
and validate* — and both answers are here: the directory constants that name the core
tree, and the readers that turn a path into either the mapping it holds or the
``path: message`` lines saying why it cannot be used.

The ``path: message`` shape is load-bearing rather than cosmetic. A load-time failure
and a lint walk report the same source, and when the two spelled a path differently a
reader could not tell they were about the same file (basicly-ky5z); :func:`rel` is the
single place that decides, so they cannot drift apart again.

Split out of ``catalog_lint`` when the module-size ratchet caught that module growing,
and it has two consumers rather than one: :mod:`basicly.catalog_lint` rules on the
Tier-1 source contract, :mod:`basicly.routing_evals` runs the Tier-2 evals, and both
read through here. The boundary is *reading* against *ruling* — nothing here knows
which invariants a catalog must satisfy, so this module needs no import back into
either of the gates that ask it.
"""

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
SCHEMAS_DIR = CORE_DIR / "schemas"


def rel(path: Path, repo_root: Path) -> str:
    """*path* as a repo-relative POSIX string, or unchanged when it is outside the repo."""
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def schema_validator(repo_root: Path, name: str) -> Draft202012Validator:
    """The validator built from ``core/schemas/<name>``.

    Built on demand rather than at import: a repo with no catalog has no schemas
    directory, and constructing one eagerly would fail a consumer that installed
    nothing.
    """
    schema = json.loads((repo_root / SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _missing_required(err: ValidationError) -> str | None:
    """The property name a jsonschema ``required`` error is about, else None.

    Derived by diffing the required list against the instance rather than parsed
    out of ``err.message``, so a jsonschema wording change cannot silently stop a
    caller's suppression from matching.
    """
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
    """Schema violations for one source, as ``path: message`` lines.

    ``owned_required`` names properties whose absence a later, more helpful check
    reports instead: the raw jsonschema line is dropped so one defect yields one
    diagnostic. Nothing else is suppressed — a wrong *value* for such a property
    still reports here.
    """
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
    """The YAML mapping at *path*, or ``None`` when it is malformed or is not one.

    Silent on purpose: every caller runs after :func:`schema_violations` has already
    reported the malformed source by name, and raising here would replace that
    diagnostic with a traceback.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None
