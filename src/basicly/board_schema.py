"""Rule on whether one document is a ``harness-board`` snapshot this consumer may read.

The boundary is *the ruling* against *the folding*: nothing here reads engine state or
composes a snapshot, which is the producer's, and nothing here resolves or parses the
schema file, which is :mod:`basicly.catalog_source`'s. What is left is the verdict, and
the section inventory is part of it rather than a second job — "which panels can be
drawn" is the answer a caller wants from a document it was told is readable.

Deliberately unlike :mod:`basicly.handoff`, which shares the schema-resolution seam and
inverts every tolerance around it. A handoff artifact is strict, so an undeclared key is
a refusal; a board snapshot is a contract for producers that are not this harness, so an
undeclared key is counted and named and the document still passes. Both directions are
the versioning rule the ledger already fixes - only add keys and optional sections - read
from the two ends of it.

Absence of the schema is *not* inert here, and that is the one place this departs from
``handoff`` on purpose. A gate inside the loop that cannot find its contract has to admit
the unit or stop the harness; a caller that has explicitly asked whether a file conforms
must be told it could not be answered, or the answer is a fail-open pass.
"""

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

# The major this consumer speaks. A different one is a different contract, not a
# newer version of this one.
MAJOR = 1
VERSION = f"{CONTRACT}/v{MAJOR}"

_DECLARED = re.compile(rf"^{re.escape(CONTRACT)}/v([1-9][0-9]*)$")

OK = "ok"
WRONG_MAJOR = "wrong-major"
INVALID = "invalid"
UNREADABLE = "unreadable"
NOT_INSTALLED = "not-installed"

# Reserved for the one refusal that is the contract speaking rather than a defect in the
# file or in this install: the document is well formed and belongs to another major.
REFUSED = 2


@dataclass(frozen=True)
class SnapshotVerdict:
    """What one candidate snapshot turned out to be, and what a board could draw from it."""

    outcome: str
    declared: str | None = None
    present: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    detail: str = ""

    @property
    def readable(self) -> bool:
        """True when a board may render this document."""
        return self.outcome == OK

    @property
    def exit_code(self) -> int:
        """0 readable, :data:`REFUSED` for another major, 1 for everything else."""
        if self.outcome == OK:
            return 0
        return REFUSED if self.outcome == WRONG_MAJOR else 1

    @property
    def summary(self) -> str:
        """The verdict as a caller prints it, the inventory and the unknown count included.

        Rendered here rather than by the command, following ``handoff.ArtifactVerdict``:
        the words a contract is refused in are part of the contract, and a second surface
        wording them differently is how one refusal comes to read as two.
        """
        return "\n".join(_summary_lines(self))


def _summary_lines(verdict: SnapshotVerdict) -> Iterator[str]:
    """The lines of :attr:`SnapshotVerdict.summary`."""
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
    yield f"{VERSION}, ok" if verdict.readable else f"{VERSION}, does not validate"
    yield f"present   {', '.join(verdict.present) or 'nothing beyond the required keys'}"
    yield f"absent    {', '.join(verdict.absent) or 'nothing'}"
    if verdict.unknown:
        yield f"unknown   {len(verdict.unknown)} key(s): {', '.join(verdict.unknown)}"
    for violation in verdict.violations:
        yield f"invalid   {violation}"


def _validator(repo_root: Path) -> Draft202012Validator | None:
    """The installed snapshot validator, or None where the contract is not installed."""
    try:
        return catalog_source.schema_validator(repo_root, SCHEMA_FILE)
    except OSError:
        return None


def adopted(repo_root: Path) -> bool:
    """True when *repo_root* carries the snapshot contract."""
    return _validator(repo_root) is not None


def declared_major(document: object) -> int | None:
    """The major *document* declares, or None when it declares nothing parseable.

    Read off the document rather than validated, because a consumer has to name the
    version of a file it is about to refuse.
    """
    if not isinstance(document, dict):
        return None
    declared = document.get("schema")
    found = _DECLARED.match(declared) if isinstance(declared, str) else None
    return int(found.group(1)) if found else None


def _violations(validator: Draft202012Validator, document: object) -> tuple[str, ...]:
    """*document*'s violations as ``<json path>: <message>`` lines."""
    instance = cast("Any", document)
    errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.path))
    return tuple(f"{err.json_path}: {err.message}" for err in errors)


def _child(node: dict, key: str) -> dict | None:
    """The subschema *key* is read under, or None when *node* declares no home for it."""
    declared = node.get("properties")
    if isinstance(declared, dict) and isinstance(declared.get(key), dict):
        return declared[key]
    extra = node.get("additionalProperties")
    return extra if isinstance(extra, dict) else None


def _unknown_keys(node: object, instance: object, path: str) -> Iterator[str]:
    """Paths of keys *node* does not define, at every depth.

    Derived by diffing the instance against the declared properties rather than by
    reading jsonschema's ``additionalProperties`` message, for the reason
    ``catalog_source._missing_required`` states: a wording change upstream must not be
    able to silently empty this list. Nothing here is an error - the count is the
    contract's tolerance made visible, so an added key is reported and never dropped.
    """
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
    """The optional top-level sections, taken from the schema so the two cannot drift."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    return tuple(name for name in properties if name not in required)


def verdict(repo_root: Path, document: object) -> SnapshotVerdict:
    """Rule on an already-decoded *document* against the contract installed in *repo_root*.

    The major is checked before the body: a v2 document may well validate against the v1
    schema field for field and still mean something else, so a structural pass on it
    would be the guess the version rule exists to forbid.
    """
    validator = _validator(repo_root)
    if validator is None:
        # Repo-relative: this document is published the moment anyone commits it, and an
        # absolute path is the shortest route for a machine username into one.
        missing = (catalog_source.SCHEMAS_DIR / SCHEMA_FILE).as_posix()
        return SnapshotVerdict(NOT_INSTALLED, detail=f"{missing} is not installed")
    declared = document.get("schema") if isinstance(document, dict) else None
    major = declared_major(document)
    if major is not None and major != MAJOR:
        return SnapshotVerdict(WRONG_MAJOR, declared=str(declared))
    # jsonschema types a schema as `bool | Mapping`; a boolean schema would admit
    # everything, and the installed one is the object this module ships.
    schema = cast("dict[str, Any]", validator.schema)
    sections = _sections(schema)
    held = set(document) if isinstance(document, dict) else set()
    violations = _violations(validator, document)
    return SnapshotVerdict(
        OK if not violations else INVALID,
        declared=declared if isinstance(declared, str) else None,
        present=tuple(name for name in sections if name in held),
        absent=tuple(name for name in sections if name not in held),
        unknown=tuple(_unknown_keys(schema, document, "$")),
        violations=violations,
    )


def validate_file(repo_root: Path, path: Path) -> SnapshotVerdict:
    """Rule on the snapshot at *path*; a file that will not decode is ``UNREADABLE``."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        return SnapshotVerdict(UNREADABLE, detail=f"{path}: {err.strerror}")
    except json.JSONDecodeError as err:
        return SnapshotVerdict(UNREADABLE, detail=f"{path}: not JSON: {err}")
    return verdict(repo_root, document)
