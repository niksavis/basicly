"""The file a verify run leaves behind for the evidence gate to point at.

One responsibility, and it is the artifact: turn a run's verdict into a durable file
at :data:`RUN_ARTIFACT`, in a way that never costs the caller the verdict it already
has. ``[policy.evidence]`` (basicly-m4zv.13) refuses an advance past a phase unless
that phase's declared artifact exists and is non-empty — presence only, the engine
never opens it. It ships no producer by design, and verify had nothing to point at:
check output streams straight to the terminal and is captured only on the diagnostic
re-run, so a *passing* run wrote nothing anywhere and declaring an artifact for the
verify phase would have refused every advance (basicly-m0s4).

Split out of :mod:`basicly.verify` when the module-size ratchet caught that module
growing. The boundary is *the record* against *the run*: nothing here executes a
check, reads the config, or decides pass from fail — that is the whole of
``verify``, which calls in once per run with the verdict it produced.
:class:`RunVerdict` is a structural protocol rather than an import of
``verify.VerifyReport``, so the split leaves no import back into the module it came
from.

Verdict metadata only, never a check's output. Streaming is the contract a consumer
watching a gate depends on, so nothing here captures anything; and the redaction rule
``run_record`` states holds the same way — a tool's stdout can carry a secret, a
status and a return code cannot.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

# Written into the self-ignored ``.basicly/usage/`` directory for the reason
# ``run_record`` puts its records there: the landing refuses to merge while the
# checkout carries dirt outside ``.beads/``, so a file rewritten by every verify run
# must not be tracked. The bead keeps the durable half — ``policy.record_evidence``
# records the declared *path* on the issue.
RUN_ARTIFACT = Path(".basicly/usage/verify-run.json")


@runtime_checkable
class CheckOutcome(Protocol):
    """One check's recorded outcome. ``verify.CheckResult`` satisfies it structurally."""

    @property
    def name(self) -> str:
        """The check's configured name."""
        ...

    @property
    def status(self) -> str:
        """``pass``, ``fail`` or ``skip``."""
        ...

    @property
    def returncode(self) -> int:
        """The command's exit status, or the synthetic one a failed spawn reports."""
        ...

    @property
    def detail(self) -> str:
        """One line of context the tool itself could not report; empty when it did."""
        ...


@runtime_checkable
class RunVerdict(Protocol):
    """A whole run's verdict. ``verify.VerifyReport`` satisfies it structurally.

    Members are read-only properties rather than mutable attributes, for the reason
    ``plan_gate.PlannedFields`` states: a plain ``mode: str`` declares a writable slot
    that a frozen dataclass can never satisfy.
    """

    @property
    def mode(self) -> str:
        """The verify mode the run was configured from."""
        ...

    @property
    def passed(self) -> bool:
        """Whether no check failed."""
        ...

    @property
    def results(self) -> tuple[CheckOutcome, ...]:
        """Every check the run produced an outcome for."""
        ...


def write_run_artifact(repo_root: Path, report: RunVerdict) -> Path | None:
    """Persist *report*'s verdict to :data:`RUN_ARTIFACT`; its path, or None if unwritable.

    Always non-empty, including for a mode that configures no checks: the object
    still records that a run happened and found nothing to run, which is a
    different fact from no run at all — and an empty file fails the gate.

    Written for a failing run too. The artifact is the record of a run, not of a
    pass; the verdict is what the required ``verify`` gate is for, and an artifact
    that appeared only on success would make "the file is here" mean two things.

    Never raises. The verdict is what the caller asked for and must not be lost to
    an artifact write — and the failure is not silent, because with no file on
    disk the evidence gate refuses the advance and names this exact path. Writes
    through a pid-scoped temporary file rather than
    :func:`basicly.projection.atomic_write_text` for the reason
    :func:`basicly.usage.record_verify_check` does: two runs in one checkout would
    otherwise interleave a truncated write with the other's rename.
    """
    path = repo_root / RUN_ARTIFACT
    payload = {
        "mode": report.mode,
        "recorded_at": datetime.now(UTC).isoformat(),
        "passed": report.passed,
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "returncode": r.returncode,
                "detail": r.detail,
            }
            for r in report.results
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        gitignore = path.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    return path
