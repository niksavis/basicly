from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import decisions, decompose, policy

if TYPE_CHECKING:
    from .config import SizingConfig


SIZING_QUESTION = "working set is outside the configured band: re-scope it or widen the band?"

UNSIZED_QUESTION = "working set was never checked: declare this package's scope?"

_UNDERSIZE_DISPOSAL = (
    "dispatched anyway: under-cutting the floor wastes per-lane overhead, "
    "but the package is deliverable and holding it would strand it"
)
_UNSIZED_DISPOSAL = (
    "dispatched anyway: an undeclared scope is the normal state of a hand-filed bead, "
    "so holding it would ban hand-filed work rather than size it — recorded so this "
    "dispatch is not mistaken for one the band checked"
)

_DECLARE_WORKING_SET = (
    " — or, if completing that declaration for the merge collision gate is what made it "
    "large, declare the subset the lane must actually read under a `## Working Set` "
    "heading, which the band prices instead"
)


@dataclass(frozen=True)
class WorkingSetAdmission:
    issue_id: str
    sizing: decompose.DispatchSizing | None
    violation: str | None
    refused: bool
    absence: str = ""

    def record_inputs(self, repo_root: Path) -> dict[str, object]:

        return {} if self.sizing is None else self.sizing.record_inputs(repo_root)

    @property
    def checked(self) -> bool:

        return self.sizing is not None


def admit_working_set(repo_root: Path, issue_id: str, sizing: SizingConfig) -> WorkingSetAdmission:

    lookup = decompose.SizingLookup(None, decompose.SCOPE_UNREADABLE)
    with contextlib.suppress(RuntimeError, ValueError, OSError):
        lookup = decompose.resolve_dispatch_sizing(repo_root, issue_id)
    resolved = lookup.sizing
    if resolved is None:
        unchecked = (
            policy.unchecked_working_set(issue_id, sizing)
            if lookup.absence in (decompose.SCOPE_UNDECLARED, decompose.SCOPE_GREENFIELD)
            else None
        )
        return WorkingSetAdmission(issue_id, None, unchecked, refused=False, absence=lookup.absence)
    estimate = resolved.estimate
    violation = policy.check_working_set(issue_id, estimate.total, estimate.scope_tokens, sizing)
    refused = violation is not None and estimate.total > sizing.working_set_max
    if refused and resolved.working_set_source == decompose.WORKING_SET_FROM_SCOPE:
        violation = f"{violation}{_DECLARE_WORKING_SET}"
    return WorkingSetAdmission(issue_id, resolved, violation, refused=refused)


def escalate_working_set(
    repo_root: Path, admission: WorkingSetAdmission
) -> decisions.DecisionItem | None:

    if admission.violation is None:
        return None
    unsized = admission.absence in (decompose.SCOPE_UNDECLARED, decompose.SCOPE_GREENFIELD)
    item = decisions.enqueue(
        repo_root,
        admission.issue_id,
        "escalation",
        UNSIZED_QUESTION if unsized else SIZING_QUESTION,
        admission.violation,
        human_required=admission.refused,
    )
    if not admission.refused:
        decisions.answer(
            repo_root,
            item.decision_id,
            _UNSIZED_DISPOSAL if unsized else _UNDERSIZE_DISPOSAL,
            by=decisions.ENGINE_BY,
        )
    return item


def band_coverage(working_sets: tuple[WorkingSetAdmission, ...]) -> str:

    if not working_sets:
        return "no lanes to check"
    by_absence: dict[str, list[str]] = {}
    checked: list[str] = []
    for item in working_sets:
        if item.checked:
            checked.append(item.issue_id)
        else:
            by_absence.setdefault(item.absence, []).append(item.issue_id)
    parts = []
    if checked:
        parts.append(f"checked: {', '.join(checked)}")
    for absence, ids in sorted(by_absence.items()):
        parts.append(f"NEVER CHECKED ({absence}): {', '.join(ids)}")
    return "; ".join(parts)


def band_report(working_sets: tuple[WorkingSetAdmission, ...]) -> tuple[str, ...]:

    sized = sorted(
        (w for w in working_sets if w.sizing is not None),
        key=lambda w: w.sizing.estimate.total if w.sizing else 0,
        reverse=True,
    )
    lines = [
        f"  {w.issue_id:<22} {w.sizing.estimate.total:>9} tok  {_band_verdict(w)}"
        for w in sized
        if w.sizing is not None
    ]
    lines += [
        f"  {w.issue_id:<22} {'unsized':>9}      no scope the estimator can read"
        for w in working_sets
        if w.sizing is None
    ]
    return tuple(lines)


def _band_verdict(admission: WorkingSetAdmission) -> str:

    if admission.refused:
        return "REFUSED - too large, split it"
    if admission.violation is not None:
        return "under the floor - dispatches, but merge it with a sibling"
    if admission.sizing is not None and admission.sizing.estimate.scope_tokens == 0:
        return "in band, but its scope matched no file"
    return "in band"
