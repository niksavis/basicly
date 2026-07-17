"""Behavioral-rubric catalog sources: authoring + selection (basicly-0122).

basicly gates *artifacts* generically (tests/lint pass) and offers an advisory
semantic review, but has no use-case-tied yes/no **behavioral** rubrics — "did
the agent add a regression test for the bug?", "did it address every acceptance
criterion?" (foundry spike Dimension 7). This module is the authoring half: a
rubric is a catalog source (``*.rubric.yaml``) shaped like the other lightweight
catalog manifests (``hooks.yaml``/``permissions.yaml`` — imperative validation,
no JSON schema), and rubrics are selected for a bead by its work type.

Each rubric lists yes/no ``checks``; a check is either ``deterministic`` (a
command whose exit code answers it — evaluated via the verify runner) or
``judged`` (an agent answers yes/no with evidence — evaluated via a review-style
prompt through the agent-agnostic runner). The evaluation + advisory-gate half
lands in basicly-0122.2; this module owns the source model and selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalog import bundled_catalog_root

RUBRICS_DIRNAME = "rubrics"
RUBRIC_GLOB = "*.rubric.yaml"

# Check kinds.
DETERMINISTIC = "deterministic"
JUDGED = "judged"
CHECK_KINDS = (DETERMINISTIC, JUDGED)


@dataclass(frozen=True)
class RubricCheck:
    """One yes/no behavioral check within a rubric."""

    id: str
    question: str
    kind: str
    # For a deterministic check: the command whose exit code answers the question
    # (0 = yes/pass). Empty for a judged check.
    command: str = ""


@dataclass(frozen=True)
class Rubric:
    """A work-type-tied set of behavioral checks."""

    id: str
    description: str
    applies_to: tuple[str, ...]
    checks: tuple[RubricCheck, ...]


def _catalog_rubrics_dir() -> Path:
    return bundled_catalog_root() / RUBRICS_DIRNAME


def _parse_check(entry: object, where: str) -> RubricCheck:
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a mapping")
    for key in ("id", "question", "kind"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ValueError(f"{where} is missing a non-empty {key!r}")
    kind = entry["kind"].strip()
    if kind not in CHECK_KINDS:
        raise ValueError(f"{where} has unknown kind {kind!r}; allowed: {list(CHECK_KINDS)}")
    command = entry.get("command", "")
    if not isinstance(command, str):
        raise ValueError(f"{where} 'command' must be a string")
    if kind == DETERMINISTIC and not command.strip():
        raise ValueError(f"{where} is deterministic but has no 'command' to run")
    if kind == JUDGED and command.strip():
        raise ValueError(f"{where} is judged, so it must not carry a 'command'")
    return RubricCheck(
        id=entry["id"].strip(),
        question=entry["question"].strip(),
        kind=kind,
        command=command.strip(),
    )


def _parse_rubric(data: object, path: Path) -> Rubric:
    if not isinstance(data, dict):
        raise ValueError(f"{path}: rubric must be a mapping")
    for key in ("id", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{path}: rubric is missing a non-empty {key!r}")
    applies_to = data.get("applies_to")
    if not (isinstance(applies_to, list) and applies_to) or not all(
        isinstance(item, str) and item.strip() for item in applies_to
    ):
        raise ValueError(f"{path}: 'applies_to' must be a non-empty list of work-type strings")
    raw_checks = data.get("checks")
    if not (isinstance(raw_checks, list) and raw_checks):
        raise ValueError(f"{path}: 'checks' must be a non-empty list")
    checks = tuple(
        _parse_check(entry, f"{path}: check[{index}]") for index, entry in enumerate(raw_checks)
    )
    return Rubric(
        id=data["id"].strip(),
        description=data["description"].strip(),
        applies_to=tuple(item.strip() for item in applies_to),
        checks=checks,
    )


def load_rubrics(rubrics_dir: Path | None = None) -> list[Rubric]:
    """Load and validate every ``*.rubric.yaml`` in the given (or bundled) dir.

    Validated imperatively (the lightweight ``hooks.yaml`` pattern, no JSON
    schema). A missing directory yields no rubrics; a malformed file raises.
    """
    rubrics_dir = rubrics_dir or _catalog_rubrics_dir()
    if not rubrics_dir.is_dir():
        return []
    rubrics: list[Rubric] = []
    for path in sorted(rubrics_dir.glob(RUBRIC_GLOB)):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rubrics.append(_parse_rubric(data, path))
    return rubrics


def select_rubrics(rubrics: list[Rubric], work_type: str) -> list[Rubric]:
    """The rubrics whose ``applies_to`` includes *work_type*, in load order."""
    return [rubric for rubric in rubrics if work_type in rubric.applies_to]
