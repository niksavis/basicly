from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from . import artifact_record, catalog_source, plan_gate

if TYPE_CHECKING:
    from jsonschema import Draft202012Validator

IMPLEMENTATION_PLAN = "implementation-plan"
CHANGE_SUMMARY = "change-summary"
RELEASE_RECORD = "release-record"

PRODUCERS: dict[str, str | None] = {
    IMPLEMENTATION_PLAN: "decompose:decompose",
    CHANGE_SUMMARY: "loop:_record_change_summary",
    RELEASE_RECORD: "curate:record",
    "classification": None,
    "change-shape": None,
    "validation-transcript": None,
    "verification-evidence": None,
    "solution-design": None,
}

SCHEMA_VERSION = 1


class ArtifactError(ValueError):
    def __init__(self, verdict: ArtifactVerdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass(frozen=True)
class ArtifactVerdict:
    issue_id: str
    kind: str
    violations: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return not self.violations

    @property
    def reason(self) -> str:
        if not self.violations:
            return ""
        return (
            f"{self.issue_id}'s {self.kind} artifact does not validate: "
            f"{'; '.join(self.violations)}"
        )


def wired(kind: str) -> bool:
    return PRODUCERS.get(kind) is not None


def _validator(repo_root: Path, kind: str) -> Draft202012Validator | None:

    if not wired(kind):
        return None
    try:
        return catalog_source.schema_validator(repo_root, f"{kind}.schema.json")
    except OSError:
        return None


def adopted(repo_root: Path, kind: str) -> bool:

    return _validator(repo_root, kind) is not None


def _violations(validator: Draft202012Validator, payload: object) -> tuple[str, ...]:

    instance = cast("Any", payload)
    errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.path))
    return tuple(f"{err.json_path}: {err.message}" for err in errors)


def record(repo_root: Path, issue_id: str, kind: str, payload: dict) -> None:

    validator = _validator(repo_root, kind)
    if validator is None:
        return
    violations = _violations(validator, payload)
    if violations:
        raise ArtifactError(ArtifactVerdict(issue_id, kind, violations))
    artifact_record.write(repo_root, issue_id, kind, payload)


def entry_verdict(repo_root: Path, issue_id: str, kind: str) -> ArtifactVerdict:

    validator = _validator(repo_root, kind)
    if validator is None:
        return ArtifactVerdict(issue_id, kind)
    payload = artifact_record.read(repo_root, issue_id, kind)
    if payload is None:
        return ArtifactVerdict(issue_id, kind)
    violations = _violations(validator, payload)
    if not violations:
        return ArtifactVerdict(issue_id, kind)
    cut = artifact_record.cut_violation(repo_root, issue_id, kind, payload)
    return ArtifactVerdict(issue_id, kind, (cut,) if cut else violations)


@runtime_checkable
class PlannedTask(Protocol):
    @property
    def issue_id(self) -> str: ...

    @property
    def spec(self) -> plan_gate.PlannedUnit: ...

    @property
    def depends_on(self) -> tuple[str, ...]: ...


@runtime_checkable
class RecordedDecomposition(Protocol):
    @property
    def feature_id(self) -> str: ...

    @property
    def children(self) -> tuple[PlannedTask, ...]: ...

    @property
    def groups(self) -> tuple[tuple[str, ...], ...]: ...


def plan_payload(result: RecordedDecomposition) -> dict:

    return {
        "schema_version": SCHEMA_VERSION,
        "feature": result.feature_id,
        "tasks": [
            {
                "issue_id": child.issue_id,
                "title": child.spec.title,
                "acceptance": list(child.spec.acceptance),
                "scope": list(child.spec.scope),
                "depends_on": list(child.depends_on),
                "budget_tokens": child.spec.budget_tokens,
                "integrity": child.spec.integrity,
                "demonstration": child.spec.demonstration,
            }
            for child in result.children
        ],
        "groups": [list(group) for group in result.groups],
    }


@dataclass(frozen=True)
class SelfCheck:
    status: str
    detail: str
    passed: bool


def _changed_facts(changed: tuple[str, ...]) -> tuple[int, str]:

    paths = sorted(set(changed))
    return len(paths), hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()


def summary_payload(
    issue_id: str, why: str, built: tuple[str, tuple[str, ...]], self_check: SelfCheck
) -> dict:

    commit, changed = built
    count, digest = _changed_facts(changed)
    return {
        "schema_version": SCHEMA_VERSION,
        "issue": issue_id,
        "why": why,
        "commit": commit,
        "changed_count": count,
        "changed_digest": digest,
        "self_check": {
            "status": self_check.status,
            "passed": self_check.passed,
            "detail": self_check.detail,
        },
    }
