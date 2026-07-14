"""Classify step: record an agent-proposed br work type (onb.6.2).

Thin engine, same shape as the decomposer: the *agent* proposes the work class,
this module VALIDATES it against the fixed br set and RECORDS it with
``br update -t``. ``br`` itself is permissive about the type string, so the
allow-list guard lives here — an unknown class is a loud error, never silently
written.

Definition-of-Ready is reported, not enforced here: per architecture §12.2 the
loop runs Classify (agent proposes, engine records the type) → _[human
checkpoint]_ → Decompose, and the Decompose entry is what DoR gates. So
:func:`classify` records the type unconditionally and surfaces the DoR verdict
(via the policy engine, onb.3); the state machine (onb.6.3) blocks the exit
from classify — advancement to decompose — until DoR is ready. The human
classify checkpoint stays in the policy engine (``approve_checkpoint``); this
module records only the ``br`` type.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import policy
from .config import WORK_TYPES


def _run_br(
    repo_root: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a ``br`` subcommand. Raises if ``br`` is absent — classify records into the tracker."""
    br = shutil.which("br")
    if not br:
        raise RuntimeError("br is not on PATH; the classifier requires the beads tracker")
    proc = subprocess.run(  # nosec B603
        [br, *args], cwd=repo_root, capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"br {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


@dataclass(frozen=True)
class ClassifyResult:
    """The recorded work type plus the Definition-of-Ready verdict for the transition."""

    issue_id: str
    work_type: str
    dor: policy.DoRResult

    @property
    def can_leave_classify(self) -> bool:
        """True when DoR is satisfied, so the loop may advance to decompose (§12.2)."""
        return self.dor.ready


def classify(repo_root: Path, issue_id: str, work_type: str) -> ClassifyResult:
    """Record the agent-proposed *work_type* on *issue_id* and report its DoR verdict.

    Rejects a type outside the fixed br set (:data:`WORK_TYPES`) with a loud
    ``ValueError`` before touching the tracker. The recorded type is written with
    ``br update -t``; the returned :class:`ClassifyResult` carries the
    Definition-of-Ready verdict so the state machine can gate the exit from
    classify.
    """
    if work_type not in WORK_TYPES:
        raise ValueError(f"unknown work type {work_type!r}; expected one of {list(WORK_TYPES)}")
    _run_br(repo_root, ["update", issue_id, "-t", work_type])
    dor = policy.definition_of_ready(repo_root, issue_id)
    return ClassifyResult(issue_id=issue_id, work_type=work_type, dor=dor)
