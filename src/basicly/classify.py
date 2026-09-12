from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import integrity, policy
from .config import WORK_TYPES
from .tracker import add_comment as _add_comment
from .tracker import read_comments as _read_comments
from .tracker import write as _write

CLASSIFICATION_MARKER = integrity.CLASSIFICATION_MARKER


@dataclass(frozen=True)
class ClassifyResult:
    issue_id: str
    work_type: str
    dor: policy.DoRResult
    level: str = ""

    @property
    def can_leave_classify(self) -> bool:
        return self.dor.ready


def classify(
    repo_root: Path, issue_id: str, work_type: str, scope: tuple[str, ...] = ()
) -> ClassifyResult:

    if work_type not in WORK_TYPES:
        raise ValueError(f"unknown work type {work_type!r}; expected one of {list(WORK_TYPES)}")
    _write(repo_root, ["update", issue_id, "-t", work_type])
    assignment = integrity.assign(scope)
    _record_classification(repo_root, issue_id, assignment)
    dor = policy.definition_of_ready(repo_root, issue_id)
    return ClassifyResult(issue_id=issue_id, work_type=work_type, dor=dor, level=assignment.level)


def _record_classification(
    repo_root: Path, issue_id: str, assignment: integrity.Assignment
) -> None:

    selects = assignment.selection
    body = (
        f"{CLASSIFICATION_MARKER} level={assignment.level} rule={assignment.rule} "
        f"gates={','.join(selects.gates)} tier={selects.model_tier} "
        f"rework={selects.rework_allowance} ship={selects.ship} "
        f"reason={assignment.reason}"
    )
    if any(
        str(comment.get("text", "")).strip() == body
        for comment in _read_comments(repo_root, issue_id)
    ):
        return
    _add_comment(repo_root, issue_id, body)
