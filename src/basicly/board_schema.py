from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from . import catalog_source

if TYPE_CHECKING:
    from collections.abc import Iterator

    from jsonschema import Draft202012Validator

SCHEMA_FILE = "board-snapshot.schema.json"
CONTRACT = "harness-board"

MAJOR = 1
VERSION = f"{CONTRACT}/v{MAJOR}"

_DECLARED = re.compile(rf"^{re.escape(CONTRACT)}/v([1-9][0-9]*)$")

OK = "ok"
PARTIAL = "partly-renderable"
WRONG_MAJOR = "wrong-major"
INVALID = "invalid"
UNREADABLE = "unreadable"
NOT_INSTALLED = "not-installed"

REFUSED = 2

PARTLY_RENDERABLE = 3


@dataclass(frozen=True)
class SectionVerdict:
    name: str
    violations: tuple[str, ...] = ()

    @property
    def conformant(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class SnapshotVerdict:
    outcome: str
    declared: str | None = None
    present: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    sections: tuple[SectionVerdict, ...] = ()
    detail: str = ""

    @property
    def readable(self) -> bool:
        return self.outcome in {OK, PARTIAL}

    @property
    def renderable(self) -> tuple[str, ...]:

        if not self.readable:
            return ()
        return tuple(section.name for section in self.sections if section.conformant)

    @property
    def withheld(self) -> tuple[str, ...]:
        return tuple(section.name for section in self.sections if not section.conformant)

    @property
    def exit_code(self) -> int:
        if self.outcome == OK:
            return 0
        if self.outcome == WRONG_MAJOR:
            return REFUSED
        return PARTLY_RENDERABLE if self.outcome == PARTIAL else 1

    @property
    def summary(self) -> str:

        return "\n".join(_summary_lines(self))


_HEADLINE = {OK: "ok", PARTIAL: "partly renderable"}


def _summary_lines(verdict: SnapshotVerdict) -> Iterator[str]:
    if verdict.outcome == WRONG_MAJOR:
        yield (
            f'refused - snapshot declares schema "{verdict.declared}", '
            f"this consumer reads {VERSION}"
        )
        yield "A major version is a different contract. Nothing was rendered."
        return
    if verdict.outcome in {UNREADABLE, NOT_INSTALLED}:
        yield f"{verdict.outcome}: {verdict.detail}"
        return
    yield f"{VERSION}, {_HEADLINE.get(verdict.outcome, 'does not validate')}"
    yield f"present   {', '.join(verdict.present) or 'nothing beyond the required keys'}"
    yield f"absent    {', '.join(verdict.absent) or 'nothing'}"
    if verdict.unknown:
        yield f"unknown   {len(verdict.unknown)} key(s): {', '.join(verdict.unknown)}"
    if verdict.outcome == PARTIAL:
        yield f"renders   {', '.join(verdict.renderable) or 'nothing beyond the required keys'}"
        yield f"withheld  {', '.join(verdict.withheld)}"
        yield "A withheld section renders as not conformant; the rest of the board draws."
    elif verdict.outcome == INVALID:
        yield "The required part does not validate, so no section renders."
    for violation in verdict.violations:
        yield f"invalid   {violation}"


def _validator(repo_root: Path) -> Draft202012Validator | None:
    try:
        return catalog_source.schema_validator(repo_root, SCHEMA_FILE)
    except OSError:
        return None


def adopted(repo_root: Path) -> bool:
    return _validator(repo_root) is not None


def declared_major(document: object) -> int | None:

    if not isinstance(document, dict):
        return None
    declared = document.get("schema")
    found = _DECLARED.match(declared) if isinstance(declared, str) else None
    return int(found.group(1)) if found else None


def _attributed(
    validator: Draft202012Validator, document: object, sections: tuple[str, ...]
) -> list[tuple[str | None, str]]:

    instance = cast("Any", document)
    errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.path))
    owned = set(sections)
    pairs = []
    for err in errors:
        head = next(iter(err.path), None)
        owner = head if isinstance(head, str) and head in owned else None
        pairs.append((owner, f"{err.json_path}: {err.message}"))
    return pairs


def _child(node: dict, key: str) -> dict | None:
    declared = node.get("properties")
    if isinstance(declared, dict) and isinstance(declared.get(key), dict):
        return declared[key]
    extra = node.get("additionalProperties")
    return extra if isinstance(extra, dict) else None


def _unknown_keys(node: object, instance: object, path: str) -> Iterator[str]:

    if not isinstance(node, dict):
        return
    if isinstance(instance, dict):
        for key, value in instance.items():
            child = _child(node, key)
            if child is None:
                yield f"{path}.{key}"
            else:
                yield from _unknown_keys(child, value, f"{path}.{key}")
    elif isinstance(instance, list):
        item = node.get("items")
        for index, value in enumerate(instance):
            yield from _unknown_keys(item, value, f"{path}[{index}]")


def _sections(schema: dict) -> tuple[str, ...]:
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    return tuple(name for name in properties if name not in required)


def _outcome(pairs: list[tuple[str | None, str]], ruled: tuple[SectionVerdict, ...]) -> str:
    if any(owner is None for owner, _ in pairs):
        return INVALID
    return PARTIAL if any(not section.conformant for section in ruled) else OK


def verdict(repo_root: Path, document: object) -> SnapshotVerdict:

    validator = _validator(repo_root)
    if validator is None:
        missing = (catalog_source.SCHEMAS_DIR / SCHEMA_FILE).as_posix()
        return SnapshotVerdict(NOT_INSTALLED, detail=f"{missing} is not installed")
    declared = document.get("schema") if isinstance(document, dict) else None
    major = declared_major(document)
    if major is not None and major != MAJOR:
        return SnapshotVerdict(WRONG_MAJOR, declared=str(declared))
    schema = cast("dict[str, Any]", validator.schema)
    sections = _sections(schema)
    held = set(document) if isinstance(document, dict) else set()
    present = tuple(name for name in sections if name in held)
    pairs = _attributed(validator, document, sections)
    ruled = tuple(
        SectionVerdict(name, tuple(line for owner, line in pairs if owner == name))
        for name in present
    )
    return SnapshotVerdict(
        _outcome(pairs, ruled),
        declared=declared if isinstance(declared, str) else None,
        present=present,
        absent=tuple(name for name in sections if name not in held),
        unknown=tuple(_unknown_keys(schema, document, "$")),
        violations=tuple(line for _, line in pairs),
        sections=ruled,
    )


def validate_file(repo_root: Path, path: Path) -> SnapshotVerdict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        return SnapshotVerdict(UNREADABLE, detail=f"{path}: {err.strerror}")
    except json.JSONDecodeError as err:
        return SnapshotVerdict(UNREADABLE, detail=f"{path}: not JSON: {err}")
    return verdict(repo_root, document)
